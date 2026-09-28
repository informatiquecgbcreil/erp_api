"""Bascule d'une ancienne installation (audit C4, 4.1, 4.2, 4.3, 6.4).

Tests de bout en bout sur un VRAI PostgreSQL (job PostgreSQL de la CI) : la
reprise tourne dans un processus séparé, comme sur Windows, pour que la
configuration de l'application lise la base de destination et jamais celle
des tests.
"""
import json
import os
import shutil
import subprocess
import sys
import textwrap
import uuid
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Unitaires (tous moteurs)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("mot_de_passe", ["P@ss%é!w0rd", "a:b/c@d", "fin@", "%40%3A"])
def test_champs_separes_mot_de_passe_special(tmp_path, mot_de_passe):
    """Audit 4.3 : aucun caractère du mot de passe n'est pris pour un séparateur."""
    from desktop.migration import read_source
    url = read_source(tmp_path, champs={"host": "::1", "port": "5531", "database": "erp_prod",
                                        "user": "erp_src", "password": mot_de_passe})["url"]
    assert (url.host, url.port, url.database, url.username, url.password) == ("::1", 5531, "erp_prod", "erp_src", mot_de_passe)


def test_env_mal_forme_refuse_sans_fragment_de_secret(tmp_path):
    from desktop.migration import MigrationError, read_source
    (tmp_path / ".env").write_text("SQLALCHEMY_DATABASE_URI=postgresql://erp_src:P@ss%é!w0rd@127.0.0.1:5531/erp_prod\n")
    with pytest.raises(MigrationError) as erreur:
        read_source(tmp_path)
    assert "ss%é" not in str(erreur.value) and "w0rd" not in str(erreur.value)
    assert "champs séparés" in str(erreur.value)


def test_diagnostic_distingue_source_et_destination():
    from sqlalchemy.engine import make_url
    from desktop.migration import _diagnostic
    url = make_url("postgresql://u:secret@h/b")
    assert "destination" in _diagnostic(b"ERROR: permission denied for table x", url, "destination")
    assert "source" in _diagnostic(b"ERROR: permission denied for table x", url, "source")
    assert "WIN1252" in _diagnostic(b'character with byte sequence 0x81 in encoding "WIN1252" has no equivalent in encoding "UTF8"', url)
    assert "secret" not in _diagnostic(b"FATAL: password authentication failed secret", url)


@pytest.mark.parametrize("chemin", [r"\\serveur\partage\x.pdf", "//serveur/partage/x.pdf", r"\\?\C:\x.pdf", r"\\.\pipe\x"])
def test_chemins_reseau_jamais_ouverts(tmp_path, chemin):
    """Audit 4.2 : un chemin UNC n'est même pas testé (connexion SMB avec les
    identifiants de l'administrateur)."""
    from sqlalchemy import create_engine, text
    from desktop import migration
    ouverts = []
    original = Path.resolve

    def espion(self, *a, **k):
        if str(self).startswith(("\\\\", "//")):
            ouverts.append(str(self))
        return original(self, *a, **k)
    old = tmp_path / "AppGestion"
    (old / "instance").mkdir(parents=True)
    source = {"root": old, "roots": {"instance": old / "instance", "uploads": old / "uploads"}}
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE piece (id INTEGER PRIMARY KEY, file_path TEXT)"))
        conn.execute(text("INSERT INTO piece VALUES (1, :v)"), {"v": chemin})
        Path.resolve = espion
        try:
            plan = migration.plan_documents(conn, source)
        finally:
            Path.resolve = original
    assert not ouverts
    assert plan["refuses"] == {"piece.file_path": 1} and not plan["external"]


