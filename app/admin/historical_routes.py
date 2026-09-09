"""Private, owner-scoped preview workflow for historical spreadsheet imports.

The browser sends decisions, never a trusted import plan or a filesystem path.
The staged workbook is checked again before each analysis and application.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import time
from uuid import uuid4

from flask import abort, current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from werkzeug.exceptions import HTTPException
from werkzeug.utils import secure_filename

from app.admin.routes import bp
from app.extensions import db
from app.rbac import can_access_secteur, require_perm
from app.secteurs import get_secteur_labels


def _sectors():
    return [s for s in get_secteur_labels(active_only=True) if can_access_secteur(s)]


def _assert_sectors(plan):
    allowed = set(_sectors())
    if any(a.get("secteur") and a["secteur"] not in allowed for a in plan.get("activities", [])):
        abort(403)


def _root() -> Path:
    root = Path(current_app.instance_path) / "historical_imports"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    return root.resolve()


def _path(stage_id: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{32}", stage_id):
        abort(404)
    path = _root() / stage_id
    if path.is_symlink() or path.resolve().parent != _root():
        abort(404)
    return path


def _read_json(path: Path):
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def _write_json(path: Path, value):
    temp = path.with_name(path.name + ".tmp")
    with temp.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, default=str)
    temp.replace(path)


def _sha256(path: Path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _cleanup_expired():
    """Only remove recognized expired stages within our private directory."""
    root = _root()
    for stage in root.iterdir():
        if not re.fullmatch(r"[a-f0-9]{32}", stage.name) or stage.is_symlink() or not stage.is_dir():
            continue
        if stage.resolve().parent != root or (stage / ".lock").exists():
            continue
        try:
            metadata = _read_json(stage / "metadata.json")
            if float(metadata["expires_at"]) < time.time():
                shutil.rmtree(stage)
        except (OSError, ValueError, TypeError, KeyError):
            continue


def _load(stage_id):
    stage = _path(stage_id)
    try:
        metadata = _read_json(stage / "metadata.json")
    except (OSError, ValueError):
        abort(404)
    # A different owner must not learn whether a stage exists or has expired.
    if str(metadata.get("owner_id")) != str(current_user.id):
        abort(404)
    _assert_sectors(_read_json(stage / "plan.json"))
    if float(metadata.get("expires_at", 0)) <= time.time():
        abort(410, "Cet aperçu a expiré. Déposez à nouveau le classeur.")
    filename = metadata.get("source_file", "")
    source = stage / filename
    if not filename or source.is_symlink() or source.resolve().parent != stage.resolve():
        abort(409, "Le fichier préparé n'est plus valide.")
    if not source.is_file() or _sha256(source) != metadata.get("source_sha256"):
        abort(409, "Le fichier préparé a changé. Déposez à nouveau le classeur.")
    return stage, metadata, source


@contextmanager
def _stage_lock(stage):
    """Serialize decisions and apply, including across worker processes."""
    lock = stage / ".lock"
    try:
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        abort(409, "Une opération est déjà en cours sur cet aperçu.")
    try:
        os.close(fd)
        yield
    finally:
        lock.unlink(missing_ok=True)


class ApercuPerime(Exception):
    """L'aperçu envoyé ne correspond plus à celui du serveur.

    Une page d'erreur nue laisserait l'utilisateur sans savoir si sa saisie
    est passée : l'appelant réaffiche l'aperçu à jour avec l'explication.
    """


def _assert_preview(plan, metadata):
    if metadata.get("applied"):
        raise ApercuPerime("Ce lot a déjà été importé. Vos modifications n'ont pas été enregistrées.")
    if not request.form.get("digest") or request.form["digest"] != plan.get("digest"):
        raise ApercuPerime(
            "Vos décisions n'ont PAS été enregistrées : l'aperçu a changé pendant votre saisie. "
            "Cela arrive quand la base bouge, par exemple si quelqu'un d'autre saisit une présence, "
            "ou si vous travaillez dans deux onglets. La page ci-dessous est à jour : refaites la "
            "saisie ici, et enregistrez plus souvent pour limiter la reprise.")


def _rows(plan):
    rows = plan.get("matching", {}).get("rows", [])
    if isinstance(rows, dict):
        return [dict(row, key=key) for key, row in rows.items()]
    return rows


def _key(row):
    return str(row.get("source_key") or row.get("key") or row.get("source_id") or "")


VUES_PARTICIPANTS = ("a-traiter", "regles", "tous")
DOSSIERS_PAR_PAGE = 50


def _en_attente(row):
    """Une ligne attend une décision aux conditions exactes qui la rendent bloquante."""
    revue = row.get("classification") == "REVIEW" or row.get("status") == "REVIEW"
    return bool(revue) and not row.get("resolved") and not row.get("group_key")


def _libelle_ligne(row):
    brut = row.get("raw", {}) if isinstance(row.get("raw"), dict) else {}
    nom = str(row.get("nom") or brut.get("nom") or "").strip()
    prenom = str(row.get("prenom") or brut.get("prenom") or "").strip()
    return (f"{nom.upper()} {prenom}".strip()) or _key(row) or "Ligne sans identité"


def _dossiers(plan, decisions):
    """Regroupe les lignes source par identité, exactement comme le triage.

    L'aperçu compte des lignes, l'utilisateur raisonne par personne : une même
    personne occupe souvent plusieurs lignes réparties dans autant de feuilles.
    L'écran et les propositions du triage parlent ainsi des mêmes dossiers.
    Un rapport inexploitable retombe sur un dossier par ligne plutôt que de
    priver l'utilisateur de son aperçu.
    """
    rows = _rows(plan)
    enregistrees = decisions.get("participants", {})
    positions = {}
    for index, row in enumerate(rows):
        positions.setdefault(_key(row), (index, row))

    try:
        from app.ateliers.historical_triage import trier_personnes
        matching = dict(plan.get("matching", {}), rows=rows)
        groupes = trier_personnes(dict(plan, matching=matching))
    except Exception:
        current_app.logger.info("Regroupement par dossier indisponible", exc_info=True)
        groupes = [{"libelle": _libelle_ligne(row), "lignes": [_key(row)], "presences": 0,
                    "feuilles": [], "orthographes": [], "fiches_erp": [], "voisins": [],
                    "alertes": [], "statut": None, "categorie": None, "motif": ""}
                   for row in rows]

    # « Même personne que la ligne X » exige que X porte elle-même une décision :
    # l'identifiant de regroupement est la voie sûre, on la propose toute faite.
    from app.ateliers.historical_triage import _slug
    pris = {d["group"] for d in enregistrees.values() if d.get("group")}

    dossiers = []
    for groupe in groupes:
        lignes = []
        for key in groupe.get("lignes", []):
            if key not in positions:
                continue
            index, row = positions[key]
            lignes.append({"index": index, "key": key, "row": row,
                           "saved": enregistrees.get(key, {}),
                           "en_attente": _en_attente(row)})
        if not lignes:
            continue
        dossier = dict(groupe, lignes=lignes)
        dossier["a_traiter"] = any(ligne["en_attente"] for ligne in lignes)
        if len(lignes) > 1 and not (groupe.get("decision") or {}).get("group"):
            base = _slug(f"{groupe.get('nom') or ''} {groupe.get('prenom') or ''} "
                         f"{groupe.get('annee') or 'sans-annee'}") or "dossier"
            suggestion, suffixe = base, 2
            while suggestion in pris:
                suggestion, suffixe = f"{base}-{suffixe}", suffixe + 1
            pris.add(suggestion)
            dossier["groupe_suggere"] = suggestion
        dossier["recherche"] = " ".join([
            str(groupe.get("libelle") or ""), *groupe.get("orthographes", []),
            *groupe.get("feuilles", []), *(ligne["key"] for ligne in lignes),
        ]).casefold()
        dossiers.append(dossier)
    return dossiers


def _selection_dossiers(dossiers, vue, recherche, page, taille=DOSSIERS_PAR_PAGE):
    """Découpe la liste des dossiers : un écran doit rester lisible et fini."""
    if vue == "regles":
        retenus = [d for d in dossiers if not d["a_traiter"]]
    elif vue == "tous":
        retenus = list(dossiers)
    else:
        retenus = [d for d in dossiers if d["a_traiter"]]
    if recherche:
        motif = recherche.casefold()
        retenus = [d for d in retenus if motif in d["recherche"]]
    pages = max(1, -(-len(retenus) // taille))
    page = min(max(page, 1), pages)
    return {
        "dossiers": retenus[(page - 1) * taille:page * taille],
        "page": page, "pages": pages, "retenus": len(retenus),
        "total": len(dossiers), "taille": taille,
        "a_traiter": sum(1 for d in dossiers if d["a_traiter"]),
        "lignes": sum(len(d["lignes"]) for d in dossiers),
    }


def _vue_participants(plan):
    """Lit les filtres de l'URL ; toute valeur inattendue retombe sur la vue utile."""
    vue = request.args.get("vue", "a-traiter")
    if vue not in VUES_PARTICIPANTS:
        vue = "a-traiter"
    recherche = (request.args.get("q") or "").strip()[:80]
    try:
        page = int(request.args.get("page", 1))
    except (TypeError, ValueError):
        page = 1
    decisions = plan.get("decisions", {})
    return vue, recherche, _selection_dossiers(_dossiers(plan, decisions), vue, recherche, page)


