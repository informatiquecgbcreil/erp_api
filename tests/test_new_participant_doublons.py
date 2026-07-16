"""Anti-doublon à la création manuelle (/participants/new).

Même garde-fou que le kiosque : on propose les fiches ressemblantes avant
de créer, sans jamais bloquer (bouton « créer quand même »).
"""
import uuid


def test_creation_propose_les_doublons(app, admin_client):
    from app.extensions import db
    from app.models import Participant
    suf = uuid.uuid4().hex[:6]
    nom = f"Benali{suf}"
    with app.app_context():
        db.session.add(Participant(nom=nom, prenom="Mohamed", ville="Creil"))
        db.session.commit()

    # Variante « Mohammed » (proche) → panneau d'alerte, pas de création.
    r = admin_client.post("/participants/new", data={"nom": nom, "prenom": "Mohammed"})
    body = r.get_data(as_text=True)
    assert r.status_code == 200
    assert "existe peut-être déjà" in body
    assert nom in body
    assert "créer quand même" in body
    with app.app_context():
        assert Participant.query.filter_by(nom=nom).count() == 1


def test_force_creation_passe_outre(app, admin_client):
    from app.extensions import db
    from app.models import Participant
    suf = uuid.uuid4().hex[:6]
    nom = f"Traore{suf}"
    with app.app_context():
        db.session.add(Participant(nom=nom, prenom="Awa"))
        db.session.commit()

    r = admin_client.post("/participants/new",
                          data={"nom": nom, "prenom": "Awa", "force_creation": "1"},
                          follow_redirects=True)
    assert r.status_code == 200
    with app.app_context():
        assert Participant.query.filter_by(nom=nom).count() == 2


def test_nom_unique_cree_directement(app, admin_client):
    from app.extensions import db
    from app.models import Participant
    nom = f"Zorglub{uuid.uuid4().hex[:6]}"
    r = admin_client.post("/participants/new", data={"nom": nom, "prenom": "Leon"},
                          follow_redirects=True)
    assert r.status_code == 200
    with app.app_context():
        assert Participant.query.filter_by(nom=nom).count() == 1
