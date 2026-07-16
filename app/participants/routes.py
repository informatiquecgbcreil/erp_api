from __future__ import annotations

import csv
from datetime import datetime, date, timedelta

from io import BytesIO, StringIO

from flask import Blueprint, current_app, render_template, request, redirect, url_for, flash, abort, send_file, Response
from flask_login import login_required, current_user
from ..rbac import require_perm, can

from app.extensions import db
from app.models import (
    AtelierActivite,
    Evaluation,
    OrientationAccesDroit,
    Participant,
    PasseportNote,
    PasseportPieceJointe,
    PresenceActivite,
    Quartier,
    QuestionnaireResponseGroup,
    SessionActivite,
    InsertionTitreSejourTypeRef,
    InsertionDiplomeRef,
)
from app.services.quartiers import normalize_quartier_for_ville
from app.secteurs import get_secteur_labels
from sqlalchemy import select, exists
from app.utils.delete_guard import commit_delete
from app.services.insertion import (
    can_edit_insertion as can_edit_insertion_module,
    can_view_insertion_sensitive as can_view_insertion_sensitive_module,
    sync_legacy_insertion_fields,
)
from app.services.purge_rgpd import NOM_ANONYME



bp = Blueprint("participants", __name__, url_prefix="/participants")

DROIT_IMAGE_STATUTS = {"non_renseigne", "accepte", "refuse"}


def _appliquer_droit_image(p: Participant) -> None:
    """Enregistre le consentement « droit à l'image » saisi dans le formulaire.

    Preuve dématérialisée : quand le statut change, on trace la date du
    jour et l'agent connecté qui a recueilli la réponse.
    """
    statut = (request.form.get("droit_image_statut") or "").strip()
    if statut not in DROIT_IMAGE_STATUTS:
        return
    if statut == (p.droit_image_statut or "non_renseigne"):
        return
    p.droit_image_statut = statut
    if statut == "non_renseigne":
        p.droit_image_date = None
        p.droit_image_recueilli_par = None
    else:
        p.droit_image_date = date.today()
        p.droit_image_recueilli_par = f"{current_user.nom} ({current_user.email})"


def _current_secteur() -> str:
    return (getattr(current_user, "secteur_assigne", "") or "").strip()


def _is_global_role() -> bool:
    return current_user.has_perm("participants:view_all") or current_user.has_perm("scope:all_secteurs")


def _can_read_participant(p: Participant) -> bool:
    return bool(can('participants:view') or can('participants:edit') or can('participants:delete'))


def _can_edit_participant(p: Participant) -> bool:
    return bool(can('participants:edit'))


def _can_view_sensitive_insertion() -> bool:
    return bool(can_view_insertion_sensitive_module())


def _can_edit_insertion() -> bool:
    return bool(can_edit_insertion_module())


def _can_see_participant(p: Participant) -> bool:
    # Visible (ancienne logique) : créé par secteur OU déjà présent dans secteur
    # Utilisé uniquement pour les listings "dans mon secteur", PAS pour l'édition.
    if _is_global_role():
        return True
    if not current_user.has_perm("participants:view_all"):
        sec = _current_secteur()
        if not sec:
            return False
        if (p.created_secteur or "") == sec:
            return True
        has_presence = (
            db.session.query(PresenceActivite.id)
            .join(SessionActivite, SessionActivite.id == PresenceActivite.session_id)
            .filter(PresenceActivite.participant_id == p.id)
            .filter(SessionActivite.secteur == sec)
            .first()
        )
        return bool(has_presence)
    return False


def _parse_iso_date(raw: str | None) -> date | None:
    try:
        value = (raw or "").strip()
        return date.fromisoformat(value) if value else None
    except Exception:
        return None


def _parse_bool(raw: str | None) -> bool | None:
    value = (raw or "").strip().lower()
    if value in {"1", "true", "on", "oui", "yes"}:
        return True
    if value in {"0", "false", "off", "non", "no"}:
        return False
    return None

def _distinct_participant_values(column_name: str, *, current_value: str | None = None) -> list[str]:
    """Liste triée de valeurs distinctes non vides déjà présentes sur Participant."""
    col = getattr(Participant, column_name, None)
    if col is None:
        values: set[str] = set()
    else:
        values = {
            (v or "").strip()
            for (v,) in db.session.query(col).distinct().all()
            if isinstance(v, str) and (v or "").strip()
        }
    if current_value and str(current_value).strip():
        values.add(str(current_value).strip())
    return sorted(values, key=lambda s: s.lower())


def _active_reference_options(model, *, current_value: str | None = None, legacy_column: str | None = None) -> list[str]:
    """Retourne les libellés actifs d'un référentiel insertion.

    Les valeurs actives du référentiel sont proposées en priorité. On garde
    temporairement les anciennes valeurs legacy présentes sur Participant pour
    éviter de casser l'édition de fiches existantes pendant la transition.
    """
    values = [
        row.label.strip()
        for row in model.query.filter_by(is_active=True).order_by(model.sort_order.asc(), model.label.asc()).all()
        if getattr(row, 'label', None) and row.label.strip()
    ]
    deduped = []
    seen = set()
    for val in values:
        low = val.lower()
        if low not in seen:
            seen.add(low)
            deduped.append(val)

    legacy_values = _distinct_participant_values(legacy_column, current_value=current_value) if legacy_column else []
    for val in legacy_values:
        low = val.lower()
        if low not in seen:
            seen.add(low)
            deduped.append(val)

    if current_value and str(current_value).strip():
        cur = str(current_value).strip()
        low = cur.lower()
        if low not in seen:
            deduped.append(cur)

    return deduped



def _normalize_gender_group(value: str | None) -> str:
    raw = (value or "").strip().lower()
    if not raw:
        return "unknown"
    raw = raw.replace("é", "e").replace("è", "e").replace("ê", "e").replace("à", "a")
    if raw in {"f", "femme", "feminin", "female", "woman"}:
        return "femmes"
    if raw in {"h", "m", "homme", "masculin", "male", "man"}:
        return "hommes"
    if raw in {"autre", "non binaire", "non-binaire", "nb", "x"}:
        return "other"
    return "unknown"


AGE_BUCKETS = [
    ("0-5 ans", 0, 5),
    ("6-11 ans", 6, 11),
    ("12-17 ans", 12, 17),
    ("18-25 ans", 18, 25),
    ("26-39 ans", 26, 39),
    ("40-59 ans", 40, 59),
    ("60 ans et +", 60, None),
]


def _compute_age(dob: date | None, ref_date: date | None) -> int | None:
    if not dob or not ref_date:
        return None
    years = ref_date.year - dob.year
    if (ref_date.month, ref_date.day) < (dob.month, dob.day):
        years -= 1
    if years < 0 or years > 120:
        return None
    return years


def _age_bucket(age: int | None) -> str | None:
    if age is None:
        return None
    for label, low, high in AGE_BUCKETS:
        if low is not None and age < low:
            continue
        if high is not None and age > high:
            continue
        return label
    return None


def _clean_city_label(participant: Participant) -> str:
    quartier = getattr(participant, "quartier", None)
    quartier_ville = (getattr(quartier, "ville", None) or "").strip()
    participant_ville = (getattr(participant, "ville", None) or "").strip()
    return quartier_ville or participant_ville or "Ville non renseignée"


def _clean_quartier_label(participant: Participant) -> str:
    quartier = getattr(participant, "quartier", None)
    quartier_nom = (getattr(quartier, "nom", None) or "").strip()
    return quartier_nom or "Quartier non renseigné"


def _session_date_value(session: SessionActivite | None) -> date | None:
    return (session.rdv_date or session.date_session) if session else None


def _format_date(value) -> str:
    if not value:
        return "Non renseigne"
    try:
        return value.strftime("%d/%m/%Y")
    except Exception:
        return str(value)


def _event_sort_value(value) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    return datetime.min


def _event_date_label(value) -> str:
    return _format_date(value) if value else "Date inconnue"


def _orientation_status_label(code: str | None) -> str:
    labels = {
        "oriente": "Oriente",
        "rdv_pris": "Rendez-vous pris",
        "accompagne": "Accompagne",
        "a_rappeler": "A rappeler",
        "resolu": "Resolu",
        "non_abouti": "Non abouti",
    }
    return labels.get(code or "", code or "Non renseigne")


def _orientation_domain_label(code: str | None) -> str:
    labels = {
        "logement": "Logement",
        "caf": "CAF / prestations",
        "sante": "Sante",
        "emploi": "Emploi / insertion",
        "retraite": "Retraite",
        "juridique": "Juridique",
        "administratif": "Administratif",
        "numerique": "Mediation numerique",
        "mobilite": "Mobilite",
        "aide_alimentaire": "Aide alimentaire",
        "violences": "Violences / protection",
        "handicap": "Handicap",
        "education": "Education / parentalite",
        "autre": "Autre",
    }
    return labels.get(code or "", code or "Non renseigne")