def _form_decisions(plan, previous):
    upload = request.files.get("decisions_file")
    if upload and upload.filename:
        raw = upload.stream.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError("Le fichier de décisions dépasse 2 Mo.")
        decisions = json.loads(raw.decode("utf-8-sig"))
        if not isinstance(decisions, dict):
            raise ValueError("Le fichier de décisions doit contenir un objet JSON.")
        return decisions
    decisions = dict(previous)
    collections = {
        "participants": _rows(plan),
        "activities": plan.get("activities", []),
        "sessions": plan.get("sessions", []),
    }
    for kind, rows in collections.items():
        decisions[kind] = dict(previous.get(kind, {}))
        for index, row in enumerate(rows):
            field = f"{kind}.{index}"
            if field not in request.form:
                continue
            key = _key(row)
            choice = request.form[field]
            if not choice:
                # Un secteur ou un nom métier corrigé valent décision à eux seuls :
                # le moteur les honore sans action de correspondance, et jeter une
                # saisie en silence la fait disparaître de l'écran sans explication.
                partielle = {}
                if kind == "activities":
                    if request.form.get(field + ".secteur"):
                        partielle["secteur"] = request.form[field + ".secteur"]
                    if request.form.get(field + ".name", "").strip():
                        partielle["name"] = request.form[field + ".name"].strip()
                if partielle:
                    decisions[kind][key] = partielle
                else:
                    decisions[kind].pop(key, None)
                continue
            if choice == "ignore":
                decision = {"action": "ignore"}
            elif choice == "new":
                decision = {"action": "new"}
                if kind == "participants":
                    group = request.form.get(field + ".group", "").strip()
                    if group:
                        decision["group"] = group
                    corrected = {name: request.form.get(field + "." + name, "").strip() for name in ("nom", "prenom")}
                    if any(corrected.values()):
                        decision["values"] = {k: v for k, v in corrected.items() if v}
                elif kind == "sessions":
                    corrected_date = request.form.get(field + ".date", "").strip()
                    if corrected_date:
                        decision["date_session"] = corrected_date
            elif choice == "date" and kind == "sessions":
                decision = {"action": "date", "date_session": request.form.get(field + ".date", "").strip()}
            elif choice.startswith("existing:"):
                target = int(choice.split(":", 1)[1])
                id_field = {"participants": "participant_id", "activities": "atelier_id", "sessions": "session_id"}[kind]
                decision = {"action": "participant" if kind == "participants" else "existing", id_field: target}
            elif choice.startswith("source:") and kind == "participants":
                decision = {"action": "source", "source_id": choice.split(":", 1)[1]}
            else:
                raise ValueError("Une décision de rapprochement est invalide.")
            if kind == "activities" and request.form.get(field + ".secteur"):
                decision["secteur"] = request.form[field + ".secteur"]
            if kind == "activities" and request.form.get(field + ".name", "").strip():
                decision["name"] = request.form[field + ".name"].strip()
            decisions[kind][key] = decision
    # The list is intentionally replaced, so users may withdraw an acknowledgement.
    decisions["acknowledged_anomalies"] = request.form.getlist("acknowledged_anomalies")
    return decisions


