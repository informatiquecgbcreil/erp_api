from __future__ import annotations

import json
import os
import re
from copy import deepcopy
from typing import Any

from flask import current_app


MODEL_FILE_NAME = "emargement_models.json"
GLOBAL_SCOPE = "__ALL__"
CUSTOM_SENTINEL = "__CUSTOM__"

ENGINE_REGISTRY: dict[str, dict[str, str]] = {
    "collectif_standard": {
        "label": "Collectif standard",
        "session_family": "COLLECTIF",
        "description": "Feuille collective standard avec signatures.",
    },
    "collectif_dispositif_bw": {
        "label": "Collectif par dispositif (N&B)",
        "session_family": "COLLECTIF",
        "description": "Feuille collective triée par dispositif, optimisée noir et blanc.",
    },
    "individuel_mensuel_standard": {
        "label": "Individuel mensuel standard",
        "session_family": "INDIVIDUEL_MENSUEL",
        "description": "Feuille mensuelle standard pour rendez-vous individuels.",
    },
}


def _slugify(value: str) -> str:
    raw = re.sub(r"[^a-zA-Z0-9]+", "-", (value or "").strip().lower()).strip("-")
    return raw or "modele-emargement"


DEFAULT_MODELS: list[dict[str, Any]] = [
    {
        "key": "collectif-standard",
        "label": "Collectif standard",
        "engine": "collectif_standard",
        "session_family": "COLLECTIF",
        "secteur": GLOBAL_SCOPE,
        "description": "Modèle standard réutilisable par tous les secteurs pour les feuilles collectives.",
        "sort_order": 10,
        "is_active": True,
        "is_builtin": True,
    },
    {
        "key": "individuel-mensuel-standard",
        "label": "Individuel mensuel standard",
        "engine": "individuel_mensuel_standard",
        "session_family": "INDIVIDUEL_MENSUEL",
        "secteur": GLOBAL_SCOPE,
        "description": "Modèle standard réutilisable pour les rendez-vous individuels mensuels.",
        "sort_order": 20,
        "is_active": True,
        "is_builtin": True,
    },
    {
        "key": "collectif-dispositif-bw",
        "label": "Collectif par dispositif (N&B)",
        "engine": "collectif_dispositif_bw",
        "session_family": "COLLECTIF",
        "secteur": GLOBAL_SCOPE,
        "description": "Feuille collective regroupée par dispositif, sans dépendance aux couleurs.",
        "sort_order": 30,
        "is_active": True,
        "is_builtin": True,
    },
]


def _models_path(app=None) -> str:
    app = app or current_app
    return os.path.join(app.instance_path, MODEL_FILE_NAME)


def _normalize_entry(entry: dict[str, Any]) -> dict[str, Any]:
    engine = str(entry.get("engine") or "collectif_standard").strip()
    if engine not in ENGINE_REGISTRY:
        engine = "collectif_standard"
    session_family = ENGINE_REGISTRY[engine]["session_family"]
    secteur = (entry.get("secteur") or GLOBAL_SCOPE).strip() or GLOBAL_SCOPE
    return {
        "key": _slugify(str(entry.get("key") or entry.get("label") or "modele-emargement")),
        "label": " ".join(str(entry.get("label") or "").split()) or ENGINE_REGISTRY[engine]["label"],
        "engine": engine,
        "session_family": session_family,
        "secteur": secteur,
        "description": " ".join(str(entry.get("description") or "").split()),
        "sort_order": int(entry.get("sort_order") or 0),
        "is_active": bool(entry.get("is_active", True)),
        "is_builtin": bool(entry.get("is_builtin", False)),
    }


def _seed_if_needed(app=None) -> None:
    path = _models_path(app)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        return
    payload = {"models": deepcopy(DEFAULT_MODELS)}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)


def load_models(app=None) -> list[dict[str, Any]]:
    _seed_if_needed(app)
    path = _models_path(app)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh) or {}
    except Exception:
        payload = {}
    items = payload.get("models") or []
    normalized = [_normalize_entry(item) for item in items if isinstance(item, dict)]
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for item in sorted(normalized, key=lambda x: (x["sort_order"], x["label"].lower(), x["key"])):
        if item["key"] in seen:
            continue
        seen.add(item["key"])
        deduped.append(item)
    return deduped


def save_models(models: list[dict[str, Any]], app=None) -> None:
    path = _models_path(app)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {"models": [_normalize_entry(item) for item in models]}
    payload["models"] = sorted(payload["models"], key=lambda x: (x["sort_order"], x["label"].lower(), x["key"]))
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)


def get_model(key: str, app=None) -> dict[str, Any] | None:
    wanted = _slugify(key)
    for item in load_models(app):
        if item["key"] == wanted:
            return item
    return None


def list_models_for_scope(secteur: str | None = None, session_family: str | None = None, include_inactive: bool = False, app=None) -> list[dict[str, Any]]:
    secteur = (secteur or "").strip()
    rows = []
    for item in load_models(app):
        if not include_inactive and not item["is_active"]:
            continue
        if session_family and item["session_family"] != session_family:
            continue
        if secteur and item["secteur"] not in {GLOBAL_SCOPE, secteur}:
            continue
        rows.append(item)
    return rows


def upsert_model(data: dict[str, Any], original_key: str | None = None, app=None) -> dict[str, Any]:
    models = load_models(app)
    normalized = _normalize_entry(data)
    original_slug = _slugify(original_key) if original_key else None

    for item in models:
        if original_slug and item["key"] == original_slug:
            continue
        if item["key"] == normalized["key"]:
            raise ValueError("Une valeur avec ce code existe déjà.")

    replaced = False
    if original_slug:
        for idx, item in enumerate(models):
            if item["key"] == original_slug:
                normalized["is_builtin"] = bool(item.get("is_builtin"))
                models[idx] = normalized
                replaced = True
                break
    if not replaced:
        models.append(normalized)
    save_models(models, app)
    return normalized


def delete_model(key: str, app=None) -> None:
    target = _slugify(key)
    models = [item for item in load_models(app) if item["key"] != target]
    save_models(models, app)


def selection_options(secteur: str | None, session_family: str, app=None) -> list[tuple[str, str]]:
    rows = list_models_for_scope(secteur=secteur, session_family=session_family, include_inactive=False, app=app)
    options = []
    for item in rows:
        scope_suffix = "" if item["secteur"] == GLOBAL_SCOPE else f" · {item['secteur']}"
        options.append((item["key"], f"{item['label']}{scope_suffix}"))
    return options


def decode_storage_value(raw: str | None) -> tuple[str | None, bool]:
    value = (raw or "").strip()
    if not value:
        return None, False
    if value.startswith("builtin:"):
        return value.split(":", 1)[1].strip() or None, False
    return None, True


def encode_storage_value(key: str | None, current_value: str | None = None) -> str | None:
    key = (key or "").strip()
    current_value = (current_value or "").strip() or None
    if not key:
        return current_value
    if key == CUSTOM_SENTINEL:
        return current_value
    return f"builtin:{_slugify(key)}"
