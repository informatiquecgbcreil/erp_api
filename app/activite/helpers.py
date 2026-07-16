import os
from datetime import date


from flask import (
    request,
    redirect,
    url_for,
    flash,
    current_app,
    abort,
)
from flask_login import current_user
from sqlalchemy import or_, false

from app.extensions import db
from app.models import (
    AtelierActivite,
    SessionActivite,
    Participant,
    PresenceActivite,
    Referentiel,
    AtelierCapaciteMois,
    ArchiveEmargement,
    Objectif,
    Secteur,
)

from ..rbac import can_access_secteur
from .services.emargement_models import (
    GLOBAL_SCOPE,
    CUSTOM_SENTINEL,
    selection_options,
    decode_storage_value,
)



PASSPORT_NOTE_CATEGORIES = {"journal", "participation", "progression", "temoignage", "session"}


def _require_any_perm(*codes: str) -> None:
    if not current_user.is_authenticated:
        abort(403)
    if not any(current_user.has_perm(code) for code in codes):
        abort(403)


def _normalize_note_category(raw: str | None) -> str:
    val = (raw or "session").strip().lower()
    return val if val in PASSPORT_NOTE_CATEGORIES else "session"


# ------------------ Helpers ------------------


def _is_admin_global() -> bool:
    return current_user.is_authenticated and getattr(current_user, "has_role", lambda *_: False)("admin_tech")


def _has_all_secteurs_scope() -> bool:
    return current_user.is_authenticated and bool(getattr(current_user, "has_perm", lambda *_: False)("scope:all_secteurs"))


def _user_secteur() -> str:
    requested = (request.args.get("secteur") or "").strip()
    assigned = (getattr(current_user, "secteur_assigne", None) or "").strip()
    if _is_admin_global() or _has_all_secteurs_scope():
        return requested or assigned
    return assigned


def _can_access_activity_secteur(secteur: str | None) -> bool:
    return _is_admin_global() or can_access_secteur(secteur)


def _deny_activity_access():
    flash("Accès refusé pour ce secteur.", "danger")
    return redirect(url_for("activite.index"))



def _parse_iso_date_arg(raw: str | None):
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw).strip()[:10])
    except Exception:
        return None


def _parse_int_list(values) -> list[int]:
    ids: list[int] = []
    seen: set[int] = set()
    for value in values or []:
        for raw in str(value or "").replace(";", ",").split(","):
            raw = raw.strip()
            if not raw:
                continue
            try:
                item_id = int(raw)
            except Exception:
                continue
            if item_id > 0 and item_id not in seen:
                ids.append(item_id)
                seen.add(item_id)
    return ids


def _session_effective_date_expr():
    return db.func.coalesce(SessionActivite.rdv_date, SessionActivite.date_session)


def _format_decimal_fr(value: float | int | None, precision: int = 2) -> str:
    try:
        number = round(float(value or 0), precision)
    except Exception:
        number = 0.0
    if number.is_integer():
        return str(int(number))
    return f"{number:.{precision}f}".rstrip("0").rstrip(".").replace(".", ",")


def _attestation_selected_participant_ids() -> list[int]:
    return _parse_int_list(
        request.values.getlist("participant_ids")
        + request.values.getlist("participant_id")
        + request.values.getlist("ids")
    )


def _resolve_attestation_secteur() -> str:
    requested = (request.values.get("secteur") or "").strip()
    assigned = (getattr(current_user, "secteur_assigne", None) or "").strip()
    if _is_admin_global() or _has_all_secteurs_scope():
        return requested or ""
    return assigned


def _attestation_participant_scope_query(secteur: str | None):
    query = (
        db.session.query(Participant)
        .join(PresenceActivite, PresenceActivite.participant_id == Participant.id)
        .join(SessionActivite, SessionActivite.id == PresenceActivite.session_id)
        .filter(SessionActivite.is_deleted.is_(False))
        .filter(or_(SessionActivite.statut.is_(None), SessionActivite.statut != "annulee"))
    )
    if secteur:
        query = query.filter(SessionActivite.secteur == secteur)
    elif not (_is_admin_global() or _has_all_secteurs_scope()):
        query = query.filter(false())
    return query.distinct()