def test_lien_vers_un_dossier_interdit_refuse(tmp_path):
    """Un lien symbolique placé sous l'ancien dossier ne fait pas sortir des
    emplacements autorisés : on juge le chemin RÉSOLU."""
    from sqlalchemy import create_engine, text
    from desktop.migration import plan_documents
    secret = tmp_path / "Administrateur"; secret.mkdir()
    (secret / "releve.pdf").write_bytes(b"%PDF")
    old = tmp_path / "AppGestion"; (old / "instance").mkdir(parents=True)
    try:
        (old / "raccourci").symlink_to(secret, target_is_directory=True)
    except OSError:
        pytest.skip("liens symboliques indisponibles")
    source = {"root": old, "roots": {"instance": old / "instance", "uploads": old / "uploads"}}
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE piece (id INTEGER PRIMARY KEY, file_path TEXT)"))
        conn.execute(text("INSERT INTO piece VALUES (1, :v)"), {"v": str(old / "raccourci" / "releve.pdf")})
        plan = plan_documents(conn, source)
    assert not plan["external"] and plan["refuses"] == {"piece.file_path": 1}


def test_reglages_kiosque_de_l_ancien_env_non_importes(tmp_path):
    """Audit 6.1 : l'adresse du kiosque est celle de la NOUVELLE installation."""
    from desktop.migration import read_source
    (tmp_path / ".env").write_text("DATABASE_URL=postgresql://u:p@localhost/base\n"
                                   "KIOSK_PUBLIC_BASE_URL=http://ancien:8080\nKIOSK_PUBLIC_HOST=centre.tail.ts.net\n")
    reglages = read_source(tmp_path)["settings"]
    assert "KIOSK_PUBLIC_BASE_URL" not in reglages
    assert reglages["KIOSK_PUBLIC_HOST"] == "centre.tail.ts.net"


# ---------------------------------------------------------------------------
# De bout en bout sur PostgreSQL
# ---------------------------------------------------------------------------

def _pg_bin():
    for candidat in ("/usr/lib/postgresql/18/bin", "/usr/lib/postgresql/17/bin", "/usr/lib/postgresql/16/bin"):
        if Path(candidat, "pg_dump").exists():
            return Path(candidat)
    outil = shutil.which("pg_dump")
    return Path(outil).parent if outil else None


