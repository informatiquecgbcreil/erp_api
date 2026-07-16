"""Module RH minimal — réservé à la direction (permissions rh:view / rh:edit).

Salariés du centre : la direction affecte chaque salarié à son secteur et à
son poste dans le secteur. Nourrit le SENACS (emplois / ETP) et la masse
salariale. Peut être alimenté par import CSV/Excel depuis un outil RH externe
(la colonne « référence » sert de clé de rapprochement : réimporter met à
jour au lieu de dupliquer).
"""
import csv
import io
from datetime import date

from flask import render_template, request, redirect, url_for, flash, current_app, Response
from flask_login import login_required

from app.rbac import require_perm, can
from app.extensions import db
from app.models import RecupRhSnapshot, Salarie, SENACS_TYPES_CONTRAT, SENACS_TYPES_CONTRAT_DICT
from app.services.audit import journaliser

from app.main.common import bp


def _parse_date(raw):
    raw = (raw or "").strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            from datetime import datetime as _dt
            return _dt.strptime(raw, fmt).date()
        except Exception:
            continue
    return None


def _parse_float(raw, default=None):
    try:
        return round(float(str(raw).replace(",", ".").replace(" ", "")), 2)
    except Exception:
        return default


def _remplir_salarie(salarie: Salarie, form) -> None:
    salarie.nom = (form.get("nom") or salarie.nom or "").strip()
    salarie.prenom = (form.get("prenom") or "").strip() or None
    salarie.poste = (form.get("poste") or "").strip() or None
    salarie.secteur = (form.get("secteur") or "").strip() or None
    contrat = (form.get("type_contrat") or "cdi").strip()
    salarie.type_contrat = contrat if contrat in SENACS_TYPES_CONTRAT_DICT else "autre"
    etp = _parse_float(form.get("etp"), 1.0) or 1.0
    salarie.etp = min(max(etp, 0.0), 2.0)
    salarie.salaire_brut_charge = _parse_float(form.get("salaire_brut_charge"))
    salarie.date_entree = _parse_date(form.get("date_entree"))
    salarie.date_sortie = _parse_date(form.get("date_sortie"))
    salarie.commentaire = (form.get("commentaire") or "").strip() or None


def _salaire_annuel(salarie: Salarie, snapshot: RecupRhSnapshot | None) -> float:
    """Valeur RH saisie manuellement, sinon estimation issue de Recup."""
    if salarie.salaire_brut_charge is not None:
        return float(salarie.salaire_brut_charge)
    if snapshot is not None:
        return float(snapshot.annual_loaded_cost_eur(salarie.etp) or 0)
    return 0.0


def stats_rh(annee: int) -> dict:
    """Effectif, ETP et masse salariale des salariés actifs sur l'exercice."""
    salaries = Salarie.query.order_by(Salarie.nom.asc(), Salarie.prenom.asc()).all()
    actifs = [s for s in salaries if s.actif_sur(annee)]
    recup_snapshots = {
        snapshot.salarie_id: snapshot
        for snapshot in RecupRhSnapshot.query.filter_by(year=annee).all()
    }
    par_secteur: dict[str, dict] = {}
    for s in actifs:
        sec = (s.secteur or "Non affecté").strip() or "Non affecté"
        d = par_secteur.setdefault(sec, {"nb": 0, "etp": 0.0, "masse": 0.0})
        d["nb"] += 1
        d["etp"] = round(d["etp"] + float(s.etp or 0), 2)
        d["masse"] = round(d["masse"] + _salaire_annuel(s, recup_snapshots.get(s.id)), 2)
    return {
        "salaries": salaries,
        "actifs": actifs,
        "sortis": [s for s in salaries if not s.actif_sur(annee)],
        "nb_actifs": len(actifs),
        "total_etp": round(sum(float(s.etp or 0) for s in actifs), 2),
        "masse_salariale": round(sum(_salaire_annuel(s, recup_snapshots.get(s.id)) for s in actifs), 2),
        "non_affectes": sum(1 for s in actifs if not (s.secteur or "").strip()),
        "par_secteur": dict(sorted(par_secteur.items(), key=lambda kv: -kv[1]["etp"])),
        "recup_snapshots": recup_snapshots,
        "recup_count": len(recup_snapshots),
        "recup_mileage_cents": sum(s.mileage_amount_cents for s in recup_snapshots.values()),
        "recup_last_sync": max((s.synced_at for s in recup_snapshots.values()), default=None),
    }


