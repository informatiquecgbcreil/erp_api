"""Registre des anonymisations et suppressions, hors base et hors sauvegardes.

Mineur RGPD de l'audit : restaurer une sauvegarde d'avant une anonymisation
faisait réapparaître la personne, sans que personne ne s'en aperçoive. Chaque
fiche anonymisée ou supprimée est donc notée dans un registre placé dans le
dossier ``runtime`` des données de l'installation (APP_DATA_DIR, défini par
l'installateur Windows), que les sauvegardes ne contiennent pas — même
emplacement que le registre des numéros de reçus et factures.

Après une restauration, ``reappliquer`` anonymise de nouveau les fiches
concernées et le dit (journal + message à l'écran). Une fiche supprimée
entre-temps est anonymisée, pas supprimée : l'effacement complet reste une
décision humaine, prise depuis la fiche.

Sans APP_DATA_DIR (installation manuelle), le registre n'est pas tenu :
limite documentée.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

CATEGORIES = ("anonymises", "supprimes")


def chemin() -> Path | None:
    racine = os.environ.get("APP_DATA_DIR")
    return Path(racine) / "runtime" / "effacements.json" if racine else None


def _empreinte(participant) -> dict:
    """Identifiant ET date de création : SQLite peut réattribuer l'identifiant
    d'une fiche supprimée à une nouvelle personne ; sans la date, une
    restauration pourrait anonymiser la mauvaise personne."""
    cree = getattr(participant, "created_at", None)
    return {"id": int(participant.id), "cree_le": cree.isoformat(timespec="seconds") if cree else None}


def lire() -> dict[str, list[dict]]:
    fichier = chemin()
    try:
        donnees = json.loads(fichier.read_text(encoding="utf-8")) if fichier and fichier.exists() else {}
    except (OSError, ValueError):
        donnees = {}
    return {c: [e for e in donnees.get(c, []) if isinstance(e, dict) and "id" in e] for c in CATEGORIES}


def noter(categorie: str, participant) -> None:
    fichier = chemin()
    if fichier is None or categorie not in CATEGORIES:
        return
    try:
        fichier.parent.mkdir(parents=True, exist_ok=True)
        donnees = lire()
        entree = _empreinte(participant)
        if entree in donnees[categorie]:
            return
        donnees[categorie].append(entree)
        temporaire = fichier.with_suffix(".tmp")
        temporaire.write_text(json.dumps(donnees, sort_keys=True), encoding="utf-8")
        temporaire.replace(fichier)
    except OSError:
        pass  # Le journal d'audit garde la trace de l'action.


def reappliquer() -> list[int]:
    """Après restauration : réanonymise les fiches revenues avec leur
    identité (même identifiant ET même date de création). Renvoie les
    identifiants traités (commit inclus)."""
    from app.extensions import db
    from app.models import Participant
    from app.services.audit import enregistrer
    from app.services.purge_rgpd import NOM_ANONYME, anonymiser_participant

    registre = lire()
    attendus = {}
    for categorie in CATEGORIES:
        for entree in registre[categorie]:
            attendus.setdefault(int(entree["id"]), {})[entree.get("cree_le")] = categorie
    if not attendus:
        return []
    traites = []
    for p in Participant.query.filter(Participant.id.in_(list(attendus)), Participant.nom != NOM_ANONYME).all():
        categorie = attendus[p.id].get(_empreinte(p)["cree_le"])
        if categorie is None:
            continue  # même numéro, autre personne : on n'y touche pas
        anonymiser_participant(p)
        enregistrer("rgpd.reapplique_apres_restauration", cible=f"participant #{p.id}", participant_id=p.id,
                    details={"motif": "supprimée" if categorie == "supprimes" else "anonymisée"})
        traites.append(p.id)
    db.session.commit()
    return traites
