from __future__ import annotations

from copy import deepcopy

from datetime import date, timedelta

import os

from flask import abort, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from app.rbac import can

from sqlalchemy import func


from app.extensions import db

from app.models import (
    AtelierActivite,
    Participant,
    Quartier,
    PresenceActivite,
    SessionActivite,
)

from app.services.quartiers import normalize_quartier_for_ville

from .occupancy import compute_occupancy_stats

from .engine import (
    compute_volume_activity_stats,
    compute_participation_frequency_stats,
    compute_transversalite_stats,
    compute_demography_stats,
    compute_participants_stats,
    compute_magatomatique,
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
    _enrich_stats_with_consumption,
    _individual_consumption_stats_for_filter,
    _query_args_preserve_lists,
)

def _dialect_name() -> str:
    try:
        return (db.engine.dialect.name or "").lower()
    except Exception:
        return ""

def _month_key_expr(date_expr):
    """
    Retourne une expression SQL qui donne une clé de mois 'YYYY-MM'
    compatible SQLite / Postgres.
    """
    d = _dialect_name()
    if "sqlite" in d:
        # SQLite: strftime
        return func.strftime("%Y-%m", date_expr)
    # Postgres / autres
    # to_char(date_trunc('month', ...), 'YYYY-MM')
    return func.to_char(func.date_trunc("month", date_expr), "YYYY-MM")



def _build_activity_charts(flt):
    """
    2 séries:
      - presences par mois
      - sessions réelles par mois (statut != 'annulee')
    Respecte les filtres communs via _apply_common_filters.
    """
    date_expr = _session_date_expr()
    month_key = _month_key_expr(date_expr)

    # ---------- Présences / mois ----------
    pres_q = (
        db.session.query(
            month_key.label("m"),
            func.count(PresenceActivite.id).label("nb"),
        )
        .select_from(PresenceActivite)
        .join(SessionActivite, PresenceActivite.session_id == SessionActivite.id)
        .join(AtelierActivite, SessionActivite.atelier_id == AtelierActivite.id)
    )
    pres_q = _apply_common_filters(pres_q, flt)
    pres_q = pres_q.group_by("m").order_by("m")
    pres_rows = pres_q.all()

    # ---------- Sessions réelles / mois ----------
    sess_q = (
        db.session.query(
            month_key.label("m"),
            func.count(SessionActivite.id).label("nb"),
        )
        .select_from(SessionActivite)
        .join(AtelierActivite, SessionActivite.atelier_id == AtelierActivite.id)
    )
    sess_q = _apply_common_filters(sess_q, flt)
    # "réelles" = pas annulée (attention: statut peut être None)
    sess_q = sess_q.filter(func.lower(func.coalesce(SessionActivite.statut, "")) != "annulee")
    sess_q = sess_q.group_by("m").order_by("m")
    sess_rows = sess_q.all()

    # ---------- Fusion mois (pour aligner labels) ----------
    months = sorted({r.m for r in pres_rows if r.m} | {r.m for r in sess_rows if r.m})

    pres_map = {r.m: int(r.nb or 0) for r in pres_rows if r.m}
    sess_map = {r.m: int(r.nb or 0) for r in sess_rows if r.m}

    pres_values = [pres_map.get(m, 0) for m in months]
    sess_values = [sess_map.get(m, 0) for m in months]

    return {
        "months": months,
        "presences": pres_values,
        "sessions_real": sess_values,
    }


def _compute_compare_payload(flt, stats, freq):
    """Optionnel : calcule la période précédente + deltas KPI, sans alourdir toute la page."""
    if not getattr(flt, "date_from", None) or not getattr(flt, "date_to", None):
        return {"enabled": False}

    try:
        span_days = (flt.date_to - flt.date_from).days
        prev_to = flt.date_from - timedelta(days=1)
        prev_from = prev_to - timedelta(days=span_days)
    except Exception:
        return {"enabled": False}

    flt_prev = deepcopy(flt)
    flt_prev.date_from = prev_from
    flt_prev.date_to = prev_to

    stats_prev = compute_volume_activity_stats(flt_prev)
    freq_prev = compute_participation_frequency_stats(flt_prev)

    def _safe_num(v):
        try:
            return float(v)
        except Exception:
            return 0.0

    cur = getattr(stats, "kpi", {}) or {}
    prv = getattr(stats_prev, "kpi", {}) or {}

    def _get(obj, key, default=0):
        if hasattr(obj, key):
            return getattr(obj, key)
        if isinstance(obj, dict):
            return obj.get(key, default)
        return default

    def _delta(key):
        c = _safe_num(_get(cur, key, 0))
        p = _safe_num(_get(prv, key, 0))
        d = c - p
        pct = (d / p * 100.0) if p else None
        return {"current": c, "previous": p, "delta": d, "delta_pct": pct}

    return {
        "enabled": True,
        "current_period": {"from": flt.date_from, "to": flt.date_to},
        "previous_period": {"from": prev_from, "to": prev_to},
        "kpis": {
            "sessions": _delta("sessions"),
            "presences": _delta("presences"),
            "uniques": _delta("uniques"),
            "avg_per_session": _delta("avg_per_session"),
            "new_participants": _delta("new_participants"),
        },
        "freq": {
            "freq_avg": {"current": _safe_num(_get(freq, "freq_avg", 0)), "previous": _safe_num(_get(freq_prev, "freq_avg", 0))},
            "returning_rate": {"current": _safe_num(_get(freq, "returning_rate", 0)), "previous": _safe_num(_get(freq_prev, "returning_rate", 0))},
            "regulars_4plus": {"current": _safe_num(_get(freq, "regulars_4plus", 0)), "previous": _safe_num(_get(freq_prev, "regulars_4plus", 0))},
        },
    }




