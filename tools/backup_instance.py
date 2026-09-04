#!/usr/bin/env python3
"""Sauvegarde complète de l'instance : base + pièces jointes, rotation, copie hors serveur.

Script appelé par la tâche planifiée quotidienne (Windows :
``AppGestionBackupDaily``, Linux : ``deploy/linux/cron/backup.cron``).

Déroulé :
1. création du lot (dump base + archive des pièces jointes + empreintes) ;
2. rotation locale (``BACKUP_RETENTION_LOTS`` lots conservés) ;
3. copie vers chaque destination ``BACKUP_OFFSITE_DIRS``, empreintes
   revérifiées à l'arrivée, puis rotation à la destination.

Code de sortie : 0 si tout est bon, 1 si la sauvegarde a échoué **ou** si une
copie hors serveur n'a pas abouti — la tâche planifiée remonte alors une
erreur, seule façon de savoir qu'on a cessé d'être protégé.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import create_app
from app.services.sauvegarde import (
    copier_lot_hors_serveur,
    creer_sauvegarde,
    destinations_hors_serveur,
    dossier_sauvegardes,
    nettoyer_sauvegardes,
)


def main() -> int:
    app = create_app()
    with app.app_context():
        info = creer_sauvegarde()
        out_dir = dossier_sauvegardes()
        print("Sauvegarde créée ✅")
        print(f"- Base: {out_dir / info['db_fichier']}")
        print(f"- Uploads: {out_dir / info['uploads_fichier']}")

        supprimes = nettoyer_sauvegardes()
        if supprimes:
            print(f"- Rotation: {supprimes} fichier(s) ancien(s) purgé(s)")

        if not destinations_hors_serveur():
            print(
                "\n⚠️  AUCUNE COPIE HORS SERVEUR : les sauvegardes restent sur la machine\n"
                "   qu'elles protègent. Renseignez BACKUP_OFFSITE_DIRS dans le fichier .env\n"
                "   (voir README, section « Sauvegarde et restauration »)."
            )
            return 0

        echecs = 0
        print("\nCopies hors serveur :")
        for rapport in copier_lot_hors_serveur(info["base"]):
            symbole = "✅" if rapport["ok"] else "❌"
            print(f"  {symbole} {rapport['destination']} — {rapport['detail']}")
            if rapport["purges"]:
                print(f"     rotation : {rapport['purges']} fichier(s) purgé(s)")
            if not rapport["ok"]:
                echecs += 1
        if echecs:
            print(f"\n❌ {echecs} destination(s) hors serveur en échec — sauvegarde NON sécurisée.")
            return 1
        return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        print(f"ERREUR: {e}")
        raise SystemExit(1)
