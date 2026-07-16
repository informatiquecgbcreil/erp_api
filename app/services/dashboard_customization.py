from __future__ import annotations

import json
from typing import Any

from flask import url_for
from werkzeug.routing import BuildError

from app.extensions import db
from app.models import UserDashboardPreference

DEFAULT_UI_MODE = "simple"
DEFAULT_QUICK_ACTIONS = [
    "find_person",
    "add_person",
    "attendance",
    "bilans",
    "workshops",
]
DEFAULT_WIDGETS = [
    {"key": "alerts", "visible": True, "position": 1},
    {"key": "charts", "visible": True, "position": 2},
    {"key": "recents", "visible": True, "position": 3},
]
MAX_QUICK_ACTIONS = 6


QUICK_ACTION_CATALOG = {
    "find_person": {
        "label": "Retrouver une personne",
        "icon": "👤",
        "hint": "Ouvrir la liste des participants pour retrouver une fiche.",
        "perm_any": ["participants:view", "participants:view_all", "participants:edit"],
        "endpoint": "participants.list_participants",
    },
    "add_person": {
        "label": "Ajouter une personne",
        "icon": "➕",
        "hint": "Créer une nouvelle fiche participant.",
        "perm_any": ["participants:edit"],
        "endpoint": "participants.new_participant",
    },
    "attendance": {
        "label": "Faire l’émargement",
        "icon": "🖊️",
        "hint": "Accéder aux ateliers et aux feuilles de présence.",
        "perm_any": ["emargement:view", "emargement:edit"],
        "endpoint": "activite.index",
    },
    "attestations": {
        "label": "Attestations de présence",
        "icon": "A4",
        "hint": "Générer des attestations depuis les présences d’émargement.",
        "perm_any": ["participants:view", "participants:view_all"],
        "endpoint": "activite.attestations",
    },
    "bilans": {
        "label": "Voir les bilans",
        "icon": "🧾",
        "hint": "Ouvrir les bilans, synthèses et exports utiles.",
        "perm_any": ["stats:view", "bilans:view"],
        "endpoint": "main.stats_bilans",
        "fallback_endpoint": "main.dashboard",
    },
    "search_participants": {
        "label": "Rechercher dans les fiches",
        "icon": "🔎",
        "hint": "Ouvrir la liste des participants pour chercher une fiche.",
        "perm_any": ["participants:view", "participants:view_all", "participants:edit"],
        "endpoint": "participants.list_participants",
    },
    "workshops": {
        "label": "Ouvrir les ateliers",
        "icon": "📅",
        "hint": "Voir les ateliers et les sessions.",
        "perm_any": ["emargement:view", "emargement:edit"],
        "endpoint": "activite.index",
    },
    "partners": {
        "label": "Voir les partenaires",
        "icon": "🤝",
        "hint": "Consulter l’annuaire des partenaires.",
        "perm_any": ["partenaires:view"],
        "endpoint": "partenaires.index",
    },
    "quartiers": {
        "label": "Voir les quartiers",
        "icon": "📍",
        "hint": "Retrouver la liste des quartiers et des villes.",
        "perm_any": ["quartiers:view"],
        "endpoint": "quartiers.index",
    },
    "projects": {
        "label": "Voir les projets",
        "icon": "🗂️",
        "hint": "Ouvrir le module projets.",
        "perm_any": ["projets:view"],
        "endpoint": "projets.projets_list",
    },
    "statsimpact": {
        "label": "Fréquentation et résultats",
        "icon": "📊",
        "hint": "Consulter la fréquentation, les publics et les résultats des activités.",
        "perm_any": ["statsimpact:view"],
        "endpoint": "statsimpact.dashboard",
    },
    "inventory": {
        "label": "Inventaire du matériel",
        "icon": "📦",
        "hint": "Ouvrir l’inventaire matériel.",
        "perm_any": ["inventaire:view"],
        "endpoint": "inventaire_materiel.list_items",
    },
}

WIDGET_CATALOG = {
    "alerts": {
        "label": "Alertes à vérifier",
        "hint": "Les éléments qui demandent une attention rapide.",
    },
    "charts": {
        "label": "Indicateurs et graphiques",
        "hint": "Les chiffres et visualisations du tableau de bord.",
    },
    "recents": {
        "label": "Éléments récents",
        "hint": "Les dernières fiches, sessions et dépenses.",
    },
}


def _safe_url(endpoint: str | None, fallback_endpoint: str | None = None, **values) -> str:
    for candidate in (endpoint, fallback_endpoint):
        if not candidate:
            continue
        try:
            return url_for(candidate, **values)
        except BuildError:
            continue
    return "#"


def _user_has_any_perm(user, codes: list[str] | tuple[str, ...] | None) -> bool:
    if not codes:
        return True
    has_perm = getattr(user, "has_perm", None)
    if not callable(has_perm):
        return False
    return any(has_perm(code) for code in codes)


def _json_load(value: str | None, fallback: Any):
    if not value:
        return fallback
    try:
        return json.loads(value)
    except Exception:
        return fallback


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def get_or_create_dashboard_pref(user) -> UserDashboardPreference:
    pref = getattr(user, "dashboard_pref", None)
    if pref:
        return pref
    pref = UserDashboardPreference(user_id=user.id, ui_mode=DEFAULT_UI_MODE)
    db.session.add(pref)
    return pref


