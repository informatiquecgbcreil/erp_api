from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from flask_login import current_user
from sqlalchemy import func

from app.extensions import db
from app.models import AtelierActivite, PresenceActivite, SessionActivite, PeriodeFinancement, Participant



def _user_has_any_perm(user=None, *codes: str) -> bool:
    user = user or current_user
    has_perm = getattr(user, "has_perm", None)
    if not callable(has_perm):
        return False
    return any(has_perm(code) for code in codes if code)


def _user_can_view_all_stats(user=None) -> bool:
    return _user_has_any_perm(user, "scope:all_secteurs", "stats:view_all", "statsimpact:view_all")


def _user_assigned_secteur(user=None) -> Optional[str]:
    user = user or current_user
    return (getattr(user, "secteur_assigne", None) or getattr(user, "secteur", None) or "").strip() or None


def _sanitize_atelier_ids_for_scope(selected_ids: List[int], secteur_scope: Optional[str]) -> List[int]:
    if not selected_ids or not secteur_scope or secteur_scope == "__restricted__":
        return selected_ids
    rows = (
        db.session.query(AtelierActivite.id)
        .filter(AtelierActivite.id.in_(selected_ids))
        .filter(AtelierActivite.secteur == secteur_scope)
        .all()
    )
    allowed = {int(r[0]) for r in rows}
    return [aid for aid in selected_ids if aid in allowed]


# ---------------------------
# Helpers: parsing & labeling
# ---------------------------

def _parse_date(s: str) -> Optional[date]:
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except Exception:
        return None


def _parse_time_minutes(t: Optional[str]) -> Optional[int]:
    """
    Accepts formats like: "14:30", "14h30", "14h", "14:30:00".
    Returns minutes since midnight.
    """
    if not t:
        return None
    s = str(t).strip().lower()
    s = s.replace(" ", "")
    s = s.replace("h", ":")
    if s.endswith(":"):
        s += "00"
    try:
        parts = s.split(":")
        if len(parts) == 1:
            hh = int(parts[0])
            mm = 0
        else:
            hh = int(parts[0])
            mm = int(parts[1]) if parts[1] else 0
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            return hh * 60 + mm
    except Exception:
        return None
    return None


def _month_label(y: int, m: int) -> str:
    return f"{y}-{m:02d}"


def _quarter_label(y: int, q: int) -> str:
    return f"{y}-Q{q}"


def _group_label(d: date, group_by: str) -> Tuple[Tuple[int, int, int], str]:
    """
    Returns (sort_key, label) for a date given a grouping.
    sort_key is used to sort labels in chronological order.
    """
    gb = (group_by or "MONTH").upper()
    if gb == "DAY":
        return (d.year, d.month, d.day), d.strftime("%Y-%m-%d")
    if gb == "YEAR":
        return (d.year, 0, 0), f"{d.year}"
    if gb == "QUARTER":
        q = (d.month - 1) // 3 + 1
        return (d.year, q, 0), _quarter_label(d.year, q)
    # default MONTH
    return (d.year, d.month, 0), _month_label(d.year, d.month)


def _apply_preset(preset: str, today: Optional[date] = None) -> Tuple[date, date]:
    """
    Returns (date_from, date_to) for a preset.
    date_to is inclusive.
    """
    p = (preset or "").upper().strip()
    t = today or date.today()

    if p == "TODAY":
        return t, t
    if p == "YESTERDAY":
        y = t - timedelta(days=1)
        return y, y

    if p in ("THIS_MONTH", "MONTH_THIS"):
        start = date(t.year, t.month, 1)
        next_month = start.replace(day=28) + timedelta(days=4)
        end = next_month - timedelta(days=next_month.day)
        return start, end

    if p in ("PREV_MONTH", "MONTH_PREV", "LAST_MONTH"):
        start_this = date(t.year, t.month, 1)
        end_prev = start_this - timedelta(days=1)
        start_prev = date(end_prev.year, end_prev.month, 1)
        return start_prev, end_prev

    if p in ("THIS_YEAR", "YEAR_THIS"):
        return date(t.year, 1, 1), date(t.year, 12, 31)

    if p in ("PREV_YEAR", "YEAR_PREV", "LAST_YEAR"):
        y = t.year - 1
        return date(y, 1, 1), date(y, 12, 31)

    if p in ("THIS_QUARTER", "QUARTER_THIS"):
        q = (t.month - 1) // 3 + 1
        start_month = (q - 1) * 3 + 1
        start = date(t.year, start_month, 1)
        end_month = start_month + 2
        end_base = date(t.year, end_month, 1)
        next_month = end_base.replace(day=28) + timedelta(days=4)
        end = next_month - timedelta(days=next_month.day)
        return start, end

    if p in ("PREV_QUARTER", "QUARTER_PREV", "LAST_QUARTER"):
        q = (t.month - 1) // 3 + 1
        y = t.year
        q_prev = q - 1
        if q_prev <= 0:
            q_prev = 4
            y -= 1
        start_month = (q_prev - 1) * 3 + 1
        start = date(y, start_month, 1)
        end_month = start_month + 2
        end_base = date(y, end_month, 1)
        next_month = end_base.replace(day=28) + timedelta(days=4)
        end = next_month - timedelta(days=next_month.day)
        return start, end

    return t, t


def _session_date_expr():
    return func.coalesce(SessionActivite.rdv_date, SessionActivite.date_session)


def _session_duration_minutes(session: SessionActivite, atelier: AtelierActivite) -> int:
    if (session.session_type or "").upper() == "COLLECTIF":
        start = _parse_time_minutes(session.heure_debut)
        end = _parse_time_minutes(session.heure_fin)
        if start is not None and end is not None and end > start:
            return int(end - start)
        if getattr(atelier, "duree_defaut_minutes", None):
            return int(atelier.duree_defaut_minutes)
        return 0

    start = _parse_time_minutes(session.rdv_debut)
    end = _parse_time_minutes(session.rdv_fin)
    if start is not None and end is not None and end > start:
        return int(end - start)
    if getattr(atelier, "duree_defaut_minutes", None):
        return int(atelier.duree_defaut_minutes)
    return 0