def _quality_flags_for_participant(p: Participant) -> list[str]:
    flags = []
    if not (p.telephone or p.email):
        flags.append("Aucun moyen de contact renseigne")
    if not p.date_naissance:
        flags.append("Date de naissance manquante")
    if not p.genre:
        flags.append("Genre non renseigne")
    if not (p.ville or p.quartier):
        flags.append("Ville/quartier manquant")
    if not p.created_secteur:
        flags.append("Secteur de rattachement manquant")
    return flags


@bp.route("/")
@login_required
@require_perm("participants:view")
def list_participants():

    q = (request.args.get("q") or "").strip()
    scope = (request.args.get("scope") or "").strip()

    f_ville = (request.args.get("ville") or "").strip()
    f_quartier_id = (request.args.get("quartier_id") or "").strip()
    f_genre = (request.args.get("genre") or "").strip()
    f_type_public = (request.args.get("type_public") or "").strip()
    f_presence = (request.args.get("presence") or "").strip()
    f_naissance = (request.args.get("naissance") or "").strip()
    f_created_secteur = (request.args.get("created_secteur") or "").strip()

    dashboard_from = _parse_iso_date(request.args.get("dashboard_from"))
    dashboard_to = _parse_iso_date(request.args.get("dashboard_to"))
    dashboard_active = (request.args.get("dashboard_active") or "").strip() == "1"
    genre_group = (request.args.get("genre_group") or "").strip().lower()
    age_bucket_label = (request.args.get("age_bucket") or "").strip()
    city_label_filter = (request.args.get("city_label") or "").strip()
    quartier_label_filter = (request.args.get("quartier_label") or "").strip()

    if scope == "annuaire" and (not q or len(q) < 2):
        scope = "secteur"

    participants_q = Participant.query

    if not current_user.has_perm("participants:view_all"):
        sec = _current_secteur()
        if not sec:
            abort(403)

        if not (scope == "annuaire" and q and len(q) >= 2):
            if scope == "created":
                participants_q = participants_q.filter(Participant.created_secteur == sec)
            else:
                subq_presence_ids = (
                    db.session.query(PresenceActivite.participant_id)
                    .join(SessionActivite, SessionActivite.id == PresenceActivite.session_id)
                    .filter(SessionActivite.secteur == sec)
                    .distinct()
                )
                participants_q = participants_q.filter(
                    (Participant.created_secteur == sec) | (Participant.id.in_(subq_presence_ids))
                )
    else:
        if scope == "secteur":
            sec = (request.args.get("secteur") or "").strip()
            if sec:
                participants_q = participants_q.filter(Participant.created_secteur == sec)

        if f_created_secteur:
            participants_q = participants_q.filter(Participant.created_secteur == f_created_secteur)

    base_q = Participant.query

    if not current_user.has_perm("participants:view_all"):
        sec = _current_secteur()
        if not sec:
            abort(403)

        if not (scope == "annuaire" and q and len(q) >= 2):
            if scope == "created":
                base_q = base_q.filter(Participant.created_secteur == sec)
            else:
                subq_presence_ids = (
                    db.session.query(PresenceActivite.participant_id)
                    .join(SessionActivite, SessionActivite.id == PresenceActivite.session_id)
                    .filter(SessionActivite.secteur == sec)
                    .distinct()
                )
                base_q = base_q.filter(
                    (Participant.created_secteur == sec) | (Participant.id.in_(subq_presence_ids))
                )
    else:
        if scope == "secteur":
            sec2 = (request.args.get("secteur") or "").strip()
            if sec2:
                base_q = base_q.filter(Participant.created_secteur == sec2)

        if f_created_secteur:
            base_q = base_q.filter(Participant.created_secteur == f_created_secteur)

    if dashboard_active and dashboard_from and dashboard_to:
        session_date = db.func.coalesce(SessionActivite.rdv_date, SessionActivite.date_session)
        active_ids_q = (
            db.session.query(PresenceActivite.participant_id)
            .join(SessionActivite, SessionActivite.id == PresenceActivite.session_id)
            .filter(session_date.isnot(None))
            .filter(session_date >= dashboard_from)
            .filter(session_date <= dashboard_to)
        )
        if not current_user.has_perm("participants:view_all"):
            active_ids_q = active_ids_q.filter(SessionActivite.secteur == _current_secteur())
        active_ids_q = active_ids_q.distinct()
        participants_q = participants_q.filter(Participant.id.in_(active_ids_q))
        base_q = base_q.filter(Participant.id.in_(active_ids_q))
        f_presence = "with"

    base_ids_sq = base_q.with_entities(Participant.id).subquery()
    base_ids_select = select(base_ids_sq.c.id)

    villes = (
        db.session.query(Participant.ville)
        .filter(Participant.ville.isnot(None))
        .filter(Participant.ville != "")
        .filter(Participant.id.in_(base_ids_select))
        .distinct()
        .order_by(Participant.ville.asc())
        .all()
    )
    villes = [v[0] for v in villes]

    quartiers = (
        db.session.query(Quartier)
        .join(Participant, Participant.quartier_id == Quartier.id)
        .filter(Participant.id.in_(base_ids_select))
        .distinct()
        .order_by(Quartier.ville.asc(), Quartier.nom.asc())
        .all()
    )

    genres = (
        db.session.query(Participant.genre)
        .filter(Participant.genre.isnot(None))
        .filter(Participant.genre != "")
        .filter(Participant.id.in_(base_ids_select))
        .distinct()
        .order_by(Participant.genre.asc())
        .all()
    )
    genres = [g[0] for g in genres]

    types_public = (
        db.session.query(Participant.type_public)
        .filter(Participant.type_public.isnot(None))
        .filter(Participant.type_public != "")
        .filter(Participant.id.in_(base_ids_select))
        .distinct()
        .order_by(Participant.type_public.asc())
        .all()
    )
    types_public = [t[0] for t in types_public]

    if f_ville:
        participants_q = participants_q.filter(Participant.ville == f_ville)

    if f_quartier_id:
        try:
            qid = int(f_quartier_id)
            participants_q = participants_q.filter(Participant.quartier_id == qid)
        except Exception:
            pass

    if f_genre:
        participants_q = participants_q.filter(Participant.genre == f_genre)

    if f_type_public:
        participants_q = participants_q.filter(Participant.type_public == f_type_public)

    has_presence = exists().where(PresenceActivite.participant_id == Participant.id)

    if f_presence == "with":
        participants_q = participants_q.filter(has_presence)
    elif f_presence == "without":
        participants_q = participants_q.filter(~has_presence)

    if f_naissance == "sans":
        participants_q = participants_q.filter(Participant.date_naissance.is_(None))
    elif f_naissance == "avec":
        participants_q = participants_q.filter(Participant.date_naissance.isnot(None))

    if q:
        like = f"%{q.lower()}%"
        participants_q = participants_q.filter(
            db.or_(
                db.func.lower(Participant.nom).like(like),
                db.func.lower(Participant.prenom).like(like),
                db.func.lower(db.func.coalesce(Participant.email, "")).like(like),
                db.func.lower(db.func.coalesce(Participant.telephone, "")).like(like),
            )
        )

    items = participants_q.order_by(Participant.nom.asc(), Participant.prenom.asc()).all()

    if genre_group:
        items = [item for item in items if _normalize_gender_group(getattr(item, "genre", None)) == genre_group]

    if age_bucket_label:
        ref_date = dashboard_to or date.today()
        normalized_age_bucket = age_bucket_label.strip().lower()
        if normalized_age_bucket in {"âge non renseigné", "age non renseigne", "unknown", "inconnu", "non renseigne"}:
            items = [
                item
                for item in items
                if _age_bucket(_compute_age(getattr(item, "date_naissance", None), ref_date)) is None
            ]
        else:
            items = [
                item
                for item in items
                if _age_bucket(_compute_age(getattr(item, "date_naissance", None), ref_date)) == age_bucket_label
            ]

    if city_label_filter:
        items = [item for item in items if _clean_city_label(item) == city_label_filter]

    if quartier_label_filter:
        items = [item for item in items if _clean_quartier_label(item) == quartier_label_filter]

    items = items[:1000]

    dashboard_filters = {
        "active_period": bool(dashboard_active and dashboard_from and dashboard_to),
        "from": dashboard_from,
        "to": dashboard_to,
        "genre_group": genre_group,
        "age_bucket": age_bucket_label,
        "city_label": city_label_filter,
        "quartier_label": quartier_label_filter,
    }

    return render_template(
        "participants/list.html",
        items=items,
        q=q,
        scope=scope,
        secteur=_current_secteur(),
        villes=villes,
        quartiers=quartiers,
        genres=genres,
        types_public=types_public,
        f_ville=f_ville,
        f_quartier_id=f_quartier_id,
        f_genre=f_genre,
        f_type_public=f_type_public,
        f_presence=f_presence,
        f_naissance=f_naissance,
        f_created_secteur=f_created_secteur,
        is_global=_is_global_role(),
        dashboard_filters=dashboard_filters,
        pagination=None,
    )


def _csat_gender_code(genre: str | None) -> str:
    """Convertit le genre interne (Homme/Femme/Autre) vers le code attendu par
    l'import CSV du portail CSAT (M/F/N)."""
    if genre == "Homme":
        return "M"
    if genre == "Femme":
        return "F"
    return "N"


