"""Socle de tests de l'application.

Principe : chaque session de test crée une base jetable, laisse
l'application exécuter ses migrations alembic au démarrage (exactement
comme en production), puis crée un compte admin avec le rôle RBAC.

Fixtures principales :
- ``app``          : application initialisée avec un compte admin en base
- ``client``       : client HTTP anonyme
- ``admin_client`` : client HTTP connecté en admin
- ``fresh_app``    : application sur base vierge (test du premier démarrage)
- ``dialecte``     : le nom du moteur réellement utilisé

## Tourner sur PostgreSQL

Par défaut, tout tourne sur SQLite : rien à installer, rien à démarrer.

Mais la production tourne sur PostgreSQL, et **deux pannes de production
ont eu pour cause un comportement que SQLite pardonne et que PostgreSQL
refuse** : un booléen écrit ``1`` au lieu de ``true``, et une fonction SQL
propre à SQLite appelée sur PostgreSQL. Dans les deux cas la suite était
au vert — elle ne tournait que sur SQLite.

D'où cette bascule :

    TESTS_DATABASE_URL=postgresql+psycopg://user:motdepasse@localhost:5432/erp \
        python -m pytest

Les bases de travail sont DÉRIVÉES de cette URL en leur ajoutant le
suffixe ``_pytest_app`` et ``_pytest_fresh``, puis créées et supprimées
autour de la session. La base nommée dans l'URL n'est jamais touchée :
elle ne sert qu'à indiquer le serveur et les identifiants.

La variable s'appelle ``TESTS_DATABASE_URL`` et non ``DATABASE_URL``
exprès : la seconde est vidée quelques lignes plus bas, pour qu'une base
de production configurée dans l'environnement ne puisse jamais être
atteinte par un test.
"""
import os
import tempfile

import pytest

# Dossiers jetables AVANT l'import de la config (config.py crée des
# dossiers au moment de l'import).
_TMP = os.environ.get("MCS_TEST_SESSION_ROOT") or tempfile.mkdtemp(prefix="juin-tests-")
os.environ["MCS_TEST_SESSION_ROOT"] = _TMP
os.environ["APP_DATA_DIR"] = os.path.join(_TMP, "data")
os.environ["APP_UPLOAD_DIR"] = os.path.join(_TMP, "uploads")
os.environ["ERP_LOG_DIR"] = os.path.join(_TMP, "logs")
os.environ["MCS_INSTANCE_DIR"] = os.path.join(_TMP, "instance")
os.environ["MCS_BACKUP_DIR"] = os.path.join(_TMP, "backups")
os.environ["BACKUP_OFFSITE_DIRS"] = ""
# On neutralise une éventuelle base configurée dans l'environnement :
# les tests ne doivent JAMAIS toucher une vraie base.
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "bootstrap.db")
os.environ["SQLALCHEMY_DATABASE_URI"] = os.environ["DATABASE_URL"]

from config import Config  # noqa: E402

ADMIN_EMAIL = "admin@example.org"
ADMIN_PASSWORD = "motdepasse-tests"

#: URL d'un PostgreSQL de test. Vide -> SQLite, comportement historique.
_URL_POSTGRES = (os.environ.get("TESTS_DATABASE_URL") or "").strip()
_SUR_POSTGRES = _URL_POSTGRES.startswith("postgres")

#: Suffixe imposé aux bases de travail. Elles sont SUPPRIMÉES avant chaque
#: session : le suffixe garantit qu'on ne peut pas viser une vraie base,
#: même si quelqu'un pointe TESTS_DATABASE_URL sur un serveur de production.
_SUFFIXE = "_pytest_"


def _url_de_base(nom: str):
    """L'URL de la base de travail « nom », dérivée de celle fournie."""
    from sqlalchemy.engine import make_url

    url = make_url(_URL_POSTGRES)
    return url.set(database=f"{url.database}{_SUFFIXE}{nom}")