def _preview_response(stage_id, metadata, plan, error=None, status=200):
    vue, recherche, participants = _vue_participants(plan)
    return render_template(
        "admin_import_historical.html", stage_id=stage_id, metadata=metadata,
        plan=plan, match_rows=_rows(plan), row_key=_key,
        participants=participants, vue=vue, recherche=recherche,
        decisions=plan.get("decisions", {}), error=error, secteurs=_sectors(),
    ), status


@bp.after_request
def _private_historical_response(response):
    if (request.endpoint or "").startswith("admin.historical_"):
        response.headers["Cache-Control"] = "no-store, private"
        response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@bp.route("/import-historical", methods=["GET", "POST"])
@login_required
@require_perm("ateliers:sync")
def historical_import():
    if request.method == "GET":
        return render_template("admin_import_historical.html", secteurs=_sectors())
    upload = request.files.get("xlsx_file")
    if not upload or not upload.filename or Path(upload.filename).suffix.lower() != ".xlsx":
        return render_template("admin_import_historical.html", secteurs=_sectors(), error="Choisissez un classeur .xlsx."), 400
    try:
        year = int(request.form["year"]) if request.form.get("year", "").strip() else None
        if year is not None and not 1900 <= year <= 2100:
            raise ValueError()
    except ValueError:
        return render_template("admin_import_historical.html", secteurs=_sectors(), error="L'année doit être comprise entre 1900 et 2100."), 400
    _cleanup_expired()
    stage_id = uuid4().hex
    stage = _path(stage_id)
    stage.mkdir(mode=0o700)
    filename = secure_filename(upload.filename) or "source.xlsx"
    source = stage / filename
    try:
        upload.save(source)
        if source.stat().st_size > current_app.config.get("HISTORICAL_IMPORT_MAX_BYTES", 20 * 1024 * 1024):
            abort(413)
        now = time.time()
        ttl = min(max(int(current_app.config.get("HISTORICAL_IMPORT_TTL_SECONDS", 7 * 86400)), 60), 30 * 86400)
        metadata = {
            "owner_id": current_user.id, "year": year,
            "source_filename": upload.filename, "source_file": filename,
            "source_sha256": _sha256(source), "created_at": now,
            "expires_at": now + ttl, "applied": False,
        }
        from app.ateliers.historical_import import analyze_import
        plan = analyze_import(str(source), year=year)
        _assert_sectors(plan)
        _write_json(stage / "plan.json", plan)
        _write_json(stage / "decisions.json", plan.get("decisions", {}))
        _write_json(stage / "metadata.json", metadata)
    except Exception as exc:
        db.session.rollback()
        shutil.rmtree(stage)
        if isinstance(exc, HTTPException):
            raise
        return render_template("admin_import_historical.html", secteurs=_sectors(), error=f"Analyse impossible : {exc}"), 400
    return redirect(url_for("admin.historical_preview", stage_id=stage_id))


