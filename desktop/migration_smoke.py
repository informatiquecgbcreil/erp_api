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
    payload = json.load(sys.stdin)
    c = payload["source"]
    root = Path(c["data_root"])
    if not root.resolve().is_relative_to(Path(os.environ["RUNNER_TEMP"]).resolve()):
        raise RuntimeError("La source de recette doit rester dans RUNNER_TEMP.")
    import psycopg
    from sqlalchemy import create_engine, text
    from desktop.migration import fingerprints, read_source

    if mode == "stop":
        runtime.run_tool([install / "postgresql/bin/pg_ctl.exe", "-D", root / "postgresql",
                          "-w", "-m", "fast", "stop"])
        return
    if mode == "prepare":
        runtime.configure_environment(c)
        runtime.start_database(c, root)
        with psycopg.connect(host="127.0.0.1", port=c["db_port"], user="postgres",
                             password=c["db_admin_password"], dbname="postgres", autocommit=True) as conn:
            conn.execute("CREATE DATABASE erp_pedagogie OWNER mcs")
        from app import create_app
        from app.extensions import db
        from app.models import Participant, PasseportNote, PasseportPieceJointe
        app = create_app()
        runtime.bootstrap_account(app, c)
        document = root / "instance/passeport_uploads/preuve.txt"
        document.parent.mkdir(parents=True, exist_ok=True)
        document.write_text("Pièce de recette conservée, avec accents.", encoding="utf-8")
        (root / "uploads/logo.txt").write_text("Logo de recette", encoding="utf-8")
        with app.app_context():
            participant = Participant(nom="RECETTE", prenom="Migration", created_secteur="Familles")
            db.session.add(participant)
            db.session.flush()
            db.session.add(PasseportNote(participant_id=participant.id, secteur="Familles", contenu="Suivi conservé"))
            db.session.add(PasseportPieceJointe(participant_id=participant.id, secteur="Familles",
                                               file_path=str(document), original_name="preuve.txt"))
            db.session.commit()
            # Revient à une vraie révision antérieure, sans modifier ses données métier.
            from flask_migrate import downgrade
            downgrade(directory=str(runtime.APP / "migrations"), revision="de23fa45bc67")
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
    print("MIGRATION_BASE_ANCIENNE_COMPTES_DOCUMENTS_PARAMETRES_SOURCE_INTACTE_OK")


if __name__ == "__main__":
    main()
