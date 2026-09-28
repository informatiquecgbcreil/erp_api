"""Restauration d'une sauvegarde plus ancienne que l'application (défaut 2).

Reproduit sur SQLite : une sauvegarde au schéma de la PR #59
(``b8d0f2a4c593``) restaurée par le parcours web d'une application plus
récente laissait la base à l'ancien schéma ; les traitements suivants
échouaient sur ``effacement_rgpd`` et ``rapprochement_bulletin`` absentes.

Ces tests passent par le vrai parcours (``restaurer_lot`` et la route
d'administration), sur une application et une base propres au module : une
restauration remplace toute la base, elle ne doit pas toucher celle des
autres tests.
"""
from __future__ import annotations

import hashlib
import shutil
import uuid
from pathlib import Path

import pytest

import conftest

REVISION_PR59 = "b8d0f2a4c593"


@pytest.fixture(scope="module")
def app_restauration():
    from app.extensions import db
    from app.models import Role, User
    application = conftest._create_app("restauration.db")
    with application.app_context():
        user = User(email=conftest.ADMIN_EMAIL, nom="Admin restauration")
        user.set_password(conftest.ADMIN_PASSWORD)
        user.roles.append(Role.query.filter_by(code="direction").first())
        db.session.add(user)
        db.session.commit()
    yield application
    with application.app_context():
        db.session.remove()
        db.engine.dispose()
    if conftest._SUR_POSTGRES:
        conftest._supprimer_base("restauration")


@pytest.fixture()
def lots(app_restauration, tmp_path, monkeypatch):
    from app.services import sauvegarde as svc
    dossier = tmp_path / "lots"
    dossier.mkdir()
    monkeypatch.setattr(svc, "dossier_sauvegardes", lambda: dossier)
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "donnees"))
    (tmp_path / "donnees" / "runtime").mkdir(parents=True)
    with app_restauration.app_context():
        if conftest._SUR_POSTGRES and not (svc._trouver_psql() and svc._trouver_pg_dump()):
            pytest.skip("psql ou pg_dump indisponible")
    return dossier


def _client(application):
    c = application.test_client()
    assert c.post("/", data={"email": conftest.ADMIN_EMAIL, "password": conftest.ADMIN_PASSWORD}).status_code == 302
    return c


def _sha(chemin: Path) -> str:
    return hashlib.sha256(chemin.read_bytes()).hexdigest()


def _abaisser_le_lot(application, dossier: Path, base: str, revision: str) -> None:
    """Transforme un lot au schéma courant en lot au schéma ``revision`` :
    copie de sa base dans une base jetable, retour arrière des migrations,
    puis réécriture du fichier du lot et de ses empreintes."""
    from flask import Flask
    from flask_migrate import downgrade
    from app.extensions import db, migrate
    from app.services import sauvegarde as svc
    from config import Config
    if conftest._SUR_POSTGRES:
        conftest._recreer_base("restauration_ancienne")
        url = conftest._url_de_base("restauration_ancienne").render_as_string(hide_password=False)
        with application.app_context():
            svc._restaurer_postgres_brut(dossier / f"{base}.sql", url)
    else:
        copie = dossier / "ancienne.db"
        shutil.copy2(dossier / f"{base}.db", copie)
        url = "sqlite:///" + str(copie)
    ancienne = Flask("lot-ancien", instance_path=str(dossier / "instance-ancienne"))
    ancienne.config.from_object(Config)
    ancienne.config["SQLALCHEMY_DATABASE_URI"] = url
    db.init_app(ancienne)
    migrate.init_app(ancienne, db, directory=str(conftest.os.path.join(Path(__file__).resolve().parents[1],
                                                                        "migrations")))
    with ancienne.app_context():
        downgrade(revision=revision)
        db.session.remove()
        db.engine.dispose()
        if conftest._SUR_POSTGRES:
            svc._pg_dump(url, dossier / f"{base}.sql")
    if conftest._SUR_POSTGRES:
        conftest._supprimer_base("restauration_ancienne")
        fichier = dossier / f"{base}.sql"
    else:
        fichier = dossier / f"{base}.db"
        shutil.move(copie, fichier)
    zip_ = dossier / f"{base}_uploads.zip"
    (dossier / f"{base}.sha256").write_text(f"{_sha(fichier)}  {fichier.name}\n{_sha(zip_)}  {zip_.name}\n",
                                            encoding="utf-8")


def _revision(application) -> str:
    from sqlalchemy import text
    from app.extensions import db
    with application.app_context():
        return db.session.execute(text("SELECT version_num FROM alembic_version")).scalar_one()


def _tete() -> str:
    from alembic.config import Config as AlembicConfig
    from alembic.script import ScriptDirectory
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
    return ScriptDirectory.from_config(cfg).get_current_head()