@bp.route("/import-historical/<stage_id>")
@login_required
@require_perm("ateliers:sync")
def historical_preview(stage_id):
    stage, metadata, _ = _load(stage_id)
    return _preview_response(stage_id, metadata, _read_json(stage / "plan.json"))


@bp.route("/import-historical/<stage_id>/decisions", methods=["POST"])
@login_required
@require_perm("ateliers:sync")
def historical_decisions(stage_id):
    stage, _, _ = _load(stage_id)
    with _stage_lock(stage):
        stage, metadata, source = _load(stage_id)
        plan = _read_json(stage / "plan.json")
        try:
            _assert_preview(plan, metadata)
            precedentes = _read_json(stage / "decisions.json")
            decisions = _form_decisions(plan, precedentes)
            from app.ateliers.historical_import import analyze_import
            depart = time.monotonic()
            refreshed = analyze_import(str(source), decisions=decisions, year=metadata["year"])
            duree = time.monotonic() - depart
            _assert_sectors(refreshed)
            _write_json(stage / "plan.json", refreshed)
            _write_json(stage / "decisions.json", refreshed.get("decisions", decisions))
        except ApercuPerime as exc:
            return _preview_response(stage_id, metadata, plan, error=str(exc), status=409)
        except Exception as exc:
            db.session.rollback()
            if isinstance(exc, HTTPException):
                raise
            return _preview_response(stage_id, metadata, plan, error=f"Décisions non enregistrées : {exc}", status=400)
    message, ton = _bilan_enregistrement(precedentes, decisions, plan, refreshed, duree)
    flash(message, ton)
    return redirect(_retour_apercu(stage_id))


