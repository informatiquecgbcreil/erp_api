"""Lot 3 — Traçabilité (journal d'audit) & page Santé du système."""
import uuid


def test_journaliser_ecrit_une_entree(app):
    from app.extensions import db
    from app.models import AuditLog
    from app.services.audit import journaliser

    with app.app_context():
        avant = AuditLog.query.count()
        journaliser("test.action", cible="cible-x", details={"k": "v"})
        assert AuditLog.query.count() == avant + 1
        e = AuditLog.query.order_by(AuditLog.id.desc()).first()
        assert e.action == "test.action" and e.cible == "cible-x"
        assert "v" in (e.details or "")


def test_creation_user_journalisee(admin_client, app):
    from app.models import AuditLog

    email = f"audit{uuid.uuid4().hex[:8]}@ex.org"
    admin_client.post("/admin/users", data={
        "email": email, "nom": "Audit", "password": "motdepasse-audit",
        "role": "responsable_secteur",
    }, follow_redirects=True)
    with app.app_context():
        e = AuditLog.query.filter_by(action="user.create", cible=email).first()
        assert e is not None
        assert e.user_email  # auteur renseigné


def test_page_journal(admin_client):
    r = admin_client.get("/admin/journal")
    assert r.status_code == 200
    assert "Journal d'audit" in r.get_data(as_text=True)


def test_page_sante(admin_client):
    r = admin_client.get("/admin/sante")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Santé du système" in body
    assert "Base de données" in body


def test_test_smtp_sans_config(admin_client):
    r = admin_client.post("/admin/sante/test-smtp", follow_redirects=True)
    assert r.status_code == 200
    # Sans SMTP configuré : message d'échec lisible, pas de crash.
    assert "SMTP" in r.get_data(as_text=True)


def test_test_portail_sans_config(admin_client, app, monkeypatch):
    from app.services import portail_apprenants as portail
    monkeypatch.setattr(portail, "portail_configure", lambda: False)
    r = admin_client.post("/admin/sante/test-portail", follow_redirects=True)
    assert r.status_code == 200
    assert "non configuré" in r.get_data(as_text=True)
