"""Chantiers B1-B5 : parcours, assistant bilan, qualité bilan, matrice droits, stockage."""


def test_parcours_metier_accessible(admin_client):
    r = admin_client.get("/parcours-metier")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Parcours par métier" in body
    assert "Accueil" in body
    assert "Direction" in body


def test_assistant_bilan_financeur_accessible(app, admin_client):
    from app.extensions import db
    from app.models import Subvention

    with app.app_context():
        sub = Subvention(nom="Bilan B", secteur="Familles", annee_exercice=2032, financeur="CAF")
        db.session.add(sub)
        db.session.commit()

    r = admin_client.get("/bilans/assistant-financeur?year=2032")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Assistant bilan financeur" in body
    assert "Bilan B" in body
    assert "Dépenses imputées" in body


def test_qualite_bilans_accessible(admin_client):
    r = admin_client.get("/qualite/bilans")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Qualité des données pour les bilans" in body
    assert "Bloquant pour les bilans" in body


def test_matrice_droits_accessible(admin_client):
    r = admin_client.get("/admin/droits/matrice")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Matrice des droits" in body
    assert "Participants" in body
    assert "admin:rbac" in body


def test_stockage_refuse_prefixe_hors_racine(app, monkeypatch):
    from werkzeug.exceptions import BadRequest
    from app.services import storage

    with app.app_context():
        root = storage.get_upload_root()
        monkeypatch.setattr(storage, "get_upload_root", lambda: root)
        try:
            storage._safe_abs_under_root("..", root.rsplit("/", 1)[-1] + "_evil", "fichier.txt")
        except BadRequest:
            pass
        else:
            raise AssertionError("un chemin au préfixe ressemblant doit être refusé")
