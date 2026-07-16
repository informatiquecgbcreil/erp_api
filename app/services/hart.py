"""Échelle de Hart — participation des habitants (centre social).

Référentiel : échelle de Roger Hart (8 barreaux), adaptée à la participation
des habitants d'un centre social agréé CAF / éducation populaire (fédération
des centres sociaux). Les niveaux 1-3 relèvent de la NON-participation ;
les niveaux 4-8 de la participation effective.

Le service fournit :
- le référentiel des niveaux (libellés, couleurs, participation ou non) ;
- la détection des évaluations dues, pilotée par l'émargement (première
  venue = évaluation initiale, puis toutes les ``HART_SEUIL_SEANCES``
  séances) ;
- les statistiques collectives par secteur et période (répartition par
  étage, moyenne, comparaison entre deux périodes, détail par étage).
"""
from __future__ import annotations

from datetime import date

from app.extensions import db
from app.models import (
    HartEvaluation,
    Participant,
    PresenceActivite,
    SessionActivite,
)


# Toutes les N séances, une évaluation de suivi est proposée.
HART_SEUIL_SEANCES = 5

# Référentiel des 8 barreaux. « habitant » = formulation chaleureuse à la
# première personne, utilisée à l'écran ; « libelle » = terme officiel de
# l'échelle de Hart, conservé pour les bilans institutionnels (CAF, fédé).
HART_NIVEAUX = [
    {"niveau": 1, "emoji": "🚪", "habitant": "On me fait venir",
     "libelle": "Manipulation", "participation": False, "couleur": "#e2795c",
     "description": "Présent, mais sans comprendre ni choisir."},
    {"niveau": 2, "emoji": "🪑", "habitant": "Je suis là, sans plus",
     "libelle": "Décoration", "participation": False, "couleur": "#eb9d5f",
     "description": "Présent pour « faire joli », sans rôle réel."},
    {"niveau": 3, "emoji": "📣", "habitant": "On m'écoute… pour la forme",
     "libelle": "Participation symbolique", "participation": False, "couleur": "#f2bd6b",
     "description": "Consulté, mais l'avis ne change rien."},
    {"niveau": 4, "emoji": "🙋", "habitant": "J'ai un rôle et je sais pourquoi",
     "libelle": "Désigné mais informé", "participation": True, "couleur": "#e9d26e",
     "description": "Un vrai rôle, compris et accepté."},
    {"niveau": 5, "emoji": "💬", "habitant": "Mon avis compte",
     "libelle": "Consulté et informé", "participation": True, "couleur": "#b5cf6d",
     "description": "L'avis est demandé et vraiment pris en compte."},
    {"niveau": 6, "emoji": "🤝", "habitant": "On décide ensemble",
     "libelle": "Décisions partagées (initiative pros)", "participation": True, "couleur": "#7cbf74",
     "description": "Projet lancé par l'équipe, décidé avec les habitants."},
    {"niveau": 7, "emoji": "🚀", "habitant": "Je porte le projet",
     "libelle": "Initiative et direction habitants", "participation": True, "couleur": "#4da96f",
     "description": "Projet imaginé et conduit par les habitants."},
    {"niveau": 8, "emoji": "🌟", "habitant": "Mon projet, on le fait ensemble",
     "libelle": "Initiative habitants, décisions partagées", "participation": True, "couleur": "#2e8b64",
     "description": "Projet des habitants, professionnels en appui."},
]
HART_NIVEAUX_DICT = {d["niveau"]: d for d in HART_NIVEAUX}


def niveau_label(niveau: int) -> str:
    info = HART_NIVEAUX_DICT.get(int(niveau or 0))
    return f"{niveau}. {info['libelle']}" if info else str(niveau)


def _date_session_effective():
    return db.func.coalesce(SessionActivite.date_session, SessionActivite.rdv_date)


def derniere_evaluation(participant_id: int) -> HartEvaluation | None:
    return (HartEvaluation.query
            .filter_by(participant_id=participant_id)
            .order_by(HartEvaluation.date_evaluation.desc(), HartEvaluation.id.desc())
            .first())


def nb_seances_depuis(participant_id: int, depuis: date | None) -> int:
    """Nombre de séances émargées (tous secteurs) depuis une date exclue."""
    q = (db.session.query(db.func.count(PresenceActivite.id))
         .join(SessionActivite, SessionActivite.id == PresenceActivite.session_id)
         .filter(PresenceActivite.participant_id == participant_id)
         .filter(SessionActivite.is_deleted.is_(False)))
    if depuis is not None:
        q = q.filter(_date_session_effective() > depuis)
    return int(q.scalar() or 0)


def evaluation_due(participant_id: int) -> dict | None:
    """Retourne {motif, derniere, nb_seances} si une évaluation est due, sinon None.

    - Aucune évaluation -> « initiale » (première venue).
    - ``HART_SEUIL_SEANCES`` séances ou plus depuis la dernière -> « suivi ».
    Une évaluation de fin de parcours clôt le cycle : plus rien n'est dû.
    """
    derniere = derniere_evaluation(participant_id)
    if derniere is None:
        return {"motif": "initiale", "derniere": None,
                "nb_seances": nb_seances_depuis(participant_id, None)}
    if derniere.type_evaluation == "fin_parcours":
        return None
    nb = nb_seances_depuis(participant_id, derniere.date_evaluation)
    if nb >= HART_SEUIL_SEANCES:
        return {"motif": "suivi", "derniere": derniere, "nb_seances": nb}
    return None


