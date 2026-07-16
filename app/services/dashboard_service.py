from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, List, Tuple

from flask import url_for
from werkzeug.routing import BuildError
from sqlalchemy import func, or_

from app.models import (
    Subvention,
    Depense,
    LigneBudget,
    Projet,
    ProjetAction,
    ProjetIndicateur,
    SessionActivite,
    PresenceActivite,
    Participant,
    Quartier,
    OrientationAccesDroit,
    SuiviRappel,
)
from app.services.dashboard_customization import resolved_quick_actions


AGE_BUCKETS: List[Tuple[str, int | None, int | None]] = [
    ("0-5 ans", 0, 5),
    ("6-11 ans", 6, 11),
    ("12-17 ans", 12, 17),
    ("18-25 ans", 18, 25),
    ("26-39 ans", 26, 39),
    ("40-59 ans", 40, 59),
    ("60 ans et +", 60, None),
]


def _month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _last_n_months(n: int, today: date | None = None) -> List[Tuple[int, int]]:
    today = today or date.today()
    y, m = today.year, today.month
    out: List[Tuple[int, int]] = []
    for _ in range(n):
        out.append((y, m))
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    out.reverse()
    return out


def _session_effective_date_expr():
    return func.coalesce(SessionActivite.rdv_date, SessionActivite.date_session)


def _session_effective_date(session: SessionActivite):
    return session.rdv_date or session.date_session


def _normalize_gender(value: str | None) -> str:
    raw = (value or "").strip().lower()
    if not raw:
        return "Non renseigné"
    raw = raw.replace("é", "e").replace("è", "e").replace("ê", "e").replace("à", "a")
    if raw in {"f", "femme", "feminin", "feminin", "female", "woman"}:
        return "Femmes"
    if raw in {"h", "m", "homme", "masculin", "male", "man"}:
        return "Hommes"
    if raw in {"autre", "non binaire", "non-binaire", "nb", "x"}:
        return "Autre"
    return "Non renseigné"


def _compute_age(dob: date | None, ref_date: date) -> int | None:
    if not dob:
        return None
    years = ref_date.year - dob.year
    if (ref_date.month, ref_date.day) < (dob.month, dob.day):
        years -= 1
    if years < 0 or years > 120:
        return None
    return years


def _age_bucket(age: int | None) -> str | None:
    if age is None:
        return None
    for label, low, high in AGE_BUCKETS:
        if low is not None and age < low:
            continue
        if high is not None and age > high:
            continue
        return label
    return None


def _clean_city(q_ville: str | None, participant_ville: str | None) -> str:
    city = (q_ville or participant_ville or "").strip()
    return city if city else "Ville non renseignée"


def _clean_quartier(value: str | None) -> str:
    quartier = (value or "").strip()
    return quartier if quartier else "Quartier non renseigné"



def _month_bounds_from_key(month_key: str) -> tuple[date, date] | tuple[None, None]:
    try:
        year_str, month_str = (month_key or "").split("-", 1)
        year = int(year_str)
        month = int(month_str)
        start = date(year, month, 1)
        if month == 12:
            end = date(year + 1, 1, 1) - timedelta(days=1)
        else:
            end = date(year, month + 1, 1) - timedelta(days=1)
        return start, end
    except Exception:
        return None, None


def _resolve_period(period_key: str | None, *, days: int, budget_year: int, today: date) -> tuple[str, date, date, str, int]:
    key = (period_key or "").strip().lower()
    if key == "year":
        since = date(budget_year, 1, 1)
        until = date(budget_year, 12, 31)
        if budget_year == today.year:
            until = today
        effective_days = max(1, (until - since).days + 1)
        return "year", since, until, f"année {budget_year}", effective_days

    if key not in {"30", "90", "365"}:
        key = str(int(days or 90))
    effective_days = int(key)
    since = today - timedelta(days=max(effective_days - 1, 0))
    return key, since, today, f"{effective_days} jours", effective_days


