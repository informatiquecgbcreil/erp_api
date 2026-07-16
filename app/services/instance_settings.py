from app.extensions import db
from app.models import InstanceSettings


def get_or_create_instance_settings() -> InstanceSettings:
    row = InstanceSettings.query.first()
    if row:
        return row
    row = InstanceSettings()
    db.session.add(row)
    db.session.commit()
    return row


def resolve_identity(default_app_name: str, default_org_name: str) -> tuple[str, str, str | None, str | None]:
    row = InstanceSettings.query.first()
    if not row:
        return default_app_name, default_org_name, None, None

    app_name = (row.app_name or "").strip() or default_app_name
    org_name = (row.organization_name or "").strip() or default_org_name
    return app_name, org_name, row.app_logo_path, row.organization_logo_path


def resolve_mail_settings(config: dict) -> dict:
    """Retourne la config SMTP effective (DB prioritaire, fallback env)."""
    row = InstanceSettings.query.first()

    host = (config.get("MAIL_HOST") or "").strip()
    sender = (config.get("MAIL_SENDER") or "").strip()
    username = (config.get("MAIL_USERNAME") or "").strip()
    password = config.get("MAIL_PASSWORD") or ""
    port = int(config.get("MAIL_PORT", 587) or 587)
    use_tls = bool(config.get("MAIL_USE_TLS", True))

    if row:
        host = (row.smtp_host or "").strip() or host
        sender = (row.smtp_sender or "").strip() or sender
        username = (row.smtp_username or "").strip() or username
        password = (row.smtp_password or "") or password
        port = int(row.smtp_port or port)
        use_tls = bool(row.smtp_use_tls if row.smtp_use_tls is not None else use_tls)

    return {
        "host": host,
        "port": port,
        "username": username,
        "password": password,
        "use_tls": use_tls,
        "sender": sender,
    }


def envoyer_email_test(config: dict, to: str) -> None:
    """Envoie un e-mail de test via la configuration SMTP effective.

    Lève une exception (message lisible) si la configuration est absente ou
    si l'envoi échoue — utilisé par la page « Santé du système »."""
    import smtplib
    from email.message import EmailMessage

    cfg = resolve_mail_settings(config)
    host, sender = cfg["host"], cfg["sender"]
    if not host or not sender:
        raise RuntimeError("SMTP non configuré (hôte ou expéditeur manquant).")
    if not to:
        raise RuntimeError("Aucune adresse de destination (renseignez l'e-mail de votre compte).")

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = "Test SMTP — " + (config.get("APP_NAME") or "Application")
    msg.set_content(
        "Ceci est un e-mail de test.\n\n"
        "Si vous le recevez, la configuration SMTP de l'application fonctionne."
    )

    port = int(cfg["port"])
    if port == 465:
        server = smtplib.SMTP_SSL(host, port, timeout=10)
        server.ehlo()
    else:
        server = smtplib.SMTP(host, port, timeout=10)
        server.ehlo()
        if cfg["use_tls"]:
            server.starttls(timeout=10)
            server.ehlo()
    try:
        if cfg["username"] and cfg["password"]:
            server.login(cfg["username"], cfg["password"])
        server.send_message(msg)
    finally:
        try:
            server.quit()
        except Exception:  # noqa: BLE001
            pass


def resolve_public_base_url(config: dict) -> str:
    """Retourne l'URL publique de l'application (DB prioritaire, fallback env)."""
    row = InstanceSettings.query.first()
    if row and (row.public_base_url or '').strip():
        return row.public_base_url.strip().rstrip('/')
    return (config.get('PUBLIC_BASE_URL') or '').strip().rstrip('/')