def evaluations_dues_pour_session(session: SessionActivite) -> list[dict]:
    """Pour l'écran d'émargement : participants présents dont l'évaluation est due."""
    presences = PresenceActivite.query.filter_by(session_id=session.id).all()
    dues = []
    for pr in presences:
        p = db.session.get(Participant, pr.participant_id)
        if p is None:
            continue
        due = evaluation_due(p.id)
        if due:
            dues.append({"participant": p, **due})
    dues.sort(key=lambda d: ((d["participant"].nom or "").lower(), (d["participant"].prenom or "").lower()))
    return dues


def _participants_actifs(secteur: str | None, date_from: date, date_to: date) -> list[int]:
    """Participants avec au moins une présence sur le secteur pendant la période."""
    eff = _date_session_effective()
    q = (db.session.query(PresenceActivite.participant_id)
         .join(SessionActivite, SessionActivite.id == PresenceActivite.session_id)
         .filter(SessionActivite.is_deleted.is_(False))
         .filter(eff >= date_from, eff <= date_to))
    if secteur:
        q = q.filter(SessionActivite.secteur == secteur)
    return [pid for (pid,) in q.distinct().all()]


def _derniere_evaluation_avant(participant_ids: list[int], date_limite: date) -> dict[int, HartEvaluation]:
    """Dernière évaluation de chaque participant à la date limite (photo à l'instant T)."""
    out: dict[int, HartEvaluation] = {}
    if not participant_ids:
        return out
    rows = (HartEvaluation.query
            .filter(HartEvaluation.participant_id.in_(participant_ids))
            .filter(HartEvaluation.date_evaluation <= date_limite)
            .order_by(HartEvaluation.date_evaluation.asc(), HartEvaluation.id.asc())
            .all())
    for ev in rows:   # tri croissant : la dernière écrase les précédentes
        out[ev.participant_id] = ev
    return out


def stats_collectives(secteur: str | None, date_from: date, date_to: date) -> dict:
    """Photo de l'échelle pour un secteur et une période.

    - participants : au moins une présence sur le secteur pendant la période ;
    - niveau retenu : dernière évaluation connue à la fin de la période
      (instant T), même si elle a été posée avant la période ;
    - « non évalués » : présents sur la période mais jamais positionnés.
    """
    pids = _participants_actifs(secteur, date_from, date_to)
    dernieres = _derniere_evaluation_avant(pids, date_to)

    etages = {d["niveau"]: [] for d in HART_NIVEAUX}
    non_evalues = []
    for pid in pids:
        ev = dernieres.get(pid)
        if ev is None or ev.niveau not in etages:
            non_evalues.append(pid)
        else:
            etages[ev.niveau].append(ev)

    participants_map = {}
    if pids:
        for p in Participant.query.filter(Participant.id.in_(pids)).all():
            participants_map[p.id] = p

    evalues = [ev for evs in etages.values() for ev in evs]
    niveaux = [ev.niveau for ev in evalues]
    moyenne = round(sum(niveaux) / len(niveaux), 2) if niveaux else None
    nb_participation = sum(1 for n in niveaux if HART_NIVEAUX_DICT[n]["participation"])
    taux_participation = round(nb_participation / len(niveaux) * 100, 1) if niveaux else None

    detail = []
    for niv in HART_NIVEAUX:
        n = niv["niveau"]
        evs = sorted(etages[n], key=lambda e: ((participants_map.get(e.participant_id).nom or "").lower()
                                               if participants_map.get(e.participant_id) else ""))
        detail.append({
            **niv,
            "count": len(evs),
            "evaluations": [{"participant": participants_map.get(e.participant_id), "evaluation": e} for e in evs],
        })

    return {
        "date_from": date_from, "date_to": date_to, "secteur": secteur,
        "total_actifs": len(pids),
        "total_evalues": len(niveaux),
        "non_evalues": [participants_map.get(pid) for pid in non_evalues if participants_map.get(pid)],
        "moyenne": moyenne,
        "taux_participation": taux_participation,
        "detail": detail,
        "counts": {n: len(etages[n]) for n in etages},
    }


def comparaison(stats_a: dict, stats_b: dict) -> dict:
    """Écarts entre deux photos (période A = référence récente, B = comparée)."""
    deltas = {}
    for n in stats_a["counts"]:
        deltas[n] = stats_a["counts"][n] - stats_b["counts"].get(n, 0)
    d_moy = None
    if stats_a["moyenne"] is not None and stats_b["moyenne"] is not None:
        d_moy = round(stats_a["moyenne"] - stats_b["moyenne"], 2)
    return {"deltas": deltas, "delta_moyenne": d_moy,
            "delta_evalues": stats_a["total_evalues"] - stats_b["total_evalues"]}
