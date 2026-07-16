#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import create_app
from app.services.sauvegarde import creer_sauvegarde, dossier_sauvegardes


def main() -> int:
    app = create_app()
    with app.app_context():
        info = creer_sauvegarde()
        out_dir = dossier_sauvegardes()
        print("Sauvegarde créée ✅")
        print(f"- Base: {out_dir / info['db_fichier']}")
        print(f"- Uploads: {out_dir / info['uploads_fichier']}")
        return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        print(f"ERREUR: {e}")
        raise SystemExit(1)