def _parse_int_list(values: Any) -> List[int]:
    if values is None:
        return []

    if isinstance(values, (str, int)):
        raw_items = [values]
    elif isinstance(values, Iterable):
        raw_items = list(values)
    else:
        raw_items = [values]

    parsed: List[int] = []
    seen: Set[int] = set()
    for item in raw_items:
        if item in (None, "", "None"):
            continue
        if isinstance(item, str) and "," in item:
            chunks = [chunk.strip() for chunk in item.split(",")]
        else:
            chunks = [item]
        for chunk in chunks:
            if chunk in (None, "", "None"):
                continue
            try:
                value = int(chunk)
            except Exception:
                continue
            if value not in seen:
                seen.add(value)
                parsed.append(value)
    return parsed


def _selected_atelier_ids(flt: StatsFilters) -> List[int]:
    ids = _parse_int_list(getattr(flt, "atelier_ids", None))
    if ids:
        return ids
    if getattr(flt, "atelier_id", None):
        return [int(flt.atelier_id)]
    return []


def _has_atelier_filter(flt: StatsFilters) -> bool:
    return bool(_selected_atelier_ids(flt))


# ---------------------------
# Filters
# ---------------------------

@dataclass
class StatsFilters:
    secteur: Optional[str] = None
    atelier_id: Optional[int] = None
    atelier_ids: List[int] = None

    date_from: Optional[date] = None
    date_to: Optional[date] = None  # inclusive

    preset: Optional[str] = None
    group_by: str = "MONTH"  # DAY / MONTH / QUARTER / YEAR

    periode_id: Optional[int] = None


def normalize_filters(
    args: Optional[dict] = None,
    user=None,
    secteur: Optional[str] = None,
    atelier_id: Optional[int] = None,
    atelier_ids: Optional[List[int]] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    preset: Optional[str] = None,
    group_by: Optional[str] = None,
    periode_id: Optional[int] = None,
) -> StatsFilters:
    """
    Compat wrapper: accepte 2 styles d'appel
    - normalize_filters(args, user=current_user)   (ancien style)
    - normalize_filters(secteur=..., atelier_id=..., ...) (nouveau style)
    """
    if args is not None:
        getter = getattr(args, "get", None)
        getlist = getattr(args, "getlist", None)
        if secteur is None and callable(getter):
            secteur = getter("secteur") or getter("sector")
        if atelier_ids is None:
            if callable(getlist):
                atelier_ids = _parse_int_list((getlist("atelier_ids") or []) + (getlist("atelier_id") or []))
            elif callable(getter):
                atelier_ids = _parse_int_list(getter("atelier_ids") or getter("atelier_id"))
        else:
            atelier_ids = _parse_int_list(atelier_ids)
        if atelier_id is None and callable(getter):
            v = getter("atelier_id") or getter("atelier")
            atelier_id = int(v) if v not in (None, "", "None") else None
        if date_from is None and callable(getter):
            date_from = getter("date_from")
        if date_to is None and callable(getter):
            date_to = getter("date_to")
        if preset is None and callable(getter):
            preset = getter("preset")
        if group_by is None and callable(getter):
            group_by = getter("group_by")
        if periode_id is None and callable(getter):
            v = getter("periode_id") or getter("periode")
            periode_id = int(v) if v not in (None, "", "None") else None

    selected_ids = _parse_int_list(atelier_ids)
    if not selected_ids and atelier_id not in (None, "", "None"):
        selected_ids = _parse_int_list([atelier_id])

    user_obj = user or current_user
    if not _user_can_view_all_stats(user_obj):
        forced_secteur = _user_assigned_secteur(user_obj)
        if forced_secteur:
            secteur = forced_secteur
            selected_ids = _sanitize_atelier_ids_for_scope(selected_ids, forced_secteur)
        else:
            selected_ids = []

    flt = StatsFilters(
        secteur=secteur or None,
        atelier_id=(selected_ids[0] if len(selected_ids) == 1 else (atelier_id or None)),
        atelier_ids=selected_ids,
        preset=preset or None,
        group_by=(group_by or "MONTH").upper(),
        periode_id=periode_id or None,
    )

    df = _parse_date(date_from) if date_from else None
    dt = _parse_date(date_to) if date_to else None

    if flt.periode_id and (df is None and dt is None):
        p = db.session.get(PeriodeFinancement, flt.periode_id)
        if p:
            df = p.date_debut
            dt = p.date_fin

    if flt.preset and (df is None and dt is None):
        df, dt = _apply_preset(flt.preset)

    flt.date_from = df
    flt.date_to = dt
    return flt


def _resolve_secteur_scope(flt: StatsFilters) -> Optional[str]:
    """Détermine le secteur effectif visible pour l'utilisateur."""
    if _user_can_view_all_stats(current_user):
        return flt.secteur or None

    user_sect = _user_assigned_secteur(current_user)
    if user_sect:
        return user_sect

    return "__restricted__"


def _apply_common_filters(query, flt: StatsFilters):
    query = query.filter(SessionActivite.is_deleted.is_(False))
    query = query.filter(AtelierActivite.is_deleted.is_(False))
    selected_atelier_ids = _selected_atelier_ids(flt)
    if not selected_atelier_ids:
        query = query.filter(AtelierActivite.is_active.is_(True))

    eff_secteur = _resolve_secteur_scope(flt)
    if eff_secteur:
        query = query.filter(AtelierActivite.secteur == eff_secteur)

    if selected_atelier_ids:
        query = query.filter(AtelierActivite.id.in_(selected_atelier_ids))

    if flt.date_from:
        query = query.filter(_session_date_expr() >= flt.date_from)
    if flt.date_to:
        query = query.filter(_session_date_expr() <= flt.date_to)

    return query