def _annee_demandee() -> int:
    try:
        return int(request.args.get("annee") or date.today().year)
    except Exception:
        return date.today().year


@bp.route("/rh")
@login_required
@require_perm("rh:view")
def rh():
    annee = _annee_demandee()
    stats = stats_rh(annee)
    from app.services.recup_rh import recup_configure

    return render_template(
        "rh.html",
        stats=stats,
        annee=annee,
        secteurs=current_app.config.get("SECTEURS", []) or [],
        types_contrat=SENACS_TYPES_CONTRAT,
        can_edit=can("rh:edit"),
        recup_configured=recup_configure(),
    )


@bp.route("/rh/recup/sync", methods=["POST"])
@login_required
@require_perm("rh:edit")
def rh_recup_sync():
    from app.services.recup_rh import RecupRhError, recup_configure, synchroniser_rh

    try:
        annee = int(request.form.get("annee") or date.today().year)
    except (TypeError, ValueError):
        annee = date.today().year
    if not recup_configure():
        flash("L'intégration Récup n'est pas configurée (RECUP_BASE_URL / RECUP_TOKEN).", "warning")
        return redirect(url_for("main.rh", annee=annee))
    try:
        resume = synchroniser_rh(annee)
        journaliser("rh.recup_sync", cible="salaries", details=resume)
        flash(
            f"Synchronisation Récup réussie : {resume['created']} salarié(s) créé(s), "
            f"{resume['updated']} mis à jour, {resume['ignored']} ignoré(s).",
            "success",
        )
    except RecupRhError as exc:
        current_app.logger.warning("Échec de la synchronisation RH Récup : %s", exc)
        flash(f"La synchronisation Récup a échoué : {exc}", "danger")
    except Exception as exc:
        current_app.logger.exception("Échec inattendu de la synchronisation RH Récup")
        flash(f"La synchronisation Récup a échoué : {exc}", "danger")
    return redirect(url_for("main.rh", annee=annee))


@bp.route("/rh/salaries", methods=["POST"])
@login_required
@require_perm("rh:edit")
def rh_salarie_create():
    nom = (request.form.get("nom") or "").strip()
    if not nom:
        flash("Le nom du salarié est obligatoire.", "danger")
        return redirect(url_for("main.rh"))
    salarie = Salarie(nom=nom)
    _remplir_salarie(salarie, request.form)
    db.session.add(salarie)
    db.session.commit()
    journaliser("rh.salarie_create", cible=f"salarie#{salarie.id}",
                details={"nom": salarie.nom, "secteur": salarie.secteur, "poste": salarie.poste})
    flash(f"Salarié·e « {salarie.prenom or ''} {salarie.nom} » enregistré·e.", "success")
    return redirect(url_for("main.rh"))


@bp.route("/rh/salaries/<int:salarie_id>", methods=["POST"])
@login_required
@require_perm("rh:edit")
def rh_salarie_update(salarie_id: int):
    salarie = db.get_or_404(Salarie, salarie_id)
    _remplir_salarie(salarie, request.form)
    if not salarie.nom:
        flash("Le nom du salarié est obligatoire.", "danger")
        return redirect(url_for("main.rh"))
    db.session.commit()
    journaliser("rh.salarie_update", cible=f"salarie#{salarie.id}",
                details={"nom": salarie.nom, "secteur": salarie.secteur, "poste": salarie.poste})
    flash("Fiche salarié mise à jour.", "success")
    return redirect(url_for("main.rh"))


@bp.route("/rh/salaries/<int:salarie_id>/supprimer", methods=["POST"])
@login_required
@require_perm("rh:edit")
def rh_salarie_supprimer(salarie_id: int):
    salarie = db.get_or_404(Salarie, salarie_id)
    nom = f"{salarie.prenom or ''} {salarie.nom}".strip()
    db.session.delete(salarie)
    db.session.commit()
    journaliser("rh.salarie_suppr", cible=f"salarie#{salarie_id}", details={"nom": nom})
    flash(f"Fiche « {nom} » supprimée. (Pour un départ, préférez renseigner la date de sortie.)", "warning")
    return redirect(url_for("main.rh"))


