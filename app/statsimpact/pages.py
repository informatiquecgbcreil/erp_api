from __future__ import annotations

from collections import Counter

from datetime import date


from flask import abort, render_template, request, redirect, url_for
from flask_login import login_required, current_user
from app.rbac import can



from app.extensions import db

from app.models import (
    AtelierActivite,
    Participant,
    PresenceActivite,
    SessionActivite,
    ProjetAtelier,
)



from .engine import (
    normalize_filters,
    _apply_common_filters,
    _session_date_expr,
    _resolve_secteur_scope,
)


from app.statsimpact.common import bp
from app.statsimpact.common import (
    _apply_atelier_lifecycle_filters,
    _can_view,
    _consumption_stats_for_filter,
    _csv_fields_for_current_user,
    _ensure_default_export_period,
    _export_query_string_from_filter,
    _individual_consumption_stats_for_filter,
    _query_args_preserve_lists,
)

def _quality_participant_item(participant: Participant, detail: str) -> dict:
    label = f"{participant.nom or ''} {participant.prenom or ''}".strip() or f"Participant #{participant.id}"
    return {
        "label": label,
        "detail": detail,
        "url": url_for("participants.edit_participant", participant_id=participant.id),
    }


def _statsimpact_quality_payload(flt) -> dict:
    base = db.session.query(SessionActivite, AtelierActivite).join(
        AtelierActivite, AtelierActivite.id == SessionActivite.atelier_id
    )
    base = _apply_common_filters(base, flt)
    sessions_rows = base.order_by(_session_date_expr().desc()).all()
    session_ids = [session.id for session, _ in sessions_rows]

    presences = []
    if session_ids:
        presences = PresenceActivite.query.filter(PresenceActivite.session_id.in_(session_ids)).all()

    presence_counts = Counter(pr.session_id for pr in presences)
    participant_ids = sorted({pr.participant_id for pr in presences if pr.participant_id})
    participants = []
    if participant_ids:
        participants = (
            Participant.query
            .filter(Participant.id.in_(participant_ids))
            .order_by(Participant.nom.asc(), Participant.prenom.asc())
            .all()
        )

    def participant_issue(code: str, title: str, why: str, rows: list[Participant], severity: str) -> dict:
        return {
            "code": code,
            "title": title,
            "why": why,
            "severity": severity,
            "count": len(rows),
            "items": [_quality_participant_item(p, f"{p.ville or 'Ville inconnue'} · {p.quartier.nom if p.quartier else 'Quartier inconnu'}") for p in rows[:12]],
        }

    issues = [
        participant_issue(
            "participant_no_quartier",
            "Participants sans quartier",
            "Impact direct sur les statistiques QPV, hors QPV et quartier inconnu.",
            [p for p in participants if not getattr(p, "quartier_id", None)],
            "bad",
        ),
        participant_issue(
            "participant_no_genre",
            "Participants sans genre",
            "Fragilise les répartitions femmes / hommes / autre dans les bilans.",
            [p for p in participants if not (p.genre or "").strip()],
            "warn",
        ),
        participant_issue(
            "participant_no_birthdate",
            "Participants sans date de naissance",
            "Empêche le calcul fiable des tranches d'âge et de l'âge moyen.",
            [p for p in participants if not p.date_naissance],
            "warn",
        ),
        participant_issue(
            "participant_no_city",
            "Participants sans ville",
            "Rend moins lisibles l'origine géographique et les contrôles Creil / hors Creil.",
            [p for p in participants if not (p.ville or "").strip()],
            "warn",
        ),
    ]

    empty_sessions = [
        (session, atelier)
        for session, atelier in sessions_rows
        if presence_counts.get(session.id, 0) == 0
        and (session.statut or "").lower() != "annulee"
        and ((session.rdv_date or session.date_session) is None or (session.rdv_date or session.date_session) <= date.today())
    ]
    issues.append({
        "code": "session_no_presence",
        "title": "Séances réalisées sans présence",
        "why": "Une séance sans émargement peut faire baisser les moyennes et masquer une feuille non saisie.",
        "severity": "bad",
        "count": len(empty_sessions),
        "items": [
            {
                "label": f"{atelier.nom} · {(session.rdv_date or session.date_session).strftime('%d/%m/%Y') if (session.rdv_date or session.date_session) else 'Date inconnue'}",
                "detail": atelier.secteur,
                "url": url_for("activite.emargement", session_id=session.id),
            }
            for session, atelier in empty_sessions[:12]
        ],
    })

    eff_secteur = _resolve_secteur_scope(flt)
    ateliers_q = AtelierActivite.query.filter(
        AtelierActivite.is_deleted.is_(False),
        AtelierActivite.is_active.is_(True),
    )
    if eff_secteur == "__restricted__":
        ateliers_q = ateliers_q.filter(AtelierActivite.id == -1)
    elif eff_secteur:
        ateliers_q = ateliers_q.filter(AtelierActivite.secteur == eff_secteur)
    if flt.atelier_ids:
        ateliers_q = ateliers_q.filter(AtelierActivite.id.in_(flt.atelier_ids))
    ateliers = ateliers_q.order_by(AtelierActivite.secteur.asc(), AtelierActivite.nom.asc()).all()
    linked_atelier_ids = {
        row[0] for row in db.session.query(ProjetAtelier.atelier_id).distinct().all() if row and row[0]
    }
    ateliers_without_project = [a for a in ateliers if a.id not in linked_atelier_ids]
    issues.append({
        "code": "atelier_no_project",
        "title": "Ateliers actifs sans projet lié",
        "why": "Les indicateurs de projet ne peuvent pas piocher ces ateliers tant qu'ils ne sont pas rattachés.",
        "severity": "info",
        "count": len(ateliers_without_project),
        "items": [
            {
                "label": a.nom,
                "detail": a.secteur,
                "url": url_for("activite.sessions", atelier_id=a.id),
            }
            for a in ateliers_without_project[:12]
        ],
    })

    bad = sum(issue["count"] for issue in issues if issue["severity"] == "bad")
    warn = sum(issue["count"] for issue in issues if issue["severity"] == "warn")
    score = max(0, min(100, 100 - (bad * 8) - (warn * 3)))

    return {
        "score": score,
        "sessions_count": len(sessions_rows),
        "presences_count": len(presences),
        "participants_count": len(participants),
        "issues": issues,
        "bad_count": bad,
        "warn_count": warn,
        "info_count": sum(issue["count"] for issue in issues if issue["severity"] == "info"),
    }




