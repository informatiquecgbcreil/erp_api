"""Tests du kiosque public d'émargement (accueil, session, création, présence, avis).

Le kiosque est public (sans connexion) : ces flux doivent fonctionner
pour un visiteur anonyme, et l'interface tactile doit charger sa feuille
de style dédiée.
"""
import uuid
from datetime import date, timedelta

import pytest


@pytest.fixture()
def session_kiosk(app):
    """Une session ouverte au kiosque aujourd'hui (atelier collectif)."""
    suffixe = uuid.uuid4().hex[:8]
    with app.app_context():
        from app.extensions import db
        from app.models import AtelierActivite, SessionActivite

        atelier = AtelierActivite(nom=f"Atelier kiosk {suffixe}", secteur="Numérique")
        db.session.add(atelier)
        db.session.flush()
        s = SessionActivite(
            atelier_id=atelier.id,
            secteur="Numérique",
            session_type="COLLECTIF",
            date_session=date.today(),
            heure_debut="14:00",
            heure_fin="16:00",
            kiosk_open=True,
            kiosk_pin="4242",
            kiosk_token=f"tok{suffixe}",
        )
        db.session.add(s)
        db.session.commit()
        return {"token": s.kiosk_token, "pin": s.kiosk_pin, "atelier": atelier.nom, "session_id": s.id}

def test_accueil_kiosque_public_et_style(client, session_kiosk):
    r = client.get("/kiosk/")
    assert r.status_code == 200, "le kiosque doit être public (sans connexion)"
    page = r.get_data(as_text=True)
    assert "kiosk.css" in page, "la feuille de style tactile doit être chargée"
    assert session_kiosk["atelier"] in page, "l'atelier ouvert doit apparaître comme pavé tactile"
    assert 'class="k-session"' in page


def test_accueil_kiosque_liste_les_sessions_ouvertes_hors_date(app, client):
    """Une session ouverte doit remonter même si sa date métier n'est pas aujourd'hui."""
    suffixe = uuid.uuid4().hex[:8]
    with app.app_context():
        from app.extensions import db
        from app.models import AtelierActivite, SessionActivite

        atelier = AtelierActivite(nom=f"Atelier ouvert veille {suffixe}", secteur="Numérique")
        db.session.add(atelier)
        db.session.flush()
        db.session.add(
            SessionActivite(
                atelier_id=atelier.id,
                secteur="Numérique",
                session_type="COLLECTIF",
                date_session=date.today() - timedelta(days=1),
                heure_debut="09:00",
                heure_fin="11:00",
                kiosk_open=True,
                kiosk_pin=f"{int(suffixe[:4], 16) % 10000:04d}",
                kiosk_token=f"tokveille{suffixe}",
            )
        )
        db.session.commit()
        atelier_nom = atelier.nom

    r = client.get("/kiosk/")
    assert r.status_code == 200
    assert atelier_nom in r.get_data(as_text=True)


def test_lien_et_qr_kiosque_utilisent_url_publique(app, admin_client, session_kiosk):
    """Le QR doit encoder l'URL LAN configurée, pas localhost/request.url_root."""
    previous_public_base_url = app.config.get("PUBLIC_BASE_URL")
    app.config["PUBLIC_BASE_URL"] = "http://192.168.1.50:8000/"
    try:
        r = admin_client.get(f"/activite/session/{session_kiosk['session_id']}/emargement")
        assert r.status_code == 200
        page = r.get_data(as_text=True)
        expected = f"http://192.168.1.50:8000/kiosk/session/{session_kiosk['token']}"
        assert expected in page
        assert "http://localhost/kiosk/session" not in page

        launcher = admin_client.get("/launcher/")
        assert launcher.status_code == 200
        assert "http://192.168.1.50:8000/kiosk/" in launcher.get_data(as_text=True)

        qr = admin_client.get("/launcher/qr?target=kiosk")
        assert qr.status_code == 200
        assert "image/svg+xml" in qr.headers.get("Content-Type", "")
    finally:
        app.config["PUBLIC_BASE_URL"] = previous_public_base_url

def test_entree_par_code_pin(client, session_kiosk):
    r = client.post("/kiosk/", data={"pin": session_kiosk["pin"]})
    assert r.status_code == 302
    assert session_kiosk["token"] in r.headers["Location"]


def test_code_invalide_revient_accueil(client):
    r = client.post("/kiosk/", data={"pin": "0000"}, follow_redirects=True)
    assert r.status_code == 200
    assert "invalide" in r.get_data(as_text=True).lower()


def test_page_session_rouages_intacts(client, session_kiosk):
    r = client.get(f"/kiosk/session/{session_kiosk['token']}")
    assert r.status_code == 200
    page = r.get_data(as_text=True)
    # Les rouages fonctionnels doivent être préservés par le restylage
    for hook in ['id="emargeForm"', 'name="action" value="emarger"',
                 'id="signature_data"', 'id="participant_id"',
                 'id="sig"', 'id="search"']:
        assert hook in page, f"rouage manquant : {hook}"


def test_creation_participant_au_kiosque(app, client, session_kiosk):
    nom = f"KioskNew{uuid.uuid4().hex[:6]}"
    r = client.post(
        f"/kiosk/session/{session_kiosk['token']}",
        data={"action": "add_participant", "nom": nom, "prenom": "Test", "type_public": "H"},
    )
    assert r.status_code == 302
    with app.app_context():
        from app.models import Participant

        assert Participant.query.filter_by(nom=nom).first() is not None


def test_emargement_enregistre_presence(app, client, session_kiosk):
    with app.app_context():
        from app.extensions import db
        from app.models import Participant

        p = Participant(nom=f"KioskPres{uuid.uuid4().hex[:6]}", prenom="Test")
        db.session.add(p)
        db.session.commit()
        pid = p.id

    r = client.post(
        f"/kiosk/session/{session_kiosk['token']}",
        data={"action": "emarger", "participant_id": pid},
    )
    assert r.status_code == 200
    assert "bon" in r.get_data(as_text=True).lower()  # « Merci, c'est bon ! »

    with app.app_context():
        from app.models import PresenceActivite

        pr = PresenceActivite.query.filter_by(
            session_id=session_kiosk["session_id"], participant_id=pid
        ).first()
        assert pr is not None, "la présence doit être enregistrée"


def test_recherche_participant_kiosk(app, client, session_kiosk):
    with app.app_context():
        from app.extensions import db
        from app.models import Participant

        db.session.add(Participant(nom="Zorglub", prenom="Marcel"))
        db.session.commit()

    r = client.get(f"/kiosk/session/{session_kiosk['token']}/search?q=Zorg")
    assert r.status_code == 200
    data = r.get_json()
    assert any("Zorglub" in res["label"] for res in data["results"])


def test_page_avis_publique(client, session_kiosk):
    r = client.get(f"/kiosk/session/{session_kiosk['token']}/feedback")
    assert r.status_code == 200
    assert "kiosk.css" in r.get_data(as_text=True)


def test_session_inexistante_404(client):
    assert client.get("/kiosk/session/inexistant").status_code == 404


def test_programme_en_direct_public(client, session_kiosk):
    """Le programme est public et liste les ateliers ouverts, sans émarger."""
    r = client.get("/kiosk/programme")
    assert r.status_code == 200
    page = r.get_data(as_text=True)
    assert "kiosk.css" in page
    assert session_kiosk["atelier"] in page, "l'atelier ouvert doit apparaître au programme"

def test_accueil_kiosque_lien_vers_programme(client, session_kiosk):
    r = client.get("/kiosk/")
    assert "/kiosk/programme" in r.get_data(as_text=True)
