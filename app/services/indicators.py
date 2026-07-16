from __future__ import annotations

from datetime import date
from uuid import uuid4

from app.extensions import db
from app.models import Participant, PresenceActivite, ProjetAtelier, ProjetIndicateur, SessionActivite


INDICATOR_METRICS = {
    "participants_uniques": {
        "label": "Participants uniques",
        "unit": "",
        "group": "Participation",
    },
    "presences_totales": {
        "label": "Présences totales",
        "unit": "",
        "group": "Participation",
    },
    "sessions_totales": {
        "label": "Sessions réalisées",
        "unit": "",
        "group": "Activité",
    },
    "recurrence_2plus": {
        "label": "Participants récurrents (≥2 séances)",
        "unit": "",
        "group": "Participation",
    },
    "fidelite_3plus": {
        "label": "Participants fidèles (≥3 présences)",
        "unit": "",
        "group": "Participation",
    },
    "fidelite_3plus_rate": {
        "label": "Taux de fidélité (≥3 présences)",
        "unit": "%",
        "group": "Participation",
    },
    "frequence_moyenne": {
        "label": "Présences moyennes par participant",
        "unit": "",
        "group": "Participation",
    },
    "age_moyen": {
        "label": "Âge moyen",
        "unit": " ans",
        "group": "Publics",
    },
    "participants_mineurs": {
        "label": "Participants mineurs",
        "unit": "",
        "group": "Publics",
    },
    "participants_18_25": {
        "label": "Participants 18-25 ans",
        "unit": "",
        "group": "Publics",
    },
    "participants_60_plus": {
        "label": "Participants 60 ans et plus",
        "unit": "",
        "group": "Publics",
    },
    "participants_femmes": {
        "label": "Femmes",
        "unit": "",
        "group": "Publics",
    },
    "participants_hommes": {
        "label": "Hommes",
        "unit": "",
        "group": "Publics",
    },
    "participants_genre_autre": {
        "label": "Genre autre / non renseigné",
        "unit": "",
        "group": "Publics",
    },
    "participants_creil": {
        "label": "Habitants de Creil",
        "unit": "",
        "group": "Territoires",
    },
    "participants_qpv": {
        "label": "Habitants QPV",
        "unit": "",
        "group": "Territoires",
    },
    "taux_qpv": {
        "label": "Taux QPV",
        "unit": "%",
        "group": "Territoires",
    },
    "type_public_h": {
        "label": "Type public H - Habitants",
        "unit": "",
        "group": "Publics",
    },
    "type_public_s": {
        "label": "Type public S - Salariés",
        "unit": "",
        "group": "Publics",
    },
    "type_public_b": {
        "label": "Type public B - Bénévoles",
        "unit": "",
        "group": "Publics",
    },
    "type_public_a": {
        "label": "Type public A - Administrateurs",
        "unit": "",
        "group": "Publics",
    },
    "type_public_p": {
        "label": "Type public P - Partenaires",
        "unit": "",
        "group": "Publics",
    },
    "depenses_totales": {
        "label": "Dépenses totales (charges)",
        "unit": "€",
        "group": "Financier",
    },
    "recettes_totales": {
        "label": "Recettes totales (produits)",
        "unit": "€",
        "group": "Financier",
    },
    "cout_par_participant": {
        "label": "Coût par participant",
        "unit": "€",
        "group": "Financier",
    },
    "cout_par_presence": {
        "label": "Coût par présence",
        "unit": "€",
        "group": "Financier",
    },
}


INDICATOR_TEMPLATES = {
    code: cfg["label"] for code, cfg in INDICATOR_METRICS.items()
}


INDICATOR_METRIC_GROUPS = {}
for _metric_code, _metric_cfg in INDICATOR_METRICS.items():
    INDICATOR_METRIC_GROUPS.setdefault(_metric_cfg["group"], []).append({
        "code": _metric_code,
        "label": _metric_cfg["label"],
    })


