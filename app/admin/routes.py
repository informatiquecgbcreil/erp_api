import os
import tempfile
from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app, jsonify
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from app.extensions import db
from app.models import User, Role, Permission, Secteur, InstanceSettings, Framework, Skill
from app.utils.delete_guard import commit_delete
from app.rbac import require_perm, can, HIDDEN_LEGACY_PERMS
from app.services.audit import journaliser
from app.services.storage import ensure_upload_subdir, media_relpath
from app.ateliers.excel_import import import_presences_from_xlsx

bp = Blueprint("admin", __name__, url_prefix="/admin")

CATEGORY_ORDER = [
    "Accueil et navigation",
    "Secteurs et portée",
    "Administration",
    "Contrôle",
    "Activité",
    "Ateliers",
    "Émargement",
    "Participants",
    "Insertion",
    "Quartiers",
    "Partenaires",
    "Questionnaires",
    "Inventaire",
    "Pédagogie",
    "Statistiques et bilans",
    "Projets",
    "Budget AAP",
    "Subventions",
    "Dépenses",
]


def _get_single_role_code_from_form() -> str | None:
    """Accept both old multi-select ('role_codes') and new single-select ('role_code' or 'roles')."""
    # Old UI: <select multiple name="role_codes">
    role_codes = request.form.getlist("role_codes")
    if role_codes:
        # take the first one deterministically
        return (role_codes[0] or "").strip() or None

    # New UI possibilities
    for key in ("role_code", "roles", "role"):
        v = (request.form.get(key) or "").strip()
        if v:
            return v
    return None


# ---------------------------------------------------------------------------
# Garde-fous « dernier administrateur » : ne jamais laisser une action vider
# l'ensemble des comptes ACTIFS capables de gérer les droits (admin:rbac),
# sous peine de verrouiller toute l'application.
# ---------------------------------------------------------------------------
def _role_ids_admin_rbac() -> set[int]:
    return {
        r.id for r in Role.query.all()
        if any(p.code == "admin:rbac" for p in (r.permissions or []))
    }


def _role_donne_admin(role) -> bool:
    return bool(role) and any(p.code == "admin:rbac" for p in (role.permissions or []))


def _est_admin(u) -> bool:
    admin_ids = _role_ids_admin_rbac()
    return any(r.id in admin_ids for r in (getattr(u, "roles", None) or []))


def _nb_admins_actifs(exclure_user_id=None, exclure_role_id=None) -> int:
    """Comptes ACTIFS pouvant administrer (admin:rbac), en excluant
    éventuellement un utilisateur ou un rôle (pour simuler une action)."""
    admin_ids = _role_ids_admin_rbac()
    if exclure_role_id is not None:
        admin_ids = admin_ids - {exclure_role_id}
    n = 0
    for u in User.query.all():
        if exclure_user_id is not None and u.id == exclure_user_id:
            continue
        if not getattr(u, "actif", True):
            continue
        if any(r.id in admin_ids for r in (getattr(u, "roles", None) or [])):
            n += 1
    return n


@bp.route("/users", methods=["GET", "POST"])
@login_required
@require_perm("admin:users")
def users():
    from app.models import User, Role
    from app.secteurs import get_secteur_labels
    from app.extensions import db

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        nom = request.form.get("nom", "").strip()
        # Strip cohérent avec la page de connexion (qui strip aussi) et le
        # wizard d'installation : évite un « mot de passe incorrect » dû à un
        # espace en trop collé au moment de la création.
        password = request.form.get("password", "").strip()
        role_code = request.form.get("role")
        secteur = request.form.get("secteur_assigne") or None

        if not email or not password or not role_code:
            flash("Certains champs obligatoires sont manquants.", "danger")
            return redirect(url_for("admin.users"))

        if User.query.filter_by(email=email).first():
            flash("Un utilisateur utilisant cette adresse e-mail existe déjà.", "danger")
            return redirect(url_for("admin.users"))

        u = User(email=email, nom=nom or "Utilisateur")
        u.set_password(password)
        u.secteur_assigne = secteur

        role = Role.query.filter_by(code=role_code).first()
        if role:
            u.roles.append(role)

        db.session.add(u)
        db.session.commit()

        journaliser("user.create", cible=email, details={"role": role_code, "secteur": secteur})
        flash("Le compte utilisateur a bien été créé.", "success")
        return redirect(url_for("admin.users"))

    users = User.query.order_by(User.nom).all()
    roles = Role.query.order_by(Role.code).all()
    secteurs = get_secteur_labels(active_only=True)

    # Les comptes à portée globale n'ont pas besoin d'un secteur assigné.
    role_sector_policy = {
        "admin_tech": {"allow_sector": False},
        "direction": {"allow_sector": False},
        "directrice": {"allow_sector": False},
        "finance": {"allow_sector": False},
    }

    return render_template(
        "admin_users.html",
        users=users,
        roles=roles,
        secteurs=secteurs,
        role_sector_policy=role_sector_policy,
    )


