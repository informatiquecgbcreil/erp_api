"""Dépouillement et restitution des questionnaires d'impact.

Calcule, pour un questionnaire donné, la synthèse exploitable par un chef de
projet : nombre de réponses, taux de réponse sur les séances enquêtées, et par
question la distribution / moyenne adaptée au type de question.

Aucune écriture en base : ces fonctions sont purement lecture.
"""
from __future__ import annotations

import json

from app.extensions import db
from app.models import (
    Question,
    QuestionnaireResponseGroup,
    QuestionResponse,
    PresenceActivite,
)


def _options_for(question: Question) -> list[str]:
    if not question.options_json:
        return []
    try:
        opts = json.loads(question.options_json)
        return [str(o) for o in opts] if isinstance(opts, list) else []
    except Exception:
        return []


def compute_questionnaire_stats(questionnaire) -> dict:
    """Synthèse complète d'un questionnaire (KPIs + dépouillement par question)."""
    groups = (
        QuestionnaireResponseGroup.query
        .filter_by(questionnaire_id=questionnaire.id)
        .all()
    )
    group_ids = [g.id for g in groups]
    nb_reponses = len(groups)
    nb_identifies = sum(1 for g in groups if g.participant_id)
    nb_anonymes = nb_reponses - nb_identifies

    # Taux de réponse sur les séances réellement enquêtées.
    session_ids = {g.session_id for g in groups if g.session_id}
    presences_total = 0
    if session_ids:
        presences_total = (
            PresenceActivite.query
            .filter(PresenceActivite.session_id.in_(session_ids))
            .count()
        )
    taux_reponse = round(nb_reponses / presences_total * 100, 1) if presences_total else None

    questions = (
        Question.query.filter_by(questionnaire_id=questionnaire.id)
        .order_by(Question.position.asc(), Question.id.asc())
        .all()
    )

    # Réponses regroupées par question (une seule requête).
    responses_by_q: dict[int, list[QuestionResponse]] = {}
    if group_ids:
        for r in (QuestionResponse.query
                  .filter(QuestionResponse.response_group_id.in_(group_ids))
                  .all()):
            responses_by_q.setdefault(r.question_id, []).append(r)

    q_stats = []
    for q in questions:
        rs = responses_by_q.get(q.id, [])
        nb = len(rs)
        item = {"question": q, "kind": q.kind, "nb": nb, "distribution": [], "moyenne": None, "textes": []}

        if q.kind == "scale":
            valeurs = [r.value_number for r in rs if r.value_number is not None]
            if valeurs:
                item["moyenne"] = round(sum(valeurs) / len(valeurs), 2)
            buckets: dict[int, int] = {}
            for v in valeurs:
                buckets[int(round(v))] = buckets.get(int(round(v)), 0) + 1
            total = len(valeurs)
            for note in range(1, 6):
                c = buckets.get(note, 0)
                item["distribution"].append({"label": str(note), "count": c,
                                             "pct": round(c / total * 100, 1) if total else 0})
        elif q.kind == "yesno":
            buckets = {}
            for r in rs:
                key = (r.value_text or "").strip() or "—"
                buckets[key] = buckets.get(key, 0) + 1
            total = sum(buckets.values())
            for label, c in sorted(buckets.items(), key=lambda kv: -kv[1]):
                item["distribution"].append({"label": label, "count": c,
                                             "pct": round(c / total * 100, 1) if total else 0})
        elif q.kind == "multi":
            buckets = {opt: 0 for opt in _options_for(q)}
            for r in rs:
                vals = []
                if r.value_json:
                    try:
                        vals = json.loads(r.value_json)
                    except Exception:
                        vals = []
                for v in vals:
                    buckets[str(v)] = buckets.get(str(v), 0) + 1
            for label, c in sorted(buckets.items(), key=lambda kv: -kv[1]):
                item["distribution"].append({"label": label, "count": c,
                                             "pct": round(c / nb * 100, 1) if nb else 0})
        else:  # text
            item["textes"] = [(r.value_text or "").strip() for r in rs if (r.value_text or "").strip()]

        q_stats.append(item)

    return {
        "nb_reponses": nb_reponses,
        "nb_identifies": nb_identifies,
        "nb_anonymes": nb_anonymes,
        "taux_reponse": taux_reponse,
        "presences_total": presences_total,
        "questions": q_stats,
    }


def scale_average_global(questionnaire) -> float | None:
    """Moyenne de toutes les réponses aux questions à échelle d'un questionnaire."""
    rows = (
        db.session.query(QuestionResponse.value_number)
        .join(QuestionnaireResponseGroup, QuestionResponse.response_group_id == QuestionnaireResponseGroup.id)
        .join(Question, QuestionResponse.question_id == Question.id)
        .filter(QuestionnaireResponseGroup.questionnaire_id == questionnaire.id)
        .filter(Question.kind == "scale")
        .filter(QuestionResponse.value_number.isnot(None))
        .all()
    )
    vals = [r[0] for r in rows if r[0] is not None]
    return round(sum(vals) / len(vals), 2) if vals else None


def compute_projet_comparison(projet) -> dict | None:
    """Comparaison avant/après pour un projet, sur la moyenne des échelles.

    Retourne ``None`` si le projet n'a pas à la fois un questionnaire « avant »
    et un questionnaire « après ».
    """
    if projet is None:
        return None
    from app.models import Questionnaire

    qs = Questionnaire.query.filter_by(projet_id=projet.id).all()
    avant = [q for q in qs if q.type_questionnaire == "avant"]
    apres = [q for q in qs if q.type_questionnaire == "apres"]
    if not avant or not apres:
        return None

    def _avg(items):
        vals = [v for v in (scale_average_global(q) for q in items) if v is not None]
        return round(sum(vals) / len(vals), 2) if vals else None

    moy_avant = _avg(avant)
    moy_apres = _avg(apres)
    if moy_avant is None or moy_apres is None:
        return None
    return {
        "moy_avant": moy_avant,
        "moy_apres": moy_apres,
        "evolution": round(moy_apres - moy_avant, 2),
        "nb_avant": len(avant),
        "nb_apres": len(apres),
    }
