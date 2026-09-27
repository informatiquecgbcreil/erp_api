"""Portée métier commune. Lire l'annuaire ne donne pas le droit d'agir partout."""
from flask import abort
from flask_login import current_user
from sqlalchemy import exists, or_, false

from app.rbac import can
from app.models import ORIGINE_KIOSQUE, InscriptionActivite, Participant, PresenceActivite, SessionActivite


def own_sector():
    return (getattr(current_user, "secteur_assigne", None) or "").strip()


def sector_allowed(sector):
    return bool(can("scope:all_secteurs") or (own_sector() and own_sector() == (sector or "").strip()))


def participant_filter(sector=None, *, valide=False):
    """Filtre SQL réutilisable : fiche créée par le secteur, ou présence
    effective dans une de ses séances.

    ``valide=False`` (lecture) : toute présence compte, y compris posée par la
    personne elle-même au kiosque.
    ``valide=True`` (agir sur la fiche) : seule compte une présence posée par
    le personnel, ou une présence de kiosque d'une personne inscrite à
    l'atelier. Une présence de kiosque (page publique, sans compte) ne peut
    pas ouvrir à un secteur le droit de modifier la fiche d'un autre.
    """
    if sector is None:
        if can("scope:all_secteurs"):
            from sqlalchemy import true
            return true()
        sector = own_sector()
    if not sector:
        return false()
    conditions = [
        PresenceActivite.participant_id == Participant.id,
        PresenceActivite.session_id == SessionActivite.id,
        SessionActivite.secteur == sector,
        SessionActivite.is_deleted.is_(False),
    ]
    if valide:
        inscrit = exists().where(
            InscriptionActivite.participant_id == Participant.id,
            InscriptionActivite.atelier_id == SessionActivite.atelier_id,
            InscriptionActivite.statut == "inscrit",
        )
        conditions.append(or_(
            PresenceActivite.origine.is_(None),
            PresenceActivite.origine != ORIGINE_KIOSQUE,
            inscrit,
        ))
    return or_(Participant.created_secteur == sector, exists().where(*conditions))


def participant_allowed(participant, *, valide=False):
    if can("scope:all_secteurs"):
        return True
    return bool(Participant.query.filter(
        Participant.id == participant.id, participant_filter(valide=valide)
    ).first())


def participant_destructible(participant):
    """Supprimer ou anonymiser : portée structure, ou secteur qui a créé la fiche.

    Une présence, même validée, ne suffit jamais : ces actions sont
    irréversibles et touchent une personne suivie ailleurs.
    """
    if can("scope:all_secteurs"):
        return True
    secteur = own_sector()
    return bool(secteur) and (participant.created_secteur or "").strip() == secteur


def require_participant(participant):
    """Agir sur une fiche (évaluer, suivre, inscrire…) : présence validée requise."""
    if not participant_allowed(participant, valide=True):
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