@bp.route("/stats-impact", methods=["GET"])
@login_required
def exports():
    """Page légère dédiée aux exports (pas de calcul de stats lourdes)."""
    if not _can_view():
        abort(403)

    flt = _ensure_default_export_period(normalize_filters(request.args, user=current_user))

    secteurs = []
    if can("statsimpact:view_all") or can("scope:all_secteurs"):
        secteurs = [
            s[0]
            for s in (
                AtelierActivite.query.with_entities(AtelierActivite.secteur)
                .filter(AtelierActivite.is_deleted.is_(False))
                .filter(AtelierActivite.is_active.is_(True))
                .distinct()
                .order_by(AtelierActivite.secteur.asc())
                .all()
            )
            if s and s[0]
        ]

    q = _apply_atelier_lifecycle_filters(AtelierActivite.query, flt)
    eff_secteur = _resolve_secteur_scope(flt)
    if eff_secteur and eff_secteur != "__restricted__":
        q = q.filter(AtelierActivite.secteur == eff_secteur)
    ateliers = q.order_by(AtelierActivite.secteur.asc(), AtelierActivite.nom.asc()).all()

    csv_groups, csv_defaults, _ = _csv_fields_for_current_user()
    export_qs = _export_query_string_from_filter(flt)
    consumption_stats = _consumption_stats_for_filter(flt)
    individual_consumption_stats = _individual_consumption_stats_for_filter(flt)
    return render_template(
        "statsimpact/exports.html",
        flt=flt,
        secteurs=secteurs,
        ateliers=ateliers,
        csv_field_groups=csv_groups,
        csv_default_fields=csv_defaults,
        export_qs=export_qs,
        consumption_stats=consumption_stats,
        individual_consumption_stats=individual_consumption_stats,
    )

@bp.route("/stats-impact/", methods=["GET"])
@login_required
def exports_slash():
    """Compat: /stats-impact/ -> /stats-impact"""
    if not _can_view():
        abort(403)
    return redirect(url_for("statsimpact.exports", **_query_args_preserve_lists(request.args)))


@bp.route("/stats-impact/qualite-donnees", methods=["GET"])
@login_required
def qualite_donnees():
    if not _can_view():
        abort(403)

    flt = _ensure_default_export_period(normalize_filters(request.args, user=current_user))
    quality = _statsimpact_quality_payload(flt)

    secteurs = []
    if can("statsimpact:view_all") or can("scope:all_secteurs"):
        secteurs = [
            s[0]
            for s in (
                AtelierActivite.query.with_entities(AtelierActivite.secteur)
                .filter(AtelierActivite.is_deleted.is_(False))
                .filter(AtelierActivite.is_active.is_(True))
                .distinct()
                .order_by(AtelierActivite.secteur.asc())
                .all()
            )
            if s and s[0]
        ]

    q = AtelierActivite.query.filter(
        AtelierActivite.is_deleted.is_(False),
        AtelierActivite.is_active.is_(True),
    )
    eff_secteur = _resolve_secteur_scope(flt)
    if eff_secteur == "__restricted__":
        q = q.filter(AtelierActivite.id == -1)
    elif eff_secteur:
        q = q.filter(AtelierActivite.secteur == eff_secteur)
    ateliers = q.order_by(AtelierActivite.secteur.asc(), AtelierActivite.nom.asc()).all()

    return render_template(
        "statsimpact/qualite_donnees.html",
        flt=flt,
        quality=quality,
        secteurs=secteurs,
        ateliers=ateliers,
    )



