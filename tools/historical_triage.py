"""Triage d'un rapport de migration historique : propositions groupées, aucune écriture.

Exécuter depuis le dépôt : python -m tools.historical_triage --help

L'outil ne touche ni la base ni le classeur : il lit le rapport JSON produit par
``python -m tools.historical_import ... analyze`` et écrit des décisions à relire.
Les fichiers produits contiennent des données personnelles : accès privé.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from app.ateliers.historical_triage import (
    COLONNES_CSV, exporter_arbitrages, fusionner_arbitrages, rapport_markdown, trier,
)


def _lire_json(chemin):
    return json.loads(Path(chemin).read_text(encoding="utf-8-sig"))


def _ecrire(chemin, contenu):
    chemin = Path(chemin).resolve()
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(contenu, encoding="utf-8")
    return chemin


def _ecrire_csv(chemin, lignes):
    chemin = Path(chemin).resolve()
    chemin.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig et point-virgule : ouverture directe dans Excel francophone.
    with chemin.open("w", encoding="utf-8-sig", newline="") as sortie:
        writer = csv.DictWriter(sortie, fieldnames=COLONNES_CSV, delimiter=";")
        writer.writeheader()
        writer.writerows(lignes)
    return chemin


def _secteurs(rapport, demandes):
    if demandes:
        return [s.strip() for s in demandes.split(",") if s.strip()]
    if rapport.get("secteurs"):
        return list(rapport["secteurs"])
    from config import Config
    return list(Config.SECTEURS)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commandes = parser.add_subparsers(dest="commande", required=True)

    trie = commandes.add_parser("trier", help="Regrouper le rapport et proposer les décisions évidentes")
    trie.add_argument("--report", required=True, help="Rapport JSON d'analyse")
    trie.add_argument("--decisions", required=True, help="Décisions proposées, à relire puis rejouer")
    trie.add_argument("--markdown", help="Compte rendu lisible du triage")
    trie.add_argument("--csv", help="Arbitrages restants, à compléter au tableur")
    trie.add_argument("--secteurs", help="Libellés séparés par des virgules ; sinon référentiel du rapport")
    trie.add_argument("--sans-secteurs", action="store_true",
                      help="Ne proposer aucun secteur : les 66 affectations restent manuelles")

    fusion = commandes.add_parser("fusionner", help="Reprendre les arbitrages saisis dans le CSV")
    fusion.add_argument("--report", required=True)
    fusion.add_argument("--csv", required=True, help="CSV d'arbitrages complété")
    fusion.add_argument("--decisions", required=True, help="Décisions complètes en sortie")
    fusion.add_argument("--secteurs")
    fusion.add_argument("--sans-secteurs", action="store_true")

    args = parser.parse_args(argv)
    rapport = _lire_json(args.report)
    triage = trier(rapport, proposer_secteurs=not args.sans_secteurs,
                   secteurs_connus=_secteurs(rapport, args.secteurs))

    if args.commande == "trier":
        decisions, refusees = triage["decisions"], []
        if args.markdown:
            print("Compte rendu  : " + str(_ecrire(args.markdown, rapport_markdown(triage))))
        if args.csv:
            print("Arbitrages    : " + str(_ecrire_csv(args.csv, exporter_arbitrages(triage))))
    else:
        with Path(args.csv).open(encoding="utf-8-sig", newline="") as entree:
            lignes = list(csv.DictReader(entree, delimiter=";"))
        decisions, refusees = fusionner_arbitrages(triage, lignes)

    _ecrire(args.decisions, json.dumps(decisions, ensure_ascii=False, indent=2, sort_keys=True))
    print("Décisions     : " + str(Path(args.decisions).resolve()))
    print(json.dumps(triage["resume"], ensure_ascii=False, indent=2))
    for refus in refusees:
        print(f"  ligne {refus['ligne']} ignorée ({refus['cle']} = {refus['decision']}) : {refus['erreur']}")
    print("\nRelire les décisions, puis rejouer l'analyse avec --decisions avant tout apply.")
    return 1 if refusees else 0


if __name__ == "__main__":
    raise SystemExit(main())
