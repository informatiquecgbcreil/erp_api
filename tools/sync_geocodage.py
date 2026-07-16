#!/usr/bin/env python3
"""Géocode les adresses des participants (carte des habitants).

À planifier (tâche planifiée Windows / cron), comme la synchro portail et la
publication. Interroge la Base Adresse Nationale (gratuite, sans clé). En cas
d'erreur réseau, le script s'arrête proprement sans rien corrompre : les
participants non traités seront repris au prochain passage. L'adresse reste
facultative : ceux qui n'en ont pas restent simplement « non localisés ».

Usage : python tools/sync_geocodage.py [limite]
(limite = nombre maximum d'adresses à traiter sur ce passage ; défaut : tout)
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import create_app
from app.services.geocodage import (
    nombre_a_geocoder,
    nombre_a_geocoder_partenaires,
    synchroniser_geocodages,
    synchroniser_geocodages_partenaires,
)


def main() -> int:
    limite = None
    if len(sys.argv) > 1:
        try:
            limite = int(sys.argv[1])
        except ValueError:
            limite = None

    app = create_app()
    code = 0
    with app.app_context():
        # Par défaut, on traite tout ce qui reste en un passage (pas de timeout web).
        for libelle, compter, synchro in (
            ("Habitants", nombre_a_geocoder, synchroniser_geocodages),
            ("Partenaires", nombre_a_geocoder_partenaires, synchroniser_geocodages_partenaires),
        ):
            total = limite if limite is not None else max(1, compter())
            resume = synchro(limit=total)
            print(f"Géocodage {libelle} ✅")
            print(f"- Localisés : {resume['localises']}")
            print(f"- Sans coordonnées : {resume['non_localises']}")
            print(f"- Restants : {resume['restants']}")
            if resume["erreurs"]:
                print(f"- Interrompu (réseau) : {resume['erreur']}")
                code = 1
    return code


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:  # noqa: BLE001
        print(f"ERREUR: {e}")
        raise SystemExit(1)