def _attestation_participant_options(secteur: str | None, q: str | None, selected_ids: list[int]) -> list[Participant]:
    query = _attestation_participant_scope_query(secteur)
    if q:
        like = f"%{q.lower()}%"
        query = query.filter(
            or_(
                db.func.lower(Participant.nom).like(like),
                db.func.lower(Participant.prenom).like(like),
                db.func.lower(db.func.coalesce(Participant.email, "")).like(like),
                db.func.lower(db.func.coalesce(Participant.telephone, "")).like(like),
            )
        )
    if selected_ids:
        selected_query = _attestation_participant_scope_query(secteur).filter(Participant.id.in_(selected_ids))
        selected = {p.id: p for p in selected_query.all()}
    else:
        selected = {}
    rows = query.order_by(Participant.nom.asc(), Participant.prenom.asc()).limit(800).all()
    by_id = {p.id: p for p in rows}
    by_id.update(selected)
    return sorted(by_id.values(), key=lambda p: ((p.nom or "").lower(), (p.prenom or "").lower(), p.id))


def _attestation_options_from_request() -> dict[str, bool]:
    defaults = {
        "include_period": True,
        "include_hours": True,
        "include_sessions": True,
        "include_ateliers_count": True,
        "include_activities_detail": True,
        "include_source": True,
    }
    if request.method != "POST":
        return defaults
    return {key: request.form.get(key) == "1" for key in defaults}


def _attestation_document_fields() -> dict[str, str]:
    try:
        from app.services.instance_settings import resolve_identity

        _app_name, organization_name, _app_logo, _org_logo = resolve_identity(
            current_app.config.get("APP_NAME", "Gestion du Centre"),
            current_app.config.get("ORGANIZATION_NAME", "Centre Social Georges Brassens"),
        )
    except Exception:
        organization_name = current_app.config.get("ORGANIZATION_NAME", "Centre Social Georges Brassens")

    centre_name = (request.values.get("centre_name") or organization_name or "Centre Social Georges Brassens").strip()
    centre_text_name = (request.values.get("centre_text_name") or centre_name).strip()
    centre_determiner = (request.values.get("centre_determiner") or "du").strip()
    allowed_determiners = {"de", "du", "de la", "de l'", "de l’", "des"}
    if centre_determiner not in allowed_determiners:
        centre_determiner = "du"
    centre_reference = f"{centre_determiner}{centre_text_name}" if centre_determiner in {"de l'", "de l’"} else f"{centre_determiner} {centre_text_name}"

    return {
        "centre_name": centre_name,
        "centre_text_name": centre_text_name,
        "centre_determiner": centre_determiner,
        "centre_reference": centre_reference,
        "centre_address": (request.values.get("centre_address") or "Creil").strip(),
        "centre_contact": (request.values.get("centre_contact") or "").strip(),
        "signatory_title": (request.values.get("signatory_title") or "pour le compte du centre").strip(),
        "city": (request.values.get("city") or "Creil").strip(),
    }