@bp.route("/delete/<int:user_id>", methods=["POST"])
@login_required
@require_perm("admin:users")
def delete_user(user_id):
    if current_user.id == user_id:
        flash("Vous ne pouvez pas supprimer votre propre compte.", "danger")
        return redirect(url_for("admin.users"))

    u = db.get_or_404(User, user_id)
    if _est_admin(u) and _nb_admins_actifs(exclure_user_id=u.id) == 0:
        flash("Impossible de supprimer le dernier administrateur (compte capable de gérer les droits).", "danger")
        return redirect(url_for("admin.users"))
    cible = u.email
    db.session.delete(u)
    commit_delete(
        f"l'utilisateur « {u.nom} »",
        "Le compte utilisateur a bien été supprimé.",
        success_category="warning",
        blocked_message=f"Impossible de supprimer l'utilisateur « {u.nom} » : il est encore référencé ailleurs.",
    )
    journaliser("user.delete", cible=cible)
    return redirect(url_for("admin.users"))


@bp.route("/users/<int:user_id>/edit", methods=["GET", "POST"])
@login_required
@require_perm("admin:users")
def edit_user(user_id):
    from app.secteurs import get_secteur_labels

    u = db.get_or_404(User, user_id)
    roles = Role.query.order_by(Role.code).all()
    secteurs = get_secteur_labels(active_only=True)

    if request.method == "POST":
        nom = (request.form.get("nom") or "").strip()
        if nom:
            u.nom = nom
        u.secteur_assigne = (request.form.get("secteur_assigne") or "").strip() or None

        # Changement de rôle : réservé à qui peut gérer les droits (admin:rbac).
        role_code = (request.form.get("role") or "").strip()
        if role_code and can("admin:rbac"):
            nouveau_role = Role.query.filter_by(code=role_code).first()
            if (not _role_donne_admin(nouveau_role)
                    and getattr(u, "actif", True)
                    and _nb_admins_actifs(exclure_user_id=u.id) == 0):
                flash("Impossible : ce changement retirerait le dernier administrateur.", "danger")
                return redirect(url_for("admin.edit_user", user_id=u.id))
            if nouveau_role:
                u.roles = [nouveau_role]

        # Réinitialisation du mot de passe par l'admin (optionnel, sans SMTP).
        new_pw = (request.form.get("password") or "").strip()
        if new_pw:
            if len(new_pw) < 8:
                flash("Le mot de passe doit contenir au moins 8 caractères.", "danger")
                return redirect(url_for("admin.edit_user", user_id=u.id))
            u.set_password(new_pw)
            current_app.logger.warning(
                "Mot de passe réinitialisé par admin %s pour %s",
                getattr(current_user, "email", "?"), u.email,
            )

        db.session.commit()
        journaliser("user.edit", cible=u.email,
                    details={"role_change": bool(role_code), "password_reset": bool(new_pw)})
        flash("Compte mis à jour.", "success")
        return redirect(url_for("admin.users"))

    return render_template(
        "admin_user_edit.html",
        u=u,
        roles=roles,
        secteurs=secteurs,
        current_role=(u.role_codes[0] if u.role_codes else ""),
        peut_changer_role=can("admin:rbac"),
    )


@bp.route("/users/<int:user_id>/toggle-actif", methods=["POST"])
@login_required
@require_perm("admin:users")
def toggle_user_actif(user_id):
    u = db.get_or_404(User, user_id)
    if u.id == current_user.id:
        flash("Vous ne pouvez pas désactiver votre propre compte.", "danger")
        return redirect(url_for("admin.users"))

    if getattr(u, "actif", True):
        if _est_admin(u) and _nb_admins_actifs(exclure_user_id=u.id) == 0:
            flash("Impossible de désactiver le dernier administrateur.", "danger")
            return redirect(url_for("admin.users"))
        u.actif = False
        flash(f"Compte « {u.nom} » désactivé (il ne peut plus se connecter).", "warning")
        _action_audit = "user.disable"
    else:
        u.actif = True
        flash(f"Compte « {u.nom} » réactivé.", "success")
        _action_audit = "user.enable"
    db.session.commit()
    journaliser(_action_audit, cible=u.email)
    return redirect(url_for("admin.users"))