def _query_sessions_for_period(flt: StatsFilters, date_from: Optional[date], date_to: Optional[date]):
    query = db.session.query(SessionActivite, AtelierActivite).join(
        AtelierActivite, AtelierActivite.id == SessionActivite.atelier_id
    )
    query = query.filter(SessionActivite.is_deleted.is_(False))
    query = query.filter(AtelierActivite.is_deleted.is_(False))
    selected_atelier_ids = _selected_atelier_ids(flt)
    if not selected_atelier_ids:
        query = query.filter(AtelierActivite.is_active.is_(True))

    eff_secteur = _resolve_secteur_scope(flt)
    if eff_secteur:
        query = query.filter(AtelierActivite.secteur == eff_secteur)

    if selected_atelier_ids:
        query = query.filter(AtelierActivite.id.in_(selected_atelier_ids))

    if date_from:
        query = query.filter(_session_date_expr() >= date_from)
    if date_to:
        query = query.filter(_session_date_expr() <= date_to)

    return query



# ---------------------------
# Main compute (Phase 1)
# ---------------------------

def _continuity_group_id(atelier: AtelierActivite) -> int:
    return int(getattr(atelier, "continuity_parent_id", None) or atelier.id)


def compute_volume_activity_stats(flt: StatsFilters) -> Dict[str, Any]:
    sessions_rows: List[Tuple[SessionActivite, AtelierActivite]] = _query_sessions_for_period(
        flt, flt.date_from, flt.date_to
    ).all()
    session_ids = [s.id for s, _ in sessions_rows]

    previous_atelier_ids: Optional[Set[int]] = None
    if flt.date_from and flt.date_to and flt.date_to >= flt.date_from:
        span_days = (flt.date_to - flt.date_from).days + 1
        prev_end = flt.date_from - timedelta(days=1)
        prev_start = prev_end - timedelta(days=span_days - 1) if span_days > 0 else prev_end
        prev_rows: List[Tuple[SessionActivite, AtelierActivite]] = _query_sessions_for_period(
            flt, prev_start, prev_end
        ).all()
        previous_atelier_ids = {a.id for _, a in prev_rows}

    if session_ids:
        presences: List[PresenceActivite] = (
            db.session.query(PresenceActivite)
            .filter(PresenceActivite.session_id.in_(session_ids))
            .all()
        )
    else:
        presences = []

    # Les séances annulées restent dans sessions_rows pour distinguer prévu/réel
    # par atelier, mais les KPI de tête reflètent l'activité RÉALISÉE : on écarte
    # les séances annulées et leurs présences.
    real_session_ids = {
        s.id for s, _ in sessions_rows if (s.statut or "").strip().lower() != "annulee"
    }
    presences_real = [p for p in presences if p.session_id in real_session_ids]

    sessions_count = len(real_session_ids)
    presences_total = len(presences_real)
    uniques = len({p.participant_id for p in presences_real})

    # New participants in period (first time in whole system within range)
    new_participants = 0
    if flt.date_from or flt.date_to:
        first_seen_sub = (
            db.session.query(
                PresenceActivite.participant_id.label("pid"),
                func.min(_session_date_expr()).label("first_date"),
            )
            .join(SessionActivite, SessionActivite.id == PresenceActivite.session_id)
            .filter(SessionActivite.is_deleted.is_(False))
            .group_by(PresenceActivite.participant_id)
            .subquery()
        )
        q_new = db.session.query(func.count()).select_from(first_seen_sub)
        if flt.date_from:
            q_new = q_new.filter(first_seen_sub.c.first_date >= flt.date_from)
        if flt.date_to:
            q_new = q_new.filter(first_seen_sub.c.first_date <= flt.date_to)
        new_participants = int(q_new.scalar() or 0)

    pres_by_session: Dict[int, int] = {}
    for p in presences_real:
        pres_by_session[p.session_id] = pres_by_session.get(p.session_id, 0) + 1

    hours_animator = 0.0
    hours_people = 0.0

    per_atelier: Dict[int, Dict[str, Any]] = {}
    for session, atelier in sessions_rows:
        gid = _continuity_group_id(atelier)
        if gid not in per_atelier:
            per_atelier[gid] = {
                "atelier_id": gid,
                "secteur": atelier.secteur,
                "nom": atelier.nom,
                "alias_noms": set(),
                "type_atelier": getattr(atelier, "type_atelier", None),
                "sessions_planned": 0,
                "sessions_real": 0,
                "planned_capacity": 0,
                "real_capacity": 0,
                "planned_hours": 0.0,
                "real_hours": 0.0,
                "sessions": 0,
                "presences": 0,
                "uniques": set(),
                "hours_animator": 0.0,
                "hours_people": 0.0,
                "dates": [],
            }

        per_atelier[gid]["alias_noms"].add(atelier.nom)
        per_atelier[gid]["sessions"] += 1
        per_atelier[gid]["sessions_planned"] += 1

        is_real = (session.statut or "").lower() != "annulee"
        if is_real:
            per_atelier[gid]["sessions_real"] += 1
        session_date = session.rdv_date or session.date_session
        if session_date:
            per_atelier[gid]["dates"].append(session_date)

        mins = _session_duration_minutes(session, atelier)
        if mins <= 0:
            continue
        h = mins / 60.0
        # planned_hours = tout le planifié ; les heures effectives (animateur /
        # face public) ne comptent que les séances réalisées (non annulées).
        per_atelier[gid]["planned_hours"] += h
        if is_real:
            per_atelier[gid]["real_hours"] += h
            hours_animator += h
            per_atelier[gid]["hours_animator"] += h

            count_p = pres_by_session.get(session.id, 0)
            if (session.session_type or "").upper() == "COLLECTIF":
                hours_people += h * float(count_p)
                per_atelier[gid]["hours_people"] += h * float(count_p)
            else:
                if count_p > 0:
                    hours_people += h
                    per_atelier[gid]["hours_people"] += h
        cap = session.capacite if session.capacite is not None else getattr(atelier, "capacite_defaut", 0) or 0
        per_atelier[gid]["planned_capacity"] += int(cap or 0)
        if is_real:
            per_atelier[gid]["real_capacity"] += int(cap or 0)

    activity_duration_days = None
    if sessions_rows:
        dates = [d for d in [(s.rdv_date or s.date_session) for s, _ in sessions_rows] if d]
        if dates:
            dmin, dmax = min(dates), max(dates)
            activity_duration_days = (dmax - dmin).days

    avg_per_session = (presences_total / sessions_count) if sessions_count else 0.0

    # Time series
    series: Dict[str, Dict[str, Any]] = {}
    series_sort: Dict[str, Tuple[int, int, int]] = {}

    for session, _atelier in sessions_rows:
        if session.id not in real_session_ids:
            continue
        d = session.rdv_date or session.date_session
        if not d:
            continue
        sk, label = _group_label(d, flt.group_by)
        series.setdefault(label, {"label": label, "sessions": 0, "presences": 0, "uniques": set()})
        series_sort.setdefault(label, sk)
        series[label]["sessions"] += 1

    session_date_map: Dict[int, date] = {}
    for session, _atelier in sessions_rows:
        d = session.rdv_date or session.date_session
        if d:
            session_date_map[session.id] = d

    for p in presences_real:
        d = session_date_map.get(p.session_id)
        if not d:
            continue
        sk, label = _group_label(d, flt.group_by)
        series.setdefault(label, {"label": label, "sessions": 0, "presences": 0, "uniques": set()})
        series_sort.setdefault(label, sk)
        series[label]["presences"] += 1
        series[label]["uniques"].add(p.participant_id)

    time_series = []
    for label, obj in sorted(series.items(), key=lambda kv: series_sort.get(kv[0], (9999, 99, 99))):
        time_series.append(
            {
                "label": obj["label"],
                "sessions": int(obj["sessions"]),
                "presences": int(obj["presences"]),
                "uniques": len(obj["uniques"]),
            }
        )

    # Heatmap weekday x bucket (session start)
    days = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"]
    buckets = [(8, 10), (10, 12), (12, 14), (14, 16), (16, 18), (18, 20)]
    bucket_labels = [f"{a:02d}-{b:02d}" for a, b in buckets]

    heat = {d: {b: 0 for b in bucket_labels} for d in days}
    for session, _atelier in sessions_rows:
        if session.id not in real_session_ids:
            continue
        sd = session.rdv_date or session.date_session
        if not sd:
            continue
        wd = sd.weekday()
        day_label = days[wd]

        t = session.heure_debut if (session.session_type or "").upper() == "COLLECTIF" else session.rdv_debut
        mins = _parse_time_minutes(t)
        if mins is None:
            continue

        bucket = None
        for a, b in buckets:
            if a * 60 <= mins < b * 60:
                bucket = f"{a:02d}-{b:02d}"
                break
        if not bucket:
            continue
        heat[day_label][bucket] += 1

    # Per-atelier presences + uniques
    session_to_atelier = {s.id: _continuity_group_id(a) for s, a in sessions_rows}
    for p in presences_real:
        aid = session_to_atelier.get(p.session_id)
        if not aid or aid not in per_atelier:
            continue
        per_atelier[aid]["presences"] += 1
        per_atelier[aid]["uniques"].add(p.participant_id)

    table_ateliers = []
    for aid, obj in per_atelier.items():
        activity_days = None
        if obj["dates"]:
            dmin, dmax = min(obj["dates"]), max(obj["dates"])
            activity_days = (dmax - dmin).days
        table_ateliers.append(
            {
                "atelier_id": aid,
                "secteur": obj["secteur"],
                "nom": obj["nom"],
                "alias_noms": sorted(obj.get("alias_noms", set())),
                "type_atelier": obj["type_atelier"],
                "sessions": int(obj["sessions"]),
                "presences": int(obj["presences"]),
                "uniques": len(obj["uniques"]),
                "hours_animator": round(float(obj["hours_animator"]), 2),
                "hours_people": round(float(obj["hours_people"]), 2),
                "is_new_vs_previous": bool(previous_atelier_ids is not None and aid not in previous_atelier_ids),
                "sessions_planned": int(obj["sessions_planned"]),
                "sessions_real": int(obj["sessions_real"]),
                "planned_capacity": int(obj["planned_capacity"]),
                "real_capacity": int(obj["real_capacity"]),
                "planned_hours": round(float(obj["planned_hours"]), 2),
                "real_hours": round(float(obj["real_hours"]), 2),
                "occupation_rate": round(
                    (int(obj["presences"]) / obj["real_capacity"] * 100.0) if obj["real_capacity"] else 0.0, 1
                ),
                "avg_per_session_real": round(
                    (int(obj["presences"]) / obj["sessions_real"]) if obj["sessions_real"] else 0.0, 2
                ),
                "activity_duration_days": activity_days,
            }
        )
    table_ateliers.sort(key=lambda r: (r["presences"], r["sessions"]), reverse=True)

    top_ateliers = table_ateliers[:3]

    sectors_agg: Dict[str, Dict[str, Any]] = {}
    for row in table_ateliers:
        sect = row["secteur"] or "(Non renseigné)"
        if sect not in sectors_agg:
            sectors_agg[sect] = {
                "secteur": sect,
                "sessions": 0,
                "presences": 0,
                "uniques": 0,
                "hours_animator": 0.0,
                "hours_people": 0.0,
            }
        sectors_agg[sect]["sessions"] += row["sessions"]
        sectors_agg[sect]["presences"] += row["presences"]
        sectors_agg[sect]["uniques"] += row["uniques"]
        sectors_agg[sect]["hours_animator"] += float(row["hours_animator"])
        sectors_agg[sect]["hours_people"] += float(row["hours_people"])

    sectors_summary = [
        {
            "secteur": v["secteur"],
            "sessions": int(v["sessions"]),
            "presences": int(v["presences"]),
            "uniques": int(v["uniques"]),
            "hours_animator": round(float(v["hours_animator"]), 2),
            "hours_people": round(float(v["hours_people"]), 2),
        }
        for v in sectors_agg.values()
    ]
    sectors_summary.sort(key=lambda r: (r["presences"], r["sessions"]), reverse=True)

    base_by_secteur: Dict[str, List[Dict[str, Any]]] = {}
    for row in table_ateliers:
        base_by_secteur.setdefault(row["secteur"] or "(Non renseigné)", []).append(row)

    return {
        "kpi": {
            "sessions": sessions_count,
            "presences": presences_total,
            "uniques": uniques,
            "new_participants": new_participants,
            "hours_animator": round(hours_animator, 2),
            "hours_people": round(hours_people, 2),
            "avg_per_session": round(avg_per_session, 2),
            "activity_duration_days": activity_duration_days,
        },
        "time_series": time_series,
        "heatmap": {"days": days, "buckets": bucket_labels, "data": heat},
        "table_ateliers": table_ateliers,
        "top_ateliers": top_ateliers,
        "sectors_summary": sectors_summary,
        "has_previous_period": previous_atelier_ids is not None,
        "base_by_secteur": base_by_secteur,
    }


