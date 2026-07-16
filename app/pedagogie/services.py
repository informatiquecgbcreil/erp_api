from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time

from app.extensions import db
from app.models import Evaluation, Objectif, ObjectifCompetenceMap, Participant


def _eval_rows(competence_ids: set[int], start_date: date | None = None, end_date: date | None = None):
    if not competence_ids:
        return []
    q = Evaluation.query.filter(Evaluation.competence_id.in_(competence_ids))
    if start_date:
        q = q.filter(Evaluation.date_evaluation >= start_date)
    if end_date:
        q = q.filter(Evaluation.date_evaluation <= end_date)
    return q.order_by(Evaluation.participant_id.asc(), Evaluation.competence_id.asc(), Evaluation.date_evaluation.asc(), Evaluation.id.asc()).all()


def participant_timeline(participant_id: int):
    participant = db.get_or_404(Participant, participant_id)
    events = (
        Evaluation.query
        .filter(Evaluation.participant_id == participant_id)
        .order_by(Evaluation.date_evaluation.asc(), Evaluation.id.asc())
        .all()
    )
    current_levels: dict[int, int] = {}
    for e in events:
        current_levels[e.competence_id] = e.etat

    # Enrichissement portail : niveau effectif = max(manuel, portail). On
    # n'écrase jamais les évaluations manuelles (events reste manuel) ; seul le
    # niveau affiché/agrégé est relevé. Une compétence uniquement échouée au
    # portail (niveau 0) n'est pas ajoutée si elle n'a pas d'évaluation manuelle.
    for cid, info in progression_portail(participant_id).items():
        if info["niveau"] > 0 or cid in current_levels:
            current_levels[cid] = max(current_levels.get(cid, 0), info["niveau"])

    return participant, events, current_levels


def _pct_tentative(a) -> float | None:
    """Pourcentage d'une tentative portail (pct si fourni, sinon score/maxScore)."""
    if a.pct is not None:
        try:
            return float(a.pct)
        except (TypeError, ValueError):
            return None
    if a.max_score:
        try:
            return float(a.score) / float(a.max_score) * 100.0
        except (TypeError, ValueError, ZeroDivisionError):
            return None
    return None


def _niveau_depuis_progression(reussis: int, total: int) -> int:
    """Niveau 0-3 déduit du ratio d'exercices réussis (échelle ERP : 2 = acquis)."""
    if total <= 0 or reussis <= 0:
        return 0
    ratio = reussis / total
    if ratio >= 1.0:
        return 3
    if ratio >= 0.5:
        return 2
    return 1


def progression_portail(participant_id: int) -> dict[int, dict]:
    """Progression déduite du portail, par compétence, pour un participant.

    Pour chaque compétence ayant des exercices mappés (actifs) : un exercice est
    « réussi » si le participant a au moins une tentative dont le pourcentage
    atteint le seuil du mapping. Renvoie ``{competence_id: {niveau, reussis,
    total, exercices: [...]}}``. Aucun effet de bord : tout est recalculé.
    """
    from app.models import PortailAttempt, PortailCompetenceMap

    maps = PortailCompetenceMap.query.filter(PortailCompetenceMap.actif.is_(True)).all()
    if not maps:
        return {}

    by_comp: dict[int, list[tuple[str, float]]] = defaultdict(list)
    activities: set[str] = set()
    for m in maps:
        by_comp[m.competence_id].append((m.activity, float(m.seuil_pct or 0)))
        activities.add(m.activity)

    attempts = (
        PortailAttempt.query
        .filter(PortailAttempt.participant_id == participant_id)
        .filter(PortailAttempt.activity.in_(activities))
        .all()
    )
    meilleur_pct: dict[str, float] = {}
    for a in attempts:
        p = _pct_tentative(a)
        if p is None:
            continue
        if a.activity not in meilleur_pct or p > meilleur_pct[a.activity]:
            meilleur_pct[a.activity] = p

    resultat: dict[int, dict] = {}
    for cid, exos in by_comp.items():
        detail = []
        reussis = 0
        attempted = False
        for activity, seuil in exos:
            mp = meilleur_pct.get(activity)
            if mp is not None:
                attempted = True
            reussi = mp is not None and mp >= seuil
            if reussi:
                reussis += 1
            detail.append(
                {"activity": activity, "seuil": seuil, "meilleur_pct": mp, "reussi": reussi}
            )
        if not attempted:
            # Le participant n'a tenté aucun exercice de cette compétence : on ne
            # l'inclut pas (pas de progression portail à afficher pour lui).
            continue
        total = len(exos)
        resultat[cid] = {
            "niveau": _niveau_depuis_progression(reussis, total),
            "reussis": reussis,
            "total": total,
            "exercices": detail,
        }
    return resultat


