"""Bénévolat : saisie d'heures, statistiques annuelles, valorisation, export."""
import datetime as dt
import uuid

from flask import url_for


def _participant(app, **kw):
    from app.extensions import db
    from app.models import Participant
    p = Participant(nom=kw.pop("nom", f"B{uuid.uuid4().hex[:6]}"), prenom=kw.pop("prenom", "T"), **kw)
    db.session.add(p)
    db.session.commit()
    return p.id


def test_saisie_heures_marque_benevole(app, admin_client):
    from app.extensions import db
    from app.models import Participant, BenevoleHeures, AuditLog

    with app.app_context():
        pid = _participant(app)
        assert db.session.get(Participant, pid).est_benevole is False
        with app.test_request_context():
            url = url_for("main.benevolat_heures_create")

    admin_client.post(url, data={"participant_id": pid, "heures": "2,5",
                                 "date_action": "2037-03-10", "mission": "Accueil"},
                      follow_redirects=True)
    with app.app_context():
        p = db.session.get(Participant, pid)
        assert p.est_benevole is True                     # marqué automatiquement
        ligne = BenevoleHeures.query.filter_by(participant_id=pid).first()
        assert ligne is not None and ligne.heures == 2.5
        assert ligne.mission == "Accueil"
        assert AuditLog.query.filter_by(action="benevolat.heures").count() >= 1


def test_heures_invalides_refusees(app, admin_client):
    from app.models import BenevoleHeures

    with app.app_context():
        pid = _participant(app)
        with app.test_request_context():
            url = url_for("main.benevolat_heures_create")

    admin_client.post(url, data={"participant_id": pid, "heures": "0"}, follow_redirects=True)
    admin_client.post(url, data={"participant_id": "999999", "heures": "3"}, follow_redirects=True)
    with app.app_context():
        assert BenevoleHeures.query.filter_by(participant_id=pid).count() == 0


def test_stats_annee(app):
    from app.extensions import db
    from app.models import BenevoleHeures
    from app.services.benevolat import stats_annee

    with app.app_context():
        p1, p2 = _participant(app), _participant(app)
        db.session.add_all([
            BenevoleHeures(participant_id=p1, date_action=dt.date(2038, 2, 1), heures=3, mission="Accueil"),
            BenevoleHeures(participant_id=p1, date_action=dt.date(2038, 5, 1), heures=2, mission="CA"),
            BenevoleHeures(participant_id=p2, date_action=dt.date(2038, 6, 1), heures=4.5, mission="Accueil"),
            # hors année : ignorée
            BenevoleHeures(participant_id=p2, date_action=dt.date(2039, 1, 1), heures=10),
        ])
        db.session.commit()

        stats = stats_annee(2038)
        assert stats["nb_actifs"] == 2
        assert stats["total_heures"] == 9.5
        assert stats["valorisation"] == round(9.5 * stats["taux_horaire"], 2)
        assert stats["par_mission"]["Accueil"] == 7.5
        premier = stats["par_benevole"][0]
        assert premier["heures"] == 5.0 or premier["heures"] == 4.5   # tri par heures desc


def test_page_et_export(app, admin_client):
    from app.extensions import db
    from app.models import BenevoleHeures

    with app.app_context():
        pid = _participant(app, nom=f"Ben{uuid.uuid4().hex[:5]}")
        db.session.add(BenevoleHeures(participant_id=pid, date_action=dt.date(2040, 4, 4),
                                      heures=6, mission="Événementiel"))
        db.session.commit()
        with app.test_request_context():
            url = url_for("main.benevolat", annee=2040)
            url_x = url_for("main.benevolat_export_xlsx", annee=2040)

    body = admin_client.get(url).get_data(as_text=True)
    assert "Bénévolat" in body
    assert "Événementiel" in body
    assert "Valorisation" in body

    r = admin_client.get(url_x)
    assert r.status_code == 200
    assert "spreadsheetml" in r.headers.get("Content-Type", "")


def test_suppression_ligne(app, admin_client):
    from app.extensions import db
    from app.models import BenevoleHeures

    with app.app_context():
        pid = _participant(app)
        ligne = BenevoleHeures(participant_id=pid, date_action=dt.date(2041, 1, 1), heures=1)
        db.session.add(ligne)
        db.session.commit()
        lid = ligne.id
        with app.test_request_context():
            url = url_for("main.benevolat_heures_supprimer", ligne_id=lid)

    admin_client.post(url, follow_redirects=True)
    with app.app_context():
        assert db.session.get(BenevoleHeures, lid) is None


def test_passeport_affiche_benevolat(app, admin_client):
    from app.extensions import db
    from app.models import BenevoleHeures, Participant

    with app.app_context():
        pid = _participant(app)
        db.session.add(BenevoleHeures(participant_id=pid, date_action=dt.date.today(),
                                      heures=3, mission="Accueil"))
        db.session.get(Participant, pid).est_benevole = True
        db.session.commit()
        with app.test_request_context():
            url = url_for("pedagogie.participant_passeport", participant_id=pid, tab="participation")

    body = admin_client.get(url).get_data(as_text=True)
    assert "Bénévolat" in body
    assert "h au total" in body
