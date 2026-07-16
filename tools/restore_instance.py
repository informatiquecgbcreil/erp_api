#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import create_app
from app.services.sauvegarde import extraire_zip_securisee


def _restore_sqlite(src_db: Path, db_uri: str) -> None:
    dst = Path(db_uri.replace("sqlite:///", "", 1))
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_db, dst)


def _restore_postgres(src_sql: Path, db_uri: str) -> None:
    cmd = ["psql", db_uri, "-f", str(src_sql)]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise RuntimeError("psql a échoué. Vérifiez l'accès DB et l'installation de psql.")


def _restore_uploads(zip_file: Path, upload_dir: Path) -> None:
    # Extraction durcie : refuse toute entrée qui écrirait hors du dossier cible.
    extraire_zip_securisee(zip_file, upload_dir)


def main() -> int:
    parser = argparse.ArgumentParser(description="Restauration instance")
    parser.add_argument("--db", required=True, help="Chemin .db (SQLite) ou .sql (PostgreSQL)")
    parser.add_argument("--uploads", required=True, help="Chemin du zip uploads")
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        db_uri = app.config.get("SQLALCHEMY_DATABASE_URI", "")
        upload_dir = Path(app.config.get("APP_UPLOAD_DIR"))

        db_path = Path(args.db)
        up_path = Path(args.uploads)

        if not db_path.exists() or not up_path.exists():
            raise RuntimeError("Fichiers de sauvegarde introuvables.")

        if db_uri.startswith("sqlite:///") and db_path.suffix == ".db":
            _restore_sqlite(db_path, db_uri)
        elif db_uri.startswith("postgresql://") and db_path.suffix == ".sql":
            _restore_postgres(db_path, db_uri)
        else:
            raise RuntimeError("Incohérence entre type DB courant et fichier fourni.")

        _restore_uploads(up_path, upload_dir)

    print("Restauration terminée ✅")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        print(f"ERREUR: {e}")
        raise SystemExit(1)
