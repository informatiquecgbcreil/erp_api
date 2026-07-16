from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Dict, List, Tuple

from sqlalchemy import func

from app.extensions import db
from app.models import AtelierActivite, PresenceActivite, SessionActivite
from .engine import _apply_common_filters


DEFAULT_COLLECTIF_CAPACITY = 12


def _session_date_expr():
    # Use rdv_date for individuel, date_session for collectif
    return func.coalesce(SessionActivite.rdv_date, SessionActivite.date_session)


def compute_occupancy_stats(flt) -> Dict[str, Any]:
    """Compute occupancy / fill-rate stats for COLLECTIF sessions only.

    Rules:
    - Only sessions with session_type == 'COLLECTIF' are considered.
    - capacity_effective = session.capacite if set else atelier.capacite_defaut if set else DEFAULT_COLLECTIF_CAPACITY.
    - RDV / individuel sessions are excluded (as requested).

    Returns safe aggregated numbers (no participant identities).
    """

    q = db.session.query(SessionActivite, AtelierActivite).join(
        AtelierActivite, AtelierActivite.id == SessionActivite.atelier_id
    )
    q = _apply_common_filters(q, flt)
    q = q.filter(SessionActivite.session_type == "COLLECTIF")

    sessions_rows: List[Tuple[SessionActivite, AtelierActivite]] = q.all()
    if not sessions_rows:
        return {
            "collective_sessions": 0,
            "collective_presences": 0,
            "avg_fill_rate_pct": None,
            "buckets": {"<50%": 0, "50-79%": 0, "80-99%": 0, "100%+": 0},
            "per_atelier": [],
        }

    session_ids = [s.id for s, _a in sessions_rows]

    # Presences per session
    pres_rows = (
        db.session.query(PresenceActivite.session_id)
        .filter(PresenceActivite.session_id.in_(session_ids))
        .all()
    )
    pres_by_session = Counter([sid for (sid,) in pres_rows])

    total_presences = sum(pres_by_session.values())

    # Compute fill rates
    fill_rates: List[float] = []
    bucket_counts = Counter({"<50%": 0, "50-79%": 0, "80-99%": 0, "100%+": 0})

    # Per-atelier aggregation
    per_atelier = defaultdict(lambda: {
        "atelier_id": None,
        "secteur": "",
        "nom": "",
        "sessions": 0,
        "presences": 0,
        "capacity_total": 0,
        "fill_rates": [],
    })

    for session, atelier in sessions_rows:
        cap = session.capacite if session.capacite is not None else atelier.capacite_defaut
        if cap is None or cap <= 0:
            cap = DEFAULT_COLLECTIF_CAPACITY

        pres = int(pres_by_session.get(session.id, 0))
        rate = (pres / float(cap)) if cap else 0.0
        fill_rates.append(rate)

        pct = rate * 100.0
        if pct < 50:
            bucket_counts["<50%"] += 1
        elif pct < 80:
            bucket_counts["50-79%"] += 1
        elif pct < 100:
            bucket_counts["80-99%"] += 1
        else:
            bucket_counts["100%+"] += 1

        a = per_atelier[atelier.id]
        a["atelier_id"] = atelier.id
        a["secteur"] = atelier.secteur
        a["nom"] = atelier.nom
        a["sessions"] += 1
        a["presences"] += pres
        a["capacity_total"] += int(cap)
        a["fill_rates"].append(rate)

    avg_fill = (sum(fill_rates) / len(fill_rates)) if fill_rates else 0.0

    per_atelier_list = []
    for aid, a in per_atelier.items():
        rates = a["fill_rates"]
        avg_a = (sum(rates) / len(rates)) if rates else 0.0
        per_atelier_list.append({
            "atelier_id": a["atelier_id"],
            "secteur": a["secteur"],
            "nom": a["nom"],
            "sessions": a["sessions"],
            "presences": a["presences"],
            "capacity_total": a["capacity_total"],
            "avg_fill_rate_pct": round(avg_a * 100.0, 1),
        })

    per_atelier_list.sort(key=lambda x: (-x["avg_fill_rate_pct"], -x["sessions"], x["nom"]))

    return {
        "collective_sessions": len(sessions_rows),
        "collective_presences": int(total_presences),
        "avg_fill_rate_pct": round(avg_fill * 100.0, 1),
        "buckets": dict(bucket_counts),
        "per_atelier": per_atelier_list,
        "default_capacity": DEFAULT_COLLECTIF_CAPACITY,
    }