@bp.route("/export-contacts-emailing.csv")
@login_required
@require_perm("participants:view")
def export_contacts_emailing_csv():
    """Export des contacts (avec e-mail) au format CSV générique, pensé pour
    l'import dans un outil d'e-mailing (Brevo, Mailjet…) : ces outils
    proposent d'associer chaque colonne à sa case au moment de l'import,
    donc les intitulés ci-dessous n'ont pas besoin de correspondre au pixel
    près à ce qu'ils attendent.

    Seules les personnes avec une adresse e-mail renseignée sont incluses ;
    les fiches anonymisées (RGPD) sont exclues comme pour tous les exports.
    """
    participants_q = Participant.query.filter(
        Participant.nom != NOM_ANONYME,
        Participant.email.isnot(None),
        Participant.email != "",
    )

    if not current_user.has_perm("participants:view_all"):
        sec = _current_secteur()
        if not sec:
            abort(403)
        subq_presence_ids = (
            db.session.query(PresenceActivite.participant_id)
            .join(SessionActivite, SessionActivite.id == PresenceActivite.session_id)
            .filter(SessionActivite.secteur == sec)
            .distinct()
        )
        participants_q = participants_q.filter(
            (Participant.created_secteur == sec) | (Participant.id.in_(subq_presence_ids))
        )
    else:
        secteur_filtre = (request.args.get("secteur") or "").strip()
        if secteur_filtre:
            participants_q = participants_q.filter(Participant.created_secteur == secteur_filtre)

    participants = participants_q.order_by(Participant.nom.asc(), Participant.prenom.asc()).all()

    # Un e-mail identique ne doit apparaître qu'une fois (import propre côté outil d'e-mailing).
    vus: set[str] = set()
    lignes = []
    for p in participants:
        email = (p.email or "").strip().lower()
        if not email or email in vus:
            continue
        vus.add(email)
        lignes.append(p)

    output = StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow(["EMAIL", "PRENOM", "NOM", "VILLE", "QUARTIER", "SECTEUR", "TYPE_PUBLIC"])
    for p in lignes:
        writer.writerow([
            (p.email or "").strip(),
            p.prenom,
            p.nom,
            p.ville or "",
            p.quartier.nom if p.quartier else "",
            p.created_secteur or "",
            p.type_public or "",
        ])

    content = output.getvalue().encode("utf-8-sig")
    filename = f"contacts_emailing_{date.today().isoformat()}.csv"
    return Response(content, mimetype="text/csv", headers={
        "Content-Disposition": f"attachment; filename={filename}"
    })


@bp.route("/export-csat.csv")
@login_required
@require_perm("participants:view")
def export_csat_csv():
    """Export des participants au format d'import CSV du portail CSAT
    (Centres Sociaux Acteurs des Transitions) : évite la ressaisie manuelle
    entre les deux outils.

    Colonnes attendues par CSAT : last_name;first_name;location;gender;birthdate
    (date au format JJ/MM/AAAA, genre M/F/N).
    """
    participants_q = Participant.query.filter(Participant.nom != NOM_ANONYME)

    if not current_user.has_perm("participants:view_all"):
        sec = _current_secteur()
        if not sec:
            abort(403)
        subq_presence_ids = (
            db.session.query(PresenceActivite.participant_id)
            .join(SessionActivite, SessionActivite.id == PresenceActivite.session_id)
            .filter(SessionActivite.secteur == sec)
            .distinct()
        )
        participants_q = participants_q.filter(
            (Participant.created_secteur == sec) | (Participant.id.in_(subq_presence_ids))
        )
    else:
        secteur_filtre = (request.args.get("secteur") or "").strip()
        if secteur_filtre:
            participants_q = participants_q.filter(Participant.created_secteur == secteur_filtre)

    participants = participants_q.order_by(Participant.nom.asc(), Participant.prenom.asc()).all()

    output = StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow(["last_name", "first_name", "location", "gender", "birthdate"])
    for p in participants:
        writer.writerow([
            p.nom,
            p.prenom,
            p.ville or "",
            _csat_gender_code(p.genre),
            p.date_naissance.strftime("%d/%m/%Y") if p.date_naissance else "",
        ])

    content = output.getvalue().encode("utf-8-sig")
    filename = f"participants_csat_{date.today().isoformat()}.csv"
    return Response(content, mimetype="text/csv", headers={
        "Content-Disposition": f"attachment; filename={filename}"
    })


@bp.route("/search")
@login_required
def search_participants():
    """Annuaire global (lecture seule) pour l'auto-complétion côté émargement."""

    q = (request.args.get("q") or "").strip()
    if not q or len(q) < 2:
        return {"items": []}

    like = f"%{q.lower()}%"
    participants_q = Participant.query.filter(
        db.or_(
            db.func.lower(Participant.nom).like(like),
            db.func.lower(Participant.prenom).like(like),
            db.func.lower(db.func.coalesce(Participant.email, "")).like(like),
            db.func.lower(db.func.coalesce(Participant.telephone, "")).like(like),
        )
    )

    items = (
        participants_q.order_by(Participant.nom.asc(), Participant.prenom.asc())
        .limit(30)
        .all()
    )

    def _year(d):
        try:
            return d.year if d else None
        except Exception:
            return None

    return {
        "items": [
            {
                "id": p.id,
                "nom": p.nom,
                "prenom": p.prenom,
                "annee_naissance": _year(getattr(p, "date_naissance", None)),
                "ville": getattr(p, "ville", None),
                "created_secteur": getattr(p, "created_secteur", None),
            }
            for p in items
        ]
    }


