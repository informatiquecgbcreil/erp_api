from __future__ import annotations

from datetime import date

from flask_login import current_user

from app.extensions import db
from app.models import (
    InsertionDiplomeRef,
    InsertionTitreSejourTypeRef,
    Participant,
    ParticipantInsertionCertification,
    ParticipantInsertionParcours,
    ParticipantInsertionProfile,
)


# ---------------------------------------------------------------------------
# RBAC helpers (durcis)
# ---------------------------------------------------------------------------

def _user_or_current(user=None):
    return user if user is not None else current_user


def _has_perm(user, code: str) -> bool:
    try:
        return bool(user and user.is_authenticated and user.has_perm(code))
    except Exception:
        return False


def can_view_insertion(user=None) -> bool:
    user = _user_or_current(user)
    return _has_perm(user, "insertion:view")


def can_edit_insertion(user=None) -> bool:
    user = _user_or_current(user)
    return _has_perm(user, "insertion:edit")


def can_view_insertion_sensitive(user=None) -> bool:
    user = _user_or_current(user)
    return _has_perm(user, "insertion:sensitive_view")


def can_manage_insertion_refs(user=None) -> bool:
    user = _user_or_current(user)
    return _has_perm(user, "insertion:admin_refs")


def can_export_insertion_sensitive(user=None) -> bool:
    user = _user_or_current(user)
    return bool(
        _has_perm(user, "insertion:sensitive_view")
        and (
            _has_perm(user, "insertion:export")
            or _has_perm(user, "statsimpact:insertion_export")
        )
    )


# ---------------------------------------------------------------------------
# Legacy bridge helpers
# ---------------------------------------------------------------------------

def _clean_label(value: str | None) -> str | None:
    cleaned = (value or "").strip()
    return cleaned or None


def get_or_create_reference(model, label: str | None):
    cleaned = _clean_label(label)
    if not cleaned:
        return None
    row = model.query.filter(db.func.lower(model.label) == cleaned.lower()).first()
    if row:
        return row
    row = model(label=cleaned, is_active=True)
    db.session.add(row)
    db.session.flush()
    return row


def _resolve_is_active(date_entree: date | None, date_sortie: date | None) -> bool:
    today = date.today()
    if date_entree and date_entree > today:
        return False
    if date_sortie and date_sortie < today:
        return False
    return True


def sync_legacy_insertion_fields(participant: Participant, actor_id: int | None = None) -> None:
    """Recopie en douceur les champs legacy de Participant vers le nouveau module insertion.

    On ne touche qu'aux enregistrements marqués legacy_source=True pour éviter d'écraser
    de futurs parcours/certifications saisis dans le nouveau module.
    """
    if not participant:
        return

    pays_origine = _clean_label(getattr(participant, "pays_origine", None))
    cir_obtenu = getattr(participant, "cir_obtenu", None)
    titre_sejour_type = _clean_label(getattr(participant, "titre_sejour_type", None))
    date_entree = getattr(participant, "date_entree_dispositif", None)
    date_sortie = getattr(participant, "date_sortie_dispositif", None)
    diplome_obtenu = _clean_label(getattr(participant, "diplome_obtenu", None))

    # Profil 1:1
    if pays_origine or cir_obtenu is not None:
        profile = participant.insertion_profile or ParticipantInsertionProfile(participant=participant)
        if not profile.created_by_user_id:
            profile.created_by_user_id = actor_id
        profile.updated_by_user_id = actor_id
        profile.pays_origine = pays_origine
        profile.cir_obtenu = cir_obtenu
        db.session.add(profile)
    elif participant.insertion_profile:
        db.session.delete(participant.insertion_profile)

    # Parcours legacy unique
    legacy_parcours = (
        ParticipantInsertionParcours.query
        .filter_by(participant_id=participant.id, legacy_source=True)
        .order_by(ParticipantInsertionParcours.id.asc())
        .first()
    )
    if titre_sejour_type or date_entree or date_sortie:
        titre_ref = get_or_create_reference(InsertionTitreSejourTypeRef, titre_sejour_type)
        parcours = legacy_parcours or ParticipantInsertionParcours(participant=participant, legacy_source=True)
        if not parcours.created_by_user_id:
            parcours.created_by_user_id = actor_id
        parcours.updated_by_user_id = actor_id
        parcours.titre_sejour_type = titre_ref
        parcours.date_entree = date_entree
        parcours.date_sortie = date_sortie
        parcours.is_active = _resolve_is_active(date_entree, date_sortie)
        db.session.add(parcours)
    elif legacy_parcours:
        db.session.delete(legacy_parcours)

    # Certification legacy unique
    legacy_cert = (
        ParticipantInsertionCertification.query
        .filter_by(participant_id=participant.id, legacy_source=True)
        .order_by(ParticipantInsertionCertification.id.asc())
        .first()
    )
    if diplome_obtenu:
        diplome_ref = get_or_create_reference(InsertionDiplomeRef, diplome_obtenu)
        cert = legacy_cert or ParticipantInsertionCertification(participant=participant, legacy_source=True)
        if not cert.created_by_user_id:
            cert.created_by_user_id = actor_id
        cert.updated_by_user_id = actor_id
        cert.diplome = diplome_ref
        cert.niveau = None
        cert.date_passage = None
        cert.date_obtention = None
        cert.resultat = None
        cert.commentaire = None
        db.session.add(cert)
    elif legacy_cert:
        db.session.delete(legacy_cert)



def get_active_insertion_parcours_at_date(participant: Participant, target_date: date | None):
    if not participant:
        return None
    if target_date is None:
        return participant.current_insertion_parcours
    rows = sorted(
        list(getattr(participant, "insertion_parcours", []) or []),
        key=lambda row: ((row.date_entree or date.min), row.id or 0),
        reverse=True,
    )
    for row in rows:
        start_ok = row.date_entree is None or row.date_entree <= target_date
        end_ok = row.date_sortie is None or row.date_sortie >= target_date
        if start_ok and end_ok:
            return row
    return None
