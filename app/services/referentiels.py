"""Tenir propres les référentiels de territoire : villes, quartiers, QPV.

Trois maux constatés, qui se tiennent :

1. **La même ville sous plusieurs écritures.** Onze écrans saisissent la
   ville en texte libre. « Nogent sur Oise », « Nogent-sur-Oise », « NOGENT
   SUR OISE » et « Nogent » comptaient pour quatre villes.

2. **La liste des quartiers qui se vide sans rien dire.** Elle est filtrée
   par égalité de chaîne avec la ville tapée. Une variante d'écriture et
   plus aucun quartier n'apparaît — l'accueil en conclut qu'il n'y en a
   pas, et laisse le champ vide.

3. **Le QPV codé dans le nom du quartier.** Faute de champ dédié, il
   fallait écrire « Rouher (QPV Hauts de Creil) » pour que les exports
   retrouvent l'appartenance par recherche de sous-chaîne — avec deux
   règles différentes selon l'export, et aucune des deux ne reconnaissant
   « Cavée de Senlis ».

Ce module apporte la déduction (reprendre ce que les noms disent déjà), la
normalisation (ramener les variantes d'écriture à une forme), et la
**fusion** — le geste qui manquait vraiment. Supprimer un quartier
doublonné était refusé dès qu'une fiche y était rattachée ; il n'y avait
donc aucun moyen de nettoyer.
"""
from __future__ import annotations

import re

from app.extensions import db
from app.models import Participant, Quartier
from app.services.villes import cle as cle_ville
from app.services.villes import normaliser as normaliser_ville

#: QPV connus du territoire, tels qu'ils sont nommés dans les conventions.
#: Sert UNIQUEMENT à relire les noms déjà saisis ; la liste n'est pas
#: fermée, un QPV se saisit librement sur la fiche du quartier.
QPV_CONNUS = ("Hauts de Creil",)

#: « Rouher (QPV Hauts de Creil) » -> « Hauts de Creil ».
_QPV_ENTRE_PARENTHESES = re.compile(r"\(\s*qpv\s+([^)]+)\)", re.IGNORECASE)


def deduire_qpv_depuis_le_nom(nom: str | None, is_qpv: bool = False) -> str | None:
    """Ce que le nom d'un quartier dit déjà de son QPV.

    Utilisé une fois, à la reprise : ensuite le champ fait foi. On ne
    devine que ce qui est écrit — un quartier coché « QPV » sans nom de
    QPV lisible reste sans QPV nommé, et c'est à quelqu'un de le dire.
    """
    brut = (nom or "").strip()
    if not brut:
        return None

    entre_parentheses = _QPV_ENTRE_PARENTHESES.search(brut)
    if entre_parentheses:
        return entre_parentheses.group(1).strip() or None

    minuscule = brut.lower()
    for connu in QPV_CONNUS:
        if connu.lower() in minuscule:
            return connu
    # « Rouher » désigne historiquement un quartier des Hauts de Creil.
    if is_qpv and "rouher" in minuscule:
        return "Hauts de Creil"
    return None


def nom_sans_qpv(nom: str | None) -> str:
    """Retire la mention « (QPV …) » devenue inutile une fois le champ posé."""
    propre = _QPV_ENTRE_PARENTHESES.sub("", (nom or "")).strip()
    return re.sub(r"\s{2,}", " ", propre).strip(" -–") or (nom or "").strip()


# ---------------------------------------------------------------------------
# Villes : uniformiser l'écriture
# ---------------------------------------------------------------------------

#: Tables et colonnes portant un nom de commune saisi librement.
COLONNES_VILLE = (
    ("participant", "ville"),
    ("quartier", "ville"),
    ("inscription_annuelle", "ville"),
    ("preneur", "ville"),
)


def normaliser_villes_existantes(bind) -> dict[str, int]:
    """Ramène chaque écriture d'une ville à sa forme d'état civil.

    Ne rapproche JAMAIS deux communes différentes : seules les variantes
    prouvables (casse, accents, traits d'union, « St » pour « Saint », code
    postal collé) sont touchées. « Nogent » reste « Nogent » — c'est à la
    fusion, faite par quelqu'un qui connaît le territoire, de décider s'il
    s'agit de Nogent-sur-Oise.
    """
    import sqlalchemy as sa

    inspecteur = sa.inspect(bind)
    tables = set(inspecteur.get_table_names())
    touchees: dict[str, int] = {}

    for table, colonne in COLONNES_VILLE:
        if table not in tables:
            continue
        if colonne not in {c["name"] for c in inspecteur.get_columns(table)}:
            continue
        distinctes = [
            ligne[0]
            for ligne in bind.execute(
                sa.text(f"SELECT DISTINCT {colonne} FROM {table} WHERE {colonne} IS NOT NULL")
            )
        ]
        for ancienne in distinctes:
            propre = normaliser_ville(ancienne)
            if propre is None or propre == ancienne:
                continue
            resultat = bind.execute(
                sa.text(f"UPDATE {table} SET {colonne} = :propre WHERE {colonne} = :ancienne"),
                {"propre": propre, "ancienne": ancienne},
            )
            touchees[f"{table}.{ancienne}"] = resultat.rowcount or 0
    return touchees