def load_dashboard_pref(user) -> dict[str, Any]:
    pref = getattr(user, "dashboard_pref", None)
    raw_quick = _json_load(getattr(pref, "quick_actions_json", None), [])
    raw_widgets = _json_load(getattr(pref, "widgets_json", None), [])
    ui_mode = (getattr(pref, "ui_mode", None) or DEFAULT_UI_MODE).strip().lower()
    if ui_mode not in {"simple", "expert"}:
        ui_mode = DEFAULT_UI_MODE
    quick_actions = sanitize_quick_actions(raw_quick, user)
    widgets = sanitize_widgets(raw_widgets)
    return {
        "ui_mode": ui_mode,
        "quick_actions": quick_actions,
        "widgets": widgets,
        "customized": bool(getattr(pref, "customized", False)),
    }


def available_quick_actions(user) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for key, meta in QUICK_ACTION_CATALOG.items():
        if not _user_has_any_perm(user, meta.get("perm_any")):
            continue
        items.append({
            "key": key,
            "label": meta["label"],
            "icon": meta.get("icon") or "➡️",
            "hint": meta.get("hint") or "",
            "url": _safe_url(meta.get("endpoint"), meta.get("fallback_endpoint")),
        })
    return items


def available_widgets() -> list[dict[str, Any]]:
    return [
        {"key": key, "label": meta["label"], "hint": meta.get("hint") or ""}
        for key, meta in WIDGET_CATALOG.items()
    ]


def sanitize_quick_actions(raw_actions: list[Any] | None, user) -> list[str]:
    allowed = {item["key"] for item in available_quick_actions(user)}
    cleaned: list[str] = []
    for key in raw_actions or []:
        if key in allowed and key not in cleaned:
            cleaned.append(key)
        if len(cleaned) >= MAX_QUICK_ACTIONS:
            break
    if cleaned:
        return cleaned
    defaults = [key for key in DEFAULT_QUICK_ACTIONS if key in allowed]
    if defaults:
        return defaults[:MAX_QUICK_ACTIONS]
    return list(sorted(allowed))[:MAX_QUICK_ACTIONS]


def sanitize_widgets(raw_widgets: list[Any] | None) -> list[dict[str, Any]]:
    by_key: dict[str, dict[str, Any]] = {}
    for item in raw_widgets or []:
        if not isinstance(item, dict):
            continue
        key = item.get("key")
        if key not in WIDGET_CATALOG:
            continue
        try:
            pos = int(item.get("position") or 999)
        except Exception:
            pos = 999
        by_key[key] = {
            "key": key,
            "visible": bool(item.get("visible", True)),
            "position": pos,
        }
    if not by_key:
        by_key = {item["key"]: dict(item) for item in DEFAULT_WIDGETS}
    for idx, default_item in enumerate(DEFAULT_WIDGETS, start=1):
        by_key.setdefault(default_item["key"], {"key": default_item["key"], "visible": default_item["visible"], "position": idx})
    items = list(by_key.values())
    items.sort(key=lambda item: (999 if not item.get("visible") else 0, int(item.get("position") or 999), item["key"]))
    for idx, item in enumerate(items, start=1):
        item["position"] = idx
    return items


def save_dashboard_pref(user, *, ui_mode: str | None = None, quick_actions: list[str] | None = None, widgets: list[dict[str, Any]] | None = None, customized: bool = True) -> dict[str, Any]:
    pref = get_or_create_dashboard_pref(user)
    current = load_dashboard_pref(user)
    if ui_mode is not None:
        mode = (ui_mode or DEFAULT_UI_MODE).strip().lower()
        pref.ui_mode = mode if mode in {"simple", "expert"} else DEFAULT_UI_MODE
    else:
        pref.ui_mode = current["ui_mode"]
    quick = sanitize_quick_actions(quick_actions if quick_actions is not None else current["quick_actions"], user)
    widget_list = sanitize_widgets(widgets if widgets is not None else current["widgets"])
    pref.quick_actions_json = _json_dump(quick)
    pref.widgets_json = _json_dump(widget_list)
    pref.customized = customized
    db.session.commit()
    return load_dashboard_pref(user)


def reset_dashboard_pref(user) -> dict[str, Any]:
    pref = get_or_create_dashboard_pref(user)
    pref.ui_mode = DEFAULT_UI_MODE
    pref.quick_actions_json = _json_dump([])
    pref.widgets_json = _json_dump([])
    pref.customized = False
    db.session.commit()
    return load_dashboard_pref(user)


def resolved_quick_actions(user) -> list[dict[str, Any]]:
    prefs = load_dashboard_pref(user)
    available = {item["key"]: item for item in available_quick_actions(user)}
    resolved: list[dict[str, Any]] = []
    for key in prefs["quick_actions"]:
        item = available.get(key)
        if item:
            resolved.append(item)
    return resolved


def resolved_widgets(user) -> list[dict[str, Any]]:
    prefs = load_dashboard_pref(user)
    meta_by_key = {item["key"]: item for item in available_widgets()}
    resolved: list[dict[str, Any]] = []
    for item in prefs["widgets"]:
        merged = dict(item)
        merged.update(meta_by_key.get(item["key"], {}))
        resolved.append(merged)
    return resolved