def _fiche(application, nom=None):
    from app.extensions import db
    from app.models import Participant
    with application.app_context():
        p = Participant(nom=nom or f"Resto{uuid.uuid4().hex[:6]}", prenom="Test", created_secteur="Familles")
        db.session.add(p)
        db.session.commit()
        return p.id, p.nom


# ---------------------------------------------------------------------------

def test_sauvegarde_pr59_restauree_par_le_parcours_web(app_restauration, lots):
    """Le défaut : sans remise à niveau, la base revient au schéma de la PR #59
    et les traitements suivants échouent. Après correction : base au dernier
    schéma, registres fusionnés, effacements et numéros postérieurs
    préservés, caisse et registres utilisables."""
    from app.extensions import db
    from app.models import Participant
    from app.services import sauvegarde as svc
    from app.services.financial_sequence import next_number, lire_registre
    from app.services.purge_rgpd import anonymiser_participant
    effacee, _ = _fiche(app_restauration)
    espace = "don:2097"
    with app_restauration.app_context():
        assert next_number(espace, []) >= 1
        db.session.commit()
        lot = svc.creer_sauvegarde()["base"]
    _abaisser_le_lot(app_restauration, lots, lot, REVISION_PR59)
    # Après la sauvegarde : un effacement et des numéros émis.
    with app_restauration.app_context():
        anonymiser_participant(db.session.get(Participant, effacee))
        db.session.commit()
        emis = [next_number(espace, []) for _ in range(3)]
        db.session.commit()
    client = _client(app_restauration)
    reponse = client.post("/admin/sauvegardes/restaurer", data={"base": lot}, follow_redirects=True)
    texte = reponse.get_data(as_text=True)
    assert "Restauration effectuée" in texte, texte[-2000:]
    assert _revision(app_restauration) == _tete()
    with app_restauration.app_context():
        from sqlalchemy import inspect
        tables = set(inspect(db.engine).get_table_names())
        assert {"effacement_rgpd", "rapprochement_bulletin"} <= tables
        assert db.session.get(Participant, effacee).nom == "ANONYME"          # effacement réappliqué
        assert lire_registre()[espace] == emis[-1]
        assert next_number(espace, []) == emis[-1] + 1                       # jamais un numéro déjà émis
        db.session.rollback()
    for page in ("/caisse", "/caisse/rapprochement-bulletins", "/controle/registres", "/caisse/a-qualifier"):
        assert client.get(page).status_code == 200, page
    assert svc.restauration_inachevee() is None


def test_sauvegarde_au_schema_actuel(app_restauration, lots):
    from app.services import sauvegarde as svc
    pid, nom = _fiche(app_restauration)
    with app_restauration.app_context():
        lot = svc.creer_sauvegarde()["base"]
    client = _client(app_restauration)
    r = client.post("/admin/sauvegardes/restaurer", data={"base": lot}, follow_redirects=True)
    assert "Restauration effectuée" in r.get_data(as_text=True)
    assert _revision(app_restauration) == _tete()
    assert client.get("/controle/registres").status_code == 200


def test_migrations_en_echec_etat_precedent_remis(app_restauration, lots, monkeypatch):
    """Les migrations échouent pendant la restauration : pas de « réussite »,
    l'état d'avant la restauration est remis depuis la sauvegarde de
    sécurité, la maintenance est levée seulement alors."""
    from app.extensions import db
    from app.models import Participant
    from app.services import sauvegarde as svc
    with app_restauration.app_context():
        lot = svc.creer_sauvegarde()["base"]
    _abaisser_le_lot(app_restauration, lots, lot, REVISION_PR59)
    apres_lot, nom = _fiche(app_restauration)          # n'existe pas dans le lot
    vraie = svc._mettre_le_schema_a_jour
    appels = {"n": 0}

    def migration_en_panne():
        appels["n"] += 1
        if appels["n"] == 1:
            raise RuntimeError("migration interrompue (simulée)")
        return vraie()

    monkeypatch.setattr(svc, "_mettre_le_schema_a_jour", migration_en_panne)
    client = _client(app_restauration)
    r = client.post("/admin/sauvegardes/restaurer", data={"base": lot}, follow_redirects=True)
    texte = r.get_data(as_text=True)
    assert "Restauration effectuée" not in texte
    assert "état d" in texte and "remis en place" in texte
    assert _revision(app_restauration) == _tete()
    with app_restauration.app_context():
        assert db.session.get(Participant, apres_lot).nom == nom          # état précédent intact
    assert svc.restauration_inachevee() is None
    assert client.get("/caisse").status_code == 200


