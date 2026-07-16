"""Lot 4 Terrain — séances en série + qualité fiches (validation e-mail, audit)."""
import datetime as dt
import uuid

from flask import url_for


def test_creation_seances_en_serie(app, admin_client):
    from app.extensions import db
    from app.models import AtelierActivite, SessionActivite

    with app.app_context():
        at = AtelierActivite(nom=f"Hebdo{uuid.uuid4().hex[:6]}", secteur="Numérique", type_atelier="COLLECTIF")
        db.session.add(at)
        db.session.commit()
        aid = at.id
        with app.test_request_context():
            url = url_for("activite.session_bulk_new", atelier_id=aid)

    d0, d1 = dt.date(2026, 6, 1), dt.date(2026, 6, 30)
    attendu = sum(1 for n in range((d1 - d0).days + 1) if (d0 + dt.timedelta(days=n)).weekday() == 3)

    admin_client.post(url, data={
        "date_debut": "2026-06-01", "date_fin": "2026-06-30", "weekday": "3",
        "heure_debut": "14:00", "heure_fin": "16:00", "capacite": "12",
    }, follow_redirects=True)

    with app.app_context():
        n = (SessionActivite.query.filter_by(atelier_id=aid)
             .filter(SessionActivite.date_session.isnot(None)).count())
        assert n == attendu and attendu >= 4   # jeudis de juin 2026


def test_page_bulk_rendue(app, admin_client):
    from app.extensions import db
    from app.models import AtelierActivite

    with app.app_context():
        at = AtelierActivite(nom=f"B{uuid.uuid4().hex[:6]}", secteur="Numérique", type_atelier="COLLECTIF")
        db.session.add(at)
        db.session.commit()
        with app.test_request_context():
            url = url_for("activite.session_bulk_new", atelier_id=at.id)
    r = admin_client.get(url)
    assert r.status_code == 200
    assert "en série" in r.get_data(as_text=True)


def _participant(app):
    from app.extensions import db
    from app.models import Participant
    p = Participant(nom=f"Fic{uuid.uuid4().hex[:6]}", prenom="T")
    db.session.add(p)
    db.session.commit()
    return p.id


def test_edit_email_invalide_refuse(app, admin_client):
    from app.extensions import db
    from app.models import Participant

    with app.app_context():
        pid = _participant(app)
        with app.test_request_context():
            url = url_for("participants.edit_participant", participant_id=pid)

    admin_client.post(url, data={"nom": "T", "prenom": "T", "email": "dupont@"}, follow_redirects=True)
    with app.app_context():
        assert db.session.get(Participant, pid).email is None   # e-mail invalide rejeté


def test_edit_valide_journalise(app, admin_client):
    from app.extensions import db
    from app.models import Participant, AuditLog

    with app.app_context():
        pid = _participant(app)
        with app.test_request_context():
            url = url_for("participants.edit_participant", participant_id=pid)

    admin_client.post(url, data={"nom": "T", "prenom": "T", "email": "ok@exemple.fr"}, follow_redirects=True)
    with app.app_context():
        assert db.session.get(Participant, pid).email == "ok@exemple.fr"
        assert AuditLog.query.filter_by(action="participant.edit").count() >= 1