def _build_attestation_summaries(
    participant_ids: list[int],
    *,
    secteur: str | None,
    date_from: date | None,
    date_to: date | None,
) -> list[dict]:
    if not participant_ids:
        return []

    scoped_participants = (
        _attestation_participant_scope_query(secteur)
        .filter(Participant.id.in_(participant_ids))
        .all()
    )
    participant_by_id = {p.id: p for p in scoped_participants}
    ordered_ids = [pid for pid in participant_ids if pid in participant_by_id]
    if not ordered_ids:
        return []

    summaries = {
        pid: {
            "participant": participant_by_id[pid],
            "total_sessions": 0,
            "total_hours": 0.0,
            "total_hours_label": "0",
            "first_date": None,
            "last_date": None,
            "activity_map": {},
            "activity_rows": [],
            "activity_count": 0,
        }
        for pid in ordered_ids
    }

    effective_date = _session_effective_date_expr()
    rows_query = (
        db.session.query(PresenceActivite, SessionActivite, AtelierActivite)
        .join(SessionActivite, SessionActivite.id == PresenceActivite.session_id)
        .join(AtelierActivite, AtelierActivite.id == SessionActivite.atelier_id)
        .filter(PresenceActivite.participant_id.in_(ordered_ids))
        .filter(SessionActivite.is_deleted.is_(False))
        .filter(AtelierActivite.is_deleted.is_(False))
        .filter(or_(SessionActivite.statut.is_(None), SessionActivite.statut != "annulee"))
        .filter(effective_date.isnot(None))
    )
    if secteur:
        rows_query = rows_query.filter(SessionActivite.secteur == secteur)
    elif not (_is_admin_global() or _has_all_secteurs_scope()):
        rows_query = rows_query.filter(false())
    if date_from:
        rows_query = rows_query.filter(effective_date >= date_from)
    if date_to:
        rows_query = rows_query.filter(effective_date <= date_to)

    rows = rows_query.order_by(PresenceActivite.participant_id.asc(), effective_date.asc(), SessionActivite.id.asc()).all()
    for presence, session, atelier in rows:
        summary = summaries.get(presence.participant_id)
        if not summary:
            continue
        session_date = session.date_reference
        hours = max(float(session.duree_heures or 0), 0.0)
        summary["total_sessions"] += 1
        summary["total_hours"] += hours
        if session_date and (summary["first_date"] is None or session_date < summary["first_date"]):
            summary["first_date"] = session_date
        if session_date and (summary["last_date"] is None or session_date > summary["last_date"]):
            summary["last_date"] = session_date
        activity = summary["activity_map"].setdefault(
            atelier.id,
            {
                "name": atelier.nom or "Activite sans nom",
                "sessions": 0,
                "hours": 0.0,
                "hours_label": "0",
                "first_date": None,
                "last_date": None,
            },
        )
        activity["sessions"] += 1
        activity["hours"] += hours
        if session_date and (activity["first_date"] is None or session_date < activity["first_date"]):
            activity["first_date"] = session_date
        if session_date and (activity["last_date"] is None or session_date > activity["last_date"]):
            activity["last_date"] = session_date

    for summary in summaries.values():
        summary["total_hours_label"] = _format_decimal_fr(summary["total_hours"])
        activity_rows = list(summary["activity_map"].values())
        for activity in activity_rows:
            activity["hours_label"] = _format_decimal_fr(activity["hours"])
        activity_rows.sort(key=lambda item: (-item["sessions"], item["name"].lower()))
        summary["activity_rows"] = activity_rows
        summary["activity_count"] = len(activity_rows)

    return [summaries[pid] for pid in ordered_ids]


def _consumption_period_request_args() -> dict:
    """Paramètres GET/POST utilisés pour le cumul individuel d'émargement."""
    mode = (request.values.get("conso_period_mode") or "civil_year").strip() or "civil_year"
    start = (request.values.get("conso_period_start") or "").strip()
    end = (request.values.get("conso_period_end") or "").strip()
    return {
        "period_mode": mode,
        "period_start": start,
        "period_end": end,
    }



def _materiel_ids_from_request_form(field_name: str = "materiel_individuel_ids") -> list[int]:
    """Lit une sélection multiple de matériels, avec compatibilité ancien champ simple."""
    raw_values = request.form.getlist(field_name)
    if not raw_values:
        legacy = (request.form.get("materiel_individuel_id") or "").strip()
        raw_values = [legacy] if legacy else []
    ids: list[int] = []
    seen: set[int] = set()
    for raw in raw_values:
        try:
            mid = int(str(raw).strip())
        except Exception:
            continue
        if mid > 0 and mid not in seen:
            ids.append(mid)
            seen.add(mid)
    return ids


