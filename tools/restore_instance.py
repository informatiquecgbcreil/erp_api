#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import create_app
from app.services.sauvegarde import _restaurer_postgres, _restaurer_uploads, _restaurer_sqlite


def _restore_postgres(src_sql: Path, db_uri: str) -> None:
    # Même chemin que la restauration depuis l'interface : psql localisé
    # (PATH, PSQL_PATH, dossiers PostgreSQL), mot de passe transmis par
    # l'environnement et jamais sur la ligne de commande.
    _restaurer_postgres(src_sql, db_uri)


def _restore_uploads(zip_file: Path, upload_dir: Path) -> None:
    # Extraction durcie : refuse toute entrée qui écrirait hors du dossier cible.
    _restaurer_uploads(zip_file, upload_dir)


def main() -> int:
    parser = argparse.ArgumentParser(description="Restauration instance")
    parser.add_argument("--db", required=True, help="Chemin .db (SQLite) ou .sql (PostgreSQL)")
    parser.add_argument("--uploads", required=True, help="Chemin du zip uploads")
    args = parser.parse_args()

    db_path, up_path = Path(args.db), Path(args.uploads)
    if not db_path.is_file() or not up_path.is_file():
        raise RuntimeError("Fichiers de sauvegarde introuvables.")
    from config import Config
    db_uri = Config.SQLALCHEMY_DATABASE_URI
    sqlite = db_uri.startswith("sqlite:///") and db_path.suffix.lower() == ".db"
    postgres = db_uri.startswith("postgresql") and db_path.suffix.lower() == ".sql"
    if not (sqlite or postgres):
        raise RuntimeError("Incohérence entre type DB courant et fichier fourni.")

    # Aucun create_app ici : il migrerait et modifierait la destination AVANT
    # de découvrir une archive corrompue. Valider les fichiers en premier.
    from app.services.instance_archive import stage_archive, install_staged, remap_paths
    from app.services.sauvegarde import creer_sauvegarde, _verifier_db_sqlite
    from flask import Flask
    from app.extensions import db
    with tempfile.TemporaryDirectory(prefix="mcs-restore-cli-") as staging:
        manifest = stage_archive(up_path, staging)
        if sqlite and not _verifier_db_sqlite(db_path)[0]:
            raise RuntimeError("Base SQLite invalide : restauration annulée.")
        app = Flask("restore-offline", instance_path=Config.INSTANCE_DIR)
        app.config.from_object(Config)
        db.init_app(app)
        with app.app_context():
            securite = creer_sauvegarde()
            if sqlite:
                _restaurer_sqlite(db_path, db_uri)
            else:
                _restore_postgres(db_path, db_uri)
            roots = {"instance": app.instance_path, "uploads": app.config["APP_UPLOAD_DIR"]}
            install_staged(staging, roots)
            if manifest:
                with db.engine.begin() as connection:
                    remap_paths(connection, manifest["roots"], roots)
            db.session.remove()
            db.engine.dispose()
    # Les migrations portent maintenant sur la base restaurée. En cas d'échec,
    # le lot de sécurité reste conservé et le service doit rester arrêté.
    create_app()

    print("Restauration terminée. Lot de sécurité conservé : " + securite["base"])
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        print(f"ERREUR: {e}")
        raise SystemExit(1)
