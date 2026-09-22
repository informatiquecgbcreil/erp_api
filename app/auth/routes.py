import smtplib
import ssl
from email.message import EmailMessage
from socket import timeout as SocketTimeout
from urllib.parse import urljoin

from flask import Blueprint, current_app, render_template, request, redirect, url_for, flash
from flask_login import login_user, logout_user, login_required
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.extensions import db
from app.models import User
from app.services.instance_settings import resolve_mail_settings, resolve_public_base_url

bp = Blueprint("auth", __name__)

PASSWORD_RESET_SALT = "password-reset"


def _reset_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"])


def _build_password_reset_token(user: User) -> str:
    """Construit un jeton signé invalide après changement de mot de passe."""
    payload = {
        "uid": user.id,
        "pwd_sig": (user.password_hash or "")[-24:],
    }
    return _reset_serializer().dumps(payload, salt=PASSWORD_RESET_SALT)


def _load_password_reset_user(token: str) -> User | None:
    max_age = int(current_app.config.get("PASSWORD_RESET_TOKEN_MAX_AGE_SECONDS", 3600))
    try:
        payload = _reset_serializer().loads(token, salt=PASSWORD_RESET_SALT, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None

    user = db.session.get(User, payload.get("uid"))
    if not user:
        return None

    if payload.get("pwd_sig") != (user.password_hash or "")[-24:]:
        return None

    return user




_HOTES_LOCAUX = {"localhost", "127.0.0.1", "::1", "[::1]"}


def _build_external_reset_link(token: str) -> str | None:
    """Construit un lien de reset utilisable hors du poste hôte.

    Sécurité (« empoisonnement du lien de réinitialisation ») : l'adresse
    du lien ne doit JAMAIS être déduite de l'en-tête Host de la requête,
    que l'auteur de la demande choisit librement. Sinon, il suffit de
    demander la réinitialisation du compte de la direction en annonçant
    « Host: site-pirate.fr » : l'e-mail légitime part avec un lien vers ce
    site, et le premier clic y dépose le jeton.

    D'où : URL publique configurée (installateur, page « Ma structure » ou
    ERP_PUBLIC_BASE_URL) ; à défaut, seul un accès local au serveur est
    accepté ; sinon, pas de lien du tout (journalisé).
    """
    public_base_url = resolve_public_base_url(current_app.config)
    reset_path = url_for("auth.password_reset_token", token=token)
    if public_base_url:
        return urljoin(public_base_url + "/", reset_path.lstrip("/"))

    hote = (request.host or "").rsplit(":", 1)[0].strip().lower() if request.host else ""
    if request.host and request.host.startswith("["):
        hote = request.host.split("]", 1)[0] + "]"
    if hote in _HOTES_LOCAUX:
        request_base = (request.host_url or "").strip().rstrip("/")
        return urljoin(request_base + "/", reset_path.lstrip("/"))

    current_app.logger.error(
        "Réinitialisation de mot de passe : aucune URL publique configurée "
        "(Administration > Ma structure, ou ERP_PUBLIC_BASE_URL). Lien non envoyé."
    )
    return None

def _send_password_reset_email(to_email: str, reset_link: str) -> bool:
    mail_cfg = resolve_mail_settings(current_app.config)
    host = mail_cfg["host"]
    sender = mail_cfg["sender"]
    if not host or not sender:
        return False

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to_email
    msg["Subject"] = f"{current_app.config.get('APP_NAME', 'Application')} - Réinitialisation du mot de passe"
    msg.set_content(
        "Bonjour,\n\n"
        "Une demande de réinitialisation de mot de passe a été reçue.\n"
        f"Lien de réinitialisation : {reset_link}\n\n"
        f"Ce lien expire dans {int(current_app.config.get('PASSWORD_RESET_TOKEN_MAX_AGE_SECONDS', 3600)) // 60} minutes.\n"
        "Si vous n'êtes pas à l'origine de cette demande, ignorez ce message.\n"
    )

    port = int(mail_cfg["port"])
    use_tls = bool(mail_cfg["use_tls"])
    username = (mail_cfg["username"] or "").strip()
    password = mail_cfg["password"] or ""
    smtp_timeout = float(current_app.config.get("MAIL_TIMEOUT_SECONDS", 10))

    server = None
    try:
        # Compat fournisseurs: 465 = SSL implicite (SMTPS), 587 = STARTTLS explicite.
        use_ssl_implicit = (port == 465)
        if use_ssl_implicit:
            server = smtplib.SMTP_SSL(host, port, timeout=smtp_timeout, context=ssl.create_default_context())
            server.ehlo()
        else:
            server = smtplib.SMTP(host, port, timeout=smtp_timeout)
            server.ehlo()
            if use_tls:
                server.starttls(context=ssl.create_default_context())
                server.ehlo()

        if username and password:
            server.login(username, password)
        server.send_message(msg)
        return True
    except (OSError, smtplib.SMTPException, SocketTimeout) as exc:
        current_app.logger.warning(
            "Password reset email send failed (host=%s port=%s tls=%s ssl_implicit=%s): %s",
            host,
            port,
            use_tls,
            (port == 465),
            exc,
        )
        return False
    finally:
        if server is not None:
            try:
                server.quit()
            except Exception:
                pass

@bp.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        from app.services.connexion_securite import (
            enregistrer_echec,
            enregistrer_succes,
            minutes_avant_deverrouillage,
        )

        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        adresse_ip = request.remote_addr

        minutes = minutes_avant_deverrouillage(email)
        if minutes > 0:
            flash(
                "Trop de tentatives échouées. Par sécurité, la connexion est "
                f"bloquée pour ce compte : réessayez dans {minutes} minute(s).",
                "danger",
            )
            current_app.logger.warning(
                "Connexion refusée (compte verrouillé) pour %s depuis %s", email, adresse_ip
            )
            return render_template("login.html")

        u = User.query.filter_by(email=email).first()
        if not u or not u.check_password(password):
            minutes = enregistrer_echec(email, adresse_ip)
            if minutes > 0:
                current_app.logger.warning(
                    "Verrouillage déclenché pour %s depuis %s (%s min)",
                    email, adresse_ip, minutes,
                )
            flash("Identifiants invalides.", "danger")
            return render_template("login.html")

        if not getattr(u, "actif", True):
            flash("Ce compte est désactivé. Contactez un administrateur.", "danger")
            current_app.logger.warning("Connexion refusée (compte désactivé) pour %s", email)
            return render_template("login.html")

        enregistrer_succes(email, adresse_ip)
        login_user(u)
        return redirect(url_for("main.dashboard"))

    return render_template("login.html")


@bp.route("/password-reset", methods=["GET", "POST"])
def password_reset_request():
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        user = User.query.filter_by(email=email).first()

        debug_link = None
        if user:
            token = _build_password_reset_token(user)
            reset_link = _build_external_reset_link(token)
            sent = bool(reset_link) and _send_password_reset_email(user.email, reset_link)

            if not sent and current_app.debug and current_app.config.get("PASSWORD_RESET_ALLOW_DEBUG_LINK", False):
                debug_link = reset_link

            current_app.logger.info("Password reset requested for %s (sent=%s)", user.email, sent)

        flash(
            "Si un compte correspond à cet email, un lien de réinitialisation a été envoyé.",
            "info",
        )
        return render_template("password_reset_request.html", debug_link=debug_link)

    return render_template("password_reset_request.html")


@bp.route("/password-reset/<token>", methods=["GET", "POST"])
def password_reset_token(token: str):
    user = _load_password_reset_user(token)
    if not user:
        flash("Le lien de réinitialisation est invalide ou expiré.", "danger")
        return redirect(url_for("auth.password_reset_request"))

    if request.method == "POST":
        password = request.form.get("password") or ""
        password_confirm = request.form.get("password_confirm") or ""

        if len(password) < 10:
            flash("Le mot de passe doit contenir au moins 10 caractères.", "danger")
            return render_template("password_reset_form.html", token=token)
        if password != password_confirm:
            flash("La confirmation du mot de passe ne correspond pas.", "danger")
            return render_template("password_reset_form.html", token=token)

        user.set_password(password)
        db.session.commit()
        flash("Votre mot de passe a été réinitialisé. Vous pouvez vous connecter.", "success")
        return redirect(url_for("auth.login"))

    return render_template("password_reset_form.html", token=token)

@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))
