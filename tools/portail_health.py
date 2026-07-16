#!/usr/bin/env python3
"""Vérifie la connexion au portail des apprenants.

GET {PORTAIL_BASE_URL}/api/health doit répondre {"ok": true}.
Pratique pour valider la configuration (URL + jeton) avant la mise en service.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import create_app
from app.services.portail_apprenants import health, portail_configure


def main() -> int:
    app = create_app()
    with app.app_context():
        if not portail_configure():
            print("Portail non configuré (renseignez PORTAIL_BASE_URL et PORTAIL_TOKEN).")
            return 1
        ok = health()
        print("Portail joignable ✅" if ok else "Réponse reçue mais ok != true ❌")
        return 0 if ok else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        print(f"ERREUR: {e}")
        raise SystemExit(1)
