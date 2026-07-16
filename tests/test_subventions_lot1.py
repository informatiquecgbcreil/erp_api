"""Lot 1 Projet — Subventions : cycle de vie, échéances, vue financeur, export.

Couvre aussi la correction du bug ``budget_pilotage.html`` qui référençait
``sub.financeur`` / ``sub.annee`` (colonnes inexistantes → AttributeError).
"""
import datetime as dt
import uuid

from flask import url_for


def test_alertes_echeances_logique(app):
    from app.models import Subvention
    from app.main.subventions import alertes_echeances

    today = dt.date(2026, 6, 30)

    # Versement attendu dépassé, pas tout reçu -> danger
    s_retard = Subvention(nom="A", secteur="Numérique", annee_exercice=2026,
                          statut_cycle="accordee", montant_attribue=1000, montant_recu=200,
                          date_versement_prevu=dt.date(2026, 6, 1))
    # Bilan à rendre bientôt -> warning
    s_bilan = Subvention(nom="B", secteur="Numérique", annee_exercice=2026,
                         statut_cycle="versee", montant_attribue=500, montant_recu=500,
                         date_bilan_prevu=dt.date(2026, 7, 10))
    # Soldée -> aucune alerte même avec dates passées
    s_soldee = Subvention(nom="C", secteur="Numérique", annee_exercice=2026,
                          statut_cycle="soldee", montant_attribue=500, montant_recu=0,
                          date_versement_prevu=dt.date(2026, 1, 1))

    alertes = alertes_echeances([s_retard, s_bilan, s_soldee], today=today)
    msgs = {a["sub"].nom: a for a in alertes}
    assert "A" in msgs and msgs["A"]["niveau"] == "danger"
    assert "B" in msgs and msgs["B"]["niveau"] == "warning"
    assert "C" not in msgs
    # tri : danger avant warning
    assert alertes[0]["niveau"] == "danger"


def test_creation_avec_financeur_et_statut(app, admin_client):
    from app.extensions import db
    from app.models import Subvention

    with app.app_context():
        with app.test_request_context():
            url = url_for("main.subvention_create")

    nom = f"Sub{uuid.uuid4().hex[:6]}"
    admin_client.post(url, data={
        "nom": nom, "secteur": "Numérique", "annee_exercice": "2026",
        "financeur": "CAF", "statut_cycle": "accordee",
        "date_versement_prevu": "2026-09-01",
        "montant_demande": "1000", "montant_attribue": "800", "montant_recu": "0",
    }, follow_redirects=True)

    with app.app_context():
        s = Subvention.query.filter_by(nom=nom).first()
        assert s is not None
        assert s.financeur == "CAF"
        assert s.statut_cycle == "accordee"
        assert s.date_versement_prevu == dt.date(2026, 9, 1)


def test_pilotage_page_rendue_sans_bug(app, admin_client):
    """La page pilotage (qui référençait sub.financeur/sub.annee) se rend en 200."""
    from app.extensions import db
    from app.models import Subvention

    with app.app_context():
        s = Subvention(nom=f"P{uuid.uuid4().hex[:6]}", secteur="Numérique",
                       annee_exercice=2026, financeur="Région", statut_cycle="sollicitee")
        db.session.add(s)
        db.session.commit()
        sid = s.id
        with app.test_request_context():
            url = url_for("main.subvention_pilotage", subvention_id=sid)

    r = admin_client.get(url)
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Région" in body
    assert "Dossier" in body  # carte cycle de vie présente


def test_update_dossier_persiste_et_journalise(app, admin_client):
    from app.extensions import db
    from app.models import Subvention, AuditLog

    with app.app_context():
        s = Subvention(nom=f"D{uuid.uuid4().hex[:6]}", secteur="Numérique", annee_exercice=2026)
        db.session.add(s)
        db.session.commit()
        sid = s.id
        with app.test_request_context():
            url = url_for("main.subvention_pilotage", subvention_id=sid)

    admin_client.post(url, data={
        "action": "update_dossier",
        "financeur": "Département", "reference": "CV-2026-42",
        "statut_cycle": "versee",
        "date_depot": "2026-01-15", "date_bilan_prevu": "2026-12-31",
    }, follow_redirects=True)

    with app.app_context():
        s = db.session.get(Subvention, sid)
        assert s.financeur == "Département"
        assert s.reference == "CV-2026-42"
        assert s.statut_cycle == "versee"
        assert s.date_depot == dt.date(2026, 1, 15)
        assert s.date_bilan_prevu == dt.date(2026, 12, 31)
        assert AuditLog.query.filter_by(action="subvention.dossier").count() >= 1


def test_vue_par_financeur(app, admin_client):
    from app.extensions import db
    from app.models import Subvention

    tag = uuid.uuid4().hex[:6]
    with app.app_context():
        db.session.add_all([
            Subvention(nom=f"X1{tag}", secteur="Numérique", annee_exercice=2027,
                       financeur=f"CAF{tag}", montant_attribue=1000, montant_recu=500),
            Subvention(nom=f"X2{tag}", secteur="Numérique", annee_exercice=2027,
                       financeur=f"CAF{tag}", montant_attribue=2000, montant_recu=2000),
        ])
        db.session.commit()
        with app.test_request_context():
            url = url_for("main.subventions_par_financeur", annee=2027)

    r = admin_client.get(url)
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert f"CAF{tag}" in body
    assert "3000.00€" in body  # total attribué du groupe (1000 + 2000)


def test_export_xlsx(app, admin_client):
    from app.extensions import db
    from app.models import Subvention

    with app.app_context():
        s = Subvention(nom=f"E{uuid.uuid4().hex[:6]}", secteur="Numérique",
                       annee_exercice=2028, financeur="Ville", montant_attribue=300)
        db.session.add(s)
        db.session.commit()
        with app.test_request_context():
            url = url_for("main.export_subventions_xlsx", annee=2028)

    r = admin_client.get(url)
    assert r.status_code == 200
    assert "spreadsheetml" in r.headers.get("Content-Type", "")
    assert r.headers.get("Content-Disposition", "").endswith(".xlsx")
    assert len(r.data) > 0
