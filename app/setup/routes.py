from flask import render_template, request, redirect, url_for, flash

from app.extensions import db
from app.setup import bp
from app.models import User, Role, InstanceSettings
from app.rbac import bootstrap_rbac


@bp.route("/", methods=["GET", "POST"])
def wizard():
    if User.query.first():
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        app_name = (request.form.get("app_name") or "").strip()
        org_name = (request.form.get("organization_name") or "").strip()
        admin_name = (request.form.get("admin_name") or "").strip() or "Admin"
        admin_email = (request.form.get("admin_email") or "").strip().lower()
        admin_password = (request.form.get("admin_password") or "").strip()
        public_base_url = (request.form.get("public_base_url") or "").strip() or None

        smtp_host = (request.form.get("smtp_host") or "").strip() or None
        smtp_port_raw = (request.form.get("smtp_port") or "").strip()
        smtp_port = int(smtp_port_raw) if smtp_port_raw.isdigit() else None
        smtp_username = (request.form.get("smtp_username") or "").strip() or None
        smtp_password = (request.form.get("smtp_password") or "").strip() or None
        smtp_use_tls = request.form.get("smtp_use_tls") in {"1", "true", "on", "yes", "YES"}
        smtp_sender = (request.form.get("smtp_sender") or "").strip() or None

        if not admin_email or "@" not in admin_email:
            flash("Veuillez saisir un email admin valide.", "danger")
            return render_template("setup/wizard.html")

        if len(admin_password) < 8:
            flash("Le mot de passe admin doit contenir au moins 8 caractères.", "danger")
            return render_template("setup/wizard.html")

        if smtp_host and (not smtp_port or smtp_port <= 0):
            flash("Port SMTP invalide. Exemple: 587.", "danger")
            return render_template("setup/wizard.html")
        if smtp_host and not smtp_sender:
            flash("Veuillez indiquer une adresse expéditeur SMTP.", "danger")
            return render_template("setup/wizard.html")

        bootstrap_rbac()

        settings = InstanceSettings.query.first() or InstanceSettings()
        if not settings.id:
            db.session.add(settings)
        settings.app_name = app_name or None
        settings.organization_name = org_name or None
        settings.public_base_url = public_base_url

        settings.smtp_host = smtp_host
        settings.smtp_port = smtp_port
        settings.smtp_username = smtp_username
        settings.smtp_password = smtp_password
        settings.smtp_use_tls = smtp_use_tls if smtp_host else None
        settings.smtp_sender = smtp_sender

        u = User(email=admin_email, nom=admin_name)
        u.set_password(admin_password)

        # Premier compte = super-administrateur complet (accès total + gestion
        # des utilisateurs/droits). Le rôle "direction" porte toutes les
        # permissions, ce qui évite qu'un premier admin ne puisse pas accéder
        # à ses propres données (ex. liste des participants).
        role = (
            Role.query.filter_by(code="direction").first()
            or Role.query.filter_by(code="admin_tech").first()
        )
        if role:
            u.roles.append(role)

        db.session.add(u)
        db.session.commit()

        flash("Installation terminée ✅ Connectez-vous avec votre compte admin.", "success")
        return redirect(url_for("auth.login"))

    return render_template("setup/wizard.html")
