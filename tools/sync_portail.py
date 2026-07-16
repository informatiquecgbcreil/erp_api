#!/usr/bin/env python3
"""Synchronise les résultats du portail des apprenants (scores/durées).

À planifier (tâche planifiée Windows / cron), comme la sauvegarde et la
publication. En cas d'erreur réseau, le script échoue proprement (code 1)
sans rien corrompre : le curseur n'est pas avancé et le prochain passage
réessaiera.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import create_app
from app.services.portail_apprenants import synchroniser_attempts


def main() -> int:
    app = create_app()
    with app.app_context():
        resume = synchroniser_attempts()
        print("Synchro portail ✅")
        print(f"- Reçues : {resume['recues']}")
        print(f"- Nouvelles : {resume['nouvelles']}")
        print(f"- Ignorées : {resume['ignorees']}")
        print(f"- Prochain since : {resume['since']}")
        return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        print(f"ERREUR: {e}")
        raise SystemExit(1)