def villes_utilisees() -> list[dict]:
    """Les villes réellement présentes, avec de quoi décider d'une fusion.

    Chaque entrée : ``{nom, participants, quartiers, forme_propre,
    a_normaliser}``. C'est la matière de l'écran de nettoyage : on ne
    demande pas à quelqu'un de fusionner à l'aveugle, on lui montre combien
    de fiches sont derrière chaque écriture.
    """
    comptes: dict[str, dict] = {}

    lignes = (
        db.session.query(Participant.ville, db.func.count(Participant.id))
        .filter(Participant.ville.isnot(None))
        .filter(Participant.ville != "")
        .group_by(Participant.ville)
        .all()
    )
    for nom, nombre in lignes:
        comptes.setdefault(nom, {"nom": nom, "participants": 0, "quartiers": 0})
        comptes[nom]["participants"] += int(nombre or 0)

    for nom, nombre in (
        db.session.query(Quartier.ville, db.func.count(Quartier.id))
        .filter(Quartier.ville.isnot(None))
        .group_by(Quartier.ville)
        .all()
    ):
        comptes.setdefault(nom, {"nom": nom, "participants": 0, "quartiers": 0})
        comptes[nom]["quartiers"] += int(nombre or 0)

    resultat = []
    for entree in comptes.values():
        propre = normaliser_ville(entree["nom"])
        entree["forme_propre"] = propre
        entree["a_normaliser"] = bool(propre) and propre != entree["nom"]
        entree["cle"] = cle_ville(entree["nom"])
        resultat.append(entree)
    return sorted(resultat, key=lambda e: (-e["participants"], e["nom"].lower()))


def fusionner_villes(source: str, cible: str) -> dict[str, int]:
    """Rabat toutes les fiches d'une écriture de ville sur une autre.

    Le geste qui manquait pour « Nogent » -> « Nogent-sur-Oise » : aucun
    écran ne permettait de le faire, et corriger fiche par fiche n'est pas
    une option à quelques centaines de fiches.
    """
    source = (source or "").strip()
    cible = (cible or "").strip()
    if not source or not cible or source == cible:
        return {"participants": 0, "quartiers": 0}

    participants = (
        Participant.query.filter(Participant.ville == source)
        .update({Participant.ville: cible}, synchronize_session=False)
    )
    quartiers = (
        Quartier.query.filter(Quartier.ville == source)
        .update({Quartier.ville: cible}, synchronize_session=False)
    )
    db.session.commit()
    return {"participants": int(participants or 0), "quartiers": int(quartiers or 0)}


# ---------------------------------------------------------------------------
# Quartiers : fusionner les doublons
# ---------------------------------------------------------------------------

def doublons_de_quartiers() -> list[list[Quartier]]:
    """Groupes de quartiers qui désignent visiblement le même endroit.

    Rapprochement sur la ville normalisée ET le nom débarrassé de sa
    mention « (QPV …) », comparés sans accents ni casse : « Rouher (QPV
    Hauts de Creil) » et « ROUHER » forment un groupe.
    """
    from app.services.recherche_texte import sans_accent

    groupes: dict[tuple[str, str], list[Quartier]] = {}
    for quartier in Quartier.query.order_by(Quartier.id.asc()).all():
        signature = (
            cle_ville(quartier.ville),
            (sans_accent(nom_sans_qpv(quartier.nom)) or "").strip(),
        )
        groupes.setdefault(signature, []).append(quartier)
    return [liste for liste in groupes.values() if len(liste) > 1]


def fusionner_quartiers(source: Quartier, cible: Quartier) -> int:
    """Rattache les fiches de ``source`` à ``cible``, puis supprime ``source``.

    Supprimer un quartier était refusé dès qu'une fiche y était rattachée —
    il n'existait donc aucun moyen de nettoyer un doublon. La fusion déplace
    d'abord, supprime ensuite.

    La cible hérite de ce que la source savait et qu'elle ignorait (QPV,
    position sur la carte, description) : on ne perd pas d'information en
    nettoyant.
    """
    if source.id == cible.id:
        return 0

    deplaces = (
        Participant.query.filter(Participant.quartier_id == source.id)
        .update({Participant.quartier_id: cible.id}, synchronize_session=False)
    )

    if not cible.qpv and source.qpv:
        cible.qpv = source.qpv
    if source.is_qpv:
        cible.is_qpv = True
    if not cible.description and source.description:
        cible.description = source.description
    if cible.latitude is None and source.latitude is not None:
        cible.latitude = source.latitude
        cible.longitude = source.longitude
        cible.geo_manuel = source.geo_manuel

    db.session.delete(source)
    db.session.commit()
    return int(deplaces or 0)


def qpv_utilises() -> list[str]:
    """Les QPV déjà nommés, pour la liste de suggestion du formulaire."""
    noms = {
        (ligne[0] or "").strip()
        for ligne in db.session.query(Quartier.qpv).filter(Quartier.qpv.isnot(None)).distinct()
    }
    noms.discard("")
    return sorted(noms | set(QPV_CONNUS))