INDICATOR_PACKS = {
    "caf_base": {
        "label": "Pack CAF (base)",
        "codes": ["participants_uniques", "presences_totales", "sessions_totales", "recurrence_2plus"],
    },
    "assiduite": {
        "label": "Pack Assiduité",
        "codes": ["recurrence_2plus", "fidelite_3plus", "fidelite_3plus_rate", "frequence_moyenne"],
    },
    "publics": {
        "label": "Pack Publics",
        "codes": ["age_moyen", "participants_mineurs", "participants_18_25", "participants_60_plus", "participants_femmes", "participants_hommes"],
    },
    "territoires": {
        "label": "Pack Territoires",
        "codes": ["participants_creil", "participants_qpv", "taux_qpv"],
    },
    "financier": {
        "label": "Pack Financier",
        "codes": ["depenses_totales", "recettes_totales", "cout_par_participant", "cout_par_presence"],
    },
    "jeunesse": {
        "label": "Pack Jeunesse (simple)",
        "codes": ["participants_uniques", "recurrence_2plus"],
    },
}


PERIOD_CHOICES = {
    "context": "Période sélectionnée (défaut)",
    "year": "Année sélectionnée",
    "custom": "Personnalisée (dates)",
}


TARGET_OP_CHOICES = {
    "ge": "Atteindre au moins (≥)",
    "gt": "Dépasser strictement (>)",
    "le": "Rester sous / égal (≤)",
    "lt": "Rester strictement sous (<)",
    "eq": "Être exactement (=)",
}


TARGET_OP_SYMBOLS = {
    "ge": "≥",
    "gt": ">",
    "le": "≤",
    "lt": "<",
    "eq": "=",
}


def parse_float_optional(value):
    raw = str(value or "").strip()
    if not raw:
        return None
    raw = raw.replace("\u00a0", "").replace(" ", "").replace("€", "").replace("â‚¬", "").replace(",", ".")
    try:
        return round(float(raw), 2)
    except Exception:
        return None


def indicator_unique_code(prefix: str) -> str:
    safe_prefix = "".join(ch if ch.isalnum() else "_" for ch in (prefix or "custom").lower()).strip("_")
    return f"{safe_prefix}_{uuid4().hex[:12]}"[:60]


def indicator_params(ind: ProjetIndicateur) -> dict:
    return ind.params() or {}


def indicator_source(ind: ProjetIndicateur, params: dict | None = None) -> str:
    params = params or indicator_params(ind)
    source = (params.get("source") or "").strip()
    if source in {"manual", "stats"}:
        return source
    if ind.code in INDICATOR_TEMPLATES or params.get("metric_code"):
        return "stats"
    return "manual"


def indicator_value_type(ind: ProjetIndicateur, params: dict | None = None) -> str:
    params = params or indicator_params(ind)
    value_type = (params.get("value_type") or params.get("type") or "").strip()
    if value_type in {"number", "check"}:
        return value_type
    return "number"


def indicator_metric_code(ind: ProjetIndicateur, params: dict | None = None) -> str:
    params = params or indicator_params(ind)
    metric_code = (params.get("metric_code") or ind.code or "").strip()
    return metric_code if metric_code in INDICATOR_METRICS else ""


def indicator_target_status(value, target, op: str):
    if value is None or target is None:
        return None
    try:
        v = float(value)
        t = float(target)
    except Exception:
        return None

    op = (op or "ge").strip()
    if op == "gt":
        ok = v > t
        ratio = v / t if t else (float("inf") if v > 0 else 0)
    elif op == "le":
        ok = v <= t
        ratio = t / v if v else float("inf")
    elif op == "lt":
        ok = v < t
        ratio = t / v if v else float("inf")
    elif op == "eq":
        ok = v == t
        ratio = 1 if ok else 0
    else:
        ok = v >= t
        ratio = v / t if t else (float("inf") if v > 0 else 0)

    if ok:
        return "ok"
    if ratio >= 0.75:
        return "warn"
    return "bad"


def indicator_date_range(params: dict, selected_annee: int | None):
    period = (params.get("period") or "context").strip()
    if period == "custom":
        d1 = _parse_iso_date(params.get("start") or "")
        d2 = _parse_iso_date(params.get("end") or "")
        if d1 and d2 and d2 < d1:
            d1, d2 = d2, d1
        return d1, d2
    if period in {"year", "context"} and selected_annee:
        return date(selected_annee, 1, 1), date(selected_annee, 12, 31)
    return None, None


