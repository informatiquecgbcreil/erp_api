"""Pilotage direction, support comité/CA et journal métier."""


def test_pilotage_direction_accessible(admin_client):
    r = admin_client.get("/direction/pilotage")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Tableau de bord direction" in body
    assert "Participants uniques" in body
    assert "Vue comité / CA" in body


def test_comite_pilotage_imprimable(admin_client):
    r = admin_client.get("/direction/comite-pilotage")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Support comité de pilotage / CA" in body
    assert "sans exposer de données nominatives" in body


def test_journal_metier_accessible_et_filtrable(app, admin_client):
    from app.extensions import db
    from app.models import AuditLog

    with app.app_context():
        db.session.add(AuditLog(action="pilotage.test", cible="direction", details="test"))
        db.session.commit()

    r = admin_client.get("/journal-metier?action=pilotage.test")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Journal métier transversal" in body
    assert "pilotage.test" in body
    assert "direction" in body