# ---------------------------
# Phase 2 stats
# ---------------------------

def _get_scoped_sessions_and_presences(flt: StatsFilters):
    base = db.session.query(SessionActivite, AtelierActivite).join(
        AtelierActivite, AtelierActivite.id == SessionActivite.atelier_id
    )
    base = _apply_common_filters(base, flt)
    sessions_rows = base.all()
    session_ids = [s.id for s, _ in sessions_rows]
    if session_ids:
        presences = (
            db.session.query(PresenceActivite)
            .filter(PresenceActivite.session_id.in_(session_ids))
            .all()
        )
    else:
        presences = []
    return sessions_rows, presences


def compute_participation_frequency_stats(flt: StatsFilters) -> Dict[str, Any]:
    sessions_rows, presences = _get_scoped_sessions_and_presences(flt)
    counts = Counter([p.participant_id for p in presences])
    uniques = len(counts)
    pres_total = sum(counts.values())
    freq_avg = (pres_total / uniques) if uniques else 0.0

    returning = sum(1 for n in counts.values() if n >= 2)
    returning_rate = (returning / uniques) if uniques else 0.0

    buckets = {"1": 0, "2-3": 0, "4-6": 0, "7+": 0}
    for n in counts.values():
        if n <= 1:
            buckets["1"] += 1
        elif 2 <= n <= 3:
            buckets["2-3"] += 1
        elif 4 <= n <= 6:
            buckets["4-6"] += 1
        else:
            buckets["7+"] += 1

    regulars_4plus = sum(1 for n in counts.values() if n >= 4)

    return {
        "uniques": uniques,
        "presences_total": pres_total,
        "freq_avg": round(freq_avg, 2),
        "returning": returning,
        "returning_rate": round(returning_rate * 100, 1),
        "regulars_4plus": regulars_4plus,
        "buckets": buckets,
    }