@bp.route("/<int:participant_id>/synthese")
@login_required
@require_perm("participants:view")
def synthese_participant(participant_id: int):
    participant = db.get_or_404(Participant, participant_id)
    if not _can_read_participant(participant) or not _can_see_participant(participant):
        abort(403)

    session_date = db.func.coalesce(SessionActivite.rdv_date, SessionActivite.date_session)

    presences_base = (
        PresenceActivite.query
        .join(SessionActivite, SessionActivite.id == PresenceActivite.session_id)
        .join(AtelierActivite, AtelierActivite.id == SessionActivite.atelier_id)
        .filter(PresenceActivite.participant_id == participant.id)
        .filter(SessionActivite.is_deleted.is_(False))
        .filter(AtelierActivite.is_deleted.is_(False))
    )
    if not _is_global_role():
        presences_base = presences_base.filter(SessionActivite.secteur == _current_secteur())

    latest_presences = (
        presences_base
        .order_by(session_date.desc(), PresenceActivite.created_at.desc())
        .limit(10)
        .all()
    )
    presence_count = presences_base.count()
    first_presence = presences_base.order_by(session_date.asc(), PresenceActivite.created_at.asc()).first()
    last_presence = presences_base.order_by(session_date.desc(), PresenceActivite.created_at.desc()).first()

    count_expr = db.func.count(PresenceActivite.id)
    atelier_stats_query = (
        db.session.query(AtelierActivite, count_expr.label("presence_count"), db.func.max(session_date).label("last_date"))
        .select_from(PresenceActivite)
        .join(SessionActivite, SessionActivite.id == PresenceActivite.session_id)
        .join(AtelierActivite, AtelierActivite.id == SessionActivite.atelier_id)
        .filter(PresenceActivite.participant_id == participant.id)
        .filter(SessionActivite.is_deleted.is_(False))
        .filter(AtelierActivite.is_deleted.is_(False))
        .group_by(AtelierActivite.id)
        .order_by(count_expr.desc(), db.func.max(session_date).desc())
        .limit(8)
    )
    secteur_stats_query = (
        db.session.query(SessionActivite.secteur, count_expr.label("presence_count"))
        .select_from(PresenceActivite)
        .join(SessionActivite, SessionActivite.id == PresenceActivite.session_id)
        .join(AtelierActivite, AtelierActivite.id == SessionActivite.atelier_id)
        .filter(PresenceActivite.participant_id == participant.id)
        .filter(SessionActivite.is_deleted.is_(False))
        .filter(AtelierActivite.is_deleted.is_(False))
        .group_by(SessionActivite.secteur)
        .order_by(count_expr.desc(), SessionActivite.secteur.asc())
    )
    if not _is_global_role():
        atelier_stats_query = atelier_stats_query.filter(SessionActivite.secteur == _current_secteur())
        secteur_stats_query = secteur_stats_query.filter(SessionActivite.secteur == _current_secteur())

    show_orientations = can("partenaires:view")
    orientations_query = OrientationAccesDroit.query.filter(OrientationAccesDroit.participant_id == participant.id)
    if not _is_global_role():
        orientations_query = orientations_query.filter(OrientationAccesDroit.secteur == _current_secteur())
    orientations = (
        orientations_query
        .order_by(OrientationAccesDroit.date_orientation.desc(), OrientationAccesDroit.created_at.desc())
        .limit(10)
        .all()
        if show_orientations else []
    )
    orientation_open_count = sum(1 for row in orientations if row.statut not in {"resolu", "non_abouti"})

    show_questionnaires = can("questionnaires:view")
    questionnaire_query = QuestionnaireResponseGroup.query.filter(QuestionnaireResponseGroup.participant_id == participant.id)
    if not _is_global_role():
        questionnaire_query = questionnaire_query.filter(QuestionnaireResponseGroup.secteur == _current_secteur())
    questionnaire_groups = questionnaire_query.order_by(QuestionnaireResponseGroup.created_at.desc()).limit(8).all() if show_questionnaires else []

    show_passeport = can("pedagogie:view")
    notes_query = PasseportNote.query.filter(PasseportNote.participant_id == participant.id)
    files_query = PasseportPieceJointe.query.filter(PasseportPieceJointe.participant_id == participant.id)
    if not _is_global_role():
        notes_query = notes_query.filter(PasseportNote.secteur == _current_secteur())
        files_query = files_query.filter(PasseportPieceJointe.secteur == _current_secteur())
    passport_notes = notes_query.order_by(PasseportNote.created_at.desc()).limit(6).all() if show_passeport else []
    passport_files = files_query.order_by(PasseportPieceJointe.created_at.desc()).limit(6).all() if show_passeport else []

    evaluations_count = Evaluation.query.filter_by(participant_id=participant.id).count()
    show_insertion = _can_view_sensitive_insertion()
    insertion_counts = {
        "parcours": len(participant.insertion_parcours or []) if show_insertion else 0,
        "positionnements": len(participant.insertion_positionnements or []) if show_insertion else 0,
        "certifications": len(participant.insertion_certifications or []) if show_insertion else 0,
        "profil": 1 if show_insertion and participant.insertion_profile else 0,
    }

    atelier_stats = [
        {"atelier": row[0], "count": row.presence_count, "last_date": row.last_date}
        for row in atelier_stats_query.all()
    ]
    secteur_stats = [
        {"secteur": row[0] or "Non renseigne", "count": row.presence_count}
        for row in secteur_stats_query.all()
    ]

    today = date.today()
    cutoff_12m = today - timedelta(days=365)
    presence_dates = [
        row[0]
        for row in presences_base.with_entities(session_date.label("date_ref")).all()
        if row[0]
    ]
    presences_12m = [value for value in presence_dates if value >= cutoff_12m]
    active_months_12m = len({(value.year, value.month) for value in presences_12m})
    last_presence_date = _session_date_value(last_presence.session) if last_presence else None
    first_presence_date = _session_date_value(first_presence.session) if first_presence else None

    if not last_presence_date:
        link_status = {
            "label": "Sans presence",
            "tone": "neutral",
            "detail": "Aucune presence visible sur le perimetre actuel.",
        }
    else:
        days_since_presence = (today - last_presence_date).days
        if presence_count == 1 and days_since_presence <= 60:
            link_status = {
                "label": "Nouveau",
                "tone": "new",
                "detail": "Une premiere presence recente a ete reperee.",
            }
        elif active_months_12m >= 4 and len(presences_12m) >= 6:
            link_status = {
                "label": "Regulier",
                "tone": "strong",
                "detail": f"{len(presences_12m)} presence(s) sur {active_months_12m} mois actifs.",
            }
        elif days_since_presence <= 60:
            link_status = {
                "label": "Actif",
                "tone": "active",
                "detail": "Une presence recente maintient le lien.",
            }
        elif days_since_presence > 180:
            link_status = {
                "label": "A reactiver",
                "tone": "warning",
                "detail": f"Derniere presence il y a {days_since_presence} jours.",
            }
        else:
            link_status = {
                "label": "En veille",
                "tone": "watch",
                "detail": f"Derniere presence il y a {days_since_presence} jours.",
            }

    open_orientation_rows = [row for row in orientations if row.statut not in {"resolu", "non_abouti"}]
    overdue_orientation_count = sum(
        1
        for row in open_orientation_rows
        if row.suite_prevue and row.suite_prevue < today
    )
    recent_note_count = sum(
        1
        for note in passport_notes
        if note.created_at and _event_sort_value(note.created_at).date() >= today - timedelta(days=30)
    )
    active_insertion_count = (
        sum(1 for row in (participant.insertion_parcours or []) if getattr(row, "is_active", False))
        if show_insertion else 0
    )
    if overdue_orientation_count:
        followup = {
            "label": "Attention",
            "tone": "warning",
            "detail": f"{overdue_orientation_count} suite(s) prevue(s) depassee(s).",
        }
    elif open_orientation_rows or active_insertion_count or recent_note_count:
        parts = []
        if open_orientation_rows:
            parts.append(f"{len(open_orientation_rows)} orientation(s) ouverte(s)")
        if active_insertion_count:
            parts.append(f"{active_insertion_count} parcours insertion actif(s)")
        if recent_note_count:
            parts.append(f"{recent_note_count} note(s) recente(s)")
        followup = {
            "label": "Suivi actif",
            "tone": "active",
            "detail": " · ".join(parts),
        }
    else:
        followup = {
            "label": "Rien a signaler",
            "tone": "neutral",
            "detail": "Aucun suivi ouvert visible aujourd'hui.",
        }

    quality_checks = [
        ("Telephone", bool(participant.telephone)),
        ("Email", bool(participant.email)),
        ("Naissance", bool(participant.date_naissance)),
        ("Genre", bool(participant.genre)),
        ("Ville", bool(participant.ville or participant.quartier)),
        ("Quartier", bool(participant.quartier)),
        ("Secteur", bool(participant.created_secteur)),
    ]
    quality_ok = sum(1 for _, ok in quality_checks if ok)
    quality_score = {
        "ok": quality_ok,
        "total": len(quality_checks),
        "percent": round((quality_ok / len(quality_checks)) * 100) if quality_checks else 0,
        "missing": [label for label, ok in quality_checks if not ok],
    }

    known_channels = [
        {"label": "Presences", "active": presence_count > 0},
        {"label": "Acces aux droits", "active": bool(summary_orientation_count := (orientations_query.count() if show_orientations else 0))},
        {"label": "Insertion", "active": show_insertion and any(insertion_counts.values())},
        {"label": "Questionnaires", "active": bool(questionnaire_query.count() if show_questionnaires else 0)},
        {"label": "Passeport", "active": bool(notes_query.count() if show_passeport else 0)},
        {"label": "Evaluations", "active": evaluations_count > 0},
    ]

    timeline_events = []
    for presence in latest_presences:
        session = presence.session
        event_date = _session_date_value(session)
        timeline_events.append({
            "type": "Presence",
            "tone": "blue",
            "date": event_date,
            "date_label": _event_date_label(event_date),
            "title": session.atelier.nom if session and session.atelier else "Presence atelier",
            "meta": f"{session.secteur if session else 'Secteur ?'}" + (f" · {presence.motif}" if presence.motif else ""),
        })
    for row in orientations:
        timeline_events.append({
            "type": "Orientation",
            "tone": "green",
            "date": row.date_orientation,
            "date_label": _event_date_label(row.date_orientation),
            "title": _orientation_domain_label(row.domaine),
            "meta": f"{_orientation_status_label(row.statut)} · {row.demande}",
        })
    for group in questionnaire_groups:
        timeline_events.append({
            "type": "Questionnaire",
            "tone": "purple",
            "date": group.created_at,
            "date_label": _event_date_label(group.created_at),
            "title": group.questionnaire.nom if group.questionnaire else "Questionnaire",
            "meta": (group.secteur or "Secteur ?") + (f" · {group.atelier.nom}" if group.atelier else ""),
        })
    for note in passport_notes:
        timeline_events.append({
            "type": "Note",
            "tone": "amber",
            "date": note.created_at,
            "date_label": _event_date_label(note.created_at),
            "title": note.categorie or "Note passeport",
            "meta": (note.contenu or "")[:160],
        })
    if show_insertion:
        for row in list(participant.insertion_parcours or [])[:6]:
            event_date = row.date_entree or row.created_at
            timeline_events.append({
                "type": "Insertion",
                "tone": "slate",
                "date": event_date,
                "date_label": _event_date_label(event_date),
                "title": row.dispositif.label if row.dispositif else "Parcours insertion",
                "meta": "Actif" if row.is_active else "Clos",
            })
    timeline_events = sorted(timeline_events, key=lambda event: _event_sort_value(event.get("date")), reverse=True)[:8]
    last_trace = timeline_events[0] if timeline_events else {
        "type": "Aucune trace",
        "tone": "neutral",
        "date": None,
        "date_label": "Aucune trace",
        "title": "Aucune activite visible",
        "meta": "Cette fiche n'a pas encore d'evenement rattache sur le perimetre visible.",
    }

    summary = {
        "presence_count": presence_count,
        "first_presence_date": first_presence_date,
        "last_presence_date": last_presence_date,
        "atelier_count": len(atelier_stats),
        "orientation_count": summary_orientation_count,
        "orientation_open_count": orientation_open_count,
        "questionnaire_count": questionnaire_query.count() if show_questionnaires else 0,
        "passport_note_count": notes_query.count() if show_passeport else 0,
        "passport_file_count": files_query.count() if show_passeport else 0,
        "evaluation_count": evaluations_count,
    }

    intensity = {
        "presences_12m": len(presences_12m),
        "active_months_12m": active_months_12m,
        "atelier_count": len(atelier_stats),
        "first_presence_date": first_presence_date,
        "last_presence_date": last_presence_date,
    }

    show_cotisations = can("cotisations:view")
    cotisations_ctx = {}
    if show_cotisations:
        from app.participants.cotisations import cotisations_contexte
        cotisations_ctx = cotisations_contexte(participant)

    return render_template(
        "participants/synthese.html",
        participant=participant,
        quality_flags=_quality_flags_for_participant(participant),
        show_cotisations=show_cotisations,
        can_edit_cotisations=can("cotisations:edit"),
        **cotisations_ctx,
        latest_presences=latest_presences,
        atelier_stats=atelier_stats,
        secteur_stats=secteur_stats,
        orientations=orientations,
        questionnaire_groups=questionnaire_groups,
        passport_notes=passport_notes,
        passport_files=passport_files,
        insertion_counts=insertion_counts,
        link_status=link_status,
        last_trace=last_trace,
        followup=followup,
        intensity=intensity,
        quality_score=quality_score,
        known_channels=known_channels,
        timeline_events=timeline_events,
        show_orientations=show_orientations,
        show_questionnaires=show_questionnaires,
        show_passeport=show_passeport,
        show_insertion=show_insertion,
        summary=summary,
        can_edit_participant=_can_edit_participant(participant),
        format_date=_format_date,
        session_date_value=_session_date_value,
        orientation_status_label=_orientation_status_label,
        orientation_domain_label=_orientation_domain_label,
    )


