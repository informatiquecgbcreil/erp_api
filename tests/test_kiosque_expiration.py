"""Pointage tablette expiré (12 h) : la page d'émargement doit le dire.

Constaté au centre : séance ouverte la veille, la page d'émargement affichait
toujours « Pointage tablette ouvert », le code et le QR code ; le kiosque, lui,
ne la listait plus et le lien répondait « Not Found » (anglais). Même règle
désormais des deux côtés, bouton « Rouvrir », page d'explication au kiosque.
"""
import uuid
from datetime import date, timedelta

import pytest

from app.utils.dates import utcnow


def _seance(app, *, ouverte_il_y_a=None, sans_date=False):
    from app.extensions import db
    from app.models import AtelierActivite, SessionActivite
    suffixe = uuid.uuid4().hex[:8]
    with app.app_context():
        atelier = AtelierActivite(nom=f"Atelier expire {suffixe}", secteur="Numérique")
        db.session.add(atelier)
        db.session.flush()
        s = SessionActivite(atelier_id=atelier.id, secteur="Numérique", session_type="COLLECTIF",
                            date_session=date.today(), heure_debut="14:00", heure_fin="16:00",
                            kiosk_open=True, kiosk_pin=f"{uuid.uuid4().int % 1000000:06d}",
                            kiosk_token=f"tok{suffixe}",
                            kiosk_opened_at=None if sans_date else utcnow() - (ouverte_il_y_a or timedelta(0)))
        db.session.add(s)
        db.session.commit()
        return {"id": s.id, "token": s.kiosk_token, "pin": s.kiosk_pin, "atelier": atelier.nom}


def _emargement(admin_client, seance):
    return admin_client.get(f"/activite/session/{seance['id']}/emargement").get_data(as_text=True)


def test_reproduction_seance_ouverte_la_veille(app, client, admin_client):
    seance = _seance(app, ouverte_il_y_a=timedelta(hours=13))
    # Kiosque : absente de la liste, lien refusé avec une explication en français.
    assert seance["atelier"] not in client.get("/kiosk/").get_data(as_text=True)
    reponse = client.get(f"/kiosk/session/{seance['token']}")
    assert reponse.status_code == 404
    texte = reponse.get_data(as_text=True)
    assert "Ce pointage n'est plus ouvert" in texte and "Not Found" not in texte
    # Page d'émargement : « expiré », plus d'ancien code ni d'ancien QR code.
    page = _emargement(admin_client, seance)
    assert 'data-kiosk-expire="1"' in page and "Pointage tablette expiré" in page
    assert "Rouvrir le pointage tablette" in page
    assert "Pointage tablette ouvert" not in page
    assert seance["token"] not in page and f'id="kiosk-pin"' not in page


def test_date_d_ouverture_inconnue_vaut_expire(app, client, admin_client):
    seance = _seance(app, sans_date=True)
    assert client.get(f"/kiosk/session/{seance['token']}").status_code == 404
    assert "Pointage tablette expiré" in _emargement(admin_client, seance)


def test_rouvrir_donne_un_nouveau_code_valable(app, client, admin_client):
    seance = _seance(app, ouverte_il_y_a=timedelta(hours=13))
    admin_client.post(f"/activite/session/{seance['id']}/kiosk_open")
    from app.extensions import db
    from app.models import SessionActivite
    with app.app_context():
        s = db.session.get(SessionActivite, seance["id"])
        assert s.kiosk_etat == "ouvert"
        nouveau, pin = s.kiosk_token, s.kiosk_pin
    assert nouveau != seance["token"]
    assert client.get(f"/kiosk/session/{seance['token']}").status_code == 404, "l'ancien QR code reste mort"
    assert client.get(f"/kiosk/session/{nouveau}").status_code == 200
    assert seance["atelier"] in client.get("/kiosk/").get_data(as_text=True)
    page = _emargement(admin_client, seance)
    assert "Pointage tablette ouvert jusqu'à" in page and pin in page and nouveau in page


def test_seance_ouverte_affiche_l_heure_de_fin(app, admin_client):
    seance = _seance(app, ouverte_il_y_a=timedelta(hours=1))
    page = _emargement(admin_client, seance)
    from app.extensions import db
    from app.models import SessionActivite
    with app.app_context():
        fin = db.session.get(SessionActivite, seance["id"]).kiosk_expire_heure
    assert f"Pointage tablette ouvert jusqu'à {fin}" in page and "Pointage tablette expiré" not in page
    assert seance["pin"] in page


@pytest.mark.parametrize("heures,etat", [(0, "ouvert"), (11.9, "ouvert"), (12.1, "expire")])
def test_etat_suit_la_regle_du_kiosque(app, heures, etat):
    from app.models import SessionActivite
    s = SessionActivite(kiosk_open=True, kiosk_opened_at=utcnow() - timedelta(hours=heures))
    assert s.kiosk_etat == etat
    assert SessionActivite(kiosk_open=False).kiosk_etat == "ferme"


def test_adresse_hors_les_murs_explique_la_liste_masquee(app, client, monkeypatch):
    _seance(app)
    monkeypatch.setitem(app.config, "KIOSK_PUBLIC_HOST", "kiosque.test")
    page = client.get("/kiosk/", headers={"Host": "kiosque.test"}).get_data(as_text=True)
    assert "la liste des ateliers n'est pas affichée" in page
    assert "Aucun atelier ouvert pour le moment" not in page
    local = client.get("/kiosk/", headers={"Host": "192.168.1.200:8443"}).get_data(as_text=True)
    assert "la liste des ateliers n'est pas affichée" not in local