@bp.route("/rh/import", methods=["POST"])
@login_required
@require_perm("rh:edit")
def rh_import():
    """Import CSV depuis un outil RH externe.

    Colonnes reconnues (entêtes insensibles à la casse, ; ou ,) :
    reference, nom, prenom, poste, secteur, contrat, etp, salaire,
    date_entree, date_sortie. La « reference » sert de clé : une ligne déjà
    importée met à jour la fiche au lieu d'en créer une nouvelle.
    """
    fichier = request.files.get("fichier")
    if fichier is None or not fichier.filename:
        flash("Choisis un fichier CSV à importer.", "danger")
        return redirect(url_for("main.rh"))

    try:
        brut = fichier.read().decode("utf-8-sig")
    except Exception:
        flash("Fichier illisible : encodage attendu UTF-8.", "danger")
        return redirect(url_for("main.rh"))

    delimiteur = ";" if brut.count(";") >= brut.count(",") else ","
    lecteur = csv.DictReader(io.StringIO(brut), delimiter=delimiteur)
    if not lecteur.fieldnames:
        flash("Fichier vide.", "danger")
        return redirect(url_for("main.rh"))
    colonnes = {(c or "").strip().lower(): c for c in lecteur.fieldnames}

    def val(ligne, *noms):
        for nom in noms:
            col = colonnes.get(nom)
            if col and ligne.get(col):
                return str(ligne[col]).strip()
        return ""

    crees, maj, ignores = 0, 0, 0
    for ligne in lecteur:
        nom = val(ligne, "nom", "name", "last_name")
        if not nom:
            ignores += 1
            continue
        reference = val(ligne, "reference", "ref", "id", "matricule") or None
        salarie = None
        if reference:
            salarie = Salarie.query.filter_by(source_ref=reference).first()
        if salarie is None:
            salarie = Salarie(nom=nom, source_ref=reference)
            db.session.add(salarie)
            crees += 1
        else:
            maj += 1
        salarie.nom = nom
        salarie.prenom = val(ligne, "prenom", "first_name") or salarie.prenom
        salarie.poste = val(ligne, "poste", "fonction", "emploi") or salarie.poste
        salarie.secteur = val(ligne, "secteur") or salarie.secteur
        contrat = val(ligne, "contrat", "type_contrat").lower()
        if contrat in SENACS_TYPES_CONTRAT_DICT:
            salarie.type_contrat = contrat
        etp = _parse_float(val(ligne, "etp", "quotite"))
        if etp is not None:
            salarie.etp = min(max(etp, 0.0), 2.0)
        salaire = _parse_float(val(ligne, "salaire", "salaire_brut_charge", "brut_charge"))
        if salaire is not None:
            salarie.salaire_brut_charge = salaire
        d_in = _parse_date(val(ligne, "date_entree", "entree", "embauche"))
        if d_in:
            salarie.date_entree = d_in
        d_out = _parse_date(val(ligne, "date_sortie", "sortie", "fin"))
        if d_out:
            salarie.date_sortie = d_out

    db.session.commit()
    journaliser("rh.import", cible="salaries", details={"crees": crees, "maj": maj, "ignores": ignores})
    flash(f"Import terminé : {crees} salarié(s) créé(s), {maj} mis à jour, {ignores} ligne(s) ignorée(s).", "success")
    return redirect(url_for("main.rh"))


@bp.route("/rh/export.xlsx")
@login_required
@require_perm("rh:view")
def rh_export_xlsx():
    from io import BytesIO
    from openpyxl import Workbook

    annee = _annee_demandee()
    stats = stats_rh(annee)

    wb = Workbook()
    ws = wb.active
    ws.title = f"Salariés {annee}"
    ws.append(["Nom", "Prénom", "Poste", "Secteur", "Contrat", "ETP",
               "Salaire brut chargé (€)", "Entrée", "Sortie", "Référence externe"])
    for s in stats["actifs"]:
        ws.append([
            s.nom, s.prenom or "", s.poste or "", s.secteur or "",
            s.contrat_label, s.etp,
            _salaire_annuel(s, stats["recup_snapshots"].get(s.id)) or "",
            s.date_entree.strftime("%d/%m/%Y") if s.date_entree else "",
            s.date_sortie.strftime("%d/%m/%Y") if s.date_sortie else "",
            s.source_ref or "",
        ])
    ws.append([])
    ws.append(["TOTAL", f"{stats['nb_actifs']} salarié(s)", "", "", "",
               stats["total_etp"], stats["masse_salariale"]])
    for col, width in zip("ABCDEFGHIJ", (22, 16, 26, 16, 14, 8, 20, 12, 12, 16)):
        ws.column_dimensions[col].width = width

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return Response(
        buf.getvalue(),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=salaries_{annee}.xlsx"},
    )
