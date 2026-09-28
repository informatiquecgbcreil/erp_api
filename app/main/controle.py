from pathlib import Path
import re

from sqlalchemy import func

from flask import (
    render_template, request, redirect, url_for, flash, current_app
)
from flask_login import login_required, current_user
from app.rbac import require_perm

from app.extensions import db
from app.models import (
    SessionActivite,
    MaterielType,
    MaterielConsommationConfig,
    MaterielConsommationLigne,
)


from datetime import date

from app.main.common import bp
from app.utils.montants import nombre_fini


def _parse_iso_date(value: str):
    try:
        if not value:
            return None
        return date.fromisoformat(value)
    except Exception:
        return None

# ---------------------------------------------------------------------
# P0.3F — Audit navigation / templates
# ---------------------------------------------------------------------

def _p03f_collect_endpoints():
    try:
        return set(current_app.view_functions.keys())
    except Exception:
        return set()


def _p03f_scan_templates():
    templates_dir = Path(current_app.root_path) / "templates"
    endpoints = _p03f_collect_endpoints()
    rows = []

    if not templates_dir.exists():
        return [{
            "level": "danger",
            "file": "app/templates",
            "type": "templates",
            "message": "Dossier templates introuvable.",
            "detail": str(templates_dir),
        }]

    # Ne pas confondre safe_url_for(...) avec url_for(...).
    url_for_re = re.compile(r"(?<!safe_)url_for\(\s*['\"]([^'\"]+)['\"]")
    endpoint_re = re.compile(r"request\.endpoint\s*==\s*['\"]([^'\"]+)['\"]")
    has_perm_re = re.compile(r"\bhas_perm\s*\(")

    for path in sorted(templates_dir.rglob("*.html")):
        rel = path.relative_to(current_app.root_path).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = path.read_text(encoding="latin-1", errors="ignore")

        for endpoint in url_for_re.findall(text):
            if endpoint not in endpoints:
                rows.append({
                    "level": "danger",
                    "file": rel,
                    "type": "url_for",
                    "message": f"Endpoint introuvable : {endpoint}",
                    "detail": "Corriger le nom d’endpoint ou supprimer le lien.",
                })

        for endpoint in endpoint_re.findall(text):
            if endpoint not in endpoints:
                rows.append({
                    "level": "warning",
                    "file": rel,
                    "type": "request.endpoint",
                    "message": f"Comparaison avec endpoint inexistant : {endpoint}",
                    "detail": "Peut simplement empêcher le bon état actif du menu.",
                })

        if has_perm_re.search(text):
            rows.append({
                "level": "danger",
                "file": rel,
                "type": "has_perm",
                "message": "Usage de has_perm(...) dans un template.",
                "detail": "Utiliser can(...) dans les templates.",
            })

    if not rows:
        rows.append({
            "level": "ok",
            "file": "templates",
            "type": "audit",
            "message": "Aucun problème évident détecté dans les templates.",
            "detail": "",
        })

    return rows


def _p03f_code_without_comments(text: str) -> str:
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        lines.append(line)
    return "\n".join(lines)


def _p03f_has_unguarded_sqlite_function(text: str) -> bool:
    clean = _p03f_code_without_comments(text)
    sqlite_needles = ("func." + "strftime", "func." + "julianday")
    if not any(needle in clean for needle in sqlite_needles):
        return False

    # Cas admis : fonction helper qui choisit explicitement SQLite vs PostgreSQL.
    lowered = clean.lower()
    if "_dialect_name" in clean and "sqlite" in lowered and ("date_trunc" in lowered or "to_char" in lowered):
        return False

    return True