def _normaliser_decisions(decisions):
    """Forme comparable : un lot vide et un lot aux sections vides sont identiques."""
    normalisees = {section: dict(decisions.get(section) or {})
                   for section in ("participants", "activities", "sessions")}
    normalisees["acknowledged_anomalies"] = sorted(decisions.get("acknowledged_anomalies") or [])
    return normalisees


def _pluriel(nombre, singulier, pluriel=None):
    return f"{nombre} {singulier if nombre <= 1 else (pluriel or singulier + 's')}"


def _bilan_enregistrement(avant, apres, plan, refreshed, duree):
    """Dire noir sur blanc ce que l'enregistrement a changé, y compris « rien »."""
    avant, apres = _normaliser_decisions(avant), _normaliser_decisions(apres)
    bloquants, restants = len(plan.get("blockers", [])), len(refreshed.get("blockers", []))
    total = ", ".join((
        _pluriel(len(apres["participants"]), "personne"),
        _pluriel(len(apres["activities"]), "activité"),
        _pluriel(len(apres["sessions"]), "séance"),
        _pluriel(len(apres["acknowledged_anomalies"]), "anomalie acceptée", "anomalies acceptées"),
    ))
    if avant == apres:
        return (f"Enregistrement effectué, mais rien n'a changé : les décisions envoyées étaient déjà "
                f"celles du serveur ({total}). Si vous veniez d'en saisir, elles n'ont pas été transmises — "
                f"vérifiez que vous étiez bien sur les dossiers affichés. Recalcul en {duree:.1f} s.", "warning")
    evolution = (f"points bloquants {bloquants} → {restants}" if restants != bloquants
                 else f"points bloquants inchangés ({restants})")
    return (f"Décisions enregistrées : {total}. {evolution.capitalize()}. "
            f"Recalcul du dry-run en {duree:.1f} s.", "success")


def _retour_apercu(stage_id):
    """Ramener l'utilisateur là où il travaillait, filtre et page compris."""
    filtres = {nom: (request.form.get(nom) or "").strip() for nom in ("vue", "q", "page")}
    return url_for("admin.historical_preview", stage_id=stage_id,
                   **{k: v for k, v in filtres.items() if v}, _anchor="participants")


def _fusion_decisions(existantes, proposees):
    """Une proposition complète les décisions prises, elle ne les remplace jamais."""
    fusion = {}
    for section in ("participants", "activities", "sessions"):
        fusion[section] = dict(proposees.get(section, {}))
        fusion[section].update(existantes.get(section, {}))
    fusion["acknowledged_anomalies"] = list(dict.fromkeys(
        list(existantes.get("acknowledged_anomalies", []))
        + list(proposees.get("acknowledged_anomalies", []))))
    return fusion