def _moteur_de_maintenance():
    """Moteur sur la base « postgres », pour créer et supprimer les autres.

    AUTOCOMMIT est obligatoire : CREATE DATABASE ne s'exécute pas dans une
    transaction.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url

    cible = make_url(_URL_POSTGRES).set(database="postgres")
    return create_engine(cible, isolation_level="AUTOCOMMIT")


def _recreer_base(nom: str) -> None:
    """Repart d'une base vide. Idempotent."""
    from sqlalchemy import text

    base = _url_de_base(nom).database
    assert _SUFFIXE in base, f"garde-fou : {base!r} n'est pas une base de test"

    with _moteur_de_maintenance().connect() as connexion:
        try:
            connexion.execute(text(f'DROP DATABASE IF EXISTS "{base}" WITH (FORCE)'))
        except Exception:  # noqa: BLE001 — WITH (FORCE) demande PostgreSQL 13+
            connexion.execute(text(f'DROP DATABASE IF EXISTS "{base}"'))
        connexion.execute(text(f'CREATE DATABASE "{base}"'))


def _supprimer_base(nom: str) -> None:
    from sqlalchemy import text

    base = _url_de_base(nom).database
    assert _SUFFIXE in base, f"garde-fou : {base!r} n'est pas une base de test"
    try:
        with _moteur_de_maintenance().connect() as connexion:
            connexion.execute(text(f'DROP DATABASE IF EXISTS "{base}" WITH (FORCE)'))
    except Exception:  # noqa: BLE001 — le ménage ne fait jamais échouer une suite
        pass


def _create_app(db_name: str):
    from app import create_app

    if _SUR_POSTGRES:
        nom = db_name.removesuffix(".db")
        _recreer_base(nom)
        Config.SQLALCHEMY_DATABASE_URI = _url_de_base(nom).render_as_string(hide_password=False)
    else:
        Config.SQLALCHEMY_DATABASE_URI = "sqlite:///" + os.path.join(_TMP, db_name).replace("\\", "/")

    app = create_app()
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    return app


def _ligne_de_moteur() -> str:
    if _SUR_POSTGRES:
        from sqlalchemy.engine import make_url

        url = make_url(_URL_POSTGRES)
        # Une connexion par socket unix porte hôte et port dans la query,
        # pas dans l'URL : afficher 5432 par défaut annoncerait un serveur
        # qui n'est pas celui sur lequel on vient de tourner.
        hote = url.host or url.query.get("host") or "local"
        port = url.port or url.query.get("port") or 5432
        return f"base de test : PostgreSQL ({hote}:{port}, bases {url.database}{_SUFFIXE}*)"
    return "base de test : SQLite — pose TESTS_DATABASE_URL pour tourner sur PostgreSQL"


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Annoncer le moteur MÊME en mode silencieux, juste avant le décompte.

    Le projet lance pytest avec « -q » (voir pytest.ini), qui masque
    l'en-tête habituel. Or une suite verte sur SQLite ne dit rien de
    PostgreSQL — c'est exactement ce qui a laissé passer deux pannes de
    production. On ne doit jamais avoir à deviner sur quoi on vient de
    tourner, et le meilleur moment pour le lire est à côté du résultat.
    """
    terminalreporter.write_line("")
    terminalreporter.write_line(_ligne_de_moteur(), bold=True)


def pytest_report_header(config):
    return _ligne_de_moteur()


def pytest_sessionfinish(session, exitstatus):
    if _SUR_POSTGRES:
        for nom in ("app", "fresh"):
            _supprimer_base(nom)


@pytest.fixture(scope="session")
def dialecte(app):
    """Le moteur réellement utilisé : « sqlite » ou « postgresql ».

    Permet à un test de sauter proprement ce qui n'a de sens que sur l'un
    des deux, sans deviner.
    """
    with app.app_context():
        from app.extensions import db

        return db.engine.dialect.name


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