def compute_transversalite_stats(flt: StatsFilters) -> Dict[str, Any]:
    sessions_rows, presences = _get_scoped_sessions_and_presences(flt)
    scope_secteur = _resolve_secteur_scope(flt)

    scope_participants: Set[int] = set(p.participant_id for p in presences)
    if not scope_participants:
        return {
            "scope_secteur": scope_secteur,
            "uniques": 0,
            "multi_count": 0,
            "multi_rate": 0.0,
            "top_cross": [],
            "scope_limited": not _user_can_view_all_stats(current_user),
        }

    if not _user_can_view_all_stats(current_user):
        return {
            "scope_secteur": scope_secteur,
            "uniques": len(scope_participants),
            "multi_count": 0,
            "multi_rate": 0.0,
            "top_cross": [],
            "scope_limited": True,
        }

    base = (
        db.session.query(PresenceActivite.participant_id, AtelierActivite.secteur)
        .join(SessionActivite, SessionActivite.id == PresenceActivite.session_id)
        .join(AtelierActivite, AtelierActivite.id == SessionActivite.atelier_id)
        .filter(SessionActivite.is_deleted.is_(False))
        .filter(AtelierActivite.is_deleted.is_(False))
        .filter(PresenceActivite.participant_id.in_(list(scope_participants)))
    )
    if flt.date_from:
        base = base.filter(_session_date_expr() >= flt.date_from)
    if flt.date_to:
        base = base.filter(_session_date_expr() <= flt.date_to)

    rows = base.all()

    secteurs_by_pid: Dict[int, Set[str]] = defaultdict(set)
    for pid, sect in rows:
        if sect:
            secteurs_by_pid[pid].add(str(sect).strip())

    multi_pids = [pid for pid, sset in secteurs_by_pid.items() if len(sset) >= 2]
    multi_count = len(multi_pids)
    uniques = len(scope_participants)
    multi_rate = (multi_count / uniques) * 100.0 if uniques else 0.0

    cross_counts = Counter()
    if scope_secteur:
        for pid in scope_participants:
            sset = secteurs_by_pid.get(pid, set())
            if scope_secteur in sset:
                for other in sset:
                    if other != scope_secteur:
                        cross_counts[other] += 1

    top_cross = [{"secteur": k, "participants_communs": v} for k, v in cross_counts.most_common(10)]

    return {
        "scope_secteur": scope_secteur,
        "uniques": uniques,
        "multi_count": multi_count,
        "multi_rate": round(multi_rate, 1),
        "top_cross": top_cross,
        "scope_limited": False,
    }