def indicator_list_rows(indicateurs: list[ProjetIndicateur]) -> list[dict]:
    rows = []
    for ind in indicateurs:
        params = indicator_params(ind)
        source = indicator_source(ind, params)
        value_type = indicator_value_type(ind, params)
        metric_code = indicator_metric_code(ind, params)
        manual_value = parse_float_optional(params.get("manual_value"))
        checked = bool(params.get("checked"))
        target = parse_float_optional(params.get("target"))
        target_op = (params.get("target_op") or "ge").strip()
        if target_op not in TARGET_OP_CHOICES:
            target_op = "ge"
        status = None
        if value_type == "check":
            status = "ok" if checked else "bad"
        elif source == "manual":
            status = indicator_target_status(manual_value, target, target_op)

        rows.append({
            "ind": ind,
            "params": params,
            "source": source,
            "source_label": "Stats-impact" if source == "stats" else "Manuel",
            "value_type": value_type,
            "metric_code": metric_code,
            "metric_label": INDICATOR_TEMPLATES.get(metric_code, ""),
            "manual_value": manual_value,
            "checked": checked,
            "target": target,
            "target_op": target_op,
            "target_symbol": TARGET_OP_SYMBOLS.get(target_op, "≥"),
            "status": status,
        })
    return rows


def indicator_counts(rows: list[dict]) -> dict:
    return {
        "total": len(rows),
        "active": sum(1 for row in rows if row["ind"].is_active),
        "manual": sum(1 for row in rows if row["source"] == "manual"),
        "stats": sum(1 for row in rows if row["source"] == "stats"),
    }


def compute_project_indicators(projet, selected_annee: int | None = None, subventions=None) -> list[dict]:
    atelier_ids = [
        row.atelier_id for row in ProjetAtelier.query.filter_by(projet_id=projet.id).all()
    ]
    depenses_total, recettes_total = _finance_totals(projet, subventions)

    indicateurs = (
        ProjetIndicateur.query.filter_by(projet_id=projet.id, is_active=True)
        .order_by(ProjetIndicateur.created_at.asc())
        .all()
    )

    rows = []
    for ind in indicateurs:
        params = indicator_params(ind)
        source = indicator_source(ind, params)
        value_type = indicator_value_type(ind, params)
        metric_code = indicator_metric_code(ind, params)
        val = None
        display_value = None
        unit = params.get("unit") or ""

        if value_type == "check":
            checked = bool(params.get("checked"))
            val = 1 if checked else 0
            display_value = "Validé" if checked else "Non validé"
            target = None
            op = "eq"
            status = "ok" if checked else "bad"
        elif source == "manual":
            val = parse_float_optional(params.get("manual_value"))
            target = parse_float_optional(params.get("target"))
            op = params.get("target_op", "ge")
            status = indicator_target_status(val, target, op)
        else:
            dmin, dmax = indicator_date_range(params, selected_annee)
            scoped_ateliers = _indicator_atelier_scope(atelier_ids, params.get("atelier_id"))
            metrics = _participants_metrics(scoped_ateliers, dmin, dmax)
            val = _metric_value(metric_code, metrics, depenses_total, recettes_total)
            unit = INDICATOR_METRICS.get(metric_code, {}).get("unit", "")
            target = parse_float_optional(params.get("target"))
            op = params.get("target_op", "ge")
            status = indicator_target_status(val, target, op)

        rows.append({
            "label": ind.label,
            "code": ind.code,
            "metric_code": metric_code,
            "source": source,
            "value_type": value_type,
            "value": val,
            "display_value": display_value,
            "unit": unit,
            "target": target,
            "target_op": op,
            "target_symbol": TARGET_OP_SYMBOLS.get(op, "≥"),
            "status": status,
            "period": (params.get("period") or "context"),
            "start": params.get("start"),
            "end": params.get("end"),
            "atelier_id": params.get("atelier_id"),
        })

    return rows


def indicator_gauge_pct(row: dict) -> float | None:
    """Pourcentage d'atteinte (0-100, plafonné) d'un indicateur chiffré.

    Renvoie ``None`` si la jauge n'a pas de sens (pas d'objectif, coche, valeur
    absente). Pour les règles « ne pas dépasser » (le/lt), la jauge mesure la
    marge restante sous le plafond.
    """
    if row.get("value_type") == "check":
        return 100.0 if row.get("value") else 0.0
    value = row.get("value")
    target = row.get("target")
    if value is None or target is None:
        return None
    try:
        v = float(value)
        t = float(target)
    except Exception:
        return None
    op = (row.get("target_op") or "ge").strip()
    if op in ("le", "lt"):
        if v <= 0:
            ratio = 1.0
        else:
            ratio = t / v if v else 1.0
    else:
        ratio = v / t if t else (1.0 if v > 0 else 0.0)
    pct = max(0.0, min(1.0, ratio)) * 100.0
    return round(pct, 1)


