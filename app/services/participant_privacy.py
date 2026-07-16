from __future__ import annotations

from app.extensions import db
from app.models import Participant, PresenceActivite, SessionActivite


def participant_presence_secteurs(participant_id: int, *, include_deleted_sessions: bool = False) -> set[str]:
    q = (
        db.session.query(SessionActivite.secteur)
        .join(PresenceActivite, PresenceActivite.session_id == SessionActivite.id)
        .filter(PresenceActivite.participant_id == participant_id)
    )
    if not include_deleted_sessions:
        q = q.filter(SessionActivite.is_deleted.is_(False))
    return {s for (s,) in q.distinct().all() if s}


def participant_has_presence_in_secteur(participant_id: int, secteur: str, *, include_deleted_sessions: bool = False) -> bool:
    if not secteur:
        return False
    q = (
        db.session.query(PresenceActivite.id)
        .join(SessionActivite, PresenceActivite.session_id == SessionActivite.id)
        .filter(PresenceActivite.participant_id == participant_id)
        .filter(SessionActivite.secteur == secteur)
    )
    if not include_deleted_sessions:
        q = q.filter(SessionActivite.is_deleted.is_(False))
    return q.first() is not None


def participant_has_presence_outside_secteur(participant_id: int, secteur: str, *, include_deleted_sessions: bool = False) -> bool:
    q = (
        db.session.query(PresenceActivite.id)
        .join(SessionActivite, PresenceActivite.session_id == SessionActivite.id)
        .filter(PresenceActivite.participant_id == participant_id)
        .filter(SessionActivite.secteur != secteur)
    )
    if not include_deleted_sessions:
        q = q.filter(SessionActivite.is_deleted.is_(False))
    return q.first() is not None


def anonymize_participant_fields(participant: Participant, *, strict: bool = False, tag_with_id: bool = True) -> Participant:
    participant.nom = "ANONYME"
    participant.prenom = f"P{participant.id}" if tag_with_id else "ANONYME"
    participant.adresse = None
    participant.ville = None
    participant.email = None
    participant.telephone = None
    if strict:
        participant.genre = None
        participant.date_naissance = None
        participant.type_public = "H"
        participant.quartier_id = None
    return participant