@bp.route("/import-historical/<stage_id>/triage", methods=["POST"])
@login_required
@require_perm("ateliers:sync")
def historical_triage(stage_id):
    """Regroupe l'aperçu par dossier et propose les décisions évidentes.

    Aucune règle de rapprochement n'est assouplie : le triage écrit les
    décisions qu'un humain aurait saisies pour les cas que le classeur tranche
    lui-même, et laisse tout le reste bloquant.
    """
    stage, _, _ = _load(stage_id)
    with _stage_lock(stage):
        stage, metadata, source = _load(stage_id)
        plan = _read_json(stage / "plan.json")
        try:
            _assert_preview(plan, metadata)
            from app.ateliers.historical_triage import trier
            from app.ateliers.historical_import import analyze_import
            triage = trier(plan, secteurs_connus=_sectors())
            existantes = _read_json(stage / "decisions.json")
            decisions = _fusion_decisions(existantes, triage["decisions"])
            ajoutees = sum(1 for section in ("participants", "activities", "sessions")
                           for key in triage["decisions"][section]
                           if key not in existantes.get(section, {}))
            depart = time.monotonic()
            refreshed = analyze_import(str(source), decisions=decisions, year=metadata["year"])
            duree = time.monotonic() - depart
            _assert_sectors(refreshed)
            _write_json(stage / "plan.json", refreshed)
            _write_json(stage / "decisions.json", refreshed.get("decisions", decisions))
        except ApercuPerime as exc:
            return _preview_response(stage_id, metadata, plan, error=str(exc), status=409)
        except Exception as exc:
            db.session.rollback()
            if isinstance(exc, HTTPException):
                raise
            return _preview_response(stage_id, metadata, plan,
                                     error=f"Triage non appliqué : {exc}", status=400)
    bloquants, restants = len(plan.get("blockers", [])), len(refreshed.get("blockers", []))
    evolution = (f"Points bloquants {bloquants} → {restants}." if restants != bloquants
                 else f"Points bloquants inchangés ({restants}).")
    if not ajoutees:
        # Le triage est stable : relancé sur le même classeur et la même base, il
        # propose exactement les mêmes décisions. Le dire évite de le recliquer.
        flash(f"Le triage n'a rien de nouveau à proposer : tout ce qu'il sait trancher est déjà "
              f"enregistré. {evolution} Recalcul en {duree:.1f} s. Il ne reproposera du nouveau "
              f"que si des fiches ou des activités apparaissent dans l'ERP entre-temps.", "warning")
        return redirect(_retour_apercu(stage_id))
    resume = triage["resume"]
    flash(f"Triage proposé : {_pluriel(ajoutees, 'décision ajoutée', 'décisions ajoutées')}, dont "
          f"{resume['personnes_rattachees']} rattachements à une fiche existante, "
          f"{resume['personnes_regroupees']} regroupements de lignes, "
          f"{resume['seances_datees']} dates de séance et "
          f"{resume['activites_avec_secteur']} secteurs déduits du nom métier. {evolution} "
          f"Recalcul en {duree:.1f} s. Aucune décision déjà enregistrée n'a été remplacée et aucune "
          f"anomalie n'a été acquittée. Relisez les secteurs proposés : ils sont déduits du seul nom "
          f"de la feuille.", "success")
    return redirect(_retour_apercu(stage_id))


@bp.route("/import-historical/<stage_id>/apply", methods=["POST"])
@login_required
@require_perm("ateliers:sync")
def historical_apply(stage_id):
    stage, _, _ = _load(stage_id)
    with _stage_lock(stage):
        stage, metadata, source = _load(stage_id)
        plan = _read_json(stage / "plan.json")
        try:
            _assert_preview(plan, metadata)
        except ApercuPerime as exc:
            return _preview_response(stage_id, metadata, plan, error=str(exc), status=409)
        if request.form.get("confirm") != "yes":
            return _preview_response(stage_id, metadata, plan, error="Confirmez l'import du lot présenté avant de l'enregistrer.", status=400)
        if not plan.get("ready"):
            return _preview_response(stage_id, metadata, plan, error="Les points bloquants doivent être résolus avant l'import.", status=409)
        try:
            from app.ateliers.historical_import import apply_import
            result = apply_import(
                str(source), decisions=_read_json(stage / "decisions.json"),
                year=metadata["year"], expected_digest=plan["digest"], actor_id=current_user.id,
            )
        except Exception as exc:
            db.session.rollback()
            if isinstance(exc, HTTPException):
                raise
            return _preview_response(stage_id, metadata, plan, error=f"Import non effectué : {exc}. Relancez la prévisualisation avant de réessayer.", status=409)
        metadata["applied"] = True
        metadata["applied_at"] = time.time()
        metadata["result"] = result
        _write_json(stage / "metadata.json", metadata)
    flash("Le lot a été enregistré. Le résultat et le rapport restent consultables dans cet aperçu.", "success")
    return redirect(url_for("admin.historical_preview", stage_id=stage_id))


@bp.route("/import-historical/<stage_id>/report.json")
@login_required
@require_perm("ateliers:sync")
def historical_report(stage_id):
    stage, _, _ = _load(stage_id)
    response = jsonify(_read_json(stage / "plan.json"))
    response.headers["Content-Disposition"] = 'attachment; filename="rapport-migration.json"'
    return response


@bp.route("/import-historical/<stage_id>/decisions.json")
@login_required
@require_perm("ateliers:sync")
def historical_decisions_export(stage_id):
    stage, _, _ = _load(stage_id)
    response = jsonify(_read_json(stage / "decisions.json"))
    response.headers["Content-Disposition"] = 'attachment; filename="decisions-migration.json"'
    return response