def _period_query_kwargs() -> dict:
    args = _consumption_period_request_args()
    return {
        "conso_period_mode": args["period_mode"],
        "conso_period_start": args["period_start"],
        "conso_period_end": args["period_end"],
    }


def _redirect_emargement_with_period(session_id: int, **extra):
    params = {k: v for k, v in _period_query_kwargs().items() if v}
    params.update(extra)
    return redirect(url_for("activite.emargement", session_id=session_id, **params))


def _ensure_activity_secteur_context() -> str | None:
    secteur = _user_secteur()
    if secteur:
        return secteur
    if _is_admin_global() or _has_all_secteurs_scope():
        flash("Sélectionnez un secteur pour accéder à cette vue.", "warning")
    else:
        flash("Aucun secteur n'est associé à votre compte.", "warning")
    return None


def _load_referentiels():
    return Referentiel.query.order_by(Referentiel.nom.asc()).all()


def _available_secteur_labels() -> list[str]:
    labels = [s.label for s in Secteur.query.filter(Secteur.is_active.is_(True)).order_by(Secteur.label.asc()).all() if (s.label or '').strip()]
    if not labels:
        atelier_labels = [row[0] for row in db.session.query(AtelierActivite.secteur).distinct().order_by(AtelierActivite.secteur.asc()).all() if (row[0] or '').strip()]
        labels = atelier_labels
    return labels


def _can_manage_model_for_secteur(target_secteur: str | None) -> bool:
    wanted = (target_secteur or '').strip()
    if _is_admin_global() or _has_all_secteurs_scope():
        return True
    assigned = (getattr(current_user, 'secteur_assigne', None) or '').strip()
    if not assigned:
        return False
    if not wanted or wanted == GLOBAL_SCOPE:
        return False
    return assigned == wanted


def _atelier_model_context(secteur: str, atelier=None):
    collectif_options = selection_options(secteur, 'COLLECTIF')
    individuel_options = selection_options(secteur, 'INDIVIDUEL_MENSUEL')

    collectif_selected, collectif_has_custom = decode_storage_value(getattr(atelier, 'modele_docx_collectif', None) if atelier else None)
    individuel_selected, individuel_has_custom = decode_storage_value(getattr(atelier, 'modele_docx_individuel', None) if atelier else None)

    collectif_current_custom = getattr(atelier, 'modele_docx_collectif', None) if (atelier and collectif_has_custom) else None
    individuel_current_custom = getattr(atelier, 'modele_docx_individuel', None) if (atelier and individuel_has_custom) else None

    return {
        'emargement_collectif_options': collectif_options,
        'emargement_individuel_options': individuel_options,
        'emargement_collectif_selected': collectif_selected or (CUSTOM_SENTINEL if collectif_has_custom else ''),
        'emargement_individuel_selected': individuel_selected or (CUSTOM_SENTINEL if individuel_has_custom else ''),
        'emargement_collectif_has_custom': collectif_has_custom,
        'emargement_individuel_has_custom': individuel_has_custom,
        'emargement_collectif_custom_value': collectif_current_custom,
        'emargement_individuel_custom_value': individuel_current_custom,
    }


