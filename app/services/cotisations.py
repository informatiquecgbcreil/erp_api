"""Adhésions, participation, foyers : logique métier partagée.

Règles :
- l'année scolaire court de septembre à août ; elle est identifiée par
  son année de rentrée (2026 -> libellé « 2026-2027 ») ;
- le tarif « en vigueur » à une date donnée est la ligne de barème la plus
  récente (par date de début) qui ne dépasse pas cette date — permet de
  faire évoluer un prix en cours d'année (ex. participation moins chère
  en fin d'année scolaire) ;
- le montant dû d'une cotisation est figé à sa création (photo du tarif à
  la date de référence) : un changement de barème plus tard ne modifie
  jamais rétroactivement une dette déjà enregistrée.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from app.extensions import db
from app.models import Cotisation, Foyer, Participant, TarifBareme


def annee_scolaire_de(d: date) -> int:
    """Année de rentrée de la date donnée (septembre à août)."""
    return d.year if d.month >= 9 else d.year - 1


def annee_scolaire_courante() -> int:
    return annee_scolaire_de(date.today())


def libelle_annee_scolaire(annee: int) -> str:
    return f"{annee}-{annee + 1}"


def annees_scolaires_disponibles() -> list[int]:
    """Les années scolaires pour lesquelles il existe déjà des données,
    plus l'année courante — pour peupler les sélecteurs."""
    annees = {annee_scolaire_courante()}
    for (a,) in db.session.query(TarifBareme.annee_scolaire).distinct():
        annees.add(a)
    for (a,) in db.session.query(Cotisation.annee_scolaire).distinct():
        annees.add(a)
    return sorted(annees, reverse=True)


def tarif_en_vigueur(annee_scolaire: int, type_tarif: str, a_la_date: date | None = None) -> Optional[TarifBareme]:
    """La ligne de barème applicable à la date donnée (par défaut aujourd'hui)."""
    a_la_date = a_la_date or date.today()
    return (
        TarifBareme.query
        .filter(
            TarifBareme.annee_scolaire == annee_scolaire,
            TarifBareme.type_tarif == type_tarif,
            TarifBareme.date_debut <= a_la_date,
        )
        .order_by(TarifBareme.date_debut.desc())
        .first()
    )


def bareme_annee(annee_scolaire: int) -> dict[str, list[TarifBareme]]:
    """Toutes les lignes de barème de l'année, groupées par type, triées
    par date de début (la plus récente d'abord)."""
    lignes = (
        TarifBareme.query
        .filter(TarifBareme.annee_scolaire == annee_scolaire)
        .order_by(TarifBareme.date_debut.desc())
        .all()
    )
    groupes: dict[str, list[TarifBareme]] = {"adhesion_individuelle": [], "adhesion_familiale": [], "participation": []}
    for ligne in lignes:
        groupes.setdefault(ligne.type_tarif, []).append(ligne)
    return groupes


def cotisation_existante(*, annee_scolaire: int, type_cotisation: str,
                         participant_id: int | None = None, foyer_id: int | None = None) -> Cotisation | None:
    q = Cotisation.query.filter(
        Cotisation.annee_scolaire == annee_scolaire,
        Cotisation.type_cotisation == type_cotisation,
    )
    if participant_id is not None:
        q = q.filter(Cotisation.participant_id == participant_id)
    if foyer_id is not None:
        q = q.filter(Cotisation.foyer_id == foyer_id)
    return q.first()


def cotisations_du_participant(participant: Participant, *, inclure_foyer: bool = True) -> list[Cotisation]:
    """Cotisations propres au participant + (si demandé) celles de son
    foyer (adhésion familiale), triées année décroissante puis type."""
    items = list(Cotisation.query.filter(Cotisation.participant_id == participant.id).all())
    if inclure_foyer and participant.foyer_id:
        items += list(Cotisation.query.filter(Cotisation.foyer_id == participant.foyer_id).all())
    ordre_type = {"adhesion_individuelle": 0, "adhesion_familiale": 0, "participation": 1}
    items.sort(key=lambda c: (-c.annee_scolaire, ordre_type.get(c.type_cotisation, 9)))
    return items


