"""Ce qui compte comme une venue dans les chiffres remis aux financeurs.

Une présence ne compte que si la personne est VENUE (présent ou en retard) :
une absence excusée atteste justement du contraire. Une séance ne compte
que si elle a été TENUE : ni annulée, ni encore à venir.

Ces règles étaient appliquées par la répartition des participations
(prorata) mais pas par SENACS, les indicateurs de projet, les statistiques
d'impact, les bilans ni le coût unitaire : réalité 3 personnes et 5
présences, SENACS annonçait 6 et 8. Tous ces calculs passent désormais par
ici.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import and_, func, or_

from app.models import PresenceActivite, SessionActivite

#: Statuts de présence qui attestent une venue réelle.
VENUES_REELLES = ("present", "retard")


def venue_reelle():
    """Clause SQL : la présence atteste une venue (pas une absence excusée)."""
    return PresenceActivite.presence_type.in_(VENUES_REELLES)


def date_seance():
    """Date métier de la séance : rendez-vous (individuel) sinon date collective."""
    return func.coalesce(SessionActivite.rdv_date, SessionActivite.date_session)


def seance_non_annulee():
    return func.lower(func.coalesce(SessionActivite.statut, "")) != "annulee"


def seance_tenue(aujourd_hui: date | None = None):
    """Clause SQL : séance non annulée et déjà passée (ou sans date connue)."""
    jour = date_seance()
    return and_(
        seance_non_annulee(),
        or_(jour.is_(None), jour <= (aujourd_hui or date.today())),
    )


def est_venue_reelle(presence) -> bool:
    return (presence.presence_type or "present") in VENUES_REELLES


def est_seance_tenue(session, aujourd_hui: date | None = None) -> bool:
    if (session.statut or "").strip().lower() == "annulee":
        return False
    jour = session.rdv_date or session.date_session
    return jour is None or jour <= (aujourd_hui or date.today())