def _ensure_seed_ateliers(secteur: str) -> None:
    """Seed minimal ateliers for a smoother IRL start.

    We keep this extremely conservative to avoid surprising colleagues.
    - Numérique: two INDIVIDUEL_MENSUEL ateliers requested by Antoine
    """
    if not secteur:
        return
    if secteur.strip().lower() not in {"numérique", "numerique"}:
        return

    # If the sector already has ateliers, we don't seed anything.
    if AtelierActivite.query.filter_by(secteur=secteur, is_deleted=False).count() > 0:
        return

    seeds = [
        ("S.O(rdi).S", "INDIVIDUEL_MENSUEL"),
        ("Accès aux droits", "INDIVIDUEL_MENSUEL"),
    ]
    for nom, type_atelier in seeds:
        db.session.add(
            AtelierActivite(
                secteur=secteur,
                nom=nom,
                type_atelier=type_atelier,
                # heures_dispo_defaut_mois left to the sector referent
                heures_dispo_defaut_mois=None,
                # Provide a sensible default motifs list (editable)
                motifs_json=None,
            )
        )
    db.session.commit()


def _safe_unlink(path: str | None) -> None:
    """Supprime un fichier si possible, sans jamais faire planter la requête."""
    if not path:
        return
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


def _sort_competences(items):
    return sorted(items, key=lambda c: ((c.code or "").lower(), (c.nom or "").lower()))


def _collect_session_competences(session: SessionActivite, session_objectifs: list[Objectif] | None = None):
    """Retourne les compétences de session via 3 sources fusionnées:

    1) compétences explicitement liées à la session,
    2) compétences des modules liés à la session,
    3) compétences des objectifs opérationnels de session.

    Cette fusion évite le bug où l'évaluation affiche "Aucune compétence"
    alors que des modules ont bien été sélectionnés.
    """
    by_id = {}

    for comp in (getattr(session, "competences", []) or []):
        by_id[comp.id] = comp

    for module in (getattr(session, "modules", []) or []):
        for comp in (getattr(module, "competences", []) or []):
            by_id[comp.id] = comp

    objectifs = session_objectifs
    if objectifs is None:
        objectifs = Objectif.query.filter_by(session_id=session.id, type="operationnel").all()

    for obj in objectifs:
        for comp in (getattr(obj, "competences", []) or []):
            by_id[comp.id] = comp

    return _sort_competences(list(by_id.values()))


def _ensure_month_capacity(atelier: AtelierActivite, session: SessionActivite) -> None:
    """Crée la capacité mensuelle si elle n'existe pas (ateliers INDIVIDUEL_MENSUEL)."""
    if atelier.type_atelier != "INDIVIDUEL_MENSUEL":
        return
    if not session.rdv_date:
        return
    annee, mois = session.rdv_date.year, session.rdv_date.month
    cap = AtelierCapaciteMois.query.filter_by(atelier_id=atelier.id, annee=annee, mois=mois).first()
    if cap:
        return
    heures = float(atelier.heures_dispo_defaut_mois or 0.0)
    cap = AtelierCapaciteMois(atelier_id=atelier.id, annee=annee, mois=mois, heures_dispo=heures, locked=False)
    db.session.add(cap)
    db.session.commit()


def _best_archive_path(arch: ArchiveEmargement, kind: str) -> str | None:
    """Return the best available file path for download/email.

    kind: 'docx' or 'pdf'
    Prefers corrected version if present.
    """
    if not arch:
        return None
    if kind == "pdf":
        return arch.corrected_pdf_path or arch.pdf_path
    return arch.corrected_docx_path or arch.docx_path




def _atelier_est_accessible(atelier) -> bool:
    """Un atelier intersecteur est utilisable par tous les secteurs ; sinon,
    règle habituelle (secteur de la personne ou portée globale)."""
    if atelier is None:
        return False
    if getattr(atelier, "est_intersecteur", False):
        return True
    return _can_access_activity_secteur(atelier.secteur)


def _session_est_accessible(s) -> bool:
    """Idem pour une séance : son atelier intersecteur ouvre l'accès, sinon
    contrôle sur le secteur (d'imputation) de la séance."""
    atelier = getattr(s, "atelier", None)
    if atelier is not None and getattr(atelier, "est_intersecteur", False):
        return True
    return _can_access_activity_secteur(getattr(s, "secteur", None))