def _p03f_scan_python_routes():
    app_dir = Path(current_app.root_path)
    endpoints = _p03f_collect_endpoints()
    rows = []
    url_for_re = re.compile(r"(?<!safe_)url_for\(\s*['\"]([^'\"]+)['\"]")

    ignored_suffixes = (" - Copie.py", ".bak", ".old", ".tmp")

    for path in sorted(app_dir.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        if path.name.endswith(ignored_suffixes):
            continue

        rel = path.relative_to(app_dir.parent).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = path.read_text(encoding="latin-1", errors="ignore")

        for endpoint in url_for_re.findall(text):
            if endpoint not in endpoints:
                rows.append({
                    "level": "warning",
                    "file": rel,
                    "type": "url_for python",
                    "message": f"Endpoint potentiellement introuvable : {endpoint}",
                    "detail": "À vérifier : certains endpoints peuvent être conditionnels ou rarement utilisés.",
                })

        if _p03f_has_unguarded_sqlite_function(text):
            rows.append({
                "level": "danger",
                "file": rel,
                "type": "postgres",
                "message": "Usage SQL SQLite non protégé détecté.",
                "detail": "Remplacer par une expression portable ou prévoir un branchement SQLite/PostgreSQL.",
            })

    if not rows:
        rows.append({
            "level": "ok",
            "file": "app",
            "type": "audit",
            "message": "Aucun problème évident détecté dans les routes Python.",
            "detail": "",
        })

    return rows



@bp.route("/controle/navigation")
@login_required
@require_perm("controle:view")
def controle_navigation():
    template_rows = _p03f_scan_templates()
    python_rows = _p03f_scan_python_routes()
    all_rows = template_rows + python_rows

    summary = {
        "danger": sum(1 for r in all_rows if r.get("level") == "danger"),
        "warning": sum(1 for r in all_rows if r.get("level") == "warning"),
        "ok": sum(1 for r in all_rows if r.get("level") == "ok"),
        "total": len(all_rows),
    }

    return render_template(
        "controle_navigation.html",
        summary=summary,
        template_rows=template_rows,
        python_rows=python_rows,
    )


@bp.route("/setup-start")
@login_required
@require_perm("controle:view")
def setup_start():
    # simple page de diagnostic / aide
    return render_template("controle.html")


# --------- Contrôle ---------

@bp.route("/controle", methods=["GET", "POST"])
@login_required
@require_perm("controle:view")
def controle():
    if request.method == "POST":
        action = (request.form.get("action") or "").strip()
        if action == "add_materiel":
            nom = (request.form.get("nom") or "").strip()
            if nom:
                ordre = db.session.query(func.coalesce(func.max(MaterielType.ordre), 0)).scalar() or 0
                db.session.add(MaterielType(nom=nom, ordre=int(ordre) + 10, actif=True))
                db.session.commit()
                flash("Matériel ajouté.", "success")
            else:
                flash("Nom du matériel obligatoire.", "danger")
            return redirect(url_for("main.controle"))
        if action == "toggle_materiel":
            mid = request.form.get("materiel_id", type=int)
            m = db.get_or_404(MaterielType, mid)
            m.actif = not bool(m.actif)
            db.session.commit()
            flash("État du matériel mis à jour.", "success")
            return redirect(url_for("main.controle"))
        if action == "add_conso_config":
            label = (request.form.get("label") or "").strip()
            date_debut = _parse_iso_date(request.form.get("date_debut") or "")
            date_fin = _parse_iso_date(request.form.get("date_fin") or "")
            co2 = nombre_fini(request.form.get("co2_kg_par_kwh"), 0.06) or 0.06

            if date_debut and date_fin and date_fin < date_debut:
                flash("La date de fin ne peut pas être antérieure à la date de début.", "danger")
                return redirect(url_for("main.controle"))

            if not label or not date_debut:
                flash("Label et date de début obligatoires.", "danger")
                return redirect(url_for("main.controle"))

            cfg = MaterielConsommationConfig(
                label=label,
                date_debut=date_debut,
                date_fin=date_fin,
                co2_kg_par_kwh=co2,
                actif=True,
            )
            db.session.add(cfg)
            db.session.flush()

            for m in MaterielType.query.order_by(MaterielType.ordre.asc(), MaterielType.nom.asc()).all():
                watts = nombre_fini(request.form.get(f"watts_{m.id}"))
                if watts is not None and watts > 0:
                    db.session.add(MaterielConsommationLigne(config_id=cfg.id, materiel_id=m.id, watts=watts))

            db.session.commit()
            flash("Référentiel de consommation ajouté.", "success")
            return redirect(url_for("main.controle"))

        if action == "update_conso_config":
            cfg_id = request.form.get("config_id", type=int)
            cfg = db.get_or_404(MaterielConsommationConfig, cfg_id)

            label = (request.form.get("label") or "").strip()
            date_debut = _parse_iso_date(request.form.get("date_debut") or "")
            date_fin = _parse_iso_date(request.form.get("date_fin") or "")
            co2 = nombre_fini(request.form.get("co2_kg_par_kwh"), 0.06) or 0.06

            if date_debut and date_fin and date_fin < date_debut:
                flash("La date de fin ne peut pas être antérieure à la date de début.", "danger")
                return redirect(url_for("main.controle"))

            if not label or not date_debut:
                flash("Label et date de début obligatoires.", "danger")
                return redirect(url_for("main.controle"))

            cfg.label = label
            cfg.date_debut = date_debut
            cfg.date_fin = date_fin
            cfg.co2_kg_par_kwh = co2
            cfg.actif = request.form.get("actif") == "1"

            MaterielConsommationLigne.query.filter_by(config_id=cfg.id).delete()
            for m in MaterielType.query.order_by(MaterielType.ordre.asc(), MaterielType.nom.asc()).all():
                watts = nombre_fini(request.form.get(f"watts_{m.id}"))
                if watts is not None and watts > 0:
                    db.session.add(MaterielConsommationLigne(config_id=cfg.id, materiel_id=m.id, watts=watts))

            db.session.commit()
            flash("Référentiel de consommation mis à jour.", "success")
            return redirect(url_for("main.controle"))

        if action == "delete_conso_config":
            cfg_id = request.form.get("config_id", type=int)
            cfg = db.get_or_404(MaterielConsommationConfig, cfg_id)

            used_count = SessionActivite.query.filter(SessionActivite.consommation_config_id == cfg.id).count()
            if used_count:
                flash(
                    f"Impossible de supprimer ce référentiel : il est utilisé par {used_count} séance(s). "
                    "Désactive-le ou ajoute une date de fin pour conserver l'historique.",
                    "danger",
                )
                return redirect(url_for("main.controle"))

            MaterielConsommationLigne.query.filter_by(config_id=cfg.id).delete()
            db.session.delete(cfg)
            db.session.commit()
            flash("Référentiel supprimé.", "warning")
            return redirect(url_for("main.controle"))

    materiels = MaterielType.query.order_by(MaterielType.ordre.asc(), MaterielType.nom.asc()).all()
    configs = (
        MaterielConsommationConfig.query
        .order_by(MaterielConsommationConfig.date_debut.desc(), MaterielConsommationConfig.id.desc())
        .all()
    )

    config_rows = []
    for cfg in configs:
        watts_by_materiel = {ln.materiel_id: ln.watts for ln in (cfg.lignes or [])}
        used_count = SessionActivite.query.filter(SessionActivite.consommation_config_id == cfg.id).count()
        config_rows.append({
            "cfg": cfg,
            "watts_by_materiel": watts_by_materiel,
            "used_count": used_count,
        })

    return render_template(
        "controle.html",
        materiels=materiels,
        consommation_configs=configs,
        config_rows=config_rows,
    )



@bp.route("/rbac-test")
@login_required
@require_perm("admin:rbac")
def rbac_test():
    """Page de diagnostic RBAC.
    Affiche les rôles/perms effectifs de l'utilisateur connecté (et le legacy role si présent).
    """
    from app.models import Permission  # import local pour éviter les cycles

    # Rôles RBAC (selon ton implémentation, role_codes peut être une méthode ou une propriété)
    role_codes_val = []
    if hasattr(current_user, "role_codes"):
        rc = getattr(current_user, "role_codes")
        try:
            role_codes_val = rc() if callable(rc) else list(rc or [])
        except Exception:
            try:
                role_codes_val = list(rc or [])
            except Exception:
                role_codes_val = []

    # legacy role string (compat)
    legacy_role = getattr(current_user, "role", None)

    perms = Permission.query.order_by(Permission.category.asc(), Permission.code.asc()).all()

    has_perm_fn = getattr(current_user, "has_perm", None)
    def _has(code: str) -> bool:
        try:
            return bool(has_perm_fn(code)) if callable(has_perm_fn) else False
        except Exception:
            return False

    perms_by_cat = {}
    for p in perms:
        cat = getattr(p, "category", None) or "Autre"
        perms_by_cat.setdefault(cat, []).append({
            "code": p.code,
            "label": getattr(p, "label", "") or p.code,
            "granted": _has(p.code),
        })

    # stats rapides
    total = sum(len(v) for v in perms_by_cat.values())
    granted = sum(1 for v in perms_by_cat.values() for x in v if x["granted"])

    user_info = {
        "id": getattr(current_user, "id", None),
        "email": getattr(current_user, "email", None),
        "nom": getattr(current_user, "nom", None),
        "secteur_assigne": getattr(current_user, "secteur_assigne", None),
        "legacy_role": legacy_role,
        "role_codes": role_codes_val,
        "perms_total": total,
        "perms_granted": granted,
    }

    return render_template(
        "rbac_test.html",
        user_info=user_info,
        perms_by_cat=perms_by_cat,
        total_perms=total,
        granted_perms=granted,
    )



# ---------------------------------------------------------------------
# Purge RGPD — anonymisation des participants inactifs
# ---------------------------------------------------------------------

@bp.route("/controle/purge-rgpd")
@login_required
@require_perm("controle:view")
def purge_rgpd():
    from app.services.purge_rgpd import (
        annees_inactivite,
        derniere_purge,
        participants_inactifs,
        purge_auto_active,
    )

    from app.services.conservation import REGLES, reglages
    from app.services.file_cleanup import en_attente_de_controle

    return render_template(
        "controle_purge_rgpd.html",
        en_attente=participants_inactifs(),
        annees=annees_inactivite(),
        derniere_purge=derniere_purge(),
        purge_auto=purge_auto_active(),
        regles_conservation=REGLES,
        conservation=reglages(),
        fichiers_a_controler=en_attente_de_controle(),
    )


@bp.post("/controle/purge-rgpd/lancer")
@login_required
@require_perm("participants:anonymize")
def purge_rgpd_lancer():
    from app.services.purge_rgpd import purger_par_lots

    rapport = purger_par_lots(declenchement=f"manuel par {getattr(current_user, 'email', '?')}")
    if rapport["anonymises"]:
        flash(f"{rapport['anonymises']} participant(s) inactif(s) anonymisé(s).", "success")
    elif not rapport["echecs"]:
        flash("Aucun participant inactif à anonymiser.", "info")
    if rapport["echecs"]:
        flash(f"{len(rapport['echecs'])} fiche(s) n'ont pas pu être anonymisées (voir le journal "
              f"de l'application) : n° {', '.join(map(str, rapport['echecs'][:10]))}.", "warning")
    if rapport["restants"]:
        flash(f"{rapport['restants']} fiche(s) restent en attente : traitées par lots, relancez ou "
              "laissez la purge quotidienne continuer.", "info")
    return redirect(url_for("main.purge_rgpd"))


@bp.post("/controle/purge-rgpd/conservation")
@login_required
@require_perm("participants:anonymize")
def purge_rgpd_conservation():
    """Durées de conservation hors participants (audit 3.8). 0 = sans limite."""
    from app.models import InstanceSettings
    from app.services.audit import journaliser
    from app.services.conservation import REGLES

    reglages = InstanceSettings.query.first()
    if reglages is None:
        reglages = InstanceSettings()
        db.session.add(reglages)
    valeurs = {}
    for nom, (_defaut, maximum, unite, libelle) in REGLES.items():
        try:
            valeur = int(request.form.get(nom) or 0)
        except ValueError:
            valeur = -1
        if not 0 <= valeur <= maximum:
            flash(f"{libelle} : valeur entre 0 et {maximum} {unite} attendue.", "danger")
            return redirect(url_for("main.purge_rgpd"))
        valeurs[nom] = valeur
    for nom, valeur in valeurs.items():
        setattr(reglages, nom, valeur)
    db.session.commit()
    journaliser("rgpd.conservation_reglages", details=valeurs)
    flash("Durées de conservation enregistrées.", "success")
    return redirect(url_for("main.purge_rgpd"))


@bp.post("/controle/purge-rgpd/conservation/appliquer")
@login_required
@require_perm("participants:anonymize")
def purge_rgpd_conservation_appliquer():
    from app.services.conservation import appliquer

    rapport = appliquer(f"manuel par {getattr(current_user, 'email', '?')}")
    resume = ", ".join(f"{nom} : {valeur}" for nom, valeur in rapport.items())
    flash(f"Durées de conservation appliquées ({resume}).", "success")
    return redirect(url_for("main.purge_rgpd"))


@bp.post("/controle/purge-rgpd/reglages")
@login_required
@require_perm("participants:anonymize")
def purge_rgpd_reglages():
    """Règle les critères de la purge : délai d'inactivité et activation.

    Enregistré dans les paramètres d'instance (prioritaire sur les
    variables d'environnement) — modifiable sans toucher au serveur.
    """
    from app.models import InstanceSettings
    from app.services.audit import journaliser

    reglages = InstanceSettings.query.first()
    if reglages is None:
        reglages = InstanceSettings()
        db.session.add(reglages)

    try:
        annees = int(request.form.get("annees") or 0)
    except ValueError:
        annees = 0
    if not 1 <= annees <= 10:
        flash("Le délai d'inactivité doit être compris entre 1 et 10 ans.", "danger")
        return redirect(url_for("main.purge_rgpd"))

    reglages.purge_rgpd_annees = annees
    reglages.purge_rgpd_auto = (request.form.get("auto") or "") == "1"
    db.session.commit()
    journaliser(
        "purge_rgpd.reglages",
        cible=f"{annees} an(s), auto {'activée' if reglages.purge_rgpd_auto else 'désactivée'}",
    )
    flash(
        f"Réglages enregistrés : anonymisation après {annees} an(s) d'inactivité, "
        f"purge automatique {'activée' if reglages.purge_rgpd_auto else 'désactivée'}.",
        "success",
    )
    return redirect(url_for("main.purge_rgpd"))


# ---------------------------------------------------------------------
# Registres hors base : numéros émis et effacements RGPD
# ---------------------------------------------------------------------

@bp.route("/controle/registres")
@login_required
@require_perm("controle:view")
def controle_registres():
    """État des registres qui survivent aux restaurations, et rapprochement
    des anciennes entrées d'effacement impossibles à départager seules."""
    from app.models import FinancialSequence
    from app.services import financial_sequence, registre_effacements
    from app.services.registre_externe import RegistreErreur, registres_mis_de_cote

    erreurs = []
    try:
        numeros = financial_sequence.lire_registre()
    except RegistreErreur as exc:
        numeros = None
        erreurs.append(f"Numéros émis : {exc} ({exc.detail})")
    compteurs = {ligne.namespace: ligne.value for ligne in FinancialSequence.query.all()}
    espaces = sorted(set(numeros or {}) | set(compteurs))
    try:
        effacements = registre_effacements.etat()
    except RegistreErreur as exc:
        effacements = None
        erreurs.append(f"Effacements : {exc} ({exc.detail})")
    return render_template(
        "controle_registres.html",
        tenu=financial_sequence._registre() is not None,
        numeros=numeros, compteurs=compteurs, espaces=espaces,
        effacements=effacements,
        a_verifier=registre_effacements.a_verifier(),
        mis_de_cote=[p.name for p in registres_mis_de_cote()],
        erreurs=erreurs,
    )


@bp.post("/controle/registres/effacements/<int:entree_id>")
@login_required
@require_perm("participants:anonymize")
def controle_registres_decider(entree_id: int):
    from app.services.registre_effacements import decider

    decision = (request.form.get("decision") or "").strip()
    if decision not in {"confirmer", "ecarter"}:
        flash("Décision inconnue.", "danger")
        return redirect(url_for("main.controle_registres"))
    message = decider(entree_id, decision, user_id=getattr(current_user, "id", None))
    flash(message, "info" if "déjà" in message else "success")
    return redirect(url_for("main.controle_registres"))


@bp.post("/controle/registres/recopier")
@login_required
@require_perm("controle:view")
def controle_registres_recopier():
    from app.services.registre_effacements import exporter_en_attente
    from app.services.registre_externe import RegistreErreur

    try:
        nombre = exporter_en_attente()
        flash(f"{nombre} effacement(s) recopié(s) dans le registre de l'installation.", "success")
    except RegistreErreur as exc:
        flash(f"Recopie impossible : {exc}", "danger")
    return redirect(url_for("main.controle_registres"))


@bp.get("/controle/registres/export")
@login_required
@require_perm("admin:rbac")
def controle_registres_export():
    """Copie des registres à emporter vers une nouvelle installation."""
    import json as _json
    from flask import Response
    from app.services.audit import journaliser
    from app.services.registres_transfert import instantane

    contenu = _json.dumps(instantane(), sort_keys=True, ensure_ascii=False, indent=1)
    journaliser("registres.export")
    nom = f"registres-mon-centre-social-{date.today().isoformat()}.json"
    return Response(contenu, mimetype="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{nom}"'})


@bp.post("/controle/registres/import")
@login_required
@require_perm("admin:rbac")
def controle_registres_import():
    """Transfert : fusionne une copie des registres (jamais d'écrasement)."""
    import json as _json
    from app.services.audit import journaliser
    from app.services.registre_externe import RegistreErreur
    from app.services.registres_transfert import fusionner

    fichier = request.files.get("fichier")
    if fichier is None or not fichier.filename:
        flash("Choisissez le fichier de registres exporté de l'ancienne installation.", "danger")
        return redirect(url_for("main.controle_registres"))
    try:
        donnees = _json.loads(fichier.read(20_000_000).decode("utf-8"))
        rapport = fusionner(donnees)
    except (ValueError, UnicodeDecodeError):
        flash("Ce fichier n'est pas une copie des registres de Mon Centre Social.", "danger")
        return redirect(url_for("main.controle_registres"))
    except RegistreErreur as exc:
        flash(f"Import refusé : {exc}", "danger")
        return redirect(url_for("main.controle_registres"))
    journaliser("registres.import", details={"numeros": rapport["numeros"], "effacements": rapport["effacements"],
                                              "reappliques": len(rapport["reappliques"])})
    flash(f"Registres fusionnés : {rapport['numeros']} série(s) de numéros, {rapport['effacements']} "
          f"effacement(s) ; {len(rapport['reappliques'])} fiche(s) anonymisée(s) de nouveau.", "success")
    return redirect(url_for("main.controle_registres"))


@bp.post("/controle/registres/dernier-numero")
@login_required
@require_perm("admin:rbac")
def controle_registres_dernier_numero():
    """Après un sinistre : déclarer le dernier numéro émis connu (papier)."""
    from app.services.audit import journaliser
    from app.services.registres_transfert import declarer_dernier_numero

    espace = f"{(request.form.get('serie') or '').strip()}:{(request.form.get('annee') or '').strip()}"
    try:
        numero = int(request.form.get("numero") or 0)
        declarer_dernier_numero(espace, numero)
    except ValueError:
        flash("Indiquez une série, une année (4 chiffres) et un numéro supérieur à 0.", "danger")
        return redirect(url_for("main.controle_registres"))
    journaliser("registres.dernier_numero", cible=espace, details={"numero": numero})
    flash(f"Série {espace} : la numérotation reprendra au-delà du n° {numero}.", "success")
    return redirect(url_for("main.controle_registres"))