@bp.route("/droits", methods=["GET", "POST"])
@login_required
@require_perm("admin:rbac")
def droits():
    """UI RBAC: attribuer des rôles à un user, et éditer les perms d'un rôle."""

    from collections import OrderedDict

    roles = Role.query.order_by(Role.code.asc()).all()
    perms = (
        Permission.query
        .filter(~Permission.code.in_(HIDDEN_LEGACY_PERMS))
        .order_by(Permission.category.asc(), Permission.code.asc())
        .all()
    )
    users_list = User.query.order_by(User.nom.asc()).all()

    grouped_permissions = OrderedDict((cat, []) for cat in CATEGORY_ORDER)
    for perm in perms:
        cat = perm.category or "Autres"
        if cat not in grouped_permissions:
            grouped_permissions[cat] = []
        grouped_permissions[cat].append(perm)

    if request.method == "POST":
        action = request.form.get("action")

        # --- Affecter un rôle (unique) à un user ---
        if action == "set_user_roles":
            user_id = int(request.form.get("user_id") or 0)
            u = db.get_or_404(User, user_id)

            role_code = _get_single_role_code_from_form()

            # Force: 1 seul rôle RBAC
            u.roles = []
            if role_code:
                r = Role.query.filter_by(code=role_code).first()
                if r:
                    u.roles.append(r)
                    # legacy sync

            db.session.commit()
            flash("Les rôles de l’utilisateur ont bien été mis à jour.", "success")
            return redirect(url_for("admin.droits"))

        # --- Affecter des permissions à un rôle ---
        if action == "set_role_perms":
            role_code = (request.form.get("role_code") or "").strip()
            perm_codes = set(request.form.getlist("perm_codes"))

            role = Role.query.filter_by(code=role_code).first_or_404()
            role.permissions = []
            for pcode in perm_codes:
                p = Permission.query.filter_by(code=pcode).first()
                if p:
                    role.permissions.append(p)

            db.session.commit()
            flash("Les permissions du rôle ont bien été mises à jour.", "success")
            return redirect(url_for("admin.droits"))

    return render_template(
        "admin_droits.html",
        roles=roles,
        perms=perms,
        grouped_permissions=grouped_permissions,
        users_list=users_list,
        users=users_list,
    )


# ---------------------------------------------------------------------------
# RBAC: endpoints attendus par le template admin_droits.html
# ---------------------------------------------------------------------------

@bp.route("/set_user_roles", methods=["POST"])
@login_required
@require_perm("admin:users")
@require_perm("admin:rbac")
def set_user_roles():
    user_id = int(request.form.get("user_id") or 0)
    u = db.get_or_404(User, user_id)

    role_code = _get_single_role_code_from_form()

    # Garde-fou : ne pas retirer le dernier administrateur.
    nouveau_role = Role.query.filter_by(code=role_code).first() if role_code else None
    if (not _role_donne_admin(nouveau_role)
            and getattr(u, "actif", True)
            and _nb_admins_actifs(exclure_user_id=u.id) == 0):
        flash("Impossible : ce changement retirerait le dernier administrateur.", "danger")
        return redirect(url_for("admin.droits"))

    # Force: 1 seul rôle RBAC
    u.roles = []
    if role_code:
        r = Role.query.filter_by(code=role_code).first()
        if r:
            u.roles.append(r)
            # legacy sync

    db.session.commit()
    journaliser("user.role", cible=u.email, details={"role": role_code})
    flash("Les rôles de l’utilisateur ont bien été mis à jour.", "success")
    return redirect(url_for("admin.droits"))


@bp.route("/save_role_perms", methods=["POST"])
@login_required
@require_perm("admin:rbac")
def save_role_perms():
    role_code = (request.form.get("role_code") or "").strip()
    perm_codes = set(request.form.getlist("perm_codes"))

    role = Role.query.filter_by(code=role_code).first_or_404()

    # Garde-fou : ne pas retirer admin:rbac si ce rôle est le dernier à
    # fournir l'accès administrateur à un compte actif.
    if (_role_donne_admin(role)
            and "admin:rbac" not in perm_codes
            and _nb_admins_actifs(exclure_role_id=role.id) == 0):
        flash("Impossible : retirer « admin:rbac » de ce rôle verrouillerait l'administration.", "danger")
        return redirect(url_for("admin.droits"))

    role.permissions = []
    for pcode in perm_codes:
        p = Permission.query.filter_by(code=pcode).first()
        if p:
            role.permissions.append(p)

    db.session.commit()
    journaliser("role.perms", cible=role_code, details={"nb": len(perm_codes)})
    flash("Les permissions du rôle ont bien été mises à jour.", "success")
    return redirect(url_for("admin.droits"))


@bp.route("/create_role", methods=["POST"])
@login_required
@require_perm("admin:rbac")
def create_role():
    code = (request.form.get("code") or "").strip()
    label = (request.form.get("label") or "").strip() or None

    if not code:
        flash("Le code du rôle est obligatoire.", "danger")
        return redirect(url_for("admin.droits"))

    if Role.query.filter_by(code=code).first():
        flash("Un rôle avec ce code existe déjà.", "warning")
        return redirect(url_for("admin.droits"))

    r = Role(code=code, label=label)
    db.session.add(r)
    db.session.commit()

    journaliser("role.create", cible=code)
    flash("Le rôle a bien été créé.", "success")
    return redirect(url_for("admin.droits"))