def compute_project_indicator_alerts(indicators: list[dict]) -> list[dict]:
    """Construit la liste « à traiter » à partir d'indicateurs déjà calculés.

    Signale les indicateurs non renseignés (manuel sans valeur), sous l'objectif
    (statut warn/bad) ou les coches non validées. Trié danger d'abord.
    """
    alertes = []
    for row in indicators:
        label = row.get("label") or row.get("code") or "Indicateur"
        value_type = row.get("value_type")
        source = row.get("source")
        status = row.get("status")

        if value_type == "check":
            if not row.get("value"):
                alertes.append({"label": label, "niveau": "warning", "message": "Jalon non validé."})
            continue

        if source == "manual" and row.get("value") is None:
            alertes.append({"label": label, "niveau": "warning", "message": "Valeur non renseignée."})
            continue

        if status == "bad":
            alertes.append({"label": label, "niveau": "danger",
                            "message": f"Objectif non atteint ({_fmt_val(row)} pour cible {row.get('target_symbol','≥')} {_fmt_num(row.get('target'))})."})
        elif status == "warn":
            alertes.append({"label": label, "niveau": "warning",
                            "message": f"Proche de l'objectif ({_fmt_val(row)} pour cible {row.get('target_symbol','≥')} {_fmt_num(row.get('target'))})."})

    alertes.sort(key=lambda a: 0 if a["niveau"] == "danger" else 1)
    return alertes


def _fmt_num(value) -> str:
    if value is None:
        return "—"
    try:
        f = float(value)
        return str(int(f)) if f == int(f) else f"{f:.2f}"
    except Exception:
        return str(value)


def _fmt_val(row: dict) -> str:
    if row.get("display_value"):
        return str(row["display_value"])
    return f"{_fmt_num(row.get('value'))}{(' ' + row['unit']) if row.get('unit') else ''}"


def _parse_iso_date(value: str):
    try:
        if not value:
            return None
        return date.fromisoformat(value)
    except Exception:
        return None


def _indicator_atelier_scope(atelier_ids: list[int], atelier_id_raw) -> list[int]:
    scope = list(atelier_ids)
    try:
        if atelier_id_raw:
            atelier_id = int(atelier_id_raw)
            if atelier_id in atelier_ids:
                scope = [atelier_id]
    except Exception:
        pass
    return scope


def _participants_metrics(atelier_ids_scope: list[int], dmin, dmax) -> dict:
    out = {
        "participants_uniques": 0,
        "presences_totales": 0,
        "sessions_totales": 0,
        "recurrence_2plus": 0,
    }
    if not atelier_ids_scope:
        return out

    sess_q = (
        SessionActivite.query
        .filter(SessionActivite.atelier_id.in_(atelier_ids_scope))
        .filter(SessionActivite.is_deleted.is_(False))
        .filter(SessionActivite.statut != "annulee")
    )

    session_date = db.func.coalesce(SessionActivite.date_session, SessionActivite.rdv_date)
    if dmin and dmax:
        sess_q = sess_q.filter(session_date >= dmin).filter(session_date <= dmax)

    out["sessions_totales"] = int(sess_q.count())

    sess_ids = [row[0] for row in sess_q.with_entities(SessionActivite.id).all()]
    if not sess_ids:
        return out

    pres_q = PresenceActivite.query.filter(PresenceActivite.session_id.in_(sess_ids))
    out["presences_totales"] = int(pres_q.count())

    visit_rows = (
        pres_q.with_entities(
            PresenceActivite.participant_id,
            db.func.count(PresenceActivite.id).label("c"),
        )
        .group_by(PresenceActivite.participant_id)
        .all()
    )
    visits_by_participant = {
        int(participant_id): int(count or 0)
        for participant_id, count in visit_rows
        if participant_id
    }
    out["participants_uniques"] = len(visits_by_participant)

    visit_counts = list(visits_by_participant.values())
    out["recurrence_2plus"] = sum(1 for count in visit_counts if count >= 2)
    out["fidelite_3plus"] = sum(1 for count in visit_counts if count >= 3)
    out["fidelite_3plus_rate"] = _pct_value(out["fidelite_3plus"], out["participants_uniques"])
    out["frequence_moyenne"] = round(sum(visit_counts) / len(visit_counts), 2) if visit_counts else 0.0

    _add_demography_metrics(out, list(visits_by_participant.keys()), dmax)
    return out


