"""Outils Ressources : dons & reçus fiscaux (CERFA 11580) + coût unitaire."""
import datetime as dt
import uuid

from flask import url_for


# ---------- Montant en lettres ----------

def test_montant_en_lettres():
    from app.services.dons import montant_en_lettres
    assert montant_en_lettres(0) == "zéro euro"
    assert montant_en_lettres(1) == "un euro"
    assert montant_en_lettres(21) == "vingt et un euros"
    assert montant_en_lettres(71) == "soixante et onze euros"
    assert montant_en_lettres(80) == "quatre-vingts euros"
    assert montant_en_lettres(95) == "quatre-vingt-quinze euros"
    assert montant_en_lettres(100) == "cent euros"
    assert montant_en_lettres(200) == "deux cents euros"
    assert montant_en_lettres(1234.56) == "mille deux cent trente-quatre euros et cinquante-six centimes"
    assert montant_en_lettres(1000000) == "un million euros"
    assert montant_en_lettres(150.05) == "cent cinquante euros et cinq centimes"


# ---------- Dons / registre ----------

def test_don_creation_numerotation_et_recu(app, admin_client):
    from app.extensions import db
    from app.models import Don

    nom = f"Donateur{uuid.uuid4().hex[:6]}"
    with app.app_context():
        with app.test_request_context():
            url = url_for("main.don_create")

    admin_client.post(url, data={
        "donateur_nom": nom, "donateur_prenom": "Alice", "donateur_civilite": "Madame",
        "montant": "150,50", "date_don": "2044-03-10", "type_donateur": "particulier",
        "forme_don": "numeraire", "mode_versement": "cheque",
        "organisme_nom": "Centre Test", "organisme_adresse": "2 rue Exemple, 60100 Creil",
    }, follow_redirects=True)

    with app.app_context():
        don = Don.query.filter_by(donateur_nom=nom).first()
        assert don is not None
        assert don.annee == 2044
        assert don.numero.startswith("2044-")
        assert don.montant == 150.5
        did = don.id
        with app.test_request_context():
            url_recu = url_for("main.don_recu", don_id=did)

    body = admin_client.get(url_recu).get_data(as_text=True)
    assert "REÇU AU TITRE DES DONS" in body
    assert don.numero in body
    assert "cent cinquante euros et cinquante centimes" in body   # montant en lettres
    assert "article 200" in body                                   # particulier
    assert "Centre Test" in body


def test_don_numeros_sequentiels_meme_annee(app, admin_client):
    from app.models import Don

    with app.app_context():
        with app.test_request_context():
            url = url_for("main.don_create")
    tag = uuid.uuid4().hex[:5]
    for i in range(2):
        admin_client.post(url, data={"donateur_nom": f"Seq{tag}{i}", "montant": "10",
                                     "date_don": "2045-01-01"}, follow_redirects=True)
    with app.app_context():
        nums = sorted(d.numero for d in Don.query.filter(Don.donateur_nom.like(f"Seq{tag}%")).all())
        n1, n2 = int(nums[0].split("-")[1]), int(nums[1].split("-")[1])
        assert n2 == n1 + 1


def test_don_annulation_conserve_numero(app, admin_client):
    from app.extensions import db
    from app.models import Don, AuditLog

    with app.app_context():
        with app.test_request_context():
            url = url_for("main.don_create")
    nom = f"Ann{uuid.uuid4().hex[:6]}"
    admin_client.post(url, data={"donateur_nom": nom, "montant": "99", "date_don": "2046-05-05"},
                      follow_redirects=True)
    with app.app_context():
        don = Don.query.filter_by(donateur_nom=nom).first()
        did, numero = don.id, don.numero
        with app.test_request_context():
            url_ann = url_for("main.don_annuler", don_id=did)

    admin_client.post(url_ann, data={"motif": "erreur de saisie"}, follow_redirects=True)
    with app.app_context():
        don = db.session.get(Don, did)
        assert don.est_annule is True
        assert don.numero == numero                       # le numéro reste au registre
        assert AuditLog.query.filter_by(action="don.annule").count() >= 1


def test_registre_et_export(app, admin_client):
    with app.app_context():
        with app.test_request_context():
            url_reg = url_for("main.dons_registre", annee=2044)
            url_x = url_for("main.dons_export_xlsx", annee=2044)
    body = admin_client.get(url_reg).get_data(as_text=True)
    assert "Dons &amp; reçus fiscaux" in body or "Dons & reçus fiscaux" in body
    r = admin_client.get(url_x)
    assert r.status_code == 200
    assert "spreadsheetml" in r.headers.get("Content-Type", "")


# ---------- Coût unitaire ----------

def test_mesures_et_couts(app):
    from app.extensions import db
    from app.models import AtelierActivite, SessionActivite, PresenceActivite, Participant
    from app.main.couts import mesures_activite, couts_unitaires

    with app.app_context():
        at = AtelierActivite(nom=f"Cout{uuid.uuid4().hex[:6]}", secteur="Numérique", type_atelier="COLLECTIF")
        db.session.add(at)
        db.session.flush()
        s1 = SessionActivite(atelier_id=at.id, secteur="Numérique", session_type="COLLECTIF",
                             date_session=dt.date(2013, 3, 1), heure_debut="14:00", heure_fin="16:00")
        s2 = SessionActivite(atelier_id=at.id, secteur="Numérique", session_type="COLLECTIF",
                             date_session=dt.date(2013, 3, 8), heure_debut="14:00", heure_fin="15:30")
        db.session.add_all([s1, s2])
        db.session.flush()
        p1 = Participant(nom=f"C1{uuid.uuid4().hex[:4]}", prenom="T")
        p2 = Participant(nom=f"C2{uuid.uuid4().hex[:4]}", prenom="T")
        db.session.add_all([p1, p2])
        db.session.flush()
        db.session.add_all([
            PresenceActivite(session_id=s1.id, participant_id=p1.id),
            PresenceActivite(session_id=s1.id, participant_id=p2.id),
            PresenceActivite(session_id=s2.id, participant_id=p1.id),
        ])
        db.session.commit()

        m = mesures_activite([at.id], dt.date(2013, 1, 1), dt.date(2013, 12, 31))
        assert m["sessions"] == 2
        assert m["heures"] == 3.5          # 2h + 1h30
        assert m["uniques"] == 2
        assert m["presences"] == 3

        c = couts_unitaires(700.0, m)
        assert c["par_participant"] == 350.0
        assert c["par_presence"] == 233.33
        assert c["par_heure"] == 200.0
        assert c["par_seance"] == 350.0

        # division par zéro -> None
        c0 = couts_unitaires(700.0, {"sessions": 0, "heures": 0, "uniques": 0, "presences": 0})
        assert c0["par_participant"] is None


def test_page_cout_unitaire(app, admin_client):
    with app.app_context():
        with app.test_request_context():
            url = url_for("main.cout_unitaire", montant="1000", **{"from": "2013-01-01", "to": "2013-12-31"})
    body = admin_client.get(url).get_data(as_text=True)
    assert "Coût unitaire" in body
    assert "Par participant" in body

    with app.app_context():
        with app.test_request_context():
            url_x = url_for("main.cout_unitaire_export", montant="1000")
    r = admin_client.get(url_x)
    assert r.status_code == 200
    assert "spreadsheetml" in r.headers.get("Content-Type", "")