def compute_demography_stats(flt: StatsFilters) -> Dict[str, Any]:
    sessions_rows, presences = _get_scoped_sessions_and_presences(flt)
    pids = sorted(set(p.participant_id for p in presences))
    if not pids:
        return {
            "age_avg": None,
            "age_buckets": {},
            "genre": {},
            "villes_top": [],
            "creil": {"creil": 0, "hors_creil": 0},
            "qpv": {"qpv": 0, "hors_qpv": 0, "inconnu": 0},
            "qpv_details": [],
            "type_public": {},
        }

    participants = db.session.query(Participant).filter(Participant.id.in_(pids)).all()

    # Âge figé à la fin de la période analysée (ou aujourd'hui à défaut) : un
    # bilan d'année passée ne doit pas vieillir les publics au fil du temps.
    ref_age = flt.date_to or date.today()

    ages = [p.age_au(ref_age) for p in participants if p.age_au(ref_age) is not None]
    age_avg = round(sum(ages) / len(ages), 1) if ages else None

    age_buckets = {"0-10": 0, "11-17": 0, "18-25": 0, "26-59": 0, "60+": 0, "Inconnu": 0}
    for p in participants:
        a = p.age_au(ref_age)
        if a is None:
            age_buckets["Inconnu"] += 1
        elif a <= 10:
            age_buckets["0-10"] += 1
        elif a <= 17:
            age_buckets["11-17"] += 1
        elif a <= 25:
            age_buckets["18-25"] += 1
        elif a <= 59:
            age_buckets["26-59"] += 1
        else:
            age_buckets["60+"] += 1

    genre_counts = Counter([(p.genre or "Inconnu").strip() or "Inconnu" for p in participants])

    ville_counts = Counter([(p.ville or "Inconnue").strip() or "Inconnue" for p in participants])
    villes_top = [{"ville": k, "count": v} for k, v in ville_counts.most_common(10)]

    creil = sum(1 for p in participants if getattr(p, "is_creil", False))
    hors_creil = len(participants) - creil

    qpv = 0
    hors_qpv = 0
    inconnu = 0
    qpv_detail_map = _empty_qpv_detail_map()
    for p in participants:
        bucket_key = _qpv_bucket_key(p)
        if getattr(p, "quartier", None) is None:
            inconnu += 1
        else:
            if getattr(p, "is_qpv", False):
                qpv += 1
            else:
                hors_qpv += 1
        _add_participant_to_qpv_detail(qpv_detail_map[bucket_key], p, ref_age)

    type_public_counts = Counter([(p.type_public or "H") for p in participants])

    return {
        "age_avg": age_avg,
        "age_buckets": age_buckets,
        "genre": dict(genre_counts),
        "villes_top": villes_top,
        "creil": {"creil": creil, "hors_creil": hors_creil},
        "qpv": {"qpv": qpv, "hors_qpv": hors_qpv, "inconnu": inconnu},
        "qpv_details": list(qpv_detail_map.values()),
        "type_public": dict(type_public_counts),
    }


def _empty_age_buckets() -> Dict[str, int]:
    return {"0-10": 0, "11-17": 0, "18-25": 0, "26-59": 0, "60+": 0, "Inconnu": 0}


def _empty_qpv_detail_map() -> Dict[str, Dict[str, Any]]:
    return {
        "qpv": {
            "key": "qpv",
            "label": "QPV",
            "total": 0,
            "genre": {"Femmes": 0, "Hommes": 0, "Autre / non renseigné": 0},
            "age_buckets": _empty_age_buckets(),
        },
        "hors_qpv": {
            "key": "hors_qpv",
            "label": "Hors QPV",
            "total": 0,
            "genre": {"Femmes": 0, "Hommes": 0, "Autre / non renseigné": 0},
            "age_buckets": _empty_age_buckets(),
        },
        "inconnu": {
            "key": "inconnu",
            "label": "Quartier inconnu",
            "total": 0,
            "genre": {"Femmes": 0, "Hommes": 0, "Autre / non renseigné": 0},
            "age_buckets": _empty_age_buckets(),
        },
    }


def _qpv_bucket_key(participant: Participant) -> str:
    if getattr(participant, "quartier", None) is None:
        return "inconnu"
    if getattr(participant, "is_qpv", False):
        return "qpv"
    return "hors_qpv"


def _participant_age_bucket(participant: Participant, reference: date | None = None) -> str:
    age = participant.age_au(reference)
    if age is None:
        return "Inconnu"
    if age <= 10:
        return "0-10"
    if age <= 17:
        return "11-17"
    if age <= 25:
        return "18-25"
    if age <= 59:
        return "26-59"
    return "60+"


def _participant_genre_bucket(participant: Participant) -> str:
    raw = (getattr(participant, "genre", None) or "").strip().lower()
    if raw.startswith("f") or "femme" in raw:
        return "Femmes"
    if raw.startswith("h") or "homme" in raw or raw in {"m", "masculin", "male"}:
        return "Hommes"
    return "Autre / non renseigné"


def _add_participant_to_qpv_detail(bucket: Dict[str, Any], participant: Participant, reference: date | None = None) -> None:
    bucket["total"] += 1
    bucket["genre"][_participant_genre_bucket(participant)] += 1
    bucket["age_buckets"][_participant_age_bucket(participant, reference)] += 1