def _add_demography_metrics(out: dict, participant_ids: list[int], reference: date | None = None) -> None:
    defaults = {
        "age_moyen": None,
        "participants_mineurs": 0,
        "participants_18_25": 0,
        "participants_60_plus": 0,
        "participants_femmes": 0,
        "participants_hommes": 0,
        "participants_genre_autre": 0,
        "participants_creil": 0,
        "participants_qpv": 0,
        "taux_qpv": None,
        "type_public_h": 0,
        "type_public_s": 0,
        "type_public_b": 0,
        "type_public_a": 0,
        "type_public_p": 0,
    }
    out.update(defaults)
    if not participant_ids:
        return

    participants = Participant.query.filter(Participant.id.in_(participant_ids)).all()
    # Âge figé à la fin de la période de l'indicateur (cohérent avec SENACS et
    # stable dans le temps pour les bilans d'années passées).
    ages = [p.age_au(reference) for p in participants if p.age_au(reference) is not None]
    out["age_moyen"] = round(sum(ages) / len(ages), 1) if ages else None

    for participant in participants:
        age = participant.age_au(reference)
        if age is not None:
            if age < 18:
                out["participants_mineurs"] += 1
            if 18 <= age <= 25:
                out["participants_18_25"] += 1
            if age >= 60:
                out["participants_60_plus"] += 1

        gender_key = _gender_key(participant.genre)
        if gender_key == "femme":
            out["participants_femmes"] += 1
        elif gender_key == "homme":
            out["participants_hommes"] += 1
        else:
            out["participants_genre_autre"] += 1

        if getattr(participant, "is_creil", False):
            out["participants_creil"] += 1
        if getattr(participant, "is_qpv", False):
            out["participants_qpv"] += 1

        public_code = (getattr(participant, "type_public", None) or "H").strip().lower()
        public_metric = f"type_public_{public_code}"
        if public_metric in defaults:
            out[public_metric] += 1

    out["taux_qpv"] = _pct_value(out["participants_qpv"], len(participants))


def _gender_key(value: str | None) -> str:
    raw = (value or "").strip().lower()
    if raw.startswith("f") or "femme" in raw:
        return "femme"
    if raw.startswith("h") or "homme" in raw:
        return "homme"
    if raw in {"m", "masculin", "male"}:
        return "homme"
    if raw in {"autre", "non-binaire", "non binaire"}:
        return "autre"
    return "inconnu"


def _pct_value(part, total):
    try:
        total = float(total or 0)
        part = float(part or 0)
    except Exception:
        return None
    if total <= 0:
        return None
    return round((part / total) * 100, 1)


def _finance_totals(projet, subventions=None) -> tuple[float, float]:
    if subventions is None:
        subventions = [
            link.subvention for link in getattr(projet, "subventions", []) or []
            if getattr(link, "subvention", None)
        ]

    depenses = 0.0
    recettes = 0.0
    for subvention in subventions or []:
        for ligne in getattr(subvention, "lignes", []) or []:
            montant = float(ligne.montant_reel or 0)
            nature = (ligne.nature or "").lower()
            if nature == "charge":
                depenses += montant
            elif nature == "produit":
                recettes += montant
    return round(depenses, 2), round(recettes, 2)


def _metric_value(metric_code: str, metrics: dict, depenses_total: float, recettes_total: float):
    if metric_code in metrics:
        return metrics.get(metric_code, 0)
    if metric_code == "depenses_totales":
        return depenses_total
    if metric_code == "recettes_totales":
        return recettes_total
    if metric_code == "cout_par_participant":
        participants = metrics.get("participants_uniques", 0) or 0
        return round(depenses_total / participants, 2) if participants else None
    if metric_code == "cout_par_presence":
        presences = metrics.get("presences_totales", 0) or 0
        return round(depenses_total / presences, 2) if presences else None
    return None