@pytest.fixture
def pg():
    url = (os.environ.get("TESTS_DATABASE_URL") or "").strip()
    if not url.startswith("postgres"):
        pytest.skip("reprise de bout en bout : job PostgreSQL seulement")
    binaires = _pg_bin()
    if binaires is None:
        pytest.skip("outils PostgreSQL (pg_dump) introuvables")
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url
    base = make_url(url)
    suffixe = uuid.uuid4().hex[:8]
    noms = {"source": f"src_pytest_{suffixe}", "cible": f"cible_pytest_{suffixe}"}
    admin = create_engine(base, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        for nom in noms.values():
            conn.execute(text(f'CREATE DATABASE "{nom}"'))
    yield {"base": base, "noms": noms, "bin": binaires, "admin": admin,
           "url": {k: base.set(database=v) for k, v in noms.items()}}
    with admin.connect() as conn:
        for nom in noms.values():
            conn.execute(text(f'DROP DATABASE IF EXISTS "{nom}" WITH (FORCE)'))
    admin.dispose()


def _processus(code: str, env: dict) -> subprocess.CompletedProcess:
    environnement = {k: v for k, v in os.environ.items() if k not in {"TESTS_DATABASE_URL"}}
    environnement.update(env)
    environnement["PYTHONPATH"] = str(RACINE)
    return subprocess.run([sys.executable, "-c", textwrap.dedent(code)], cwd=RACINE, env=environnement,
                          capture_output=True, text=True, timeout=600)


def _url_texte(url):
    return url.set(drivername="postgresql+psycopg").render_as_string(hide_password=False)


def _preparer_source(pg, tmp_path, *, derive=False):
    """Base source au schéma courant, avec un compte et un participant."""
    donnees = tmp_path / "donnees-source"
    donnees.mkdir()
    r = _processus("""
        from app import create_app
        from app.extensions import db
        from app.models import User, Participant, Role
        app = create_app()
        with app.app_context():
            u = User(email="ancien@example.test", nom="Ancien")
            u.set_password("mot-de-passe-ancien")
            u.roles.append(Role.query.filter_by(code="direction").one())
            db.session.add_all([u, Participant(nom="REPRISE", prenom="Test")])
            db.session.commit()
        """, {"DATABASE_URL": _url_texte(pg["url"]["source"]), "SQLALCHEMY_DATABASE_URI": _url_texte(pg["url"]["source"]),
              "MCS_INSTANCE_DIR": str(donnees / "instance"),
              "APP_DATA_DIR": str(donnees), "DB_AUTO_UPGRADE_ON_START": "1"})
    assert r.returncode == 0, r.stderr[-2000:]
    if derive:
        from sqlalchemy import create_engine, text
        moteur = create_engine(pg["url"]["source"])
        with moteur.begin() as conn:
            # Base ancienne : colonnes ajoutées jadis hors Alembic, absentes ici.
            conn.execute(text('ALTER TABLE "user" DROP COLUMN role'))
            conn.execute(text('ALTER TABLE "user" DROP COLUMN calendar_token'))
        moteur.dispose()
    ancien = tmp_path / "AppGestion"
    (ancien / "instance").mkdir(parents=True)
    (ancien / ".env").write_text("SQLALCHEMY_DATABASE_URI=" + _url_texte(pg["url"]["source"]) + "\n", encoding="utf-8")
    return ancien


def _reprise(pg, tmp_path, ancien, fonction="migrate"):
    cible = tmp_path / "nouvelle"
    for dossier in ("runtime", "logs", "instance", "uploads", "private"):
        (cible / dossier).mkdir(parents=True, exist_ok=True)
    config = {"migration_source": str(ancien), "data_root": str(cible), "url": "https://centre:8443",
              "db_name": pg["noms"]["cible"]}
    code = f"""
        import json, sys
        from pathlib import Path
        from desktop import migration
        class Runtime:
            APP = Path({str(RACINE)!r})
            @staticmethod
            def postgres_bin(root, c=None):
                return Path({str(pg['bin'])!r})
        c = json.loads({json.dumps(json.dumps(config))})
        # Politique de la distribution Windows (cible 17 ou 18) hors du sujet
        # de ces tests, qui tournent sur le PostgreSQL de la CI.
        migration.validate_postgres_versions = lambda source, cible: None
        try:
            if {fonction!r} == "migrate":
                print(json.dumps(migration.migrate(c, Runtime)))
            elif {fonction!r} == "verifier":
                migration.verifier_source(c); print("{{}}")
            else:
                migration.liberer_source(migration.read_source(c["migration_source"])["url"]); print("{{}}")
        except migration.MigrationError as exc:
            print(json.dumps({{"erreur": str(exc), "etape": exc.etape}}))
        """
    r = _processus(code, {"SQLALCHEMY_DATABASE_URI": _url_texte(pg["url"]["cible"]),
                          "DATABASE_URL": _url_texte(pg["url"]["cible"]),
                          "MCS_INSTANCE_DIR": str(cible / "instance"), "APP_DATA_DIR": str(cible),
                          "APP_UPLOAD_DIR": str(cible / "uploads"), "DB_AUTO_UPGRADE_ON_START": "1"})
    assert r.returncode == 0, r.stderr[-3000:]
    return json.loads(r.stdout.strip().splitlines()[-1])


def _lecture_seule(pg) -> bool:
    from sqlalchemy import create_engine, text
    moteur = create_engine(pg["url"]["source"])
    with moteur.connect() as conn:
        valeur = conn.execute(text("SHOW default_transaction_read_only")).scalar_one()
    moteur.dispose()
    return valeur == "on"


def test_reprise_nominale_met_la_source_au_repos(pg, tmp_path):
    ancien = _preparer_source(pg, tmp_path)
    rapport = _reprise(pg, tmp_path, ancien)
    assert "erreur" not in rapport, rapport
    assert rapport["source_mise_au_repos"] is True
    # Toute nouvelle connexion à la source est en lecture seule : une ancienne
    # application relancée ne peut plus écrire.
    assert _lecture_seule(pg)
    assert _reprise(pg, tmp_path, ancien, "verifier") == {}


def test_copie_perimee_refusee_puis_source_liberee(pg, tmp_path):
    from sqlalchemy import create_engine, text
    ancien = _preparer_source(pg, tmp_path)
    assert "erreur" not in _reprise(pg, tmp_path, ancien)
    # Saisie tardive dans l'ancienne base (session qui force l'écriture).
    moteur = create_engine(pg["url"]["source"])
    with moteur.begin() as conn:
        conn.execute(text("SET LOCAL default_transaction_read_only = off"))
        conn.execute(text("SET LOCAL transaction_read_only = off"))
        conn.execute(text("UPDATE participant SET prenom = 'Saisie tardive'"))
    moteur.dispose()
    resultat = _reprise(pg, tmp_path, ancien, "verifier")
    assert resultat["etape"] == "perimee"
    # Retour arrière : la source redevient modifiable.
    assert _reprise(pg, tmp_path, ancien, "liberer") == {}
    assert not _lecture_seule(pg)


def test_connexion_restee_ouverte_refusee_et_source_liberee(pg, tmp_path):
    from sqlalchemy import create_engine, text
    ancien = _preparer_source(pg, tmp_path)
    moteur = create_engine(pg["url"]["source"].set(query={"application_name": "ancienne-appli"}))
    connexion = moteur.connect()
    connexion.execute(text("SELECT 1"))
    try:
        resultat = _reprise(pg, tmp_path, ancien)
    finally:
        connexion.close()
        moteur.dispose()
    assert "connexion(s) encore ouverte(s)" in resultat["erreur"] and "ancienne-appli" in resultat["erreur"]
    assert not _lecture_seule(pg)  # l'échec rend la source modifiable
    assert not (tmp_path / "nouvelle/runtime/reprise/source.dump").exists()


def test_derive_de_schema_user_role(pg, tmp_path):
    """Audit 4.1 : la reprise ne s'arrête plus sur une table « user » ancienne."""
    ancien = _preparer_source(pg, tmp_path, derive=True)
    rapport = _reprise(pg, tmp_path, ancien)
    assert "erreur" not in rapport, rapport
    assert {"user.role", "user.calendar_token"} <= set(rapport["colonnes_completees"])


def test_nettoyage_des_tentatives_par_identifiant_et_non_par_prefixe(pg):
    """Audit 6.4 : les copies abandonnées sont supprimées, mais seulement
    celles de CETTE installation, reconnues à leur commentaire."""
    import psycopg
    from psycopg import sql
    from desktop import runtime
    c = {"secret_key": "cle-de-cette-installation"}
    autre = {"secret_key": "une-autre-installation"}
    suffixe = uuid.uuid4().hex[:6]
    noms = {"a_moi": f"mcs_reprise_{suffixe}a", "active": f"mcs_reprise_{suffixe}b",
            "etrangere": f"mcs_reprise_{suffixe}c", "sans_commentaire": f"mcs_reprise_{suffixe}d"}
    base = pg["base"]
    connexion = psycopg.connect(host=base.host, port=base.port or 5432, user=base.username, password=base.password,
                                dbname=base.database, autocommit=True)
    try:
        for cle, nom in noms.items():
            connexion.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(nom)))
        for cle, config in (("a_moi", c), ("active", c), ("etrangere", autre)):
            connexion.execute(sql.SQL("COMMENT ON DATABASE {} IS {}").format(
                sql.Identifier(noms[cle]), sql.Literal(runtime._marque_tentative(config) + "2026-09-28")))
        assert set(runtime.bases_de_tentative(connexion, c)) == {noms["a_moi"], noms["active"]}
        supprimees = runtime.nettoyer_tentatives(connexion, c, garder={noms["active"]})
        assert supprimees == [noms["a_moi"]]
        restantes = {r[0] for r in connexion.execute("SELECT datname FROM pg_database").fetchall()}
        assert {noms["active"], noms["etrangere"], noms["sans_commentaire"]} <= restantes
    finally:
        for nom in noms.values():
            connexion.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(nom)))
        connexion.close()