def foyer_membres_autres(participant: Participant) -> list[Participant]:
    """Les autres membres du foyer du participant (liste vide si aucun foyer)."""
    if not participant.foyer_id:
        return []
    return [
        m for m in Participant.query.filter(Participant.foyer_id == participant.foyer_id).all()
        if m.id != participant.id
    ]


def rapprocher_foyer(a: Participant, b: Participant) -> tuple[bool, str]:
    """Rattache b au foyer de a (ou l'inverse), en créant un foyer si besoin.

    Refuse si les deux appartiennent déjà à des foyers DIFFÉRENTS non vides
    (fusion non gérée automatiquement, pour ne pas mélanger silencieusement
    des adhésions familiales déjà réglées de deux familles distinctes)."""
    if a.id == b.id:
        return False, "Impossible de rapprocher une personne avec elle-même."
    if a.foyer_id and b.foyer_id and a.foyer_id != b.foyer_id:
        return False, (
            "Ces deux personnes appartiennent déjà à des foyers différents. "
            "Retire d'abord l'une d'elles de son foyer actuel avant de les rapprocher."
        )
    foyer = None
    if a.foyer_id:
        foyer = a.foyer
    elif b.foyer_id:
        foyer = b.foyer
    if foyer is None:
        foyer = Foyer(nom=f"Famille {a.nom}")
        db.session.add(foyer)
        db.session.flush()
    a.foyer_id = foyer.id
    b.foyer_id = foyer.id
    db.session.commit()
    return True, f"{b.nom} {b.prenom} est maintenant rapproché·e du foyer de {a.nom} {a.prenom}."


def regrouper_en_foyer(membres: list[Participant]) -> tuple[bool, str]:
    """Regroupe plusieurs personnes dans un même foyer (famille).

    Même règle de prudence que ``rapprocher_foyer`` : si la sélection couvre
    déjà DEUX foyers différents, on refuse (fusionner deux familles peut
    mélanger des adhésions familiales déjà réglées — l'agent doit d'abord
    détacher les personnes concernées). Un seul foyer existant : tout le
    monde le rejoint. Aucun foyer : on en crée un.
    """
    membres = [m for m in membres if m is not None]
    if len(membres) < 2:
        return False, "Sélectionne au moins deux personnes pour former une famille."

    foyers_existants = {m.foyer_id for m in membres if m.foyer_id}
    if len(foyers_existants) > 1:
        return False, (
            "La sélection contient des personnes de plusieurs familles différentes. "
            "Détache d'abord celles qui changent de famille (depuis leur fiche, "
            "partie Adhésion), puis recommence."
        )

    if foyers_existants:
        foyer = db.session.get(Foyer, foyers_existants.pop())
    else:
        foyer = Foyer(nom=f"Famille {membres[0].nom}")
        db.session.add(foyer)
        db.session.flush()

    nouveaux = 0
    for m in membres:
        if m.foyer_id != foyer.id:
            m.foyer_id = foyer.id
            nouveaux += 1
    db.session.commit()

    noms = ", ".join(f"{m.prenom} {m.nom}" for m in membres[:4])
    if len(membres) > 4:
        noms += f" (+{len(membres) - 4})"
    return True, f"Famille « {foyer.nom or 'sans nom'} » : {len(membres)} membres ({noms})."


def detacher_du_foyer(participant: Participant) -> None:
    """Retire le participant de son foyer (ne supprime pas les autres membres)."""
    foyer_id = participant.foyer_id
    participant.foyer_id = None
    db.session.commit()
    if foyer_id is not None:
        autres = Participant.query.filter(Participant.foyer_id == foyer_id).count()
        if autres == 0:
            foyer = db.session.get(Foyer, foyer_id)
            if foyer is not None and not foyer.cotisations:
                db.session.delete(foyer)
                db.session.commit()