def compute_participants_stats(flt: StatsFilters) -> Dict[str, Any]:
    sessions_rows, presences = _get_scoped_sessions_and_presences(flt)
    if not presences:
        return {"participants": [], "total": 0}

    session_map: Dict[int, Tuple[SessionActivite, AtelierActivite]] = {
        s.id: (s, a) for s, a in sessions_rows
    }
    pids = sorted(set(p.participant_id for p in presences))
    participants = {p.id: p for p in db.session.query(Participant).filter(Participant.id.in_(pids)).all()}

    per_participant: Dict[int, Dict[str, Any]] = {}

    for p in presences:
        pid = p.participant_id
        participant = participants.get(pid)
        sess_tuple = session_map.get(p.session_id)
        if not participant or not sess_tuple:
            continue
        session, atelier = sess_tuple
        date_visit = session.rdv_date or session.date_session
        aid = atelier.id

        if pid not in per_participant:
            per_participant[pid] = {
                "id": pid,
                "nom": participant.nom,
                "prenom": participant.prenom,
                "age": participant.age,
                "genre": participant.genre,
                "date_naissance": participant.date_naissance,
                "ville": participant.ville,
                "quartier": participant.quartier.nom if participant.quartier else None,
                "quartier_id": participant.quartier_id,
                "qpv": participant.is_qpv,
                "telephone": participant.telephone,
                "email": participant.email,
                "type_public": getattr(participant, "type_public", None) or "H",
                "sessions": [],
                "ateliers": {},
                "visites": 0,
            }

        per_participant[pid]["visites"] += 1
        per_participant[pid]["sessions"].append(
            {
                "date": date_visit,
                "atelier": atelier.nom,
                "atelier_id": aid,
                "secteur": atelier.secteur,
            }
        )
        a_map = per_participant[pid]["ateliers"].setdefault(
            aid, {"atelier": atelier.nom, "secteur": atelier.secteur, "visites": 0, "dates": []}
        )
        a_map["visites"] += 1
        if date_visit:
            a_map["dates"].append(date_visit)

    for obj in per_participant.values():
        obj["sessions"].sort(key=lambda s: s["date"] or date.max, reverse=True)
        for a in obj["ateliers"].values():
            a["dates"].sort(reverse=True)

    participants_list = sorted(per_participant.values(), key=lambda x: (x["nom"] or "", x["prenom"] or ""))
    return {"participants": participants_list, "total": len(participants_list)}



# ---------------------------
# Le Magatomatique (présences -> stats "Excel-like" mais intelligentes)
# ---------------------------