@bp.route("/new", methods=["GET", "POST"])
@login_required
def new_participant():

    if request.method == "POST":
        nom = (request.form.get("nom") or "").strip()
        prenom = (request.form.get("prenom") or "").strip()
        if not nom or not prenom:
            flash("Le nom et le prénom sont obligatoires.", "err")
            return redirect(url_for("participants.new_participant"))

        email = (request.form.get("email") or "").strip() or None
        if email and ("@" not in email or "." not in email.rsplit("@", 1)[-1]):
            flash("Adresse e-mail invalide (ex. nom@domaine.fr).", "err")
            return redirect(url_for("participants.new_participant"))

        # Anti-doublon : avant de créer, on propose les fiches ressemblantes.
        # « force_creation=1 » (bouton « Créer quand même ») passe outre.
        if (request.form.get("force_creation") or "") != "1":
            from app.services.doublons import candidats_doublons
            candidats = candidats_doublons(nom, prenom)
            if candidats:
                quartiers = Quartier.query.order_by(Quartier.ville.asc(), Quartier.nom.asc()).all()
                return render_template(
                    "participants/form.html",
                    item=None,
                    secteur=_current_secteur(),
                    is_editable=True,
                    can_view_sensitive_insertion=_can_view_sensitive_insertion(),
                    can_edit_insertion=_can_edit_insertion(),
                    quartiers=quartiers,
                    titre_sejour_options=_active_reference_options(InsertionTitreSejourTypeRef, legacy_column="titre_sejour_type"),
                    diplome_options=_active_reference_options(InsertionDiplomeRef, legacy_column="diplome_obtenu"),
                    secteurs_creation=get_secteur_labels(active_only=True),
                    doublons_candidats=candidats,
                    pending=request.form,
                )

        p = Participant(
            nom=nom,
            prenom=prenom,
            adresse=(request.form.get("adresse") or "").strip() or None,
            ville=(request.form.get("ville") or "").strip() or None,
            email=email,
            telephone=(request.form.get("telephone") or "").strip() or None,
            genre=(request.form.get("genre") or "").strip() or None,
            type_public=(request.form.get("type_public") or "H").strip() or "H",
            created_by_user_id=getattr(current_user, "id", None),
            created_secteur=(
                _current_secteur()
                if not current_user.has_perm("participants:view_all")
                else (request.form.get("created_secteur") or "").strip() or None
            ),
        )
        quartier_id = request.form.get("quartier_id") or None
        p.quartier_id = normalize_quartier_for_ville(p.ville, quartier_id)

        d = (request.form.get("date_naissance") or "").strip()
        if d:
            try:
                p.date_naissance = datetime.strptime(d, "%Y-%m-%d").date()
            except Exception:
                pass

        if _can_edit_insertion():
            p.pays_origine = (request.form.get("pays_origine") or "").strip() or None
            p.titre_sejour_type = (request.form.get("titre_sejour_type") or "").strip() or None
            p.diplome_obtenu = (request.form.get("diplome_obtenu") or "").strip() or None
            p.cir_obtenu = _parse_bool(request.form.get("cir_obtenu"))
            p.date_entree_dispositif = _parse_iso_date(request.form.get("date_entree_dispositif"))
            p.date_sortie_dispositif = _parse_iso_date(request.form.get("date_sortie_dispositif"))

        _appliquer_droit_image(p)
        db.session.add(p)
        db.session.flush()
        sync_legacy_insertion_fields(p, actor_id=getattr(current_user, "id", None))
        db.session.commit()
        from app.services.audit import journaliser
        journaliser("participant.create", cible=f"{p.nom} {p.prenom}")
        flash("Le participant a bien été créé.", "ok")
        return redirect(url_for("participants.edit_participant", participant_id=p.id))

    quartiers = Quartier.query.order_by(Quartier.ville.asc(), Quartier.nom.asc()).all()
    titre_sejour_options = _active_reference_options(InsertionTitreSejourTypeRef, legacy_column="titre_sejour_type")
    diplome_options = _active_reference_options(InsertionDiplomeRef, legacy_column="diplome_obtenu")
    return render_template(
        "participants/form.html",
        item=None,
        secteur=_current_secteur(),
        is_editable=True,
        can_view_sensitive_insertion=_can_view_sensitive_insertion(),
        can_edit_insertion=_can_edit_insertion(),
        quartiers=quartiers,
        titre_sejour_options=titre_sejour_options,
        diplome_options=diplome_options,
        secteurs_creation=get_secteur_labels(active_only=True),
    )


@bp.route("/<int:participant_id>/edit", methods=["GET", "POST"])
@login_required
def edit_participant(participant_id: int):

    p = db.get_or_404(Participant, participant_id)

    # Lecture globale autorisée (annuaire), mais édition verrouillée
    if not _can_read_participant(p):
        abort(403)

    is_editable = _can_edit_participant(p)

    if request.method == "POST":
        if not is_editable:
            abort(403)

        p.nom = (request.form.get("nom") or "").strip() or p.nom
        p.prenom = (request.form.get("prenom") or "").strip() or p.prenom
        p.adresse = (request.form.get("adresse") or "").strip() or None
        p.ville = (request.form.get("ville") or "").strip() or None
        email = (request.form.get("email") or "").strip() or None
        if email and ("@" not in email or "." not in email.rsplit("@", 1)[-1]):
            flash("Adresse e-mail invalide (ex. nom@domaine.fr).", "err")
            return redirect(url_for("participants.edit_participant", participant_id=p.id))
        p.email = email
        p.telephone = (request.form.get("telephone") or "").strip() or None
        p.genre = (request.form.get("genre") or "").strip() or None
        p.type_public = (request.form.get("type_public") or p.type_public or "H").strip() or "H"
        quartier_id = request.form.get("quartier_id") or None
        p.quartier_id = normalize_quartier_for_ville(p.ville, quartier_id)

        d = (request.form.get("date_naissance") or "").strip()
        if d:
            try:
                p.date_naissance = datetime.strptime(d, "%Y-%m-%d").date()
            except Exception:
                pass
        else:
            p.date_naissance = None

        if _can_edit_insertion():
            p.pays_origine = (request.form.get("pays_origine") or "").strip() or None
            p.titre_sejour_type = (request.form.get("titre_sejour_type") or "").strip() or None
            p.diplome_obtenu = (request.form.get("diplome_obtenu") or "").strip() or None
            p.cir_obtenu = _parse_bool(request.form.get("cir_obtenu"))
            p.date_entree_dispositif = _parse_iso_date(request.form.get("date_entree_dispositif"))
            p.date_sortie_dispositif = _parse_iso_date(request.form.get("date_sortie_dispositif"))

        # finance/directrice peuvent requalifier created_secteur
        if _is_global_role():
            p.created_secteur = (request.form.get("created_secteur") or "").strip() or None

        _appliquer_droit_image(p)
        sync_legacy_insertion_fields(p, actor_id=getattr(current_user, "id", None))
        db.session.commit()
        from app.services.audit import journaliser
        journaliser("participant.edit", cible=f"{p.nom} {p.prenom}")
        flash("Le participant a bien été mis à jour.", "ok")
        return redirect(url_for("participants.edit_participant", participant_id=p.id))

    quartiers = Quartier.query.order_by(Quartier.ville.asc(), Quartier.nom.asc()).all()
    titre_sejour_options = _active_reference_options(InsertionTitreSejourTypeRef, current_value=p.titre_sejour_type, legacy_column="titre_sejour_type")
    diplome_options = _active_reference_options(InsertionDiplomeRef, current_value=p.diplome_obtenu, legacy_column="diplome_obtenu")
    return render_template(
        "participants/form.html",
        item=p,
        secteur=_current_secteur(),
        is_editable=is_editable,
        can_view_sensitive_insertion=_can_view_sensitive_insertion(),
        can_edit_insertion=_can_edit_insertion(),
        quartiers=quartiers,
        titre_sejour_options=titre_sejour_options,
        diplome_options=diplome_options,
        secteurs_creation=get_secteur_labels(active_only=True),
    )