@bp.route("/delete_role", methods=["POST"])
@login_required
@require_perm("admin:rbac")
def delete_role():
    role_code = (request.form.get("role_code") or "").strip()
    if not role_code:
        flash("Sélectionnez un rôle à supprimer.", "danger")
        return redirect(url_for("admin.droits"))

    r = Role.query.filter_by(code=role_code).first()
    if not r:
        flash("Le rôle demandé est introuvable.", "warning")
        return redirect(url_for("admin.droits"))

    # Garde-fou : supprimer ce rôle ne doit pas vider l'administration.
    if _nb_admins_actifs(exclure_role_id=r.id) == 0:
        flash("Impossible de supprimer ce rôle : il donne l'accès administrateur au dernier compte capable d'administrer.", "danger")
        return redirect(url_for("admin.droits"))

    # Détache users + perms (évite erreurs tables d'association)
    for u in User.query.all():
        if hasattr(u, "roles") and r in u.roles:
            u.roles.remove(r)
    r.permissions = []

    db.session.delete(r)
    commit_delete(
        f"le rôle « {r.code} »",
        "Le rôle a bien été supprimé.",
        success_category="warning",
        blocked_message=f"Impossible de supprimer le rôle « {r.code} » : il est encore utilisé ailleurs.",
    )
    journaliser("role.delete", cible=role_code)
    return redirect(url_for("admin.droits"))


@bp.route("/get_role_perms/<role_code>", methods=["GET"])
@login_required
@require_perm("admin:rbac")
def get_role_perms(role_code):
    r = Role.query.filter_by(code=role_code).first()
    if not r:
        return jsonify({"role": role_code, "perms": []})

    perms = [p.code for p in (r.permissions or [])]
    return jsonify({"role": r.code, "perms": perms})




# ------------------------------------------------------------------
# Secteurs (admin)
# ------------------------------------------------------------------

@bp.route("/secteurs", methods=["GET", "POST"])
@login_required
@require_perm("secteurs:edit")
def secteurs():
    """Page d'administration des secteurs."""
    from app.secteurs import upsert_secteur

    if request.method == "POST":
        label = (request.form.get("label") or "").strip()
        code = (request.form.get("code") or "").strip() or None
        try:
            upsert_secteur(label=label, code=code, is_active=True)
            flash("Secteur créé / mis à jour ✅", "success")
        except Exception as e:
            current_app.logger.exception("Erreur création secteur")
            flash(f"Impossible de créer / mettre à jour : {e}", "danger")
        return redirect(url_for("admin.secteurs"))

    secteurs = Secteur.query.order_by(Secteur.label.asc()).all()
    return render_template("admin_secteurs.html", secteurs=secteurs)


@bp.route("/secteurs/<int:secteur_id>/rename", methods=["POST"])
@login_required
@require_perm("secteurs:edit")
def secteur_rename(secteur_id: int):
    s = db.get_or_404(Secteur, secteur_id)
    new_label = (request.form.get("label") or "").strip()
    if not new_label:
        flash("Nom de secteur vide ❌", "danger")
        return redirect(url_for("admin.secteurs"))

    # On garde le code stable (slug) : on ne change que le label.
    s.label = new_label
    try:
        db.session.commit()
        flash("Secteur renommé ✅", "success")
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception("Erreur renommage secteur")
        flash(f"Impossible de renommer : {e}", "danger")
    return redirect(url_for("admin.secteurs"))


@bp.route("/secteurs/<int:secteur_id>/toggle", methods=["POST"])
@login_required
@require_perm("secteurs:edit")
def secteur_toggle(secteur_id: int):
    s = db.get_or_404(Secteur, secteur_id)
    s.is_active = not bool(s.is_active)
    try:
        db.session.commit()
        flash("Le statut du secteur a bien été mis à jour.", "success")
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception("Erreur toggle secteur")
        flash(f"Impossible de modifier le statut : {e}", "danger")
    return redirect(url_for("admin.secteurs"))


@bp.route('/debug_rbac', methods=['GET'])
@login_required
@require_perm("admin:rbac")
@require_perm('admin:rbac')
def debug_rbac():
    # Liste des permissions effectives pour l'utilisateur courant (debug)
    perms=set()
    for r in getattr(current_user,'roles',[]) or []:
        for p in getattr(r,'permissions',[]) or []:
            perms.add(p.code)
    return render_template('admin_debug_rbac.html', perms=sorted(perms))


