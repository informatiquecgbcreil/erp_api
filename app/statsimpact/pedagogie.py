from __future__ import annotations



import os

from flask import abort, render_template, request, redirect, url_for, flash, current_app, send_file
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
    Projet,
    Competence,
    Evaluation,
    Referentiel,
    Objectif,
)

from app.activite.services.docx_utils import generate_participant_bilan_pdf


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
)

def _pedago_scope_secteur() -> str | None:
    if can("scope:all_secteurs"):
        return None
    return (getattr(current_user, "secteur_assigne", None) or "").strip() or None


def _build_bilan_rows(participant: Participant) -> list[dict]:
    eval_rows = (
        db.session.query(
            Evaluation,
            Competence,
            Referentiel,
            SessionActivite,
            AtelierActivite,
        )
        .join(Competence, Evaluation.competence_id == Competence.id)
        .join(Referentiel, Competence.referentiel_id == Referentiel.id)
        .outerjoin(SessionActivite, Evaluation.session_id == SessionActivite.id)
        .outerjoin(AtelierActivite, SessionActivite.atelier_id == AtelierActivite.id)
        .filter(Evaluation.participant_id == participant.id, Evaluation.etat == 2)
        .order_by(Referentiel.nom.asc(), Competence.code.asc(), Evaluation.date_evaluation.asc())
        .all()
    )

    rows: list[dict] = []
    for eval_obj, comp, ref, session, atelier in eval_rows:
        date_label = eval_obj.date_evaluation.strftime("%d/%m/%Y") if eval_obj.date_evaluation else ""
        atelier_label = atelier.nom if atelier else ""
        rows.append(
            {
                "referentiel": ref.nom,
                "competence": f"{comp.code} · {comp.nom}",
                "date": date_label,
                "atelier": atelier_label,
            }
        )

    # Compétences validées via le portail (fusionnées, marquées « Portail »).
    from app.pedagogie.services import progression_portail

    portail = progression_portail(participant.id)
    valides_cids = [cid for cid, info in portail.items() if info["niveau"] >= 2]
    if valides_cids:
        deja = {r["competence"] for r in rows}
        comps = (
            db.session.query(Competence, Referentiel)
            .join(Referentiel, Competence.referentiel_id == Referentiel.id)
            .filter(Competence.id.in_(valides_cids))
            .all()
        )
        for comp, ref in comps:
            label = f"{comp.code} · {comp.nom}"
            if label in deja:
                continue
            rows.append({"referentiel": ref.nom, "competence": label, "date": "", "atelier": "Portail"})
        rows.sort(key=lambda r: (r["referentiel"], r["competence"]))
    return rows


def _participants_success_rate(session_id: int, competences: list[Competence]) -> dict:
    if not competences:
        return {"total": 0, "success": 0, "ratio": 0}
    presences = PresenceActivite.query.filter_by(session_id=session_id).all()
    total = len(presences)
    if total == 0:
        return {"total": 0, "success": 0, "ratio": 0}
    comp_ids = [c.id for c in competences]
    success_count = 0
    for pr in presences:
        evals = (
            Evaluation.query.filter(
                Evaluation.session_id == session_id,
                Evaluation.participant_id == pr.participant_id,
                Evaluation.competence_id.in_(comp_ids),
                Evaluation.etat >= 2,
            )
            .distinct()
            .count()
        )
        if evals == len(comp_ids):
            success_count += 1
    ratio = (success_count / total * 100) if total else 0
    return {"total": total, "success": success_count, "ratio": ratio}


def _objective_success(obj: Objectif) -> dict:
    if obj.type == "operationnel" and obj.session_id:
        stats = _participants_success_rate(obj.session_id, obj.competences)
        validated = stats["ratio"] >= (obj.seuil_validation or 0)
        return {"ratio": stats["ratio"], "validated": validated, "total": stats["total"], "success": stats["success"]}

    enfants = obj.enfants or []
    if not enfants:
        return {"ratio": 0, "validated": False, "total": 0, "success": 0}
    results = [ _objective_success(child) for child in enfants ]
    total = len(results)
    success = sum(1 for r in results if r["validated"])
    ratio = (success / total * 100) if total else 0
    validated = ratio >= (obj.seuil_validation or 0)
    return {"ratio": ratio, "validated": validated, "total": total, "success": success}


def _scoped_participants_query_for_stats(flt):
    eff_secteur = _resolve_secteur_scope(flt)
    q = db.session.query(Participant).distinct()
    if eff_secteur == "__restricted__":
        return q.filter(Participant.id == -1)
    if eff_secteur:
        q = (
            q.join(PresenceActivite, PresenceActivite.participant_id == Participant.id)
            .join(SessionActivite, PresenceActivite.session_id == SessionActivite.id)
            .join(AtelierActivite, SessionActivite.atelier_id == AtelierActivite.id)
            .filter(SessionActivite.is_deleted.is_(False))
            .filter(AtelierActivite.is_deleted.is_(False))
            .filter(AtelierActivite.secteur == eff_secteur)
        )
    return q