def compute_magatomatique(
    flt: StatsFilters,
    *,
    participant_q: str | None = None,
    view: str | None = None,
    max_sessions: int = 40,
    max_participants: int = 250,
) -> dict:
    """
    Fournit une vue hautement filtrable et "zoomable" des émargements.
    - view="macro": synthèse secteurs + ateliers
    - view="participants": liste participants + métriques (sans matrice)
    - view="matrix": matrice participants x sessions (limitée par max_*)

    Note: respecte le cloisonnement secteur via _resolve_secteur_scope.
    """
    eff_secteur = _resolve_secteur_scope(flt)
    if eff_secteur == "__restricted__":
        return {"restricted": True, "view": view or "macro"}

    v = (view or "macro").lower().strip()
    if v not in ("macro", "participants", "matrix"):
        v = "macro"

    # Base: sessions filtrées
    base_q = (
        db.session.query(SessionActivite, AtelierActivite)
        .join(AtelierActivite, SessionActivite.atelier_id == AtelierActivite.id)
    )
    base_q = _apply_common_filters(base_q, flt)

    # ===== Macro =====
    # Agrégats par secteur / atelier
    sector_rows = (
        db.session.query(
            AtelierActivite.secteur.label("secteur"),
            func.count(func.distinct(SessionActivite.id)).label("nb_sessions"),
            func.count(PresenceActivite.id).label("nb_presences"),
            func.count(func.distinct(PresenceActivite.participant_id)).label("nb_participants_uniques"),
        )
        .select_from(SessionActivite)
        .join(AtelierActivite, SessionActivite.atelier_id == AtelierActivite.id)
        .outerjoin(PresenceActivite, PresenceActivite.session_id == SessionActivite.id)
    )
    sector_rows = _apply_common_filters(sector_rows, flt)
    sector_rows = (
        sector_rows.group_by(AtelierActivite.secteur)
        .order_by(AtelierActivite.secteur.asc())
        .all()
    )

    atelier_rows = (
        db.session.query(
            AtelierActivite.id.label("atelier_id"),
            AtelierActivite.nom.label("atelier_nom"),
            AtelierActivite.secteur.label("secteur"),
            func.count(func.distinct(SessionActivite.id)).label("nb_sessions"),
            func.count(PresenceActivite.id).label("nb_presences"),
            func.count(func.distinct(PresenceActivite.participant_id)).label("nb_participants_uniques"),
        )
        .select_from(SessionActivite)
        .join(AtelierActivite, SessionActivite.atelier_id == AtelierActivite.id)
        .outerjoin(PresenceActivite, PresenceActivite.session_id == SessionActivite.id)
    )
    atelier_rows = _apply_common_filters(atelier_rows, flt)
    atelier_rows = (
        atelier_rows.group_by(AtelierActivite.id, AtelierActivite.nom, AtelierActivite.secteur)
        .order_by(AtelierActivite.secteur.asc(), AtelierActivite.nom.asc())
        .all()
    )

    macro = {
        "kpis": {},
        "by_secteur": [
            {
                "secteur": r.secteur,
                "nb_sessions": int(r.nb_sessions or 0),
                "nb_presences": int(r.nb_presences or 0),
                "nb_participants_uniques": int(r.nb_participants_uniques or 0),
            }
            for r in sector_rows
        ],
        "by_atelier": [
            {
                "atelier_id": int(r.atelier_id),
                "atelier_nom": r.atelier_nom,
                "secteur": r.secteur,
                "nb_sessions": int(r.nb_sessions or 0),
                "nb_presences": int(r.nb_presences or 0),
                "nb_participants_uniques": int(r.nb_participants_uniques or 0),
            }
            for r in atelier_rows
        ],
    }

    # KPIs globaux (périmètre filtré)
    total_sessions = sum(int(r["nb_sessions"]) for r in macro["by_atelier"])
    total_presences = sum(int(r["nb_presences"]) for r in macro["by_atelier"])
    # participants uniques globaux (ne pas sommer par atelier, sinon doublons)
    uniq_q = (
        db.session.query(func.count(func.distinct(PresenceActivite.participant_id)))
        .select_from(PresenceActivite)
        .join(SessionActivite, PresenceActivite.session_id == SessionActivite.id)
        .join(AtelierActivite, SessionActivite.atelier_id == AtelierActivite.id)
    )
    uniq_q = _apply_common_filters(uniq_q, flt)
    total_participants_uniques = int(uniq_q.scalar() or 0)

    macro["kpis"] = {
        "total_sessions": int(total_sessions),
        "total_presences": int(total_presences),
        "total_participants_uniques": int(total_participants_uniques),
        "avg_presences_per_session": (float(total_presences) / float(total_sessions)) if total_sessions else 0.0,
    }

    # Si macro seulement, on peut s'arrêter là
    if v == "macro":
        return {"restricted": False, "view": v, "macro": macro}

    # ===== Participants + (option) matrice =====
    # Sessions (pour la matrice) : tri chronologique
    # On récupère un peu plus pour ne pas exploser le navigateur.
    sess_q = base_q
    # Note: date (rdv_date ou date_session) - on utilise l'expression déjà utilisée ailleurs
    sess_q = sess_q.order_by(_session_date_expr().asc(), SessionActivite.id.asc())
    if max_sessions and max_sessions > 0:
        sess_q = sess_q.limit(max_sessions)

    sessions = []
    for s, a in sess_q.all():
        d = s.rdv_date or s.date_session
        sessions.append(
            {
                "id": s.id,
                "atelier_id": a.id,
                "atelier": a.nom,
                "secteur": a.secteur,
                "date": d,
                "label": (d.strftime("%d/%m/%Y") if d else "Sans date"),
            }
        )

    session_ids = [s["id"] for s in sessions]

    # Participants filtrés : uniquement ceux qui ont au moins une présence dans le périmètre
    part_q = (
        db.session.query(Participant)
        .join(PresenceActivite, PresenceActivite.participant_id == Participant.id)
        .join(SessionActivite, PresenceActivite.session_id == SessionActivite.id)
        .join(AtelierActivite, SessionActivite.atelier_id == AtelierActivite.id)
    )
    part_q = _apply_common_filters(part_q, flt)

    if participant_q:
        pq = participant_q.strip()
        if pq:
            like = f"%{pq.lower()}%"
            part_q = part_q.filter(
                func.lower(func.coalesce(Participant.nom, "")).like(like)
                | func.lower(func.coalesce(Participant.prenom, "")).like(like)
            )

    part_q = part_q.distinct().order_by(Participant.nom.asc(), Participant.prenom.asc())
    if max_participants and max_participants > 0:
        part_q = part_q.limit(max_participants)

    participants = [
        {"id": p.id, "nom": p.nom or "", "prenom": p.prenom or "", "ville": p.ville, "quartier": p.quartier.nom if p.quartier else None}
        for p in part_q.all()
    ]
    participant_ids = [p["id"] for p in participants]

    # Comptage des visites par participant (dans le périmètre complet, pas seulement sessions limitées)
    counts_q = (
        db.session.query(
            PresenceActivite.participant_id.label("pid"),
            func.count(PresenceActivite.id).label("nb_presences"),
            func.min(_session_date_expr()).label("first_date"),
            func.max(_session_date_expr()).label("last_date"),
        )
        .select_from(PresenceActivite)
        .join(SessionActivite, PresenceActivite.session_id == SessionActivite.id)
        .join(AtelierActivite, SessionActivite.atelier_id == AtelierActivite.id)
    )
    counts_q = _apply_common_filters(counts_q, flt)
    if participant_ids:
        counts_q = counts_q.filter(PresenceActivite.participant_id.in_(participant_ids))
    counts_q = counts_q.group_by(PresenceActivite.participant_id).all()

    counts_map = {
        int(r.pid): {"nb_presences": int(r.nb_presences or 0), "first_date": r.first_date, "last_date": r.last_date}
        for r in counts_q
    }
    for p in participants:
        c = counts_map.get(p["id"], {})
        p["nb_presences"] = int(c.get("nb_presences", 0))
        fd = c.get("first_date")
        ld = c.get("last_date")
        p["first_date"] = fd
        p["last_date"] = ld

    # KPIs "financeur-friendly" basés sur la liste participants (donc respectant les limites max_participants)
    # NB: pour un export annuel exhaustif par atelier, on calcule des KPI dédiés côté export.
    if participants:
        nb_part = len(participants)
        sum_pres = sum(int(p.get("nb_presences", 0)) for p in participants)
        returning = sum(1 for p in participants if int(p.get("nb_presences", 0)) >= 2)
        heavy_3 = sum(1 for p in participants if int(p.get("nb_presences", 0)) >= 3)
        # nouveaux = première venue dans la période filtrée
        new_count = 0
        if flt.date_from and flt.date_to:
            for p in participants:
                fd = p.get("first_date")
                if fd and flt.date_from <= fd <= flt.date_to:
                    new_count += 1

        macro["kpis"].update(
            {
                "avg_sessions_per_participant": (float(sum_pres) / float(nb_part)) if nb_part else 0.0,
                "returning_participants": int(returning),
                "returning_rate": (float(returning) / float(nb_part)) if nb_part else 0.0,
                "fidelite_3plus": int(heavy_3),
                "fidelite_3plus_rate": (float(heavy_3) / float(nb_part)) if nb_part else 0.0,
                "new_participants": int(new_count),
            }
        )

    if v == "participants":
        return {
            "restricted": False,
            "view": v,
            "macro": macro,
            "participants": participants,
            "sessions": sessions,
            "limits": {"max_sessions": max_sessions, "max_participants": max_participants},
        }

    # ===== Matrice =====
    matrix = {}
    if session_ids and participant_ids:
        pres_q = (
            db.session.query(PresenceActivite.participant_id, PresenceActivite.session_id)
            .filter(PresenceActivite.session_id.in_(session_ids))
            .filter(PresenceActivite.participant_id.in_(participant_ids))
        )
        for pid, sid in pres_q.all():
            matrix[(int(pid), int(sid))] = 1

    return {
        "restricted": False,
        "view": "matrix",
        "macro": macro,
        "participants": participants,
        "sessions": sessions,
        "matrix": matrix,
        "limits": {"max_sessions": max_sessions, "max_participants": max_participants},
    }
