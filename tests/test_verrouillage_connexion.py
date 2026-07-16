"""Tests du verrouillage de la connexion (anti force brute).

Règle : 5 mots de passe erronés en moins de 15 minutes bloquent la
connexion pour cet email jusqu'à la fin de la fenêtre, même avec le bon
mot de passe. Une connexion réussie remet le compteur à zéro.
"""
from datetime import timedelta

import pytest

BON_MDP = "motdepasse-tests"


@pytest.fixture()
def utilisateur(app):
    """Crée un utilisateur dédié avec un email unique par test."""
    import uuid

    email = f"verrou-{uuid.uuid4().hex[:8]}@example.org"
    with app.app_context():
        from app.extensions import db
        from app.models import Role, User

        u = User(email=email, nom="Test Verrouillage")
        u.set_password(BON_MDP)
        u.roles.append(Role.query.filter_by(code="direction").first())
        db.session.add(u)
        db.session.commit()
    return email


def _echouer(client, email, fois=1):
    for _ in range(fois):
        r = client.post("/", data={"email": email, "password": "mauvais-mdp"})
        assert r.status_code == 200
    return r


def test_verrouillage_apres_cinq_echecs(app, client, utilisateur):
    r = _echouer(client, utilisateur, fois=4)
    assert "Identifiants invalides" in r.get_data(as_text=True)

    # 5e échec : le verrou se déclenche silencieusement
    _echouer(client, utilisateur, fois=1)

    # Même le BON mot de passe est refusé pendant le verrouillage
    r = client.post("/", data={"email": utilisateur, "password": BON_MDP})
    assert r.status_code == 200, "la connexion doit être refusée (pas de redirection)"
    page = r.get_data(as_text=True)
    assert "bloquée" in page
    assert "minute" in page


def test_deverrouillage_apres_la_fenetre(app, client, utilisateur):
    _echouer(client, utilisateur, fois=5)

    # On vieillit artificiellement les échecs au-delà de la fenêtre
    with app.app_context():
        from app.extensions import db
        from app.models import JournalConnexion
        from app.services.connexion_securite import FENETRE_MINUTES
        from app.utils.dates import utcnow

        JournalConnexion.query.filter_by(email=utilisateur).update(
            {"cree_le": utcnow() - timedelta(minutes=FENETRE_MINUTES + 1)}
        )
        db.session.commit()

    r = client.post("/", data={"email": utilisateur, "password": BON_MDP})
    assert r.status_code == 302, "après la fenêtre, la connexion doit fonctionner"
    assert "/dashboard" in r.headers["Location"]


def test_succes_remet_le_compteur_a_zero(app, client, utilisateur):
    _echouer(client, utilisateur, fois=3)

    r = client.post("/", data={"email": utilisateur, "password": BON_MDP})
    assert r.status_code == 302
    client.post("/logout")

    # 4 nouveaux échecs après le succès : toujours pas verrouillé (4 < 5)
    _echouer(client, utilisateur, fois=4)
    r = client.post("/", data={"email": utilisateur, "password": BON_MDP})
    assert r.status_code == 302, "le succès précédent doit avoir remis le compteur à zéro"


def test_email_inconnu_aussi_limite(client):
    email = "inconnu-verrouillage@example.org"
    _echouer(client, email, fois=5)
    r = client.post("/", data={"email": email, "password": "nimporte"})
    assert "bloquée" in r.get_data(as_text=True), (
        "un email inexistant doit être verrouillé comme les autres "
        "(sinon on peut deviner quels comptes existent)"
    )


def test_journal_trace_succes_et_echecs(app, client, utilisateur):
    _echouer(client, utilisateur, fois=1)
    r = client.post("/", data={"email": utilisateur, "password": BON_MDP})
    assert r.status_code == 302

    with app.app_context():
        from app.models import JournalConnexion

        lignes = JournalConnexion.query.filter_by(email=utilisateur).all()
        statuts = sorted(l.succes for l in lignes)
        assert statuts == [False, True], "le journal doit tracer l'échec ET le succès"
        assert all(l.adresse_ip for l in lignes), "l'adresse IP doit être tracée"


def test_verrouillage_journalise_dans_erreurs_log(app, client, utilisateur):
    import os

    _echouer(client, utilisateur, fois=6)
    chemin = os.path.join(os.environ["ERP_LOG_DIR"], "erreurs.log")
    with open(chemin, encoding="utf-8") as fh:
        contenu = fh.read()
    assert utilisateur in contenu, "le verrouillage doit laisser une trace dans le journal d'erreurs"