def _peut_exporter_rgpd(p: Participant) -> bool:
    """Garde dédiée à l'export intégral des données (plus stricte que l'édition).

    L'annuaire des participants est partagé entre secteurs, mais la copie
    intégrale des données d'une personne est réservée aux agents de son
    secteur (rattachement ou présence à une activité du secteur) et aux
    comptes à portée globale.
    """
    if not can("participants:edit"):
        return False
    if can("scope:all_secteurs"):
        return True
    secteur = (getattr(current_user, "secteur_assigne", None) or "").strip()
    if not secteur:
        return False
    if (p.created_secteur or "").strip() == secteur:
        return True
    from app.services.participant_privacy import participant_has_presence_in_secteur

    return participant_has_presence_in_secteur(p.id, secteur)


@bp.route("/<int:participant_id>/export-rgpd.xlsx")
@login_required
def export_rgpd(participant_id: int):
    """Export « droit d'accès » (RGPD art. 15) : toutes les données de la personne.

    Réservé aux agents habilités à gérer ce participant (cloisonnement
    secteur). Chaque export laisse une trace dans le journal de
    l'application : qui a exporté les données de qui, et quand.
    """
    p = db.get_or_404(Participant, participant_id)
    if not _peut_exporter_rgpd(p):
        abort(403)

    from app.services.rgpd_export import construire_export_rgpd

    wb = construire_export_rgpd(p)
    sortie = BytesIO()
    wb.save(sortie)
    sortie.seek(0)

    current_app.logger.warning(
        "Export RGPD (droit d'accès) du participant #%s par %s",
        p.id,
        getattr(current_user, "email", "?"),
    )
    from app.services.audit import journaliser
    journaliser("export.rgpd", cible=f"participant #{p.id}")
    nom = "".join(c if c.isalnum() else "-" for c in f"{p.nom}-{p.prenom}".lower()).strip("-")
    return send_file(
        sortie,
        as_attachment=True,
        download_name=f"droit-acces-{nom or 'participant'}-{p.id}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@bp.route("/<int:participant_id>/anonymize", methods=["POST"])
@login_required
def anonymize_participant(participant_id: int):

    p = db.get_or_404(Participant, participant_id)
    if not _can_edit_participant(p):
        abort(403)

    from app.services.purge_rgpd import anonymiser_participant

    anonymiser_participant(p, actor_id=getattr(current_user, "id", None))

    strict = (request.form.get("strict") or "").strip() == "1"
    if strict and _is_global_role():
        p.genre = None
        p.date_naissance = None
        p.quartier_id = None
        p.type_public = "H"

    db.session.commit()
    flash("Le participant a bien été anonymisé. Les statistiques sont conservées.", "ok")
    return redirect(url_for("participants.edit_participant", participant_id=p.id))


@bp.route("/<int:participant_id>/delete", methods=["POST"])
@login_required
def delete_participant(participant_id: int):
    if not can('participants:delete'):
        abort(403)

    p = db.get_or_404(Participant, participant_id)
    if not _can_edit_participant(p):
        abort(403)

    # garde-fou : un responsable secteur ne supprime pas si le participant existe ailleurs
    if not current_user.has_perm("participants:view_all"):
        sec = _current_secteur()
        other = (
            db.session.query(PresenceActivite.id)
            .join(SessionActivite, SessionActivite.id == PresenceActivite.session_id)
            .filter(PresenceActivite.participant_id == p.id)
            .filter(SessionActivite.secteur != sec)
            .first()
        )
        if other:
            flash("La suppression est refusée : ce participant est présent dans d'autres secteurs. Utilisez l'anonymisation à la place.", "err")
            return redirect(url_for("participants.edit_participant", participant_id=p.id))

    db.session.query(PresenceActivite).filter(PresenceActivite.participant_id == p.id).delete(synchronize_session=False)
    db.session.query(Evaluation).filter(Evaluation.participant_id == p.id).delete(synchronize_session=False)
    db.session.delete(p)
    commit_delete(
        f"le participant « {p.nom_complet()} »",
        "Participant supprimé définitivement.",
        success_category="warning",
        blocked_message=f"Impossible de supprimer le participant « {p.nom_complet()} » : il est encore utilisé ailleurs. Utilisez plutôt l'anonymisation si vous souhaitez conserver l'historique.",
    )
    return redirect(url_for("participants.list_participants"))


# ---------------------------------------------------------------------------
# Annuaire : ajout rapide et actions groupées (sélection par coches)
# ---------------------------------------------------------------------------

def _retour_annuaire():
    """Retourne vers l'annuaire en conservant recherche et filtres.

    Le formulaire envoie l'URL courante dans « retour » ; on ne suit que des
    chemins internes (jamais une URL absolue) pour éviter toute redirection
    ouverte.
    """
    retour = (request.form.get("retour") or "").strip()
    if retour.startswith("/") and not retour.startswith("//"):
        return redirect(retour)
    return redirect(url_for("participants.list_participants"))


@bp.post("/<int:participant_id>/date-naissance")
@login_required
@require_perm("participants:edit")
def definir_date_naissance(participant_id: int):
    """Ajout rapide de la date de naissance depuis l'annuaire, sans ouvrir la fiche."""
    p = db.get_or_404(Participant, participant_id)
    d = (request.form.get("date_naissance") or "").strip()
    try:
        p.date_naissance = datetime.strptime(d, "%Y-%m-%d").date()
    except Exception:
        flash("Date de naissance invalide.", "err")
        return _retour_annuaire()
    db.session.commit()
    from app.services.audit import journaliser
    journaliser("participant.edit", cible=f"{p.nom} {p.prenom} (date de naissance)")
    flash(f"Date de naissance enregistrée pour {p.prenom} {p.nom}.", "ok")
    return _retour_annuaire()


@bp.post("/actions-groupees")
@login_required
@require_perm("participants:view")
def actions_groupees():
    """Agit sur les personnes cochées dans l'annuaire.

    Chaque action revérifie SA permission : regrouper en famille demande
    l'édition, anonymiser la permission dédiée, supprimer la suppression.
    """
    action = (request.form.get("action") or "").strip()
    ids: list[int] = []
    for brut in request.form.getlist("pid"):
        try:
            pid = int(brut)
        except Exception:
            continue
        if pid not in ids:
            ids.append(pid)
    membres = [p for p in (db.session.get(Participant, pid) for pid in ids) if p is not None]

    if not membres:
        flash("Coche d'abord au moins une personne dans la liste.", "warning")
        return _retour_annuaire()

    from app.services.audit import journaliser

    if action == "famille":
        if not can("participants:edit"):
            abort(403)
        from app.services.cotisations import regrouper_en_foyer
        ok, message = regrouper_en_foyer(membres)
        if ok:
            journaliser("participant.famille", cible=message)
        flash(message, "ok" if ok else "err")
        return _retour_annuaire()

    if action == "anonymiser":
        if not can("participants:anonymize"):
            abort(403)
        from app.services.purge_rgpd import anonymiser_participant
        traites = 0
        for p in membres:
            if p.nom == NOM_ANONYME:
                continue  # déjà anonymisée : on ne retouche pas la fiche
            anonymiser_participant(p, actor_id=getattr(current_user, "id", None))
            journaliser("participant.anonymize", cible=f"participant #{p.id}")
            traites += 1
        db.session.commit()
        flash(
            f"{traites} fiche(s) anonymisée(s). Les présences et statistiques sont conservées."
            if traites else "Toutes les fiches cochées étaient déjà anonymisées.",
            "ok" if traites else "info",
        )
        return _retour_annuaire()

    if action == "supprimer":
        if not can("participants:delete"):
            abort(403)
        from sqlalchemy.exc import IntegrityError
        supprimes, proteges, bloques = 0, [], []
        est_global = current_user.has_perm("participants:view_all")
        sec = _current_secteur()
        for p in membres:
            # Même garde que la suppression unitaire : un agent borné à son
            # secteur ne supprime pas une personne suivie ailleurs.
            if not est_global:
                ailleurs = (
                    db.session.query(PresenceActivite.id)
                    .join(SessionActivite, SessionActivite.id == PresenceActivite.session_id)
                    .filter(PresenceActivite.participant_id == p.id)
                    .filter(SessionActivite.secteur != sec)
                    .first()
                )
                if ailleurs:
                    proteges.append(f"{p.prenom} {p.nom}")
                    continue
            nom_complet = f"{p.prenom} {p.nom}"
            try:
                db.session.query(PresenceActivite).filter(PresenceActivite.participant_id == p.id).delete(synchronize_session=False)
                db.session.query(Evaluation).filter(Evaluation.participant_id == p.id).delete(synchronize_session=False)
                db.session.delete(p)
                db.session.commit()
                journaliser("participant.delete", cible=nom_complet)
                supprimes += 1
            except IntegrityError:
                db.session.rollback()
                bloques.append(nom_complet)
        if supprimes:
            flash(f"{supprimes} fiche(s) supprimée(s) définitivement.", "warning")
        if proteges:
            flash(
                "Non supprimées (présentes dans d'autres secteurs — utilisez l'anonymisation) : "
                + ", ".join(proteges), "err",
            )
        if bloques:
            flash(
                "Non supprimées (encore utilisées ailleurs dans l'application — utilisez l'anonymisation) : "
                + ", ".join(bloques), "err",
            )
        return _retour_annuaire()

    if action == "export":
        sortie = StringIO()
        w = csv.writer(sortie, delimiter=";")
        w.writerow(["Nom", "Prénom", "Date de naissance", "Genre", "Email", "Téléphone",
                    "Ville", "Quartier", "Type de public", "Famille"])
        for p in membres:
            w.writerow([
                p.nom, p.prenom,
                p.date_naissance.strftime("%d/%m/%Y") if p.date_naissance else "",
                p.genre or "", p.email or "", p.telephone or "",
                p.ville or "", p.quartier.nom if p.quartier else "",
                p.type_public or "",
                (p.foyer.nom or f"Foyer n°{p.foyer.id}") if getattr(p, "foyer", None) else "",
            ])
        journaliser("export.selection", cible=f"{len(membres)} participant(s)")
        return Response(
            "\ufeff" + sortie.getvalue(),  # BOM : accents corrects dans Excel
            mimetype="text/csv; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=selection-participants.csv"},
        )

    flash("Choisis une action à appliquer à la sélection.", "warning")
    return _retour_annuaire()


