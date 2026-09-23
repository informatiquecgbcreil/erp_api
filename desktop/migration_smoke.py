"""Recette Windows éphémère : ce fichier n'est pas distribué avec l'application."""
from pathlib import Path
import json
import os
import sys


def main():
    if os.name != "nt" or os.environ.get("GITHUB_ACTIONS") != "true":
        raise RuntimeError("Recette réservée à la CI Windows éphémère.")
    install, mode = Path(sys.argv[1]), sys.argv[2]
    sys.path.insert(0, str(install))
    from desktop import runtime
    payload = json.loads(sys.stdin.buffer.read().decode("utf-8-sig"))
    c = payload["source"]
    root = Path(c["data_root"])
    if not root.resolve().is_relative_to(Path(os.environ["RUNNER_TEMP"]).resolve()):
        raise RuntimeError("La source de recette doit rester dans RUNNER_TEMP.")
    import psycopg
    from sqlalchemy import create_engine, text
    from desktop.migration import fingerprints, read_source

    if mode == "stop":
        if not (root / "postgresql/postmaster.pid").exists():
            return
        runtime.run_tool([install / "postgresql/bin/pg_ctl.exe", "-D", root / "postgresql",
                          "-w", "-m", "fast", "stop"])
        return
    if mode == "prepare":
        runtime.configure_environment(c)
        # Diagnostic de recette uniquement (les outils SQL du produit restent
        # silencieux). Les secrets aléatoires de la fixture sont masqués.
        import subprocess
        import tempfile
        def run_fixture_tool(args, *, timeout=120):
            with tempfile.TemporaryFile() as logfile:
                result = subprocess.run([str(x) for x in args], stdout=logfile,
                                        stderr=subprocess.STDOUT, timeout=timeout,
                                        creationflags=runtime.CREATE_NO_WINDOW)
                logfile.seek(0)
                output = logfile.read().decode("utf-8", errors="replace")
            if result.returncode:
                for key in ("db_password", "db_admin_password", "admin_password", "secret_key"):
                    if c.get(key):
                        output = output.replace(str(c[key]), "[secret]")
                raise RuntimeError(Path(args[0]).name + ": " + output)
            return result
        runtime.run_tool = run_fixture_tool
        runtime.start_database(c, root)
        with psycopg.connect(host="127.0.0.1", port=c["db_port"], user="postgres",
                             password=c["db_admin_password"], dbname="postgres", autocommit=True) as conn:
            conn.execute("CREATE DATABASE erp_pedagogie OWNER mcs")
        from flask import Flask
        from flask_migrate import upgrade
        from app.extensions import db
        from app.extensions import migrate
        from config import Config
        from sqlalchemy import MetaData, Table
        from werkzeug.security import generate_password_hash
        from datetime import datetime
        # Construit directement l'ancien schéma : aucun downgrade destructif
        # ni import d'une ancienne application dans le processus de migration.
        app = Flask("legacy-fixture", instance_path=str(root / "instance"))
        app.config.from_object(Config)
        db.init_app(app)
        migrate.init_app(app, db, directory=str(runtime.APP / "migrations"))
        document = root / "instance/passeport_uploads/preuve.txt"
        document.parent.mkdir(parents=True, exist_ok=True)
        document.write_text("Pièce de recette conservée, avec accents.", encoding="utf-8")
        (root / "uploads/logo.txt").write_text("Logo de recette", encoding="utf-8")
        with app.app_context():
            upgrade(directory=str(runtime.APP / "migrations"), revision="de23fa45bc67")
            with db.engine.begin() as connection:
                metadata = MetaData()
                def insert(name, **values):
                    table = Table(name, metadata, autoload_with=connection)
                    return connection.execute(table.insert().values(**{k:v for k,v in values.items() if k in table.c}).returning(table.c.id)).scalar_one()
                insert("user", email=c["admin_email"], password_hash=generate_password_hash(c["admin_password"]),
                       nom=c["admin_name"], role="direction", actif=True, created_at=datetime.now())
                pid = insert("participant", nom="RECETTE", prenom="Migration", created_secteur="Familles",
                             type_public="H", droit_image_statut="non_renseigne", est_benevole=False,
                             statut_inscription="actif", created_at=datetime.now(), updated_at=datetime.now())
                insert("passeport_note", participant_id=pid, secteur="Familles", contenu="Suivi conservé", categorie="journal")
                insert("passeport_piece_jointe", participant_id=pid, secteur="Familles", categorie="atelier",
                       file_path=str(document), original_name="preuve.txt")
            db.session.remove()
            db.engine.dispose()
        uri = os.environ["SQLALCHEMY_DATABASE_URI"]
        (root / ".env").write_text("SQLALCHEMY_DATABASE_URI=" + uri + "\n"
                                   "MCS_INSTANCE_DIR=instance\nAPP_UPLOAD_DIR=uploads\n"
                                   "MAIL_HOST=mail.example.test\n", encoding="utf-8")
        engine = create_engine(uri)
        with engine.connect() as connection:
            baseline = fingerprints(connection)
        engine.dispose()
        (root / "expected.json").write_text(json.dumps(baseline), encoding="utf-8")
        print("SOURCE_ANCIENNE_PREPAREE")
        return
    if mode != "verify":
        raise ValueError("Mode inconnu")
    # Vérification indépendante : aucune écriture, aucun create_app sur la source.
    source = create_engine(read_source(root)["url"])
    with source.connect() as connection:
        assert fingerprints(connection) == json.loads((root / "expected.json").read_text(encoding="utf-8"))
    source.dispose()
    target = payload["target"]
    runtime.configure_environment(target)
    engine = create_engine(os.environ["SQLALCHEMY_DATABASE_URI"])
    from werkzeug.security import check_password_hash
    with engine.connect() as connection:
        account = connection.execute(text('SELECT password_hash FROM "user" WHERE email=:email'), {"email": c["admin_email"]}).scalar_one()
        assert check_password_hash(account, c["admin_password"])
        assert connection.execute(text("SELECT nom FROM participant")).scalar_one() == "RECETTE"
        assert connection.execute(text("SELECT contenu FROM passeport_note")).scalar_one() == "Suivi conservé"
        path = Path(connection.execute(text("SELECT file_path FROM passeport_piece_jointe")).scalar_one())
        assert path.is_relative_to(Path(target["data_root"]) / "instance")
        assert path.read_bytes() == (root / "instance/passeport_uploads/preuve.txt").read_bytes()
        from alembic.config import Config
        from alembic.script import ScriptDirectory
        cfg = Config(); cfg.set_main_option("script_location", str(runtime.APP / "migrations"))
        assert set(connection.execute(text("SELECT version_num FROM alembic_version")).scalars()) == set(ScriptDirectory.from_config(cfg).get_heads())
    engine.dispose()
    assert target["application_settings"]["MAIL_HOST"] == "mail.example.test"
    assert (Path(target["data_root"]) / "uploads/logo.txt").read_bytes() == (root / "uploads/logo.txt").read_bytes()
    # Le compte importé doit se connecter par le vrai HTTPS, avec CSRF actif,
    # et ouvrir le module pédagogique avec ses droits migrés.
    import http.cookiejar
    import re
    import ssl
    import urllib.parse
    import urllib.request
    ca = Path(target["data_root"]) / "https/tls/pki/authorities/local/root.crt"
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
                                        urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=str(ca))))
    with opener.open(target["url"] + "/", timeout=30) as response:
        body = response.read().decode("utf-8")
    csrf = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', body).group(1)
    data = urllib.parse.urlencode({"email": c["admin_email"], "password": c["admin_password"], "csrf_token": csrf}).encode()
    request = urllib.request.Request(target["url"] + "/", data=data, headers={"Referer": target["url"] + "/"})
    with opener.open(request, timeout=30) as response:
        assert response.status == 200 and "/dashboard" in response.url
    with opener.open(target["url"] + "/participants/", timeout=30) as response:
        assert response.status == 200 and "RECETTE" in response.read().decode("utf-8")
    print("MIGRATION_BASE_ANCIENNE_COMPTES_DOCUMENTS_PARAMETRES_SOURCE_INTACTE_OK")


if __name__ == "__main__":
    main()
