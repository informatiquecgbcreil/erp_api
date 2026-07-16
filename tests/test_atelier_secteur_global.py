"""Un profil à portée globale (ex. direction sans secteur assigné) doit pouvoir
créer un atelier en choisissant le secteur dans le formulaire."""
import uuid

from flask import url_for


def test_formulaire_propose_le_choix_du_secteur(app, admin_client):
    """Le compte de test (direction, sans secteur assigné) voit le sélecteur."""
    with app.app_context():
        with app.test_request_context():
            url = url_for("activite.atelier_new")
    r = admin_client.get(url)
    assert r.status_code == 200                      # plus de redirection « sélectionnez un secteur »
    body = r.get_data(as_text=True)
    assert 'name="secteur"' in body
    assert "Choisir un secteur" in body


def test_creation_atelier_avec_secteur_choisi(app, admin_client):
    from app.models import AtelierActivite

    nom = f"AtDir{uuid.uuid4().hex[:6]}"
    with app.app_context():
        with app.test_request_context():
            url = url_for("activite.atelier_new")

    admin_client.post(url, data={"nom": nom, "secteur": "Numérique",
                                 "type_atelier": "COLLECTIF", "is_active": "1"},
                      follow_redirects=True)
    with app.app_context():
        at = AtelierActivite.query.filter_by(nom=nom).first()
        assert at is not None
        assert at.secteur == "Numérique"


def test_creation_sans_secteur_refusee(app, admin_client):
    from app.models import AtelierActivite

    nom = f"AtSans{uuid.uuid4().hex[:6]}"
    with app.app_context():
        with app.test_request_context():
            url = url_for("activite.atelier_new")

    r = admin_client.post(url, data={"nom": nom, "type_atelier": "COLLECTIF"})
    assert r.status_code == 200                      # re-rendu du formulaire, pas de création
    with app.app_context():
        assert AtelierActivite.query.filter_by(nom=nom).first() is None


def test_index_propose_le_selecteur_de_secteur(app, admin_client):
    with app.app_context():
        with app.test_request_context():
            url = url_for("activite.index")
    body = admin_client.get(url).get_data(as_text=True)
    assert "Tous les secteurs" in body               # sélecteur visible pour la portée globale