@bp.route("/instance", methods=["GET", "POST"])
@login_required
@require_perm("admin:rbac")
def instance_settings():
    row = InstanceSettings.query.first() or InstanceSettings()

    if request.method == "POST":
        app_name = (request.form.get("app_name") or "").strip()
        org_name = (request.form.get("organization_name") or "").strip()
        public_base_url = (request.form.get("public_base_url") or "").strip() or None

        smtp_host = (request.form.get("smtp_host") or "").strip() or None
        smtp_port_raw = (request.form.get("smtp_port") or "").strip()
        smtp_port = int(smtp_port_raw) if smtp_port_raw.isdigit() else None
        smtp_username = (request.form.get("smtp_username") or "").strip() or None
        smtp_password = (request.form.get("smtp_password") or "").strip() or None
        smtp_sender = (request.form.get("smtp_sender") or "").strip() or None
        smtp_use_tls = request.form.get("smtp_use_tls") in {"1", "true", "on", "yes", "YES"}

        if smtp_host and (not smtp_port or smtp_port <= 0):
            flash("Port SMTP invalide.", "danger")
            return redirect(url_for("admin.instance_settings"))
        if smtp_host and not smtp_sender:
            flash("Adresse expéditeur SMTP requise.", "danger")
            return redirect(url_for("admin.instance_settings"))

        if not row.id:
            db.session.add(row)

        row.app_name = app_name or None
        row.organization_name = org_name or None
        row.public_base_url = public_base_url
        row.smtp_host = smtp_host
        row.smtp_port = smtp_port
        row.smtp_username = smtp_username
        if smtp_password:
            row.smtp_password = smtp_password
        row.smtp_sender = smtp_sender
        row.smtp_use_tls = smtp_use_tls if smtp_host else None

        logo_dir = ensure_upload_subdir("branding")

        app_logo = request.files.get("app_logo")
        if app_logo and app_logo.filename:
            name = secure_filename(app_logo.filename)
            ext = os.path.splitext(name)[1].lower()
            if ext in {".png", ".jpg", ".jpeg", ".webp", ".svg"}:
                out = f"app_logo{ext}"
                app_logo.save(os.path.join(logo_dir, out))
                row.app_logo_path = media_relpath("branding", out)

        org_logo = request.files.get("organization_logo")
        if org_logo and org_logo.filename:
            name = secure_filename(org_logo.filename)
            ext = os.path.splitext(name)[1].lower()
            if ext in {".png", ".jpg", ".jpeg", ".webp", ".svg"}:
                out = f"organization_logo{ext}"
                org_logo.save(os.path.join(logo_dir, out))
                row.organization_logo_path = media_relpath("branding", out)

        db.session.commit()
        flash("Paramètres de votre structure enregistrés ✅", "success")
        return redirect(url_for("admin.instance_settings"))

    return render_template("admin_instance.html", settings=row)


@bp.route("/sauvegardes", methods=["GET"])
@login_required
@require_perm("admin:rbac")
def sauvegardes():
    from app.services.sauvegarde import lister_lots, jours_depuis_derniere

    db_uri = current_app.config.get("SQLALCHEMY_DATABASE_URI", "") or ""
    if db_uri.startswith("postgresql"):
        moteur = "PostgreSQL"
    elif db_uri.startswith("sqlite"):
        moteur = "SQLite"
    else:
        moteur = "inconnu"
    try:
        seuil = int(current_app.config.get("BACKUP_ALERT_DAYS") or 2)
    except (TypeError, ValueError):
        seuil = 2
    return render_template(
        "admin_sauvegardes.html",
        lots=lister_lots(),
        jours_depuis=jours_depuis_derniere(),
        seuil_alerte=seuil,
        moteur=moteur,
    )


@bp.route("/sauvegardes/creer", methods=["POST"])
@login_required
@require_perm("admin:rbac")
def sauvegarde_creer():
    from app.services.sauvegarde import creer_sauvegarde, nettoyer_sauvegardes

    try:
        info = creer_sauvegarde()
        supprimes = nettoyer_sauvegardes()  # rotation/rétention
        current_app.logger.info(
            "Sauvegarde manuelle créée par %s : %s (%s ancien(s) fichier(s) purgé(s))",
            getattr(current_user, "email", "?"),
            info.get("base"),
            supprimes,
        )
        flash("Sauvegarde créée avec succès ✅", "success")
    except Exception as exc:  # message lisible remonté à l'utilisateur
        current_app.logger.exception("Échec de la sauvegarde manuelle")
        flash(f"La sauvegarde a échoué : {exc}", "danger")
    return redirect(url_for("admin.sauvegardes"))


