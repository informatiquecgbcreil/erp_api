import os
import smtplib
from email.message import EmailMessage


def send_email_with_attachment(
    *,
    host: str,
    port: int,
    username: str | None,
    password: str | None,
    use_tls: bool,
    sender: str,
    to: str,
    subject: str,
    body: str,
    attachment_path: str,
):
    """Send an email with a single attachment via SMTP.

    All parameters must be explicit so we can keep configs optional in dev.
    """

    if not host:
        raise ValueError("SMTP host missing")
    if not sender:
        raise ValueError("Sender missing")
    if not to:
        raise ValueError("Recipient missing")
    if not attachment_path or not os.path.exists(attachment_path):
        raise FileNotFoundError("Attachment not found")

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject or "Document"
    msg.set_content(body or "")

    filename = os.path.basename(attachment_path)
    with open(attachment_path, "rb") as f:
        data = f.read()

    # basic mime guess
    if filename.lower().endswith(".pdf"):
        maintype, subtype = "application", "pdf"
    elif filename.lower().endswith(".docx"):
        maintype, subtype = "application", "vnd.openxmlformats-officedocument.wordprocessingml.document"
    else:
        maintype, subtype = "application", "octet-stream"

    msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)

    if use_tls:
        server = smtplib.SMTP(host, port)
        server.starttls()
    else:
        server = smtplib.SMTP(host, port)

    try:
        if username and password:
            server.login(username, password)
        server.send_message(msg)
    finally:
        try:
            server.quit()
        except Exception:
            pass
