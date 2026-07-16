#!/usr/bin/env python3
"""Publie le programme public sur l'hébergement (FTPS).

À planifier (tâche planifiée Windows / cron) pour une mise à jour automatique,
ou à lancer à la main.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import create_app
from app.services.publication_web import publier_programme


def main() -> int:
    app = create_app()
    with app.app_context():
        info = publier_programme()
        print("Programme publié ✅")
        print(f"- Fichier : {info['filename']} ({info['octets']} octets)")
        print(f"- Hôte : {info['hote']}")
        if info.get("url_publique"):
            print(f"- URL : {info['url_publique']}")
        return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        print(f"ERREUR: {e}")
        raise SystemExit(1)