@bp.route("/notifications", methods=["GET", "POST"])
@login_required
@require_perm("admin:rbac")
def notifications():
    """Réglage des notifications automatiques (digest e-mail).

    Rien n'est actif par défaut : chaque type se coche avec ses
    destinataires, sa fréquence et son seuil. « Envoyer un aperçu »
    permet de vérifier le rendu sans attendre le lendemain."""
    from app.services.notifications import (
        FREQUENCES,
        enregistrer_reglage,
        reglages_effectifs,
    )

    if request.method == "POST":
        for reglage in reglages_effectifs():
            code = reglage["code"]
            seuil_brut = (request.form.get(f"seuil_{code}") or "").strip()
            try:
                seuil = int(seuil_brut) if seuil_brut else None
            except ValueError:
                seuil = None
            enregistrer_reglage(
                code,
                actif=bool(request.form.get(f"actif_{code}")),
                destinataires=request.form.get(f"destinataires_{code}") or "",
                frequence=(request.form.get(f"frequence_{code}") or "quotidien").strip(),
                seuil_jours=seuil,
            )
        flash("Réglages des notifications enregistrés ✅", "success")
        return redirect(url_for("admin.notifications"))

    return render_template(
        "admin_notifications.html",
        reglages=reglages_effectifs(),
        frequences=FREQUENCES,
    )


@bp.route("/notifications/apercu", methods=["POST"])
@login_required
@require_perm("admin:rbac")
def notifications_apercu():
    """Envoie le digest immédiatement (types actifs, fréquence ignorée)."""
    from app.services.notifications import envoyer_digest

    try:
        resultat = envoyer_digest(forcer=True)
        if resultat["envoyes"]:
            flash(
                f"Aperçu envoyé à {resultat['envoyes']} destinataire(s) : "
                f"{', '.join(resultat['destinataires'])} ✅",
                "success",
            )
        else:
            flash(
                "Rien à envoyer : aucun type actif n'a de contenu aujourd'hui "
                "(ou aucun destinataire n'est renseigné).",
                "info",
            )
    except Exception as exc:  # noqa: BLE001
        current_app.logger.exception("Échec de l'aperçu du digest")
        flash(f"L'envoi de l'aperçu a échoué : {exc}", "danger")
    return redirect(url_for("admin.notifications"))


@bp.route("/sauvegardes/verifier", methods=["POST"])
@login_required
@require_perm("admin:rbac")
def sauvegarde_verifier():
    """Vérification à blanc : le lot est-il réellement restaurable ?
    Ne touche ni la base courante ni les fichiers du lot."""
    from app.services.sauvegarde import verifier_lot

    base = (request.form.get("base") or "").strip()
    if not base:
        flash("Sélectionnez une sauvegarde à vérifier.", "danger")
        return redirect(url_for("admin.sauvegardes"))
    try:
        rapport = verifier_lot(base)
        details = " · ".join(
            f"{c['nom']} : {'OK' if c['ok'] else ('inconnu' if c['ok'] is None else 'ÉCHEC')}"
            for c in rapport["controles"]
        )
        if rapport["ok"]:
            flash(f"Sauvegarde « {base} » vérifiée : RESTAURABLE ✅ ({details})", "success")
        else:
            echecs = "; ".join(
                f"{c['nom']} — {c['detail']}" for c in rapport["controles"] if c["ok"] is False
            )
            flash(f"Sauvegarde « {base} » NON FIABLE ❌ : {echecs}", "danger")
    except Exception as exc:  # noqa: BLE001
        current_app.logger.exception("Échec de la vérification de sauvegarde")
        flash(f"La vérification a échoué : {exc}", "danger")
    return redirect(url_for("admin.sauvegardes"))


@bp.route("/sauvegardes/restaurer", methods=["POST"])
@login_required
@require_perm("admin:rbac")
def sauvegarde_restaurer():
    from app.services.sauvegarde import restaurer_lot

    base = (request.form.get("base") or "").strip()
    if not base:
        flash("Sélectionnez une sauvegarde à restaurer.", "danger")
        return redirect(url_for("admin.sauvegardes"))
    try:
        res = restaurer_lot(base)
        journaliser("backup.restore", cible=base, details={"securite": res.get("securite")})
        current_app.logger.warning(
            "RESTAURATION effectuée par %s depuis « %s » (sécurité : %s)",
            getattr(current_user, "email", "?"), base, res.get("securite"),
        )
        flash(
            "Restauration effectuée ✅ Une sauvegarde de sécurité de l'état précédent "
            "a été créée juste avant. Reconnectez-vous si nécessaire.",
            "success",
        )
    except Exception as exc:  # noqa: BLE001
        current_app.logger.exception("Échec de la restauration")
        flash(f"La restauration a échoué : {exc}", "danger")
    return redirect(url_for("admin.sauvegardes"))



@bp.route("/journal")
@login_required
@require_perm("admin:rbac")
def journal_audit():
    from app.models import AuditLog

    action = (request.args.get("action") or "").strip()
    cible = (request.args.get("cible") or "").strip()
    q = AuditLog.query
    if action:
        q = q.filter(AuditLog.action == action)
    if cible:
        q = q.filter(AuditLog.cible.ilike(f"%{cible}%"))
    entrees = q.order_by(AuditLog.created_at.desc()).limit(300).all()
    actions = [a for (a,) in db.session.query(AuditLog.action).distinct().order_by(AuditLog.action).all()]
    return render_template("admin_journal.html", entrees=entrees, actions=actions, action=action, cible=cible)