def test_echec_de_la_remise_en_place_laisse_la_maintenance(app_restauration, lots, monkeypatch):
    """Si même l'état précédent ne peut pas être remis : maintenance
    persistante (personne ne travaille sur une base à moitié remise), seules
    l'administration des sauvegardes et la connexion restent accessibles."""
    from app.services import sauvegarde as svc
    with app_restauration.app_context():
        lot = svc.creer_sauvegarde()["base"]
    _abaisser_le_lot(app_restauration, lots, lot, REVISION_PR59)
    vraie = svc._mettre_le_schema_a_jour
    monkeypatch.setattr(svc, "_mettre_le_schema_a_jour",
                        lambda: (_ for _ in ()).throw(RuntimeError("panne persistante (simulée)")))
    client = _client(app_restauration)
    r = client.post("/admin/sauvegardes/restaurer", data={"base": lot}, follow_redirects=True)
    assert "maintenance" in r.get_data(as_text=True)
    etat = svc.restauration_inachevee()
    assert etat and etat["etat"] == "echec" and etat["securite"]
    anonyme = conftest_client = _client(app_restauration)
    assert conftest_client.get("/caisse").status_code == 503
    assert "maintenance" in app_restauration.test_client().get("/participants/").get_data(as_text=True).lower()
    assert conftest_client.get("/admin/sauvegardes").status_code == 200
    # Procédure de récupération : restaurer la sauvegarde de sécurité indiquée.
    monkeypatch.setattr(svc, "_mettre_le_schema_a_jour", vraie)
    r = conftest_client.post("/admin/sauvegardes/restaurer", data={"base": etat["securite"]}, follow_redirects=True)
    assert "Restauration effectuée" in r.get_data(as_text=True)
    assert svc.restauration_inachevee() is None
    assert anonyme.get("/caisse").status_code == 200
    assert _revision(app_restauration) == _tete()


def test_maintenance_persiste_au_redemarrage(app_restauration, lots):
    """Arrêt brutal pendant une restauration : la marque reste sur disque et
    la nouvelle instance de l'application reste en maintenance."""
    import json
    from app.services import sauvegarde as svc
    svc._marquer_restauration({"etat": "en_cours", "lot": "x", "securite": "y"})
    try:
        # Nouvelle instance sur la MÊME base (sans la recréer).
        from app import create_app
        from config import Config
        Config.SQLALCHEMY_DATABASE_URI = app_restauration.config["SQLALCHEMY_DATABASE_URI"]
        redemarree = create_app()
        redemarree.config["TESTING"] = True
        assert redemarree.test_client().get("/participants/").status_code == 503
    finally:
        svc._lever_la_maintenance()
    assert json.loads(json.dumps(svc.restauration_inachevee())) is None


def _cli(app_restauration, lots, base, monkeypatch):
    import sys
    from config import Config
    from tools import restore_instance
    fichier = lots / (f"{base}.sql" if conftest._SUR_POSTGRES else f"{base}.db")
    monkeypatch.setattr(Config, "SQLALCHEMY_DATABASE_URI", app_restauration.config["SQLALCHEMY_DATABASE_URI"])
    monkeypatch.setattr(sys, "argv", ["restore_instance.py", "--db", str(fichier),
                                      "--uploads", str(lots / f"{base}_uploads.zip")])
    return restore_instance


def test_outil_en_ligne_de_commande_meme_comportement(app_restauration, lots, monkeypatch):
    """Parcours en ligne de commande : même remise à niveau, même levée de la
    maintenance, et même retour à l'état précédent en cas d'échec."""
    from app.services import sauvegarde as svc
    with app_restauration.app_context():
        lot = svc.creer_sauvegarde()["base"]
    _abaisser_le_lot(app_restauration, lots, lot, REVISION_PR59)
    outil = _cli(app_restauration, lots, lot, monkeypatch)
    assert outil.main() == 0
    assert _revision(app_restauration) == _tete()
    assert svc.restauration_inachevee() is None
    # Échec des migrations : retour à l'état précédent, pas de « terminé ».
    pid, nom = _fiche(app_restauration)
    vraie = outil.create_app
    appels = {"n": 0}

    def demarrage(*a, **k):
        appels["n"] += 1
        if appels["n"] == 1:
            raise RuntimeError("migration interrompue (simulée)")
        return vraie(*a, **k)

    monkeypatch.setattr(outil, "create_app", demarrage)
    with pytest.raises(RuntimeError, match="rien n.a changé"):
        outil.main()
    assert svc.restauration_inachevee() is None
    assert _revision(app_restauration) == _tete()
    from app.extensions import db
    from app.models import Participant
    with app_restauration.app_context():
        assert db.session.get(Participant, pid).nom == nom
