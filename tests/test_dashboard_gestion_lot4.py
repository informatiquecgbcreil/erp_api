"""Gestion Lot 4 — Dashboard direction : jalons + RGPD au suivi, audit accessible, impression."""
import datetime as dt
import uuid

from flask import url_for


def test_jalon_en_retard_dans_suivi(app, admin_client):
    from app.extensions import db
    from app.models import Projet, ProjetJalon

    tag = uuid.uuid4().hex[:6]
    with app.app_context():
        p = Projet(nom=f"ProjJ{tag}", secteur="Numérique")
        db.session.add(p)
        db.session.flush()
        db.session.add(ProjetJalon(projet_id=p.id, libelle=f"Dépôt dossier {tag}",
                                   date_echeance=dt.date(2020, 1, 1), statut="a_faire"))
        db.session.commit()
        with app.test_request_context():
            url = url_for("main.suivi_rappels")

    body = admin_client.get(url).get_data(as_text=True)
    assert f"Dépôt dossier {tag}" in body


def test_jalon_fait_absent_du_suivi(app, admin_client):
    from app.extensions import db
    from app.models import Projet, ProjetJalon

    tag = uuid.uuid4().hex[:6]
    with app.app_context():
        p = Projet(nom=f"ProjF{tag}", secteur="Numérique")
        db.session.add(p)
        db.session.flush()
        db.session.add(ProjetJalon(projet_id=p.id, libelle=f"Fait {tag}",
                                   date_echeance=dt.date(2020, 1, 1), statut="fait"))
        db.session.commit()
        with app.test_request_context():
            url = url_for("main.suivi_rappels")

    body = admin_client.get(url).get_data(as_text=True)
    assert f"Fait {tag}" not in body


def test_historique_subvention(app, admin_client):
    from app.extensions import db
    from app.models import Subvention

    with app.app_context():
        s = Subvention(nom=f"Hist{uuid.uuid4().hex[:6]}", secteur="Numérique", annee_exercice=2026)
        db.session.add(s)
        db.session.commit()
        sid = s.id
        with app.test_request_context():
            url_dossier = url_for("main.subvention_pilotage", subvention_id=sid)
            url_hist = url_for("main.subvention_historique", subvention_id=sid)

    # Un changement de dossier crée une entrée d'audit "subvention.dossier"
    admin_client.post(url_dossier, data={"action": "update_dossier", "financeur": "CAF",
                                         "statut_cycle": "accordee"}, follow_redirects=True)
    r = admin_client.get(url_hist)
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Historique" in body
    assert "subvention.dossier" in body


def test_journal_audit_filtre_cible(app, admin_client):
    from app.extensions import db
    from app.models import Subvention

    tag = uuid.uuid4().hex[:6]
    with app.app_context():
        s = Subvention(nom=f"Cib{tag}", secteur="Numérique", annee_exercice=2026)
        db.session.add(s)
        db.session.commit()
        sid = s.id
        with app.test_request_context():
            url_dossier = url_for("main.subvention_pilotage", subvention_id=sid)

    admin_client.post(url_dossier, data={"action": "update_montants", "montant_demande": "10",
                                         "montant_attribue": "0", "montant_recu": "0"}, follow_redirects=True)
    # Le journal filtré sur la cible ne renvoie que ce dossier
    r = admin_client.get(f"/admin/journal?cible=subvention%23{sid}")
    assert r.status_code == 200
    assert "subvention.montants" in r.get_data(as_text=True)


def test_dashboard_bouton_impression(app, admin_client):
    with app.app_context():
        with app.test_request_context():
            url = url_for("main.dashboard")
    r = admin_client.get(url)
    assert r.status_code == 200
    assert "Imprimer / PDF" in r.get_data(as_text=True)