def niveaux_portail_par_paire(
    competence_ids=None, start_date: date | None = None, end_date: date | None = None
) -> dict[tuple[int, int], int]:
    """Niveau portail (0-3) par (participant_id, competence_id), pour agrégation.

    Restreint éventuellement aux compétences ``competence_ids`` et aux tentatives
    terminées dans [start_date, end_date]. Sert à fusionner la progression portail
    dans les stats (objectifs, etc.) : niveau effectif = max(manuel, portail).
    """
    from app.models import PortailAttempt, PortailCompetenceMap

    maps_q = PortailCompetenceMap.query.filter(PortailCompetenceMap.actif.is_(True))
    if competence_ids:
        maps_q = maps_q.filter(PortailCompetenceMap.competence_id.in_(list(competence_ids)))
    maps = maps_q.all()
    if not maps:
        return {}

    by_comp: dict[int, list[tuple[str, float]]] = defaultdict(list)
    activities: set[str] = set()
    for m in maps:
        by_comp[m.competence_id].append((m.activity, float(m.seuil_pct or 0)))
        activities.add(m.activity)

    att_q = PortailAttempt.query.filter(PortailAttempt.activity.in_(activities))
    if start_date:
        att_q = att_q.filter(PortailAttempt.finished_at >= datetime.combine(start_date, time.min))
    if end_date:
        att_q = att_q.filter(PortailAttempt.finished_at <= datetime.combine(end_date, time.max))
    attempts = att_q.all()

    best: dict[tuple[int, str], float] = {}
    participants: set[int] = set()
    for a in attempts:
        if a.participant_id is None:
            continue
        participants.add(a.participant_id)
        p = _pct_tentative(a)
        if p is None:
            continue
        k = (a.participant_id, a.activity)
        if k not in best or p > best[k]:
            best[k] = p

    resultat: dict[tuple[int, int], int] = {}
    for pid in participants:
        for cid, exos in by_comp.items():
            reussis = 0
            attempted = False
            for activity, seuil in exos:
                mp = best.get((pid, activity))
                if mp is not None:
                    attempted = True
                    if mp >= seuil:
                        reussis += 1
            if not attempted:
                continue
            resultat[(pid, cid)] = _niveau_depuis_progression(reussis, len(exos))
    return resultat


def compute_objectif_scores(projet_id: int | None = None, start_date: date | None = None, end_date: date | None = None):
    objectifs_q = Objectif.query
    if projet_id:
        objectifs_q = objectifs_q.filter(Objectif.projet_id == projet_id)
    objectifs = objectifs_q.order_by(Objectif.created_at.asc()).all()

    objectif_by_id = {o.id: o for o in objectifs}
    children = defaultdict(list)
    for obj in objectifs:
        if obj.parent_id:
            children[obj.parent_id].append(obj.id)

    maps = ObjectifCompetenceMap.query.filter(
        ObjectifCompetenceMap.objectif_id.in_(objectif_by_id.keys()),
        ObjectifCompetenceMap.actif.is_(True),
    ).all() if objectif_by_id else []

    grouped = defaultdict(list)
    comp_ids = set()
    for m in maps:
        grouped[m.objectif_id].append(m)
        comp_ids.add(m.competence_id)

    rows = _eval_rows(comp_ids, start_date=start_date, end_date=end_date)

    latest = {}
    per_pair = defaultdict(list)
    for e in rows:
        key = (e.participant_id, e.competence_id)
        latest[key] = e.etat
        per_pair[key].append(e.etat)

    # Fusion portail : niveau effectif = max(manuel, portail) ; ajoute aussi les
    # participants qui n'ont qu'une progression portail (sans évaluation manuelle).
    if comp_ids:
        for cle, niveau in niveaux_portail_par_paire(comp_ids, start_date, end_date).items():
            if niveau > latest.get(cle, -1):
                latest[cle] = niveau
                per_pair.setdefault(cle, [niveau])

    comp_latest_values = defaultdict(list)
    comp_participants = defaultdict(set)
    comp_progressions = defaultdict(list)
    for (pid, cid), lv in latest.items():
        comp_latest_values[cid].append(lv)
        comp_participants[cid].add(pid)
        series = per_pair[(pid, cid)]
        if len(series) >= 2:
            comp_progressions[cid].append(series[-1] - series[0])
        else:
            comp_progressions[cid].append(0)

    part_by_obj = defaultdict(set)
    eval_count_by_obj = defaultdict(int)
    progression_by_obj = {}
    score_by_obj = {}

    for obj_id, items in grouped.items():
        numerator = 0.0
        denom = 0.0
        prog_num = 0.0
        prog_den = 0.0
        for m in items:
            w = float(m.poids or 1.0)
            denom += w
            values = comp_latest_values.get(m.competence_id, [])
            if values:
                numerator += (sum(values) / len(values)) * w
                eval_count_by_obj[obj_id] += len(values)
                for pid in comp_participants.get(m.competence_id, set()):
                    part_by_obj[obj_id].add(pid)
            pvals = comp_progressions.get(m.competence_id, [])
            if pvals:
                prog_num += (sum(pvals) / len(pvals)) * w
                prog_den += w

        score_by_obj[obj_id] = round((numerator / (denom * 3.0) * 100.0), 1) if denom > 0 else None
        progression_by_obj[obj_id] = round((prog_num / prog_den), 2) if prog_den > 0 else None

    def rollup(obj_id: int):
        if score_by_obj.get(obj_id) is not None:
            return score_by_obj[obj_id], progression_by_obj.get(obj_id)
        child_vals = [rollup(cid) for cid in children.get(obj_id, [])]
        child_scores = [v[0] for v in child_vals if v[0] is not None]
        child_progs = [v[1] for v in child_vals if v[1] is not None]
        score = round(sum(child_scores) / len(child_scores), 1) if child_scores else None
        prog = round(sum(child_progs) / len(child_progs), 2) if child_progs else None
        return score, prog

    data = []
    for obj in objectifs:
        s, prog = rollup(obj.id)
        data.append({
            "objectif": obj,
            "score": s,
            "participants": len(part_by_obj.get(obj.id, set())),
            "evaluations": eval_count_by_obj.get(obj.id, 0),
            "progression_moyenne": prog,
        })
    return data