from flask import current_app

_FAKE_PREFIXES = (
    "CAPACITE", "CAPACITÉ",
    "DUREE", "DURÉE",
    "NOMBRE",
    "TAUX",
    "MOYENNE",
    "TOTAL",
    "NB ",
    "? ",
    "STAT",
    "NBRE ",
)

@bp.route("/cleanup-fakes", methods=["POST"])
@login_required
def cleanup_fakes():
    # Autorisation : tu peux choisir soit participants:delete, soit ateliers:sync
    if not (can("participants:delete") or can("ateliers:sync")):
        abort(403)

    # Mode prévisualisation (par défaut = 1)
    preview = (request.form.get("preview") or "1").strip() in ("1", "true", "on", "yes")

    # On limite au secteur de l'utilisateur (sauf rôles globaux)
    sec = _current_secteur()
    if not _is_global_role() and not sec:
        abort(403)

    # Regex simple : commence par un des mots-clés (avec ou sans accents), + éventuellement " ?"
    # Exemple: "CAPACITE D'ACCUEIL ?" / "DUREE DE L'ACTIVITE ?"
    def is_fake(p: Participant) -> bool:
        nom = (p.nom or "").strip()
        prenom = (p.prenom or "").strip()

        # Souvent les faux ont un prénom vide / bizarre
        # (si chez toi c'est toujours prenom rempli, on peut retirer ce critère)
        if prenom and len(prenom) > 1:
            return False

        up = nom.upper()

        # commence par un préfixe "métier"
        if any(up.startswith(pref) for pref in _FAKE_PREFIXES):
            return True

        # ou contient un gros marqueur Excel foireux
        if "?" in nom or "??" in nom:
            # on évite de supprimer un vrai "?" improbable, mais c'est ultra rare
            return True

        return False

    # Base query (filtre secteur si non global)
    q = Participant.query
    if not _is_global_role():
        q = q.filter(Participant.created_secteur == sec)

    # On charge un lot raisonnable
    candidates = q.order_by(Participant.id.desc()).limit(5000).all()

    # On ne supprime jamais si le participant a des présences (sécurité)
    fake_ids = []
    fake_labels = []
    for p in candidates:
        if not is_fake(p):
            continue

        has_presence = (
            db.session.query(PresenceActivite.id)
            .filter(PresenceActivite.participant_id == p.id)
            .first()
        )
        if has_presence:
            continue

        fake_ids.append(p.id)
        fake_labels.append(f"{p.id} — {p.nom} {p.prenom}".strip())

    if not fake_ids:
        flash("Aucune fiche importée par erreur n'a été détectée.", "success")
        return redirect(url_for("participants.list_participants"))

    if preview:
        # On montre juste un échantillon, sans supprimer
        sample = fake_labels[:20]
        flash(
            f"Prévisualisation : {len(fake_ids)} faux participant(s) détecté(s). "
            f"Exemples: {', '.join(sample)}",
            "warning"
        )
        flash("Relancez l'action sans prévisualisation pour supprimer réellement les fiches détectées.", "info")
        return redirect(url_for("participants.list_participants"))

    # Suppression réelle (sans présences, donc safe FK)
    try:
        db.session.query(Evaluation).filter(Evaluation.participant_id.in_(fake_ids)).delete(synchronize_session=False)
        # PresenceActivite devrait être vide par sécurité, mais on nettoie quand même
        db.session.query(PresenceActivite).filter(PresenceActivite.participant_id.in_(fake_ids)).delete(synchronize_session=False)

        db.session.query(Participant).filter(Participant.id.in_(fake_ids)).delete(synchronize_session=False)
        db.session.commit()
        flash(f"Le nettoyage a bien été effectué : {len(fake_ids)} fiche(s) ont été supprimées.", "success")
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception("cleanup_fakes failed")
        flash(f"Le nettoyage a échoué : {e}", "danger")

    return redirect(url_for("participants.list_participants"))


import re
from collections import defaultdict
from difflib import SequenceMatcher

