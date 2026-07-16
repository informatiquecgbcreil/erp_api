#!/usr/bin/env python3
"""Vérifie qu'une sauvegarde est réellement restaurable (restauration à blanc).

Une sauvegarde qui existe n'est pas une sauvegarde qui marche : ce script
contrôle le lot le plus récent (ou celui passé en argument) sans jamais
toucher la base courante ni les fichiers du lot :

- lot complet (base + pièces jointes) ;
- empreintes sha256 ;
- archive des pièces jointes lisible et sans chemin piégé ;
- base restaurable : copie temporaire + contrôle d'intégrité + comptage des
  tables métier (SQLite), ou contrôle structurel du dump (PostgreSQL).

Usage :
    python tools/verify_backup.py             # vérifie le lot le plus récent
    python tools/verify_backup.py --base NOM  # vérifie un lot précis
    python tools/verify_backup.py --list      # liste les lots disponibles

Code de sortie : 0 si tout est bon, 1 sinon — utilisable dans une tâche
planifiée pour être alerté quand les sauvegardes cessent d'être fiables.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import create_app
from app.services.sauvegarde import lister_lots, verifier_lot


def main() -> int:
    parser = argparse.ArgumentParser(description="Vérification à blanc d'une sauvegarde")
    parser.add_argument("--base", help="Nom du lot à vérifier (défaut : le plus récent complet)")
    parser.add_argument("--list", action="store_true", help="Lister les lots disponibles et sortir")
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        lots = lister_lots()

        if args.list:
            if not lots:
                print("Aucune sauvegarde trouvée.")
                return 1
            for lot in lots:
                etat = "complet" if lot["complet"] else "incomplet"
                print(f"- {lot['base']}  ({etat}, {lot['taille_lisible']}, {lot['modifie']:%d/%m/%Y %H:%M})")
            return 0

        base = args.base
        if not base:
            complets = [l for l in lots if l["complet"]]
            if not complets:
                print("ERREUR : aucun lot complet (base + pièces jointes) à vérifier.")
                return 1
            base = complets[0]["base"]

        rapport = verifier_lot(base)
        print(f"Vérification du lot « {rapport['base']} » :")
        for c in rapport["controles"]:
            if c["ok"] is True:
                symbole = "✅"
            elif c["ok"] is None:
                symbole = "➖"
            else:
                symbole = "❌"
            print(f"  {symbole} {c['nom']} — {c['detail']}")

        if rapport["ok"]:
            print("Résultat : sauvegarde RESTAURABLE ✅")
            return 0
        print("Résultat : sauvegarde NON FIABLE ❌ — à refaire ou à investiguer.")
        return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:  # message lisible, code d'erreur exploitable
        print(f"ERREUR: {e}")
        raise SystemExit(1)