def build_dashboard_context(
    user,
    *,
    days: int = 90,
    budget_year: int | None = None,
    period_key: str | None = None,
) -> Dict[str, Any]:
    """Construit un contexte riche pour le dashboard."""

    def _safe(endpoint: str, fallback: str = "#", **values) -> str:
        try:
            return url_for(endpoint, **values)
        except BuildError:
            return fallback

    has_perm = getattr(user, "has_perm", None)
    has_scope_all = callable(has_perm) and has_perm("scope:all_secteurs")
    has_business_access = callable(has_perm) and any(
        has_perm(p) for p in ("subventions:view", "projets:view", "stats:view", "statsimpact:view")
    )

    def _has(code: str) -> bool:
        return callable(has_perm) and has_perm(code)

    def _has_any(*codes: str) -> bool:
        return callable(has_perm) and any(has_perm(code) for code in codes)

    budget_year = int(budget_year or date.today().year)
    today = date.today()
    current_period_key, since_date, until_date, period_label, effective_days = _resolve_period(
        period_key,
        days=days,
        budget_year=budget_year,
        today=today,
    )

    if callable(has_perm) and has_perm("admin:users") and not has_business_access:
        return {
            "mode": "admin_tech",
            "kpis": {},
            "alerts": [],
            "shortcuts": [
                {"label": "Gérer l’équipe", "url": _safe("admin.users"), "icon": "🛠️"},
            ],
            "recents": {"depenses": [], "sessions": [], "participants": []},
            "charts": {},
            "days": effective_days,
            "budget_year": budget_year,
            "period_key": current_period_key,
            "period_label": period_label,
        }

    subs_q = Subvention.query.filter_by(est_archive=False).filter(Subvention.annee_exercice == budget_year)
    if not has_scope_all:
        subs_q = subs_q.filter(Subvention.secteur == user.secteur_assigne)
    subs = subs_q.all()

    total_attribue = sum(float(s.montant_attribue or 0) for s in subs)
    total_recu = sum(float(s.montant_recu or 0) for s in subs)
    total_engage = sum(float(s.total_engage or 0) for s in subs)
    total_reste = sum(float(s.total_reste or 0) for s in subs)
    taux = 0.0
    if total_attribue > 0:
        taux = round((total_engage / total_attribue) * 100, 1)

    alerts: List[Dict[str, Any]] = []
    for s in subs:
        recu = float(s.montant_recu or 0)
        reel_lignes = float(s.total_reel_lignes or 0)
        engage = float(s.total_engage or 0)
        reste = float(s.total_reste or 0)

        if recu > 0 and reel_lignes == 0:
            alerts.append({
                "level": "danger",
                "text": f"{s.nom} : reçu {recu:.2f}€ mais lignes réel = 0€ (ventilation manquante).",
                "url": _safe("main.subvention_pilotage", subvention_id=s.id),
            })
        if reel_lignes > 0 and engage > reel_lignes:
            alerts.append({
                "level": "danger",
                "text": f"{s.nom} : engagé {engage:.2f}€ > lignes réel {reel_lignes:.2f}€ (dépassement).",
                "url": _safe("main.subvention_pilotage", subvention_id=s.id),
            })
        if float(s.montant_attribue or 0) > 0:
            pct = (engage / float(s.montant_attribue or 0)) * 100
            if pct >= 80:
                alerts.append({
                    "level": "warning",
                    "text": f"{s.nom} : {pct:.0f}% consommé (reste {reste:.2f}€).",
                    "url": _safe("main.subvention_pilotage", subvention_id=s.id),
                })

    session_date_expr = _session_effective_date_expr()
    sessions_q = SessionActivite.query.filter_by(is_deleted=False)
    pres_q = PresenceActivite.query.join(SessionActivite)
    if not has_scope_all:
        sessions_q = sessions_q.filter(SessionActivite.secteur == user.secteur_assigne)
        pres_q = pres_q.filter(SessionActivite.secteur == user.secteur_assigne)

    sessions_recent = (
        sessions_q
        .filter(session_date_expr.isnot(None))
        .filter(session_date_expr >= since_date)
        .filter(session_date_expr <= until_date)
        .count()
    )
    uniques_recent = (
        pres_q.join(Participant)
        .filter(session_date_expr.isnot(None))
        .filter(session_date_expr >= since_date)
        .filter(session_date_expr <= until_date)
        .with_entities(Participant.id)
        .distinct()
        .count()
    )

    months = _last_n_months(6)
    month_labels = [f"{y}-{m:02d}" for (y, m) in months]

    dep_q = Depense.query.filter_by(est_supprimee=False)
    if not has_scope_all:
        dep_q = (
            dep_q.join(LigneBudget)
            .join(Subvention, LigneBudget.subvention_id == Subvention.id)
            .filter(Subvention.secteur == user.secteur_assigne)
        )
    dep_rows = dep_q.with_entities(Depense.montant, Depense.date_paiement, Depense.created_at).all()

    dep_by_month = {k: 0.0 for k in month_labels}
    for montant, date_paiement, created_at in dep_rows:
        d = date_paiement or (created_at.date() if created_at else None)
        if not d:
            continue
        mk = _month_key(d)
        if mk in dep_by_month:
            dep_by_month[mk] += float(montant or 0)

    sess_rows = sessions_q.with_entities(session_date_expr).all()
    sess_by_month = {k: 0 for k in month_labels}
    for (session_date,) in sess_rows:
        if not session_date:
            continue
        mk = _month_key(session_date)
        if mk in sess_by_month:
            sess_by_month[mk] += 1

    pub_counts = {"H": 0, "S": 0, "B": 0, "A": 0, "P": 0, "?": 0}
    gender_counts = {"Femmes": 0, "Hommes": 0, "Autre": 0, "Non renseigné": 0}
    age_counts = {label: 0 for (label, _, _) in AGE_BUCKETS}
    unknown_age_count = 0
    city_counts: Dict[str, int] = {}
    quartier_counts: Dict[tuple[str, str], int] = {}

    participant_rows = (
        pres_q.join(Participant)
        .outerjoin(Quartier, Participant.quartier_id == Quartier.id)
        .filter(session_date_expr.isnot(None))
        .filter(session_date_expr >= since_date)
        .filter(session_date_expr <= until_date)
        .with_entities(
            Participant.id,
            Participant.type_public,
            Participant.genre,
            Participant.date_naissance,
            Participant.ville,
            Quartier.ville,
            Quartier.nom,
        )
        .distinct()
        .all()
    )

    for _pid, tp, genre, dob, participant_ville, quartier_ville, quartier_nom in participant_rows:
        public_key = (tp or "?").strip().upper()
        if public_key not in pub_counts:
            public_key = "?"
        pub_counts[public_key] += 1

        gender_key = _normalize_gender(genre)
        gender_counts[gender_key] = gender_counts.get(gender_key, 0) + 1

        age = _compute_age(dob, until_date)
        age_label = _age_bucket(age)
        if age_label:
            age_counts[age_label] += 1
        else:
            unknown_age_count += 1

        city_label = _clean_city(quartier_ville, participant_ville)
        quartier_label = _clean_quartier(quartier_nom)
        city_counts[city_label] = city_counts.get(city_label, 0) + 1
        quartier_counts[(city_label, quartier_label)] = quartier_counts.get((city_label, quartier_label), 0) + 1


    activity_from_iso = since_date.isoformat()
    activity_to_iso = until_date.isoformat()

    participant_base_args = {
        "dashboard_from": activity_from_iso,
        "dashboard_to": activity_to_iso,
        "dashboard_active": "1",
        "presence": "with",
        "scope": "secteur",
    }

    public_code_map = {
        "Habitants": "H",
        "Salariés": "S",
        "Bénévoles": "B",
        "Administrateurs": "A",
        "Partenaires": "P",
        "Autres": "?",
    }

    gender_urls = [
        _safe(
            "participants.list_participants",
            **participant_base_args,
            genre_group=("other" if label == "Autre" else "unknown" if label == "Non renseigné" else label.lower()),
        )
        for label in ["Femmes", "Hommes", "Autre", "Non renseigné"]
    ]

    age_labels = [label for (label, _, _) in AGE_BUCKETS] + ["Âge non renseigné"]
    age_values = [age_counts[label] for (label, _, _) in AGE_BUCKETS] + [unknown_age_count]
    age_urls = [
        _safe("participants.list_participants", **participant_base_args, age_bucket=label)
        for label in ([label for (label, _, _) in AGE_BUCKETS] + ["Âge non renseigné"])
    ]

    public_urls = [
        _safe("participants.list_participants", **participant_base_args, type_public=public_code_map[label])
        for label in ["Habitants", "Salariés", "Bénévoles", "Administrateurs", "Partenaires", "Autres"]
    ]

    dep_month_urls = []
    sess_month_urls = []
    for mk in month_labels:
        month_start, month_end = _month_bounds_from_key(mk)
        if month_start and month_end:
            dep_month_urls.append(
                _safe(
                    "budget.depenses_list",
                    date_from=month_start.isoformat(),
                    date_to=month_end.isoformat(),
                    budget_year=budget_year,
                    source="dashboard",
                )
            )
            sess_month_urls.append(
                _safe(
                    "statsimpact.dashboard",
                    tab="magato",
                    date_from=month_start.isoformat(),
                    date_to=month_end.isoformat(),
                    group_by="DAY",
                    source="dashboard",
                )
            )
        else:
            dep_month_urls.append(_safe("budget.depenses_list"))
            sess_month_urls.append(_safe("statsimpact.dashboard", tab="magato"))

    inner_city_order = [
        city for city, _count in sorted(city_counts.items(), key=lambda item: (-item[1], item[0].lower()))
    ]
    location_inner_urls = [
        _safe("participants.list_participants", **participant_base_args, city_label=city)
        for city in inner_city_order
    ]
    city_index = {city: idx for idx, city in enumerate(inner_city_order)}
    outer_segments = sorted(
        quartier_counts.items(),
        key=lambda item: (city_index.get(item[0][0], 9999), -item[1], item[0][1].lower()),
    )

    charts = {
        "budget": {
            "labels": ["Engagé", "Disponible"],
            "values": [round(total_engage, 2), round(max(total_attribue - total_engage, 0.0), 2)],
            "urls": [
                _safe("main.stats", annee=budget_year, source="dashboard", focus="engage"),
                _safe("main.stats", annee=budget_year, source="dashboard", focus="disponible"),
            ],
        },
        "depenses": {
            "labels": month_labels,
            "values": [round(dep_by_month[k], 2) for k in month_labels],
            "urls": dep_month_urls,
        },
        "sessions": {
            "labels": month_labels,
            "values": [sess_by_month[k] for k in month_labels],
            "urls": sess_month_urls,
        },
        "public": {
            "labels": ["Habitants", "Salariés", "Bénévoles", "Administrateurs", "Partenaires", "Autres"],
            "values": [pub_counts["H"], pub_counts["S"], pub_counts["B"], pub_counts["A"], pub_counts["P"], pub_counts["?"]],
            "urls": public_urls,
        },
        "gender": {
            "labels": ["Femmes", "Hommes", "Autre", "Non renseigné"],
            "values": [
                gender_counts["Femmes"],
                gender_counts["Hommes"],
                gender_counts["Autre"],
                gender_counts["Non renseigné"],
            ],
            "money": False,
            "urls": gender_urls,
        },
        "ages": {
            "labels": age_labels,
            "values": age_values,
            "unknown": unknown_age_count,
            "money": False,
            "urls": age_urls,
        },
        "locations": {
            "inner_labels": inner_city_order,
            "inner_values": [city_counts[city] for city in inner_city_order],
            "outer_labels": [quartier for (_city, quartier), _count in outer_segments],
            "outer_values": [count for (_key, count) in outer_segments],
            "outer_parents": [city_index.get(city, 0) for (city, _quartier), _count in outer_segments],
            "outer_city_labels": [city for (city, _quartier), _count in outer_segments],
            "money": False,
            "inner_urls": location_inner_urls,
            "outer_urls": [
                _safe(
                    "participants.list_participants",
                    **participant_base_args,
                    city_label=city,
                    quartier_label=quartier,
                )
                for (city, quartier), _count in outer_segments
            ],
        },
        "budget_donut": {
            "labels": ["Engagé", "Disponible"],
            "values": [round(total_engage, 2), round(max(total_attribue - total_engage, 0.0), 2)],
            "urls": [
                _safe("main.stats", annee=budget_year, source="dashboard", focus="engage"),
                _safe("main.stats", annee=budget_year, source="dashboard", focus="disponible"),
            ],
        },
        "depenses_bar": {
            "labels": month_labels,
            "values": [round(dep_by_month[k], 2) for k in month_labels],
            "urls": dep_month_urls,
        },
        "sessions_line": {
            "labels": month_labels,
            "values": [sess_by_month[k] for k in month_labels],
            "urls": sess_month_urls,
        },
        "public_pie": {
            "labels": ["Habitants", "Salariés", "Bénévoles", "Administrateurs", "Partenaires", "Autres"],
            "values": [pub_counts["H"], pub_counts["S"], pub_counts["B"], pub_counts["A"], pub_counts["P"], pub_counts["?"]],
            "urls": public_urls,
        },
    }

    recent_depenses = dep_q.order_by(Depense.created_at.desc()).limit(6).all()
    recent_sessions = (
        sessions_q
        .order_by(session_date_expr.desc(), SessionActivite.id.desc())
        .limit(6)
        .all()
    )
    recent_participants = []
    if _has_any("participants:view", "participants:view_all"):
        recent_participants_q = Participant.query
        if not _has("participants:view_all"):
            recent_participants_q = recent_participants_q.filter(
                Participant.created_secteur == getattr(user, "secteur_assigne", None)
            )
        recent_participants = recent_participants_q.order_by(Participant.created_at.desc()).limit(6).all()

    def _project_scope_query():
        q = Projet.query
        if not has_scope_all:
            q = q.filter(Projet.secteur == getattr(user, "secteur_assigne", None))
        return q

    def _action_scope_query():
        q = ProjetAction.query.join(Projet)
        if not has_scope_all:
            q = q.filter(Projet.secteur == getattr(user, "secteur_assigne", None))
        return q

    def _orientation_scope_query():
        q = OrientationAccesDroit.query
        if not has_scope_all:
            q = q.filter(or_(
                OrientationAccesDroit.secteur.is_(None),
                OrientationAccesDroit.secteur == getattr(user, "secteur_assigne", None),
            ))
        return q

    def _rappel_scope_query():
        q = SuiviRappel.query.filter(SuiviRappel.statut == "ouvert")
        user_id = getattr(user, "id", None)
        if has_scope_all:
            return q.filter(or_(
                SuiviRappel.is_private.is_(False),
                SuiviRappel.created_by_user_id == user_id,
            ))
        user_secteur = getattr(user, "secteur_assigne", None)
        return q.filter(or_(
            SuiviRappel.created_by_user_id == user_id,
            SuiviRappel.is_private.is_(False) & (SuiviRappel.secteur == user_secteur),
            SuiviRappel.is_private.is_(False) & SuiviRappel.secteur.is_(None),
        ))

    soon_date = today + timedelta(days=7)
    action_soon_date = today + timedelta(days=14)

    project_count = actions_total = actions_open = actions_due = 0
    active_indicator_count = projects_without_indicators = projects_without_ateliers = 0
    if _has("projets:view"):
        project_rows = _project_scope_query().order_by(Projet.nom.asc()).all()
        project_count = len(project_rows)
        active_indicator_count = (
            ProjetIndicateur.query
            .join(Projet)
            .filter(ProjetIndicateur.is_active.is_(True))
        )
        if not has_scope_all:
            active_indicator_count = active_indicator_count.filter(Projet.secteur == getattr(user, "secteur_assigne", None))
        active_indicator_count = active_indicator_count.count()
        projects_without_indicators = sum(
            1 for projet in project_rows
            if not any(getattr(ind, "is_active", False) for ind in getattr(projet, "indicateurs", []) or [])
        )
        projects_without_ateliers = sum(
            1 for projet in project_rows
            if not (getattr(projet, "ateliers", []) or [])
        )
        action_q = _action_scope_query()
        actions_total = action_q.count()
        actions_open_q = action_q.filter(~ProjetAction.statut.in_(["realisee", "annulee"]))
        actions_open = actions_open_q.count()
        actions_due = actions_open_q.filter(
            ProjetAction.date_fin.isnot(None),
            ProjetAction.date_fin <= action_soon_date,
        ).count()

    orientation_year_count = orientation_open = orientation_watch = orientation_without_partner = 0
    orientation_top_domain = None
    if _has("partenaires:view"):
        year_start = date(budget_year, 1, 1)
        year_end = date(budget_year, 12, 31)
        orientation_base_q = _orientation_scope_query()
        orientation_year_q = orientation_base_q.filter(
            OrientationAccesDroit.date_orientation >= year_start,
            OrientationAccesDroit.date_orientation <= year_end,
        )
        orientation_year_count = orientation_year_q.count()
        orientation_open_q = orientation_base_q.filter(~OrientationAccesDroit.statut.in_(["resolu", "non_abouti"]))
        orientation_open = orientation_open_q.count()
        orientation_watch = orientation_open_q.filter(or_(
            OrientationAccesDroit.suite_prevue <= soon_date,
            OrientationAccesDroit.statut == "a_rappeler",
            OrientationAccesDroit.urgence.in_(["prioritaire", "urgence"]),
        )).count()
        orientation_without_partner = orientation_open_q.filter(OrientationAccesDroit.partenaire_id.is_(None)).count()
        orientation_top_domain = (
            orientation_year_q
            .with_entities(OrientationAccesDroit.domaine, func.count(OrientationAccesDroit.id).label("total"))
            .group_by(OrientationAccesDroit.domaine)
            .order_by(func.count(OrientationAccesDroit.id).desc())
            .first()
        )

    rappel_total = rappel_due = rappel_overdue = 0
    if _has("dashboard:view"):
        rappel_q = _rappel_scope_query()
        rappel_total = rappel_q.count()
        rappel_overdue = rappel_q.filter(SuiviRappel.echeance.isnot(None), SuiviRappel.echeance < today).count()
        rappel_due = rappel_q.filter(or_(
            SuiviRappel.priorite == "danger",
            SuiviRappel.echeance <= soon_date,
        )).count()

    participant_no_contact = participant_weak_demo = 0
    if _has_any("participants:view", "participants:view_all"):
        participant_q = Participant.query
        if not _has("participants:view_all"):
            participant_q = participant_q.filter(Participant.created_secteur == getattr(user, "secteur_assigne", None))
        participant_no_contact = participant_q.filter(
            or_(Participant.telephone.is_(None), Participant.telephone == ""),
            or_(Participant.email.is_(None), Participant.email == ""),
        ).count()
        participant_weak_demo = participant_q.filter(or_(
            Participant.date_naissance.is_(None),
            Participant.genre.is_(None),
            Participant.genre == "",
            Participant.ville.is_(None),
            Participant.ville == "",
            Participant.created_secteur.is_(None),
            Participant.created_secteur == "",
        )).count()

    sessions_without_presence = 0
    if _has("emargement:view"):
        sessions_without_presence = (
            sessions_q
            .filter(SessionActivite.statut == "realisee")
            .filter(session_date_expr.isnot(None))
            .filter(session_date_expr <= today)
            .outerjoin(PresenceActivite, PresenceActivite.session_id == SessionActivite.id)
            .group_by(SessionActivite.id)
            .having(func.count(PresenceActivite.id) == 0)
            .count()
        )

    quality_bad = sessions_without_presence
    quality_warn = (
        participant_no_contact
        + orientation_without_partner
        + projects_without_indicators
        + projects_without_ateliers
    )
    quality_info = participant_weak_demo
    quality_total = quality_bad + quality_warn + quality_info
    quality_score = max(0, 100 - min(95, quality_bad * 9 + quality_warn * 4 + quality_info * 2))

    financial_danger = sum(1 for item in alerts if item.get("level") == "danger")
    financial_warn = sum(1 for item in alerts if item.get("level") == "warning")
    health_penalty = min(
        95,
        financial_danger * 12
        + financial_warn * 6
        + rappel_overdue * 8
        + max(rappel_due - rappel_overdue, 0) * 4
        + orientation_watch * 4
        + actions_due * 3
        + quality_bad * 7
        + quality_warn * 2
    )
    health_score = max(0, 100 - health_penalty)
    if health_score < 60:
        health_tone = "danger"
        health_label = "Prioritaire"
    elif health_score < 80:
        health_tone = "warn"
        health_label = "À surveiller"
    else:
        health_tone = "ok"
        health_label = "Stable"

    documents_ready = (project_count if _has("projets:view") else 0) + (actions_total if _has("projets:view") else 0)
    pilotage_modules = []
    if _has_any("statsimpact:view", "stats:view", "emargement:view"):
        pilotage_modules.append({
            "key": "activity",
            "title": "Fréquentation & résultats",
            "value": sessions_recent,
            "unit": "sessions",
            "detail": f"{uniques_recent} participant(s) unique(s) sur {period_label}",
            "tone": "warn" if sessions_recent == 0 else "ok",
            "url": _safe("statsimpact.dashboard", date_from=activity_from_iso, date_to=activity_to_iso, source="dashboard"),
            "action": "Voir les présences",
            "note": "Ces chiffres viennent des présences d'émargement.",
        })
    if _has("partenaires:view"):
        pilotage_modules.append({
            "key": "orientations",
            "title": "Accès aux droits",
            "value": orientation_year_count,
            "unit": "orientations",
            "detail": f"{orientation_watch} suivi(s) à reprendre, {orientation_open} ouvert(s)",
            "tone": "warn" if orientation_watch else "ok",
            "url": _safe("partenaires.orientations", year=budget_year),
            "action": "Ouvrir les orientations",
            "note": f"Domaine principal : {orientation_top_domain[0]}" if orientation_top_domain else "Aucune orientation sur l'exercice.",
        })
    if _has("dashboard:view"):
        pilotage_modules.append({
            "key": "followups",
            "title": "Suivi & rappels",
            "value": rappel_due,
            "unit": "à traiter",
            "detail": f"{rappel_total} rappel(s) ouvert(s), {rappel_overdue} en retard",
            "tone": "danger" if rappel_overdue else ("warn" if rappel_due else "ok"),
            "url": _safe("main.suivi_rappels"),
            "action": "Ouvrir le centre de suivi",
            "note": "Inclut les rappels manuels et les suivis d'orientation.",
        })
    if _has("projets:view"):
        pilotage_modules.append({
            "key": "projects",
            "title": "Projets & indicateurs",
            "value": project_count,
            "unit": "projets",
            "detail": f"{actions_open} action(s) en cours, {active_indicator_count} indicateur(s) actif(s)",
            "tone": "warn" if projects_without_indicators or actions_due else "ok",
            "url": _safe("projets.projets_list"),
            "action": "Ouvrir les projets",
            "note": f"{projects_without_indicators} projet(s) sans indicateur actif.",
        })
    if _has("dashboard:view"):
        pilotage_modules.append({
            "key": "documents",
            "title": "Documents prêts",
            "value": documents_ready,
            "unit": "fiches",
            "detail": f"{project_count} fiche(s) projet, {actions_total} fiche(s) action",
            "tone": "ok" if documents_ready else "info",
            "url": _safe("main.documents_exports", year=budget_year),
            "action": "Ouvrir les documents",
            "note": "Point d'entrée pour fiches, bilans et exports.",
        })
    if _has("dashboard:view"):
        pilotage_modules.append({
            "key": "quality",
            "title": "Qualité des données",
            "value": quality_score,
            "unit": "/100",
            "detail": f"{quality_total} point(s) à vérifier",
            "tone": "danger" if quality_score < 60 else ("warn" if quality_score < 80 else "ok"),
            "url": _safe("main.qualite_donnees_transverse"),
            "action": "Voir la qualité",
            "note": "Score rapide, détail dans la page qualité.",
        })

    focus_items = []
    if financial_danger or financial_warn:
        focus_items.append({
            "tone": "danger" if financial_danger else "warn",
            "title": "Finance à vérifier",
            "text": f"{financial_danger + financial_warn} alerte(s) budgétaire(s) visible(s).",
            "url": _safe("main.stats", annee=budget_year, source="dashboard"),
        })
    if rappel_due:
        focus_items.append({
            "tone": "danger" if rappel_overdue else "warn",
            "title": "Rappels à traiter",
            "text": f"{rappel_due} rappel(s) prioritaire(s), dont {rappel_overdue} en retard.",
            "url": _safe("main.suivi_rappels"),
        })
    if orientation_watch:
        focus_items.append({
            "tone": "warn",
            "title": "Orientations à reprendre",
            "text": f"{orientation_watch} orientation(s) demandent un suivi.",
            "url": _safe("partenaires.orientations", year=budget_year, statut="a_rappeler"),
        })
    if sessions_without_presence:
        focus_items.append({
            "tone": "danger",
            "title": "Présences manquantes",
            "text": f"{sessions_without_presence} séance(s) réalisée(s) sans présence.",
            "url": _safe("main.qualite_donnees_transverse", famille="activites", niveau="bad"),
        })
    if projects_without_indicators:
        focus_items.append({
            "tone": "warn",
            "title": "Indicateurs projet",
            "text": f"{projects_without_indicators} projet(s) sans indicateur actif.",
            "url": _safe("main.qualite_donnees_transverse", famille="projets"),
        })
    if not focus_items:
        focus_items.append({
            "tone": "ok",
            "title": "Rien d'urgent",
            "text": "Les principaux signaux sont au vert sur ce périmètre.",
            "url": _safe("main.documents_exports", year=budget_year),
        })

    pilotage = {
        "scope_label": "Tous secteurs" if has_scope_all else (getattr(user, "secteur_assigne", None) or "Mon secteur"),
        "health_score": health_score,
        "health_label": health_label,
        "health_tone": health_tone,
        "period_label": period_label,
        "modules": pilotage_modules,
        "focus_items": focus_items[:5],
        "quality": {
            "score": quality_score,
            "bad": quality_bad,
            "warn": quality_warn,
            "info": quality_info,
            "total": quality_total,
            "url": _safe("main.qualite_donnees_transverse"),
        },
        "documents": {
            "ready": documents_ready,
            "projects": project_count,
            "actions": actions_total,
            "url": _safe("main.documents_exports", year=budget_year),
        },
        "routine": [
            {
                "label": "Présences",
                "value": sessions_without_presence,
                "text": "Séances réalisées sans présence" if sessions_without_presence else "Résultats à jour",
                "tone": "danger" if sessions_without_presence else "ok",
                "url": _safe("statsimpact.dashboard", date_from=activity_from_iso, date_to=activity_to_iso),
            },
            {
                "label": "Orientations",
                "value": orientation_watch,
                "text": "Suivis à reprendre" if orientation_watch else "Suivi lisible",
                "tone": "warn" if orientation_watch else "ok",
                "url": _safe("partenaires.orientations", year=budget_year),
            },
            {
                "label": "Rappels",
                "value": rappel_due,
                "text": "Échéances proches" if rappel_due else "Aucun rappel urgent",
                "tone": "danger" if rappel_overdue else ("warn" if rappel_due else "ok"),
                "url": _safe("main.suivi_rappels"),
            },
            {
                "label": "Documents",
                "value": documents_ready,
                "text": "Fiches prêtes à ouvrir",
                "tone": "ok" if documents_ready else "info",
                "url": _safe("main.documents_exports", year=budget_year),
            },
        ],
    }

    shortcuts = resolved_quick_actions(user)

    return {
        "mode": "global" if has_scope_all else "secteur",
        "days": effective_days,
        "budget_year": budget_year,
        "period_key": current_period_key,
        "period_label": period_label,
        "activity_start": since_date,
        "activity_end": until_date,
        "kpis": {
            "attribue": round(total_attribue, 2),
            "recu": round(total_recu, 2),
            "engage": round(total_engage, 2),
            "reste": round(total_reste, 2),
            "taux": taux,
            "sessions": sessions_recent,
            "uniques": uniques_recent,
        },
        "alerts": alerts[:12],
        "shortcuts": shortcuts,
        "recents": {
            "depenses": recent_depenses,
            "sessions": recent_sessions,
            "participants": recent_participants,
        },
        "pilotage": pilotage,
        "charts": charts,
    }
