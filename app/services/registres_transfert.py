"""Registres dans les sauvegardes, les transferts et après un sinistre.

Les registres (numéros émis, effacements RGPD) vivent hors des sauvegardes
ordinaires : restaurer une sauvegarde ancienne ne doit pas les faire
revenir en arrière. Cela protège la restauration sur la même installation,
pas la perte du serveur. D'où une COPIE des registres dans chaque lot de
sauvegarde (``<lot>_registres.json``), copiée hors serveur avec le lot, et
un export/import manuel pour le transfert vers une nouvelle machine.

Règle unique : une copie des registres n'ÉCRASE jamais rien, elle se
FUSIONNE — plus grand numéro par série, et pour les effacements l'union des
entrées (une décision l'emporte sur « à vérifier », la plus récente entre
deux décisions). Restaurer un lot ancien ne peut donc rien faire reculer ;
restaurer un lot sur une machine neuve rapporte les registres tels qu'ils
étaient au moment de ce lot.

Limite (dite, pas contournée) : après la perte complète du serveur, ce qui
a été émis ou effacé APRÈS la dernière copie survivante n'existe plus nulle
part. L'application ne peut pas le reconstituer. Pour les numéros, Contrôle
→ Registres permet de déclarer le dernier numéro émis connu (d'après les
reçus et factures papier) : la numérotation repart au-delà. Pour les
effacements, il faut rejouer les demandes d'effacement reçues depuis (registre
des demandes RGPD de la structure).

La copie porte sa propre empreinte ; elle n'est pas ajoutée au fichier
``.sha256`` du lot, qu'une version précédente de l'application (retour
arrière) refuserait sinon.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from app.services import financial_sequence
from app.services import registre_effacements
from app.services.registre_externe import RegistreEndommage
from app.utils.dates import utcnow

FORMAT = 1
SUFFIXE = "_registres.json"
ESPACE_VALIDE = re.compile(r"^(don|facture|avoir):\d{4}$")


def _empreinte(corps: dict) -> str:
    return hashlib.sha256(json.dumps(corps, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def instantane() -> dict:
    """Registres complets à cet instant : fichiers hors base ET base."""
    from app.models import EffacementRgpd
    numeros = financial_sequence.fusionner(financial_sequence.lire_registre(),
                                           financial_sequence._compteurs_base())
    effacements = registre_effacements.lire_fichier()
    for ligne in EffacementRgpd.query.all():
        effacements[ligne.cle] = registre_effacements._gagnante(effacements.get(ligne.cle),
                                                               registre_effacements._entree(ligne))
    corps = {"format": FORMAT, "cree_le": utcnow().isoformat(timespec="seconds"),
             "numeros": numeros, "effacements": effacements}
    return {**corps, "empreinte": _empreinte(corps)}


def valider(donnees) -> dict:
    if not isinstance(donnees, dict) or donnees.get("format") != FORMAT:
        raise RegistreEndommage("Ce fichier n'est pas une copie des registres de Mon Centre Social.", "format")
    corps = {k: v for k, v in donnees.items() if k != "empreinte"}
    if donnees.get("empreinte") != _empreinte(corps):
        raise RegistreEndommage("La copie des registres est altérée (empreinte différente).", "empreinte")
    if not isinstance(donnees.get("numeros"), dict) or not isinstance(donnees.get("effacements"), dict):
        raise RegistreEndommage("La copie des registres est incomplète.", "contenu")
    registre_effacements._valider({"format": registre_effacements.FORMAT, "entrees": donnees["effacements"]})
    return donnees


def lire(chemin: Path) -> dict:
    try:
        return valider(json.loads(chemin.read_text(encoding="utf-8")))
    except (OSError, ValueError) as exc:
        raise RegistreEndommage("La copie des registres est illisible.", exc.__class__.__name__) from exc


def fusionner(donnees: dict) -> dict:
    """Fusionne une copie validée dans les registres de l'installation (et
    relève les compteurs en base, jamais à la baisse). Puis réapplique les
    effacements confirmés. Rapport : {numeros, effacements, reappliques}."""
    from app.extensions import db
    from app.models import FinancialSequence
    donnees = valider(donnees)
    numeros = {k: int(v) for k, v in donnees["numeros"].items() if isinstance(v, int) and v >= 0}
    financial_sequence.noter_maxima(numeros)
    for espace, valeur in numeros.items():
        ligne = FinancialSequence.query.filter_by(namespace=espace).first()
        if ligne is None:
            db.session.add(FinancialSequence(namespace=espace, value=valeur))
        elif int(ligne.value or 0) < valeur:
            ligne.value = valeur
    db.session.commit()
    registre_effacements.fusionner_dans_fichier(donnees["effacements"])
    reappliques = registre_effacements.reappliquer()
    return {"numeros": len(numeros), "effacements": len(donnees["effacements"]), "reappliques": reappliques}


def ecrire_pour_lot(dossier: Path, base: str) -> Path:
    chemin = dossier / f"{base}{SUFFIXE}"
    provisoire = chemin.with_name(chemin.name + ".part")
    provisoire.write_text(json.dumps(instantane(), sort_keys=True, ensure_ascii=False, indent=1), encoding="utf-8")
    provisoire.replace(chemin)
    return chemin


def declarer_dernier_numero(espace: str, numero: int) -> None:
    """Après un sinistre : le dernier numéro émis connu (papier) devient un
    plancher. Jamais à la baisse. Commit inclus."""
    from app.extensions import db
    from app.models import FinancialSequence
    if not ESPACE_VALIDE.match(espace or "") or numero < 1:
        raise ValueError("Série ou numéro invalide.")
    financial_sequence.noter_maxima({espace: int(numero)})
    ligne = FinancialSequence.query.filter_by(namespace=espace).first()
    if ligne is None:
        db.session.add(FinancialSequence(namespace=espace, value=int(numero)))
    elif int(ligne.value or 0) < numero:
        ligne.value = int(numero)
    db.session.commit()
