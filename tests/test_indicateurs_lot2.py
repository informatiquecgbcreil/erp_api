"""Lot 2 Projet — Indicateurs : historique des valeurs, alertes, jauges, export."""
import json
import uuid

import pytest
from flask import url_for


def test_gauge_pct():
    from app.services.indicators import indicator_gauge_pct

    # ge : value/target, plafonné à 100
    assert indicator_gauge_pct({"value_type": "number", "value": 40, "target": 80, "target_op": "ge"}) == 50.0
    assert indicator_gauge_pct({"value_type": "number", "value": 100, "target": 80, "target_op": "ge"}) == 100.0
    # le (ne pas dépasser) : target/value
    assert indicator_gauge_pct({"value_type": "number", "value": 200, "target": 100, "target_op": "le"}) == 50.0
    # coche
    assert indicator_gauge_pct({"value_type": "check", "value": 1}) == 100.0
    assert indicator_gauge_pct({"value_type": "check", "value": 0}) == 0.0
    # pas d'objectif -> None
    assert indicator_gauge_pct({"value_type": "number", "value": 10, "target": None}) is None


def test_alertes_indicateurs_logique():
    from app.services.indicators import compute_project_indicator_alerts

    rows = [
        {"label": "Non renseigné", "value_type": "number", "source": "manual", "value": None, "status": None},
        {"label": "Sous cible", "value_type": "number", "source": "manual", "value": 10,
         "target": 100, "target_symbol": "≥", "status": "bad"},
        {"label": "Presque", "value_type": "number", "source": "stats", "value": 80,
         "target": 100, "target_symbol": "≥", "status": "warn"},
        {"label": "Atteint", "value_type": "number", "source": "stats", "value": 120,
         "target": 100, "status": "ok"},
        {"label": "Jalon", "value_type": "check", "value": 0, "status": "bad"},
    ]
    alertes = compute_project_indicator_alerts(rows)
    labels = {a["label"]: a for a in alertes}
    assert "Atteint" not in labels
    assert labels["Sous cible"]["niveau"] == "danger"
    assert labels["Presque"]["niveau"] == "warning"
    assert labels["Non renseigné"]["niveau"] == "warning"
    assert "Jalon" in labels
    # danger trié en premier
    assert alertes[0]["niveau"] == "danger"


@pytest.fixture()
def projet_indic(app):
    """Projet avec un indicateur manuel chiffré."""
    with app.app_context():
        from app.extensions import db
        from app.models import Projet, ProjetIndicateur
        suf = uuid.uuid4().hex[:6]
        p = Projet(nom=f"Proj {suf}", secteur="Numérique")
        db.session.add(p)
        db.session.flush()
        ind = ProjetIndicateur(
            projet_id=p.id, code=f"manual_number_{suf}", label=f"Taux {suf}", is_active=True,
            params_json=json.dumps({"source": "manual", "value_type": "number",
                                    "manual_value": 10, "target": 100, "target_op": "ge", "unit": "%"}),
        )
        db.session.add(ind)
        db.session.commit()
        return {"projet_id": p.id, "indic_id": ind.id, "suf": suf}


def test_update_valeur_historise(app, admin_client, projet_indic):
    from app.extensions import db
    from app.models import ProjetIndicateurValeur

    pid, iid = projet_indic["projet_id"], projet_indic["indic_id"]
    with app.app_context():
        with app.test_request_context():
            url = url_for("projets.projet_indicateurs", projet_id=pid)

    admin_client.post(url, data={
        "action": "update_indicateur", "indicateur_id": iid,
        "label": f"Taux {projet_indic['suf']}", "manual_value": "42", "unit": "%",
        "target_op": "ge", "target": "100", "is_active": "1",
        "commentaire": "Relevé juin",
    }, follow_redirects=True)

    with app.app_context():
        hist = ProjetIndicateurValeur.query.filter_by(indicateur_id=iid).all()
        assert len(hist) == 1
        assert hist[0].valeur == 42.0
        assert hist[0].commentaire == "Relevé juin"


def test_update_valeur_inchangee_pas_historisee(app, admin_client, projet_indic):
    from app.extensions import db
    from app.models import ProjetIndicateurValeur

    pid, iid = projet_indic["projet_id"], projet_indic["indic_id"]
    with app.app_context():
        with app.test_request_context():
            url = url_for("projets.projet_indicateurs", projet_id=pid)

    # On renvoie la même valeur (10) -> aucun nouvel enregistrement d'historique
    admin_client.post(url, data={
        "action": "update_indicateur", "indicateur_id": iid,
        "label": f"Taux {projet_indic['suf']}", "manual_value": "10", "unit": "%",
        "target_op": "ge", "target": "100", "is_active": "1",
    }, follow_redirects=True)

    with app.app_context():
        assert ProjetIndicateurValeur.query.filter_by(indicateur_id=iid).count() == 0


def test_page_indicateurs_restitution(app, admin_client, projet_indic):
    pid = projet_indic["projet_id"]
    with app.app_context():
        with app.test_request_context():
            url = url_for("projets.projet_indicateurs", projet_id=pid)
    r = admin_client.get(url)
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Restitution visuelle" in body
    assert "À traiter" in body          # l'indicateur à 10/100 est sous cible
    assert "gauge-fill" in body


def test_export_indicateurs_xlsx(app, admin_client, projet_indic):
    pid = projet_indic["projet_id"]
    with app.app_context():
        with app.test_request_context():
            url = url_for("projets.projet_indicateurs_export", projet_id=pid)
    r = admin_client.get(url)
    assert r.status_code == 200
    assert "spreadsheetml" in r.headers.get("Content-Type", "")
    assert len(r.data) > 0
