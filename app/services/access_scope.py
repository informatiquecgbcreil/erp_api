"""Portée métier commune. Lire l'annuaire ne donne pas le droit d'agir partout."""
from flask import abort
from flask_login import current_user
from sqlalchemy import exists, or_, false

from app.rbac import can
from app.models import Participant, PresenceActivite, SessionActivite


def own_sector():
    return (getattr(current_user, "secteur_assigne", None) or "").strip()


def sector_allowed(sector):
    return bool(can("scope:all_secteurs") or (own_sector() and own_sector() == (sector or "").strip()))


def participant_filter(sector=None):
    """Filtre SQL réutilisable : création ou présence effective dans le secteur."""
    if sector is None:
        if can("scope:all_secteurs"):
            from sqlalchemy import true
            return true()
        sector = own_sector()
    if not sector:
        return false()
    presence = exists().where(
        PresenceActivite.participant_id == Participant.id,
        PresenceActivite.session_id == SessionActivite.id,
        SessionActivite.secteur == sector,
        SessionActivite.is_deleted.is_(False),
    )
    return or_(Participant.created_secteur == sector, presence)


def participant_allowed(participant):
    if can("scope:all_secteurs"):
        return True
    return bool(Participant.query.filter(Participant.id == participant.id, participant_filter()).first())


def require_participant(participant):
    if not participant_allowed(participant):
        abort(403)


def require_sector(sector):
    if not sector_allowed(sector):
        abort(403)


def effective_sector(requested=None):
    if can("scope:all_secteurs"):
        return requested
    sector = own_sector()
    if not sector:
        abort(403)
    return sector


def sector_filter(column):
    from sqlalchemy import true
    return true() if can("scope:all_secteurs") else column == effective_sector()