def _query_presence_export(flt, participant_q: str | None = None):
    query = (
        db.session.query(
            PresenceActivite,
            Participant,
            SessionActivite,
            AtelierActivite,
            Quartier,
        )
        .join(Participant, PresenceActivite.participant_id == Participant.id)
        .join(SessionActivite, PresenceActivite.session_id == SessionActivite.id)
        .join(AtelierActivite, SessionActivite.atelier_id == AtelierActivite.id)
        .outerjoin(Quartier, Participant.quartier_id == Quartier.id)
    )
    query = _apply_common_filters(query, flt)

    if participant_q:
        like = f"%{participant_q.lower()}%"
        query = query.filter(
            func.lower(func.coalesce(Participant.nom, "")).like(like)
            | func.lower(func.coalesce(Participant.prenom, "")).like(like)
        )

    query = query.order_by(_session_date_expr().asc(), Participant.nom.asc(), Participant.prenom.asc())
    return query


@bp.route("/stats/pedagogie", methods=["GET"])
@login_required
def stats_pedagogie():
    if not _can_view():
        abort(403)

    secteur = _pedago_scope_secteur()
    
    flt = normalize_filters(request.args, user=current_user)

    projets_q = Projet.query
    ateliers_q = _apply_atelier_lifecycle_filters(AtelierActivite.query, flt)
    if secteur:
        projets_q = projets_q.filter(Projet.secteur == secteur)
        ateliers_q = ateliers_q.filter(AtelierActivite.secteur == secteur)

    projets = projets_q.order_by(Projet.secteur.asc(), Projet.nom.asc()).all()
    ateliers = ateliers_q.order_by(AtelierActivite.secteur.asc(), AtelierActivite.nom.asc()).all()
    participants = _scoped_participants_query_for_stats(flt).order_by(Participant.nom.asc(), Participant.prenom.asc()).all()

    projet_id = request.args.get("projet_id", type=int)
    atelier_id = request.args.get("atelier_id", type=int)
    participant_id = request.args.get("participant_id", type=int)
    participant_q = (request.args.get("participant_q") or "").strip()

    projet = db.session.get(Projet, projet_id) if projet_id else None
    if projet and secteur and projet.secteur != secteur:
        abort(403)

    atelier = db.session.get(AtelierActivite, atelier_id) if atelier_id else None
    if atelier and secteur and atelier.secteur != secteur:
        abort(403)

    participant = db.session.get(Participant, participant_id) if participant_id else None

    participants_for_passport_q = _scoped_participants_query_for_stats(flt)
    if participant_q:
        like = f"%{participant_q.lower()}%"
        participants_for_passport_q = participants_for_passport_q.filter(
            func.lower(func.coalesce(Participant.nom, "")).like(like)
            | func.lower(func.coalesce(Participant.prenom, "")).like(like)
        )
    participants_for_passport = participants_for_passport_q.order_by(Participant.nom.asc(), Participant.prenom.asc()).limit(200).all()

    projet_objectifs = []
    if projet:
        objectifs = Objectif.query.filter_by(projet_id=projet.id, type="general").order_by(Objectif.created_at.asc()).all()
        for obj in objectifs:
            stats = _objective_success(obj)
            projet_objectifs.append({"objectif": obj, **stats})

    atelier_stats = {}
    if atelier:
        objectifs = Objectif.query.filter_by(atelier_id=atelier.id, type="specifique").order_by(Objectif.created_at.asc()).all()
        objectifs_stats = []
        for obj in objectifs:
            stats = _objective_success(obj)
            objectifs_stats.append({"objectif": obj, **stats})
        atelier_stats = {"objectifs": objectifs_stats}

    participant_groups = []
    bilan_rows = []
    if participant:
        bilan_rows = _build_bilan_rows(participant)
        grouped: dict[str, list[dict]] = {}
        for row in bilan_rows:
            grouped.setdefault(row["referentiel"], []).append(row)
        participant_groups = [
            {"referentiel": ref, "rows": rows} for ref, rows in grouped.items()
        ]

    return render_template(
        "stats_pedagogie.html",
        projets=projets,
        ateliers=ateliers,
        participants=participants,
        projet=projet,
        atelier=atelier,
        participant=participant,
        participant_q=participant_q,
        participants_for_passport=participants_for_passport,
        projet_objectifs=projet_objectifs,
        atelier_stats=atelier_stats,
        participant_groups=participant_groups,
    )


@bp.route("/stats/pedagogie/participant/<int:participant_id>/bilan", methods=["GET"])
@login_required
def stats_pedagogie_bilan(participant_id: int):
    if not _can_view():
        abort(403)
    participant = db.get_or_404(Participant, participant_id)
    rows = _build_bilan_rows(participant)
    pdf_path = generate_participant_bilan_pdf(current_app, participant, rows)
    if pdf_path and os.path.exists(pdf_path):
        return send_file(pdf_path, as_attachment=True)
    flash("Impossible de générer le PDF.", "warning")
    return redirect(url_for("statsimpact.stats_pedagogie", participant_id=participant_id))