def _norm(s: str | None) -> str:
    s = (s or "").strip().lower()
    # enlève accents simples (sans dépendance)
    s = s.replace("é", "e").replace("è", "e").replace("ê", "e").replace("ë", "e")
    s = s.replace("à", "a").replace("â", "a")
    s = s.replace("î", "i").replace("ï", "i")
    s = s.replace("ô", "o")
    s = s.replace("ù", "u").replace("û", "u").replace("ü", "u")
    # garde lettres/chiffres/espace
    s = re.sub(r"[^a-z0-9\s\-']", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def _phone_norm(s: str | None) -> str:
    s = re.sub(r"\D+", "", (s or ""))
    # garde les 10 derniers chiffres (pratique FR)
    return s[-10:] if len(s) >= 10 else s

def _sim(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()

@bp.route("/duplicates", methods=["GET"])
@login_required
def duplicates():
    if not _can_read_participant(None):  # simple garde-fou
        abort(403)

    # réglages
    mode = (request.args.get("mode") or "certain").strip()  # certain / probable
    threshold = float(request.args.get("t") or "0.90")      # pour probable
    limit = int(request.args.get("limit") or "2000")

    # scope secteur pour non-global
    sec = _current_secteur()
    q = Participant.query
    if not _is_global_role():
        if not sec:
            abort(403)
        q = q.filter(Participant.created_secteur == sec)

    items = q.order_by(Participant.id.desc()).limit(limit).all()

    # --- doublons certains ---
    by_name = defaultdict(list)
    by_email = defaultdict(list)
    by_phone = defaultdict(list)

    for p in items:
        key_name = f"{_norm(p.nom)}|{_norm(p.prenom)}"
        if _norm(p.nom) and _norm(p.prenom):
            by_name[key_name].append(p)

        em = _norm(getattr(p, "email", None))
        if em:
            by_email[em].append(p)

        ph = _phone_norm(getattr(p, "telephone", None))
        if ph:
            by_phone[ph].append(p)

    groups = []
    def push_groups(source: str, dct):
        for k, arr in dct.items():
            if len(arr) >= 2:
                groups.append({
                    "type": source,
                    "key": k,
                    "items": sorted(arr, key=lambda x: (x.nom or "", x.prenom or "", x.id))
                })

    push_groups("nom_prenom", by_name)
    push_groups("email", by_email)
    push_groups("telephone", by_phone)

    # --- doublons probables (approx) ---
    probable = []
    if mode == "probable":
        # on compare uniquement sur nom/prénom normalisés
        normed = [(p, _norm(p.nom), _norm(p.prenom)) for p in items if _norm(p.nom) and _norm(p.prenom)]
        # petit tri pour limiter comparaisons
        normed.sort(key=lambda x: (x[1][:3], x[2][:3], x[0].id))

        # comparaison locale par “fenêtre” (évite O(n²) complet)
        for i in range(len(normed)):
            p1, n1, pr1 = normed[i]
            base1 = f"{n1} {pr1}"
            for j in range(i+1, min(i+15, len(normed))):
                p2, n2, pr2 = normed[j]
                # si les 3 premières lettres divergent trop, on stop
                if n2[:3] != n1[:3] and pr2[:3] != pr1[:3]:
                    continue
                base2 = f"{n2} {pr2}"
                s = _sim(base1, base2)
                if s >= threshold and p1.id != p2.id:
                    probable.append({
                        "score": round(s, 3),
                        "a": p1,
                        "b": p2
                    })

        probable = sorted(probable, key=lambda x: x["score"], reverse=True)[:200]

    # tri groupes certains : les plus gros d’abord
    groups.sort(key=lambda g: (-len(g["items"]), g["type"], g["key"]))

    return render_template(
        "participants/duplicates.html",
        groups=groups,
        probable=probable,
        mode=mode,
        threshold=threshold,
        secteur=sec,
        is_global=_is_global_role(),
    )


@bp.route("/merge", methods=["POST"])
@login_required
def merge_participants():
    if not can("participants:delete"):
        abort(403)

    keep_id = int(request.form.get("keep_id") or 0)
    merge_ids = request.form.getlist("merge_ids")
    merge_ids = [int(x) for x in merge_ids if str(x).isdigit() and int(x) != keep_id]

    if not keep_id or not merge_ids:
        flash("La fusion est impossible : la sélection est invalide.", "danger")
        return redirect(url_for("participants.duplicates"))

    db.get_or_404(Participant, keep_id)
    victims = Participant.query.filter(Participant.id.in_(merge_ids)).all()

    try:
        # 1) Transférer Evaluations (safe)
        db.session.query(Evaluation).filter(Evaluation.participant_id.in_(merge_ids))\
            .update({Evaluation.participant_id: keep_id}, synchronize_session=False)

        # 2) Présences: éviter les collisions AVANT l'update
        # sessions déjà présentes chez keep
        keep_session_ids = set(
            sid for (sid,) in db.session.query(PresenceActivite.session_id)
            .filter(PresenceActivite.participant_id == keep_id)
            .all()
        )

        if keep_session_ids:
            # supprimer les présences des victims qui collent sur les mêmes sessions
            db.session.query(PresenceActivite)\
                .filter(PresenceActivite.participant_id.in_(merge_ids))\
                .filter(PresenceActivite.session_id.in_(keep_session_ids))\
                .delete(synchronize_session=False)

        # maintenant, update sans risque
        db.session.query(PresenceActivite)\
            .filter(PresenceActivite.participant_id.in_(merge_ids))\
            .update({PresenceActivite.participant_id: keep_id}, synchronize_session=False)

        # 3) Delete des participants doublons (ils n'ont plus de liens)
        for v in victims:
            db.session.delete(v)

        db.session.commit()
        flash(f"La fusion a bien été effectuée : le participant #{keep_id} a absorbé {len(merge_ids)} doublon(s).", "success")
        return redirect(url_for("participants.edit_participant", participant_id=keep_id))

    except Exception as e:
        db.session.rollback()
        flash(f"La fusion a échoué : {e}", "danger")
        return redirect(url_for("participants.duplicates"))


# ---------------------------------------------------------------------
# Import d'un annuaire de participants depuis un tableur (Excel)
# ---------------------------------------------------------------------
import os
import uuid

from app.services.import_participants import (
    analyser as _import_analyser,
    charger_classeur as _import_charger,
    dedup_ambigus as _import_dedup_ambigus,
    normaliser as _import_normaliser,
)


def _import_cles_base():
    """Clés des participants déjà en base : fortes (nom+prénom+année) et faibles (nom+prénom)."""
    fortes, faibles = set(), set()
    for nom, prenom, dn in db.session.query(
        Participant.nom, Participant.prenom, Participant.date_naissance
    ):
        faible = (_import_normaliser(nom), _import_normaliser(prenom))
        faibles.add(faible)
        if dn:
            fortes.add((_import_normaliser(nom), _import_normaliser(prenom), dn.year))
    return fortes, faibles


def _import_dossier():
    chemin = os.path.join(current_app.instance_path, "imports")
    os.makedirs(chemin, exist_ok=True)
    return chemin


def _import_resoudre_quartier_id(ville, nom, qpv=False):
    """Relie à un quartier (ville + nom), en le CRÉANT s'il n'existe pas.

    - Ne crée rien si le « quartier » est en réalité la commune (nom == ville)
      ou si l'une des deux valeurs manque.
    - ``qpv`` (drapeau Quartier prioritaire) n'est appliqué qu'à la création :
      on n'écrase jamais le réglage QPV d'un quartier déjà présent.
    """
    ville = (ville or "").strip()
    nom = (nom or "").strip()
    if not (ville and nom) or nom.lower() == ville.lower():
        return None
    q = Quartier.query.filter(
        db.func.lower(Quartier.ville) == ville.lower(),
        db.func.lower(Quartier.nom) == nom.lower(),
    ).first()
    if q is None:
        q = Quartier(ville=ville, nom=nom, is_qpv=bool(qpv))
        db.session.add(q)
        db.session.flush()
    return q.id


def _import_acces_autorise() -> bool:
    # L'import en masse est une opération globale : réservé aux comptes à portée globale.
    return bool(can("participants:edit")) and bool(can("scope:all_secteurs"))


@bp.route("/import", methods=["GET", "POST"])
@login_required
def import_annuaire():
    if not _import_acces_autorise():
        abort(403)

    if request.method == "POST":
        fichier = request.files.get("fichier")
        if not fichier or not fichier.filename:
            flash("Veuillez choisir un fichier .xlsx.", "danger")
            return render_template("participants/import.html", rapport=None)
        if not fichier.filename.lower().endswith(".xlsx"):
            flash("Format non reconnu : fournissez un fichier Excel .xlsx.", "danger")
            return render_template("participants/import.html", rapport=None)

        token = uuid.uuid4().hex
        chemin = os.path.join(_import_dossier(), f"{token}.xlsx")
        fichier.save(chemin)

        try:
            feuilles = _import_charger(chemin)
        except Exception:
            current_app.logger.exception("Import participants : lecture du classeur impossible")
            try:
                os.remove(chemin)
            except OSError:
                pass
            flash("Impossible de lire ce fichier Excel. Vérifiez qu'il n'est pas corrompu.", "danger")
            return render_template("participants/import.html", rapport=None)

        fortes, faibles = _import_cles_base()
        rapport = _import_analyser(feuilles, cles_existantes=fortes, cles_faibles_existantes=faibles)
        return render_template(
            "participants/import.html",
            rapport=rapport,
            token=token,
            apercu_nouveaux=rapport.nouveaux[:15],
            apercu_ambigus=rapport.ambigus[:15],
        )

    return render_template("participants/import.html", rapport=None)


@bp.route("/import/confirmer", methods=["POST"])
@login_required
def import_annuaire_confirmer():
    if not _import_acces_autorise():
        abort(403)

    token = (request.form.get("token") or "").strip()
    inclure_ambigus = request.form.get("inclure_ambigus") in {"1", "true", "on", "yes"}

    # Sécurité : un token est un hex de 32 caractères, rien d'autre (anti-traversée de chemin).
    if not token or len(token) != 32 or not all(c in "0123456789abcdef" for c in token):
        flash("Session d'import expirée. Recommencez l'analyse.", "danger")
        return redirect(url_for("participants.import_annuaire"))

    chemin = os.path.join(_import_dossier(), f"{token}.xlsx")
    if not os.path.exists(chemin):
        flash("Fichier d'import introuvable (peut-être déjà traité). Recommencez l'analyse.", "danger")
        return redirect(url_for("participants.import_annuaire"))

    feuilles = _import_charger(chemin)
    fortes, faibles = _import_cles_base()
    rapport = _import_analyser(feuilles, cles_existantes=fortes, cles_faibles_existantes=faibles)

    crees = 0
    faibles_courantes = set(faibles)
    for p in rapport.nouveaux:
        db.session.add(Participant(
            nom=p.nom, prenom=p.prenom, genre=p.sexe,
            date_naissance=date(p.annee, 1, 1),  # jour/mois inconnus : 1er janvier de l'année connue
            type_public="H",
            adresse=p.adresse, ville=p.ville,
            quartier_id=_import_resoudre_quartier_id(p.ville, p.quartier, p.qpv),
        ))
        faibles_courantes.add(p.cle_faible)
        crees += 1

    crees_ambigus = 0
    if inclure_ambigus:
        for p in _import_dedup_ambigus(rapport.ambigus, faibles_courantes):
            db.session.add(Participant(
                nom=p.nom, prenom=p.prenom, genre=p.sexe, type_public="H",
                adresse=p.adresse, ville=p.ville,
                quartier_id=_import_resoudre_quartier_id(p.ville, p.quartier, p.qpv),
            ))
            faibles_courantes.add(p.cle_faible)
            crees_ambigus += 1

    db.session.commit()
    try:
        os.remove(chemin)
    except OSError:
        pass

    current_app.logger.warning(
        "Import participants par %s : %s fiche(s) créée(s) (dont %s ambiguës).",
        getattr(current_user, "email", "?"), crees + crees_ambigus, crees_ambigus,
    )
    if crees + crees_ambigus:
        flash(f"{crees + crees_ambigus} fiche(s) créée(s) ({crees} fiables, {crees_ambigus} sans date de naissance).", "success")
    else:
        flash("Aucune nouvelle fiche à créer (tout existait déjà).", "info")
    return redirect(url_for("participants.list_participants"))


from app.participants import cotisations as _cotisations_routes  # noqa: E402,F401
