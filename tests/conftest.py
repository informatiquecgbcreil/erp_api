"""Socle de tests de l'application.

Principe : chaque session de test crée une base SQLite jetable dans un
dossier temporaire, laisse l'application exécuter ses migrations alembic
au démarrage (exactement comme en production), puis crée un compte admin
avec le rôle RBAC ``admin_tech``.

Fixtures principales :
- ``app``          : application initialisée avec un compte admin en base
- ``client``       : client HTTP anonyme
- ``admin_client`` : client HTTP connecté en admin
- ``fresh_app``    : application sur base vierge (test du premier démarrage)
"""
import os
import tempfile

import pytest

# Dossiers jetables AVANT l'import de la config (config.py crée des
# dossiers au moment de l'import).
_TMP = tempfile.mkdtemp(prefix="juin-tests-")
os.environ["APP_DATA_DIR"] = os.path.join(_TMP, "data")
os.environ["APP_UPLOAD_DIR"] = os.path.join(_TMP, "uploads")
os.environ["ERP_LOG_DIR"] = os.path.join(_TMP, "logs")
# On neutralise une éventuelle base configurée dans l'environnement :
# les tests ne doivent JAMAIS toucher une vraie base.
os.environ.pop("DATABASE_URL", None)
os.environ.pop("SQLALCHEMY_DATABASE_URI", None)

from config import Config  # noqa: E402

ADMIN_EMAIL = "admin@example.org"
ADMIN_PASSWORD = "motdepasse-tests"


def _create_app(db_name: str):
    from app import create_app

    Config.SQLALCHEMY_DATABASE_URI = "sqlite:///" + os.path.join(_TMP, db_name).replace("\\", "/")
    app = create_app()
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    return app


@pytest.fixture(scope="session")
def app():
    """Application avec schéma migré et un compte admin RBAC."""
    app = _create_app("app.db")
    with app.app_context():
        from app.extensions import db
        from app.models import Role, User

        user = User(email=ADMIN_EMAIL, nom="Admin Tests")
        user.set_password(ADMIN_PASSWORD)
        # "direction" possède toutes les permissions (accès global total),
        # contrairement à "admin_tech" qui est volontairement limité.
        role = Role.query.filter_by(code="direction").first()
        assert role is not None, "bootstrap_rbac n'a pas créé le rôle direction"
        user.roles.append(role)
        db.session.add(user)
        db.session.commit()
    return app


@pytest.fixture()
def client(app):
    """Client HTTP anonyme."""
    return app.test_client()


@pytest.fixture()
def admin_client(app):
    """Client HTTP connecté avec le compte admin."""
    c = app.test_client()
    r = c.post("/", data={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert r.status_code == 302, "la connexion admin a échoué"
    assert "/dashboard" in r.headers.get("Location", "")
    return c


@pytest.fixture()
def fresh_app():
    """Application sur base vierge (aucun utilisateur)."""
    return _create_app("fresh.db")