@bp.route("/droits/matrice")
@login_required
@require_perm("admin:rbac")
def matrice_droits():
    """B4 — Matrice lisible des droits par rôle, orientée non-développeur."""
    categories = [
        ("Publics", ["participants:view", "participants:view_all", "participants:edit", "participants:delete", "insertion:sensitive_view"]),
        ("Activités", ["emargement:view", "emargement:edit", "ateliers:edit", "pedagogie:view"]),
        ("Finances", ["subventions:view", "subventions:edit", "depenses:view", "depenses:create", "aap:view"]),
        ("Bilans", ["stats:view", "stats:view_all", "bilans:view", "questionnaires:export"]),
        ("Ressources sensibles", ["rh:view", "dons:view", "caisse:view", "admin:users", "admin:rbac"]),
    ]
    roles = Role.query.order_by(Role.label.asc()).all()
    permission_labels = {p.code: p.label for p in Permission.query.all()}
    rows = []
    for title, codes in categories:
        rows.append({
            "title": title,
            "permissions": [
                {
                    "code": code,
                    "label": permission_labels.get(code, code),
                    "roles": {role.code: any(p.code == code for p in role.permissions) for role in roles},
                }
                for code in codes
            ],
        })
    return render_template("admin_matrice_droits.html", roles=roles, rows=rows)


@bp.route("/sante")
@login_required
@require_perm("admin:rbac")
def sante_systeme():
    import shutil as _shutil
    from sqlalchemy import text
    from app.services.sauvegarde import jours_depuis_derniere, dossier_sauvegardes
    from app.services.instance_settings import resolve_mail_settings
    from app.services.portail_apprenants import portail_configure

    # Base de données
    db_uri = current_app.config.get("SQLALCHEMY_DATABASE_URI", "") or ""
    moteur = "PostgreSQL" if db_uri.startswith("postgresql") else ("SQLite" if db_uri.startswith("sqlite") else "inconnu")
    try:
        db.session.execute(text("SELECT 1"))
        db_ok = True
    except Exception:  # noqa: BLE001
        db_ok = False

    # Espace disque (dossier des sauvegardes)
    disque = None
    try:
        usage = _shutil.disk_usage(str(dossier_sauvegardes()))
        disque = {
            "libre": usage.free,
            "total": usage.total,
            "pct_libre": round(usage.free / usage.total * 100) if usage.total else 0,
        }
    except Exception:  # noqa: BLE001
        disque = None

    mail = resolve_mail_settings(current_app.config)
    try:
        seuil = int(current_app.config.get("BACKUP_ALERT_DAYS") or 2)
    except (TypeError, ValueError):
        seuil = 2

    return render_template(
        "admin_sante.html",
        moteur=moteur,
        db_ok=db_ok,
        jours_depuis=jours_depuis_derniere(),
        seuil_alerte=seuil,
        disque=disque,
        smtp_configure=bool(mail["host"] and mail["sender"]),
        portail_configure=portail_configure(),
        nb_users=User.query.count(),
        nb_actifs=User.query.filter_by(actif=True).count(),
        nb_admins=_nb_admins_actifs(),
    )


@bp.route("/sante/test-smtp", methods=["POST"])
@login_required
@require_perm("admin:rbac")
def sante_test_smtp():
    from app.services.instance_settings import envoyer_email_test

    try:
        envoyer_email_test(current_app.config, getattr(current_user, "email", ""))
        flash(f"E-mail de test envoyé à {current_user.email} ✅ Vérifiez votre boîte.", "success")
    except Exception as exc:  # noqa: BLE001
        flash(f"Échec de l'envoi SMTP : {exc}", "danger")
    return redirect(url_for("admin.sante_systeme"))


@bp.route("/sante/test-portail", methods=["POST"])
@login_required
@require_perm("admin:rbac")
def sante_test_portail():
    from app.services import portail_apprenants as portail

    if not portail.portail_configure():
        flash("Portail des apprenants non configuré.", "warning")
        return redirect(url_for("admin.sante_systeme"))
    try:
        ok = portail.health()
        flash("Portail joignable ✅" if ok else "Portail joint mais réponse inattendue.", "success" if ok else "warning")
    except Exception as exc:  # noqa: BLE001
        flash(f"Portail injoignable : {exc}", "danger")
    return redirect(url_for("admin.sante_systeme"))


