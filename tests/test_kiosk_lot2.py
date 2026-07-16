"""Lot 2 Terrain — kiosque robuste : reçu, bannière hors-ligne, cache de recherche."""
import datetime as dt
import uuid

from flask import url_for


def _open_session(app):
    from app.extensions import db
    from app.models import AtelierActivite, SessionActivite, Participant

    token = uuid.uuid4().hex
    at = AtelierActivite(nom=f"K{uuid.uuid4().hex[:6]}", secteur="Numérique")
    db.session.add(at)
    db.session.flush()
    s = SessionActivite(atelier_id=at.id, secteur="Numérique", date_session=dt.date(2026, 5, 12),
                        session_type="COLLECTIF", kiosk_open=True, kiosk_token=token)
    db.session.add(s)
    p = Participant(nom=f"Kio{uuid.uuid4().hex[:6]}", prenom="T")
    db.session.add(p)
    db.session.commit()
    return token, p.id


def test_kiosk_recu_apres_emargement(app, client):
    with app.app_context():
        token, pid = _open_session(app)
        url = None
        with app.test_request_context():
            url = url_for("kiosk.kiosk_session", token=token)

    r = client.post(url, data={"action": "emarger", "participant_id": pid, "signature_data": ""},
                    follow_redirects=True)
    assert r.status_code == 200
    assert "Reçu n°" in r.get_data(as_text=True)


def test_kiosk_page_offline_resilience(app, client):
    with app.app_context():
        token, pid = _open_session(app)
        with app.test_request_context():
            url = url_for("kiosk.kiosk_session", token=token)

    r = client.get(url)
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "offlineBanner" in body            # bannière hors-ligne
    assert "kiosk_search_cache_" in body       # cache local de recherche
    assert "resizeCanvas" in body              # signature responsive
