"""Résilience de la connexion : un souci de base (droits PostgreSQL, etc.)
ne doit jamais provoquer une erreur 500 au login — le verrou anti-bruteforce
est ignoré proprement, et la connexion reste possible (le mot de passe est
toujours vérifié par ailleurs)."""
from sqlalchemy.exc import SQLAlchemyError


def test_minutes_resilient_si_journal_inaccessible(app, monkeypatch):
    from app.services import connexion_securite as cs

    def boom(email):
        raise SQLAlchemyError("droit refusé pour la table journal_connexion (simulé)")

    monkeypatch.setattr(cs, "_echecs_recents", boom)
    with app.app_context():
        # ne lève pas, et autorise la connexion (0 minute de verrou)
        assert cs.minutes_avant_deverrouillage("x@y.z") == 0


def test_enregistrer_echec_resilient(app, monkeypatch):
    from app.services import connexion_securite as cs

    def boom_commit(*a, **k):
        raise SQLAlchemyError("commit refusé (simulé)")

    with app.app_context():
        monkeypatch.setattr(cs.db.session, "commit", boom_commit)
        # ne lève pas, renvoie 0 (pas de verrouillage faute de journal)
        assert cs.enregistrer_echec("x@y.z", "127.0.0.1") == 0


def test_enregistrer_succes_resilient(app, monkeypatch):
    from app.services import connexion_securite as cs

    def boom_commit(*a, **k):
        raise SQLAlchemyError("commit refusé (simulé)")

    with app.app_context():
        monkeypatch.setattr(cs.db.session, "commit", boom_commit)
        # ne lève pas (la connexion réussie n'est pas cassée par le journal)
        assert cs.enregistrer_succes("x@y.z", "127.0.0.1") is None
