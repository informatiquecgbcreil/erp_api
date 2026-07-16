"""Gestion Lot 1 — Trésorerie prévisionnelle & alertes consolidées direction."""
import datetime as dt
import uuid

from flask import url_for


def test_plan_tresorerie_par_mois(app):
    from app.models import Subvention
    from app.main.tresorerie import plan_tresorerie

    today = dt.date(2026, 6, 30)
    subs = [
        # attendu en septembre : reste 800
        Subvention(nom="A", secteur="Numérique", annee_exercice=2026, financeur="CAF",
                   statut_cycle="accordee", montant_attribue=1000, montant_recu=200,
                   date_versement_prevu=dt.date(2026, 9, 1)),
        # même mois, autre financeur : reste 500
        Subvention(nom="B", secteur="Familles", annee_exercice=2026, financeur="Région",
                   statut_cycle="accordee", montant_attribue=500, montant_recu=0,
                   date_versement_prevu=dt.date(2026, 9, 20)),
        # en retard (mars, non reçu)
        Subvention(nom="C", secteur="Numérique", annee_exercice=2026, financeur="Ville",
                   statut_cycle="accordee", montant_attribue=300, montant_recu=0,
                   date_versement_prevu=dt.date(2026, 3, 1)),
        # soldée -> ignorée
        Subvention(nom="D", secteur="Numérique", annee_exercice=2026, financeur="État",
                   statut_cycle="soldee", montant_attribue=999, montant_recu=0,
                   date_versement_prevu=dt.date(2026, 9, 1)),
        # sans date -> bucket "sans_date"
        Subvention(nom="E", secteur="Numérique", annee_exercice=2026, financeur="Fondation",
                   statut_cycle="accordee", montant_attribue=400, montant_recu=100),
    ]
    plan = plan_tresorerie(subs, today=today)
    sept = next(b for b in plan["lignes"] if (b["annee"], b["mois"]) == (2026, 9))
    assert sept["total"] == 1300.0            # 800 + 500
    assert sept["financeurs"]["CAF"] == 800.0
    assert sept["en_retard"] is False
    mars = next(b for b in plan["lignes"] if (b["annee"], b["mois"]) == (2026, 3))
    assert mars["en_retard"] is True
    assert plan["total_retard"] == 300.0
    assert len(plan["sans_date"]) == 1        # E
    assert plan["total_attendu"] == 1300.0 + 300.0 + 300.0  # sept + mars + E(300 reste)


def test_taux_par_financeur(app):
    from app.models import Subvention
    from app.main.tresorerie import taux_par_financeur

    subs = [
        Subvention(nom="A", secteur="X", annee_exercice=2026, financeur="CAF",
                   statut_cycle="versee", montant_attribue=1000, montant_recu=1000),
        Subvention(nom="B", secteur="Y", annee_exercice=2026, financeur="CAF",
                   statut_cycle="accordee", montant_attribue=1000, montant_recu=0),
        Subvention(nom="R", secteur="X", annee_exercice=2026, financeur="Refusé SA",
                   statut_cycle="refusee", montant_attribue=500, montant_recu=0),
    ]
    rows = taux_par_financeur(subs)
    caf = next(g for g in rows if g["financeur"] == "CAF")
    assert caf["attribue"] == 2000.0 and caf["recu"] == 1000.0
    assert caf["taux"] == 50.0
    # les dossiers refusés sont exclus
    assert all(g["financeur"] != "Refusé SA" for g in rows)


def test_page_tresorerie_rendue(app, admin_client):
    from app.extensions import db
    from app.models import Subvention

    with app.app_context():
        s = Subvention(nom=f"Tr{uuid.uuid4().hex[:6]}", secteur="Numérique", annee_exercice=2029,
                       financeur="CAF", statut_cycle="accordee",
                       montant_attribue=1000, montant_recu=0, date_versement_prevu=dt.date(2029, 10, 1))
        db.session.add(s)
        db.session.commit()
        with app.test_request_context():
            url = url_for("main.tresorerie", annee=2029)

    r = admin_client.get(url)
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Trésorerie prévisionnelle" in body
    assert "octobre 2029" in body
    assert "Réalisation par financeur" in body


def test_export_tresorerie_xlsx(app, admin_client):
    from app.extensions import db
    from app.models import Subvention

    with app.app_context():
        s = Subvention(nom=f"Tx{uuid.uuid4().hex[:6]}", secteur="Numérique", annee_exercice=2031,
                       financeur="Région", statut_cycle="accordee",
                       montant_attribue=500, montant_recu=0, date_versement_prevu=dt.date(2031, 5, 1))
        db.session.add(s)
        db.session.commit()
        with app.test_request_context():
            url = url_for("main.tresorerie_export_xlsx", annee=2031)

    r = admin_client.get(url)
    assert r.status_code == 200
    assert "spreadsheetml" in r.headers.get("Content-Type", "")
    assert len(r.data) > 0