@bp.route("/import-excel", methods=["GET", "POST"])
@login_required
@require_perm("ateliers:sync")
def import_excel():
    """Import Excel des présences (format tableaux Antoine)."""
    if request.method == "GET":
        return render_template("admin_import_excel.html")

    f = request.files.get("xlsx_file")
    secteur = (request.form.get("secteur") or "NUMERIQUE").strip() or "NUMERIQUE"
    dry_run = (request.form.get("dry_run") in ("1", "true", "on", "yes"))

    if not f or not f.filename:
        flash("Fichier manquant.", "danger")
        return redirect(url_for("admin.import_excel"))

    filename = secure_filename(f.filename)
    tmpdir = tempfile.mkdtemp(prefix="import_excel_")
    tmp_path = os.path.join(tmpdir, filename)
    f.save(tmp_path)

    try:
        stats = import_presences_from_xlsx(tmp_path, secteur=secteur, dry_run=dry_run)
        if dry_run:
            flash("Dry-run OK : rien n'a été enregistré (rollback).", "info")
        else:
            flash("Import terminé ✅", "success")
        return render_template("admin_import_excel.html", stats=stats, secteur=secteur, dry_run=dry_run)
    except Exception as e:
        flash(f"Erreur import : {e}", "danger")
        return render_template("admin_import_excel.html", secteur=secteur, dry_run=dry_run)
    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass
        try:
            os.rmdir(tmpdir)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Référentiels / Compétences (DigComp, Pix, CléA...) - import CSV
# ---------------------------------------------------------------------------

@bp.route("/referentiels")
@login_required
@require_perm("admin:rbac")
def referentiels():
    # Nombre de compétences par framework
    frameworks = Framework.query.order_by(Framework.code.asc()).all()
    out = []
    for fw in frameworks:
        cnt = Skill.query.filter_by(framework_id=fw.id).count()
        out.append({
            "id": fw.id,
            "code": fw.code,
            "nom": fw.nom,
            "version": fw.version,
            "lang": fw.lang,
            "actif": fw.actif,
            "skill_count": cnt,
        })
    return render_template("admin_referentiels.html", frameworks=out)


@bp.route("/referentiels/import", methods=["GET", "POST"])
@login_required
@require_perm("admin:rbac")
def import_skills():
    preset_code = (request.args.get("framework_code") or "").strip() or None

    if request.method == "POST":
        fw_code = (request.form.get("framework_code") or "").strip()
        fw_name = (request.form.get("framework_name") or "").strip()
        fw_version = (request.form.get("framework_version") or "").strip() or None
        fw_lang = (request.form.get("framework_lang") or "").strip() or "fr"
        source_url = (request.form.get("source_url") or "").strip() or None

        f = request.files.get("csv_file")
        if not fw_code or not fw_name or not f:
            flash("Champs manquants (code, nom, CSV).", "danger")
            return redirect(url_for("admin.import_skills", framework_code=fw_code or ""))

        # Enregistrer temporairement (Windows-friendly) puis lire en utf-8-sig
        tmpdir = tempfile.mkdtemp(prefix="skills_import_")
        filename = secure_filename(f.filename or "skills.csv")
        csv_path = os.path.join(tmpdir, filename)
        f.save(csv_path)

        import csv as _csv

        fw = Framework.query.filter_by(code=fw_code).first()
        if not fw:
            fw = Framework(code=fw_code, nom=fw_name, version=fw_version, lang=fw_lang, source_url=source_url, actif=True)
            db.session.add(fw)
            db.session.flush()
        else:
            fw.nom = fw_name
            fw.version = fw_version
            fw.lang = fw_lang
            fw.source_url = source_url

        created = 0
        updated = 0

        with open(csv_path, "r", encoding="utf-8-sig", newline="") as fh:
            reader = _csv.DictReader(fh)
            required = {"code", "label"}
            missing = required - set(reader.fieldnames or [])
            if missing:
                flash(f"CSV invalide : colonnes manquantes {sorted(missing)}", "danger")
                return redirect(url_for("admin.import_skills", framework_code=fw_code))

            for row in reader:
                code = (row.get("code") or "").strip()
                if not code:
                    continue

                sk = Skill.query.filter_by(framework_id=fw.id, code=code).first()
                if not sk:
                    sk = Skill(framework_id=fw.id, code=code, label=(row.get('label') or '').strip() or code)
                    db.session.add(sk)
                    created += 1
                else:
                    updated += 1

                sk.label = (row.get("label") or "").strip() or sk.label
                sk.description = (row.get("description") or "").strip() or None
                sk.domain_code = (row.get("domain_code") or "").strip() or None
                sk.domain_label = (row.get("domain_label") or "").strip() or None

                so = (row.get("sort_order") or "").strip()
                if so.isdigit():
                    sk.sort_order = int(so)

                actif_raw = (row.get("actif") or "").strip().lower()
                if actif_raw in {"0", "false", "non", "n"}:
                    sk.actif = False
                elif actif_raw in {"1", "true", "oui", "o", "y", "yes"}:
                    sk.actif = True

        db.session.commit()
        flash(f"Import OK : {created} créées, {updated} mises à jour (framework {fw.code}).", "success")
        return redirect(url_for("admin.referentiels"))

    return render_template("admin_import_skills.html", preset_code=preset_code)
