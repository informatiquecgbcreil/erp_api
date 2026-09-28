"""Recherche par nom et détection de doublons (audit 5.6, 5.9, mineurs kiosque).

- Le kiosque et l'équipe partagent le même filtre : accents, apostrophes
  (droite ou typographique), ligatures, mots dans le désordre.
- La détection de doublons attrape N'Diaye/Ndiaye, nom et prénom inversés,
  Lecœur/Lecoeur, les particules et une faute sur l'initiale, sans
  rapprocher Léa et Léo.
- L'émargement par l'équipe propose les fiches proches avant de créer.
"""
import uuid
from datetime import date

import pytest

from app.utils.dates import utcnow


@pytest.fixture
def seance(app):
    from app.extensions import db
    from app.models import AtelierActivite, SessionActivite
    cle = uuid.uuid4().hex[:8]
    with app.app_context():
        atelier = AtelierActivite(nom=f"Atelier {cle}", secteur="Numérique")
        db.session.add(atelier)
        db.session.flush()
        s = SessionActivite(atelier_id=atelier.id, secteur="Numérique", session_type="COLLECTIF",
                            date_session=date.today(), kiosk_open=True, kiosk_opened_at=utcnow(),
                            kiosk_pin=str(100000 + int(cle[:4], 16) % 800000), kiosk_token=f"tok{cle}")
        db.session.add(s)
        db.session.commit()
        return {"token": s.kiosk_token, "sid": s.id, "pin": s.kiosk_pin, "cle": cle}


def _fiche(app, nom, prenom, **extra):
    from app.extensions import db
    from app.models import Participant
    with app.app_context():
        p = Participant(nom=nom, prenom=prenom, created_secteur=extra.pop("secteur", "Numérique"), **extra)
        db.session.add(p)
        db.session.commit()
        return p.id


# ---------------------------------------------------------------------------
# Recherche par nom : kiosque et équipe
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("enregistre, tape", [
    ("N'Diaye{c}", "ndiaye{c}"),
    ("N’Diaye{c}", "n'diaye{c}"),
    ("Ndiaye{c}", "n’diaye{c}"),
    ("Lecœur{c}", "lecoeur{c}"),
    ("Lecoeur{c}", "lecœur{c}"),
    ("Élodie{c}", "ELODIE{c}"),
])
def test_kiosque_trouve_malgre_apostrophes_et_ligatures(app, seance, enregistre, tape):
    nom = enregistre.format(c=seance["cle"])
    _fiche(app, nom, "Awa")
    r = app.test_client().get(f"/kiosk/session/{seance['token']}/search",
                              query_string={"q": tape.format(c=seance["cle"])})
    assert any(nom in res["label"] for res in r.get_json()["results"])


def test_recherche_de_l_equipe_prenom_nom_dans_le_desordre(app, admin_client, seance):
    nom = f"Michut{seance['cle']}"
    pid = _fiche(app, nom, "Céline")
    for q in (f"céline {nom}", f"{nom.lower()} celine", f"CELINE {nom.upper()}"):
        items = admin_client.get("/participants/search", query_string={"q": q}).get_json()["items"]
        assert pid in [i["id"] for i in items], q
    page = admin_client.get("/participants/", query_string={"q": f"{nom} celine"}).get_data(as_text=True)
    assert nom in page


def test_recherche_de_l_equipe_apostrophe_typographique(app, admin_client, seance):
    pid = _fiche(app, f"N'Guessan{seance['cle']}", "Koffi")
    items = admin_client.get("/participants/search",
                             query_string={"q": f"n’guessan{seance['cle']}"}).get_json()["items"]
    assert pid in [i["id"] for i in items]


# ---------------------------------------------------------------------------
# Détection de doublons
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("existant, saisi", [
    (("N'Diaye{c}", "Awa"), ("Ndiaye{c}", "Awa")),
    (("Ndiaye{c}", "Awa"), ("N’Diaye{c}", "Awa")),
    (("Diallo{c}", "Aminata"), ("Aminata", "Diallo{c}")),          # inversés
    (("Lecœur{c}", "Jeanne"), ("Lecoeur{c}", "Jeanne")),
    (("De La Fontaine{c}", "Jean"), ("Fontaine{c}", "Jean")),       # particules
    (("Silva{c}", "Maria"), ("Da Silva{c}", "Maria")),
    (("Michut{c}", "Céline"), ("Nichut{c}", "Céline")),             # faute sur l'initiale
    (("Moreau{c}", "Étienne"), ("Moreau{c}", "etienne")),
])
def test_doublons_cas_courants(app, seance, existant, saisi):
    from app.services.doublons import candidats_doublons
    c = seance["cle"]
    pid = _fiche(app, existant[0].format(c=c), existant[1].format(c=c))
    with app.app_context():
        trouves = [p.id for p in candidats_doublons(saisi[0].format(c=c), saisi[1].format(c=c))]
    assert pid in trouves