@bp.route("/stats-impact/dashboard", methods=["GET", "POST"])
@login_required
def dashboard():
    """Dashboard complet Stats & Impacts (calculs + onglets)."""
    if not _can_view():
        abort(403)

    args = request.args

    # Robust: normalize_filters supports MultiDict (checklistes) and kwargs-style.
    flt = normalize_filters(args, user=current_user)

    # Default: current year if no dates
    if not flt.date_from and not flt.date_to:
        today = date.today()
        flt.date_from = date(today.year, 1, 1)
        flt.date_to = date(today.year, 12, 31)

    # Pre-compute participants for access control if we need to handle edits.
    participants = compute_participants_stats(flt)

    if request.method == "POST":
        action = request.form.get("action")
        if action == "update_participant":
            try:
                participant_id = int(request.form.get("participant_id", "0"))
            except Exception:
                participant_id = 0

            allowed_ids = {p["id"] for p in participants.get("participants", [])}
            if not participant_id or participant_id not in allowed_ids:
                abort(403)

            participant = db.session.get(Participant, participant_id)
            if not participant:
                abort(404)

            participant.nom = (request.form.get("nom") or participant.nom or "").strip() or participant.nom
            participant.prenom = (request.form.get("prenom") or participant.prenom or "").strip() or participant.prenom
            participant.ville = (request.form.get("ville") or "").strip() or None
            participant.email = (request.form.get("email") or "").strip() or None
            participant.telephone = (request.form.get("telephone") or "").strip() or None
            participant.genre = (request.form.get("genre") or "").strip() or None
            participant.type_public = (request.form.get("type_public") or participant.type_public or "H").strip().upper()

            dn_raw = request.form.get("date_naissance") or None
            dn = None
            if dn_raw:
                try:
                    dn = date.fromisoformat(dn_raw)
                except Exception:
                    dn = None
            participant.date_naissance = dn

            quartier_id = request.form.get("quartier_id") or None
            participant.quartier_id = normalize_quartier_for_ville(participant.ville, quartier_id)

            try:
                from app.extensions import db

                db.session.commit()
                flash("Participant mis à jour.", "success")
            except Exception:
                db.session.rollback()
                flash("Impossible de sauvegarder ce participant.", "danger")

            args_redirect = _query_args_preserve_lists(request.args)
            args_redirect["tab"] = "participants"
            return redirect(url_for("statsimpact.dashboard", **args_redirect))

        if action == "delete_participant":
            try:
                participant_id = int(request.form.get("participant_id", "0"))
            except Exception:
                participant_id = 0

            allowed_ids = {p["id"] for p in participants.get("participants", [])}
            if not participant_id or participant_id not in allowed_ids:
                abort(403)

            participant = db.session.get(Participant, participant_id)
            if not participant:
                abort(404)

            # Sécurité secteur: un responsable_secteur ne peut purger un participant
            # que si ce participant n'a des présences que dans SON secteur (ou aucune).
            user_secteur = (getattr(current_user, "secteur_assigne", None) or "").strip()
            if not can("participants:view_all"):
                sectors = (
                    PresenceActivite.query.join(SessionActivite, PresenceActivite.session_id == SessionActivite.id)
                    .with_entities(SessionActivite.secteur)
                    .filter(PresenceActivite.participant_id == participant_id)
                    .distinct()
                    .all()
                )
                sectors = {s[0] for s in sectors if s and s[0]}
                # s'il n'a jamais émargé: OK (secteurs = vide)
                if sectors and sectors != {user_secteur}:
                    flash(
                        "Suppression refusée : ce participant a des émargements dans d'autres secteurs.",
                        "danger",
                    )
                    args_redirect = _query_args_preserve_lists(request.args)
                    args_redirect["tab"] = "participants"
                    return redirect(url_for("statsimpact.dashboard", **args_redirect))

            try:
                from app.extensions import db

                # Supprime d'abord les signatures des présences
                presences = PresenceActivite.query.filter_by(participant_id=participant_id).all()
                for pr in presences:
                    if pr.signature_path:
                        try:
                            if os.path.exists(pr.signature_path):
                                os.remove(pr.signature_path)
                        except Exception:
                            pass
                    db.session.delete(pr)

                db.session.delete(participant)
                db.session.commit()
                flash("Participant supprimé définitivement.", "success")
            except Exception:
                db.session.rollback()
                flash("Impossible de supprimer ce participant.", "danger")

            args_redirect = _query_args_preserve_lists(request.args)
            args_redirect["tab"] = "participants"
            return redirect(url_for("statsimpact.dashboard", **args_redirect))

    # Refresh computed stats after any potential mutation
    participants = compute_participants_stats(flt)
    stats = compute_volume_activity_stats(flt)
    stats = _enrich_stats_with_consumption(stats, flt)
    freq = compute_participation_frequency_stats(flt)
    trans = compute_transversalite_stats(flt)
    demo = compute_demography_stats(flt)
    occupancy = compute_occupancy_stats(flt)

    # Le Magatomatique : calcul uniquement si l'onglet est affiché (sinon on garde la page légère)
    tab = (request.args.get("tab") or "base").strip().lower()
    magato = None
    if tab in ("magato", "magatomatique"):
        participant_q = (request.args.get("participant_q") or "").strip() or None
        view = (request.args.get("magato_view") or "macro").strip().lower()
        try:
            max_sessions = int(request.args.get("max_sessions") or 40)
        except Exception:
            max_sessions = 40
        try:
            max_participants = int(request.args.get("max_participants") or 250)
        except Exception:
            max_participants = 250

        # bornes de sécurité
        max_sessions = max(5, min(max_sessions, 200))
        max_participants = max(20, min(max_participants, 1000))

        magato = compute_magatomatique(
            flt,
            participant_q=participant_q,
            view=view,
            max_sessions=max_sessions,
            max_participants=max_participants,
        )

    participants = compute_participants_stats(flt)

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

    quartiers = Quartier.query.order_by(Quartier.ville.asc(), Quartier.nom.asc()).all()

    # Années disponibles (pour presets "année") dans le périmètre accessible
    try:
        eff_secteur = flt.secteur
        if not can("scope:all_secteurs"):
            eff_secteur = (getattr(current_user, "secteur_assigne", None) or "").strip() or eff_secteur

        year_expr = func.extract("year", func.coalesce(SessionActivite.rdv_date, SessionActivite.date_session))
        years_q = (
            db.session.query(year_expr.label("y"))
            .select_from(SessionActivite)
            .join(AtelierActivite, SessionActivite.atelier_id == AtelierActivite.id)
            .filter(AtelierActivite.is_deleted.is_(False))
            .filter(AtelierActivite.is_active.is_(True))
        )
        if eff_secteur:
            years_q = years_q.filter(AtelierActivite.secteur == eff_secteur)
        years = [int(r.y) for r in years_q.distinct().order_by(year_expr.desc()).all() if r and r.y]
    except Exception:
        years = []

    charts = {"months": [], "presences": [], "sessions_real": []}
    try:
        charts = _build_activity_charts(flt)
    except Exception:
        # On garde le fallback vide pour éviter de casser le dashboard
        pass

    # Option : comparaison période précédente (même durée)
    compare = {"enabled": False}
    compare_flag = (request.args.get("compare") or "").strip().lower()
    if compare_flag in ("1", "true", "yes", "on"):
        try:
            compare = _compute_compare_payload(flt, stats, freq)
        except Exception:
            compare = {"enabled": False}

    csv_groups, csv_defaults, _ = _csv_fields_for_current_user()
    consumption_stats = _consumption_stats_for_filter(flt)
    individual_consumption_stats = _individual_consumption_stats_for_filter(flt)
    return render_template(
        ["statsimpact/dashboard.html", "statsimpact_dashboard.html"],
        flt=flt,
        stats=stats,
        freq=freq,
        trans=trans,
        demo=demo,
        secteurs=secteurs,
        ateliers=ateliers,
        occupancy=occupancy,
        participants=participants,
        magato=magato,
        quartiers=quartiers,
        available_years=years,
        charts=charts,
        compare=compare,
        csv_field_groups=csv_groups,
        csv_default_fields=csv_defaults,
        consumption_stats=consumption_stats,
        individual_consumption_stats=individual_consumption_stats,
    )



