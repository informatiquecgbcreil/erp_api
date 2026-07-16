"""Échelle de Hart — vue collective (Ressources) et pose d'évaluations.

La vue collective montre, pour un secteur et une période (année ou dates
personnalisées), la répartition des habitants sur les 8 barreaux, la
tendance par rapport à une période de comparaison, et le détail par étage
avec renvoi vers le passeport individuel.
"""
from datetime import date

from flask import render_template, request, redirect, url_for, flash, abort, current_app
from flask_login import login_required, current_user

from app.rbac import require_perm, can
from app.extensions import db
from app.models import Participant, HartEvaluation, HART_TYPES_EVALUATION, HART_TYPES_DICT
from app.services import hart as hart_service
from app.services.audit import journaliser

from app.main.common import bp


def _parse_date(raw, fallback):
    try:
        return date.fromisoformat((raw or "").strip())
    except Exception:
        return fallback


def _periode_depuis_requete(prefix=""):
    """Résout la période : année (défaut = en cours) ou dates personnalisées."""
    annee_raw = (request.args.get(prefix + "annee") or "").strip()
    d_from = (request.args.get(prefix + "from") or "").strip()
    d_to = (request.args.get(prefix + "to") or "").strip()
    if d_from and d_to:
        df = _parse_date(d_from, None)
        dt_ = _parse_date(d_to, None)
        if df and dt_:
            if dt_ < df:
                df, dt_ = dt_, df
            return df, dt_, None
    try:
        annee = int(annee_raw) if annee_raw else date.today().year
    except Exception:
        annee = date.today().year
    return date(annee, 1, 1), date(annee, 12, 31), annee


@bp.route("/hart")
@login_required
@require_perm("participants:view")
def hart_collectif():
    portee_globale = can("scope:all_secteurs")
    secteurs = current_app.config.get("SECTEURS", []) or []

    secteur = (request.args.get("secteur") or "").strip() or None
    if not portee_globale:
        secteur = getattr(current_user, "secteur_assigne", None)

    date_from, date_to, annee = _periode_depuis_requete()
    stats = hart_service.stats_collectives(secteur, date_from, date_to)

    # Comparaison optionnelle : année précédente par défaut si demandée.
    comparer = (request.args.get("comparer") or "").strip() == "1"
    stats_cmp = None
    cmp_ctx = None
    if comparer:
        c_from, c_to, c_annee = _periode_depuis_requete("cmp_")
        if (c_from, c_to) == (date_from, date_to):
            # défaut utile : même période un an plus tôt
            try:
                c_from = date_from.replace(year=date_from.year - 1)
                c_to = date_to.replace(year=date_to.year - 1)
            except ValueError:   # 29 février
                c_from = date(date_from.year - 1, date_from.month, 1)
                c_to = date(date_to.year - 1, date_to.month, 28)
            c_annee = annee - 1 if annee else None
        stats_cmp = hart_service.stats_collectives(secteur, c_from, c_to)
        cmp_ctx = hart_service.comparaison(stats, stats_cmp)

    niveau_detail = request.args.get("niveau", type=int)
    max_count = max([d["count"] for d in stats["detail"]] + [1])

    return render_template(
        "hart_collectif.html",
        stats=stats,
        stats_cmp=stats_cmp,
        cmp_ctx=cmp_ctx,
        comparer=comparer,
        niveau_detail=niveau_detail,
        max_count=max_count,
        secteurs=secteurs,
        secteur=secteur,
        portee_globale=portee_globale,
        annee=annee,
        date_from=date_from,
        date_to=date_to,
        niveaux=hart_service.HART_NIVEAUX,
        seuil_seances=hart_service.HART_SEUIL_SEANCES,
    )


@bp.route("/hart/<int:participant_id>/evaluer", methods=["POST"])
@login_required
@require_perm("participants:edit")
def hart_evaluer(participant_id: int):
    """Pose une évaluation (initiale / suivi / mi-parcours / fin de parcours)."""
    participant = db.get_or_404(Participant, participant_id)

    try:
        niveau = int(request.form.get("niveau") or 0)
    except Exception:
        niveau = 0
    if niveau not in hart_service.HART_NIVEAUX_DICT:
        flash("Choisis un niveau valide (1 à 8) sur l'échelle de Hart.", "danger")
        return redirect(request.form.get("next") or url_for("main.hart_collectif"))

    type_evaluation = (request.form.get("type_evaluation") or "suivi").strip()
    if type_evaluation not in HART_TYPES_DICT:
        type_evaluation = "suivi"

    date_eval = _parse_date(request.form.get("date_evaluation"), date.today())

    ev = HartEvaluation(
        participant_id=participant.id,
        niveau=niveau,
        type_evaluation=type_evaluation,
        date_evaluation=date_eval,
        secteur=(request.form.get("secteur") or "").strip() or getattr(current_user, "secteur_assigne", None),
        temoignage=(request.form.get("temoignage") or "").strip() or None,
        remarque_pro=(request.form.get("remarque_pro") or "").strip() or None,
        created_by_user_id=getattr(current_user, "id", None),
    )
    db.session.add(ev)
    db.session.commit()
    journaliser("hart.evaluation", cible=f"participant#{participant.id}",
                details={"niveau": niveau, "type": type_evaluation})
    flash(f"Évaluation Hart enregistrée : {hart_service.niveau_label(niveau)} ({ev.type_label}).", "success")
    return redirect(request.form.get("next") or url_for("pedagogie.participant_passeport", participant_id=participant.id, tab="participation"))


@bp.route("/hart/evaluation/<int:evaluation_id>/supprimer", methods=["POST"])
@login_required
@require_perm("participants:edit")
def hart_evaluation_supprimer(evaluation_id: int):
    ev = db.get_or_404(HartEvaluation, evaluation_id)
    pid = ev.participant_id
    db.session.delete(ev)
    db.session.commit()
    journaliser("hart.evaluation_suppr", cible=f"participant#{pid}", details={"evaluation": evaluation_id})
    flash("Évaluation supprimée.", "warning")
    return redirect(request.form.get("next") or url_for("pedagogie.participant_passeport", participant_id=pid, tab="participation"))