def test_doublons_ne_rapprochent_pas_des_personnes_differentes(app, seance):
    from app.services.doublons import candidats_doublons
    c = seance["cle"]
    _fiche(app, f"Martin{c}", "Léa")
    _fiche(app, f"Durand{c}", "Paul")
    with app.app_context():
        assert candidats_doublons(f"Martin{c}", "Léo") == []
        assert candidats_doublons(f"Dupont{c}", "Paul") == []


def test_doublons_ignorent_les_fiches_anonymisees(app, seance):
    from app.services.doublons import candidats_doublons
    _fiche(app, f"ANONYME-{seance['cle']}", "Awa")
    with app.app_context():
        assert candidats_doublons(f"ANONYME-{seance['cle']}", "Awa") == []


# ---------------------------------------------------------------------------
# Émargement par l'équipe : contrôle de doublons avant création
# ---------------------------------------------------------------------------

def test_emargement_propose_les_fiches_proches_avant_de_creer(app, admin_client, seance):
    from app.models import Participant
    nom = f"N'Diaye{seance['cle']}"
    pid = _fiche(app, nom, "Awa")
    url = f"/activite/session/{seance['sid']}/emargement"
    r = admin_client.post(url, data={"action": "add_participant", "nom": f"Ndiaye{seance['cle']}",
                                     "prenom": "awa", "ville": "Creil"}, follow_redirects=True)
    page = r.get_data(as_text=True)
    assert "Fiches proches" in page and "Créer quand même" in page
    assert f'name="participant_id" value="{pid}"' in page
    with app.app_context():
        assert Participant.query.filter_by(nom=f"Ndiaye{seance['cle']}").count() == 0
    # « Créer quand même » crée bien la fiche, dans le secteur de la séance.
    admin_client.post(url, data={"action": "add_participant", "nom": f"Ndiaye{seance['cle']}",
                                 "prenom": "awa", "force_creation": "1"})
    with app.app_context():
        creee = Participant.query.filter_by(nom=f"Ndiaye{seance['cle']}").one()
        assert creee.created_secteur == "Numérique"


def test_emargement_sans_ressemblance_cree_directement(app, admin_client, seance):
    from app.models import Participant
    nom = f"Zyx{seance['cle']}"
    admin_client.post(f"/activite/session/{seance['sid']}/emargement",
                      data={"action": "add_participant", "nom": nom, "prenom": "Unique"})
    with app.app_context():
        assert Participant.query.filter_by(nom=nom).count() == 1


# ---------------------------------------------------------------------------
# Kiosque : freins anti-abus et questionnaire
# ---------------------------------------------------------------------------

def test_adresse_du_visiteur_funnel_conservee_derriere_caddy(app):
    from app.kiosk.routes import _adresse_client
    with app.test_request_context("/kiosk/", environ_base={"REMOTE_ADDR": "127.0.0.1"},
                                  headers={"Tailscale-Funnel-Request": "?1",
                                           "X-Forwarded-For": "203.0.113.9, 127.0.0.1"}):
        assert _adresse_client() == "funnel:203.0.113.9"


def test_caddyfile_garde_l_adresse_transmise_par_tailscaled(tmp_path):
    from desktop.runtime import write_caddy
    texte = write_caddy({"hostname": "serveur", "https_port": 8443, "kiosk_http_port": 8080,
                         "web_port": 8000, "lan_ip": "192.168.1.20"}, tmp_path).read_text(encoding="utf-8")
    assert "servers :8080 {\n  trusted_proxies static 127.0.0.1/32 ::1/128\n }" in texte


