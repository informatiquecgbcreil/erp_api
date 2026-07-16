"""Kiosque : prévention des doublons à la création d'une fiche (P4 revue)."""
import datetime as dt
import uuid

import pytest


@pytest.fixture()
def session_kiosque(app):
    """Session ouverte au kiosque + un participant existant « Mohamed Benali »."""
    with app.app_context():
        from app.extensions import db
        from app.models import AtelierActivite, SessionActivite, Participant
        suf = uuid.uuid4().hex[:6]
        at = AtelierActivite(nom=f"Kd{suf}", secteur="Numérique", type_atelier="COLLECTIF")
        db.session.add(at)
        db.session.flush()
        s = SessionActivite(atelier_id=at.id, secteur="Numérique", session_type="COLLECTIF",
                            date_session=dt.date(2012, 4, 4),
                            kiosk_open=True, kiosk_token=f"tok{suf}", kiosk_pin="1234")
        db.session.add(s)
        p = Participant(nom=f"Benali{suf}", prenom="Mohamed", ville="Creil")
        db.session.add(p)
        db.session.commit()
        return {"token": s.kiosk_token, "nom": p.nom, "pid": p.id, "suf": suf}


def test_normalisation_et_candidats(app, session_kiosque):
    from app.kiosk.routes import _normaliser_nom, _candidats_doublons

    assert _normaliser_nom("Éléonore") == "eleonore"
    assert _normaliser_nom("  D'Angelo ") == "dangelo"

    with app.app_context():
        # variante accent/casse -> détectée
        c = _candidats_doublons(session_kiosque["nom"].upper(), "mohamed")
        assert any(x.id == session_kiosque["pid"] for x in c)
        # variante Mohammed (préfixe commun) -> détectée
        c = _candidats_doublons(session_kiosque["nom"], "Mohammed")
        assert any(x.id == session_kiosque["pid"] for x in c)
        # nom totalement différent -> rien
        assert _candidats_doublons(f"Zz{uuid.uuid4().hex[:6]}", "Mohamed") == []


def test_kiosque_propose_les_doublons_avant_creation(app, client, session_kiosque):
    from app.models import Participant

    url = f"/kiosk/session/{session_kiosque['token']}"
    r = client.post(url, data={"action": "add_participant",
                               "nom": session_kiosque["nom"], "prenom": "Mohammed",
                               "ville": "Creil"})
    body = r.get_data(as_text=True)
    assert r.status_code == 200
    assert "personnes qui vous ressemblent" in body
    assert session_kiosque["nom"] in body
    assert "C'est moi" in body
    with app.app_context():
        # rien n'a été créé
        assert Participant.query.filter_by(nom=session_kiosque["nom"]).count() == 1


def test_kiosque_force_creation_apres_confirmation(app, client, session_kiosque):
    from app.models import Participant

    url = f"/kiosk/session/{session_kiosque['token']}"
    r = client.post(url, data={"action": "add_participant",
                               "nom": session_kiosque["nom"], "prenom": "Mohammed",
                               "ville": "Creil", "force_creation": "1"},
                    follow_redirects=True)
    assert r.status_code == 200
    with app.app_context():
        assert Participant.query.filter_by(nom=session_kiosque["nom"]).count() == 2


def test_kiosque_creation_directe_sans_homonyme(app, client, session_kiosque):
    from app.models import Participant

    nom = f"Unique{uuid.uuid4().hex[:6]}"
    url = f"/kiosk/session/{session_kiosque['token']}"
    client.post(url, data={"action": "add_participant", "nom": nom, "prenom": "Lea"},
                follow_redirects=True)
    with app.app_context():
        assert Participant.query.filter_by(nom=nom).count() == 1
