"""Lot 1 Terrain — émargement : autocomplétion, statut de présence, correction."""
import datetime as dt
import uuid

from flask import url_for


def _session(app):
    from app.extensions import db
    from app.models import AtelierActivite, SessionActivite, Participant

    at = AtelierActivite(nom=f"At {uuid.uuid4().hex[:6]}", secteur="Numérique")
    db.session.add(at)
    db.session.flush()
    s = SessionActivite(atelier_id=at.id, secteur="Numérique",
                        date_session=dt.date(2026, 5, 12), session_type="COLLECTIF")
    db.session.add(s)
    p = Participant(nom=f"Emarg{uuid.uuid4().hex[:6]}", prenom="Test")
    db.session.add(p)
    db.session.commit()
    return s.id, p.id


def _url(app, sid):
    with app.test_request_context():
        return url_for("activite.emargement", session_id=sid)


def test_emargement_pose_le_statut(app, admin_client):
    from app.extensions import db
    from app.models import PresenceActivite

    with app.app_context():
        sid, pid = _session(app)
        url = _url(app, sid)

    admin_client.post(url, data={"action": "emarger", "participant_id": pid,
                                 "presence_type": "retard"}, follow_redirects=True)
    with app.app_context():
        pr = PresenceActivite.query.filter_by(session_id=sid, participant_id=pid).first()
        assert pr is not None and pr.presence_type == "retard"


def test_update_presence_type(app, admin_client):
    from app.extensions import db
    from app.models import PresenceActivite

    with app.app_context():
        sid, pid = _session(app)
        url = _url(app, sid)
    admin_client.post(url, data={"action": "emarger", "participant_id": pid}, follow_redirects=True)
    with app.app_context():
        pr = PresenceActivite.query.filter_by(session_id=sid, participant_id=pid).first()
        assert pr.presence_type == "present"
        prid = pr.id

    admin_client.post(url, data={"action": "update_presence_type", "presence_id": prid,
                                 "presence_type": "absent_excuse"}, follow_redirects=True)
    with app.app_context():
        pr = db.session.get(PresenceActivite, prid)
        assert pr.presence_type == "absent_excuse"


def test_delete_presence_et_audit(app, admin_client):
    from app.extensions import db
    from app.models import PresenceActivite, AuditLog

    with app.app_context():
        sid, pid = _session(app)
        url = _url(app, sid)
    admin_client.post(url, data={"action": "emarger", "participant_id": pid}, follow_redirects=True)
    with app.app_context():
        prid = PresenceActivite.query.filter_by(session_id=sid, participant_id=pid).first().id
        n_audit = AuditLog.query.filter_by(action="presence.delete").count()

    admin_client.post(url, data={"action": "delete_presence", "presence_id": prid}, follow_redirects=True)
    with app.app_context():
        assert db.session.get(PresenceActivite, prid) is None
        assert AuditLog.query.filter_by(action="presence.delete").count() == n_audit + 1


def test_page_emargement_autocomplete(app, admin_client):
    with app.app_context():
        sid, pid = _session(app)
        url = _url(app, sid)
    r = admin_client.get(url)
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "participantSearch" in body
    assert "data-search-url" in body


def test_endpoint_recherche(admin_client, app):
    from app.extensions import db
    from app.models import Participant

    nom = f"Cherchable{uuid.uuid4().hex[:6]}"
    with app.app_context():
        db.session.add(Participant(nom=nom, prenom="Z"))
        db.session.commit()
    r = admin_client.get(f"/participants/search?q={nom[:8]}")
    assert r.status_code == 200 and r.is_json
    assert any(nom in (it["nom"] or "") for it in r.get_json()["items"])