def test_plafond_global_des_codes_pin_pour_internet_seulement(app, seance):
    """Le plafond commun freine un attaquant qui change d'adresse sur Internet,
    sans jamais bloquer les tablettes du réseau local."""
    from app.kiosk import routes as kiosque
    kiosque._ECHECS_PIN.reinitialiser()
    kiosque._ECHECS_PIN_TOTAL.reinitialiser()
    funnel = {"Tailscale-Funnel-Request": "?1"}
    try:
        for i in range(kiosque._ECHECS_PIN_TOTAL.maximum):
            app.test_client().post("/kiosk/", data={"pin": "000000"}, headers={
                **funnel, "X-Forwarded-For": f"2001:db8::{i + 1:x}, 127.0.0.1"},
                environ_base={"REMOTE_ADDR": "127.0.0.1"})
        bloque = app.test_client().post("/kiosk/", data={"pin": seance["pin"]}, headers={
            **funnel, "X-Forwarded-For": "2001:db8::ffff, 127.0.0.1"},
            environ_base={"REMOTE_ADDR": "127.0.0.1"})
        assert "/kiosk/session/" not in bloque.headers.get("Location", "")
        local = app.test_client().post("/kiosk/", data={"pin": seance["pin"]},
                                       environ_base={"REMOTE_ADDR": "192.168.1.40"})
        assert f"/kiosk/session/{seance['token']}" in local.headers.get("Location", "")
    finally:
        kiosque._ECHECS_PIN.reinitialiser()
        kiosque._ECHECS_PIN_TOTAL.reinitialiser()


def test_page_429_du_kiosque_en_francais(app, seance):
    from app.kiosk import routes as kiosque
    kiosque._CREATIONS.reinitialiser()
    client = app.test_client()
    try:
        for i in range(kiosque._CREATIONS.maximum):
            kiosque._CREATIONS.noter(f"127.0.0.1|{seance['sid']}")
        r = client.post(f"/kiosk/session/{seance['token']}", data={
            "action": "add_participant", "nom": "Trop", "prenom": "Vite"},
            environ_base={"REMOTE_ADDR": "127.0.0.1"})
        assert r.status_code == 429
        page = r.get_data(as_text=True)
        assert "Trop de demandes" in page and "Too Many Requests" not in page
        assert f"/kiosk/session/{seance['token']}" in page
    finally:
        kiosque._CREATIONS.reinitialiser()


@pytest.fixture
def questionnaire(app, seance):
    from app.extensions import db
    from app.models import Question, Questionnaire
    with app.app_context():
        q = Questionnaire(nom=f"Avis {seance['cle']}", type_questionnaire="satisfaction", is_active=True)
        db.session.add(q)
        db.session.flush()
        db.session.add(Question(questionnaire_id=q.id, label="Note", kind="scale", position=1))
        db.session.commit()
        return q.id


def _nb_reponses(app, qid, sid):
    from app.models import QuestionnaireResponseGroup
    with app.app_context():
        return QuestionnaireResponseGroup.query.filter_by(questionnaire_id=qid, session_id=sid).count()


def test_questionnaire_une_reponse_par_personne_nommee(app, seance, questionnaire):
    from app.extensions import db
    from app.models import PresenceActivite
    pid = _fiche(app, f"Avis{seance['cle']}", "Nommee")
    with app.app_context():
        db.session.add(PresenceActivite(session_id=seance["sid"], participant_id=pid))
        db.session.commit()
    client = app.test_client()
    url = f"/kiosk/session/{seance['token']}/feedback"
    for _ in range(3):
        client.post(url, data={"questionnaire_id": questionnaire, "participant_id": pid, "question_1": "4"},
                    environ_base={"REMOTE_ADDR": "192.168.1.41"})
    assert _nb_reponses(app, questionnaire, seance["sid"]) == 1


def test_questionnaire_anonyme_plafonne_a_la_mesure_de_la_seance(app, seance, questionnaire):
    from app.kiosk import routes as kiosque
    url = f"/kiosk/session/{seance['token']}/feedback"
    for i in range(kiosque._AVIS_PLANCHER + 5):
        app.test_client().post(url, data={"questionnaire_id": questionnaire},
                               environ_base={"REMOTE_ADDR": f"192.168.2.{i + 1}"})
    assert _nb_reponses(app, questionnaire, seance["sid"]) == kiosque._AVIS_PLANCHER
