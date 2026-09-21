"""Le périmètre de la structure ET les droits du compte doivent autoriser l'accès.

Tous les schémas restent installés : désactiver un module ne détruit rien.
Ce registre protège aussi les anciennes routes qui contrôlent un rôle directement.
"""
from __future__ import annotations

import json
from flask import current_app, g, has_app_context, has_request_context

CATALOG = {
    "presences": ("Accueil et présences", "Participants, inscriptions, ateliers et émargement."),
    "statistiques": ("Statistiques et bilans", "Indicateurs, bilans d'activité et exports."),
    "finances": ("Finances et projets", "Budgets, subventions, dépenses, caisse et cotisations."),
    "ressources": ("Salles et matériel", "Planning des salles, locations et inventaire."),
    "accompagnement": ("Accompagnement", "Insertion et suivi pédagogique."),
    "partenaires": ("Partenaires", "Annuaire et cartographie des partenaires."),
    "questionnaires": ("Questionnaires", "Enquêtes et réponses des habitants."),
    "transitions": ("Transitions", "Défis et mesures de transition écologique."),
    "rh": ("Ressources humaines", "Salariés, affectations et masse salariale."),
}
DEPENDENCIES = {
    "statistiques": {"presences"}, "accompagnement": {"presences"},
    "questionnaires": {"presences"}, "transitions": {"presences"},
}
PROFILES = {"essentiel": ["presences", "statistiques"],
            "gestion": ["presences", "statistiques", "finances"],
            "complet": list(CATALOG)}

PERMISSIONS = {
    "presences": "participants participant ateliers emargement inscriptions inscriptions_annuelles quartiers activite benevolat",
    "statistiques": "stats statsimpact bilans bilan",
    "finances": "projets projets_edit aap budget subventions depenses dons cotisations caisse veille",
    "ressources": "inventaire salles locations",
    "accompagnement": "insertion pedagogie",
    "partenaires": "partenaires", "questionnaires": "questionnaires",
    "transitions": "transitions", "rh": "rh",
}
PERMISSION_MODULE = {p: key for key, prefixes in PERMISSIONS.items() for p in prefixes.split()}
BLUEPRINT_MODULE = {
    "participants": "presences", "activite": "presences", "kiosk": "presences",
    "quartiers": "presences", "inscriptions_annuelles": "presences",
    "budget": "finances", "previsionnel": "finances", "projets": "finances", "veille": "finances",
    "statsimpact": "statistiques", "bilans": "statistiques",
    "inventaire": "ressources", "inventaire_materiel": "ressources", "salles": "ressources",
    "insertion": "accompagnement", "pedagogie": "accompagnement",
    "partenaires": "partenaires", "questionnaires": "questionnaires", "transitions": "transitions",
}
SOURCE_MODULE = {
    "agenda": "presences", "google_agenda": "presences", "benevoles": "presences",
    "hart": "presences", "rh": "rh", "stats": "finances", "bilan_global": "finances",
    "caisse": "finances", "comparaison": "finances", "couts": "finances", "dons": "finances",
    "impayes": "finances", "repartition": "finances", "subventions": "finances",
    "tarifs": "finances", "tresorerie": "finances",
}


def normalize(values):
    if not isinstance(values, (list, tuple, set)) or any(v not in CATALOG for v in values):
        raise ValueError("Sélection de modules invalide.")
    selected = set(values)
    for key in list(selected):
        selected.update(DEPENDENCIES.get(key, ()))
    return sorted(selected)


def enabled_modules():
    if not has_app_context():
        return set(CATALOG)
    if has_request_context() and hasattr(g, "mcs_modules"):
        return g.mcs_modules
    from app.models import InstanceSettings
    row = InstanceSettings.query.first()
    raw = row.enabled_modules_json if row else None
    if raw is not None:
        # Un réglage corrompu ne réactive jamais des modules par défaut.
        try:
            selected = set(normalize(json.loads(raw)))
        except (ValueError, TypeError):
            selected = set()
    else:
        configured = current_app.config.get("ENABLED_MODULES")
        selected = set(CATALOG) if configured is None else set(normalize(
            configured.split(",") if isinstance(configured, str) and configured else (configured or [])))
    if has_request_context():
        g.mcs_modules = selected
    return selected


def module_enabled(key):
    return key in enabled_modules()


def permission_enabled(code):
    key = PERMISSION_MODULE.get((code or "").split(":", 1)[0])
    return key is None or module_enabled(key)


def endpoint_module(endpoint):
    overrides = {
        "bilans.dashboard": "finances", "bilans.dashboard_export_xlsx": "finances",
        "bilans.bilan_secteur": "finances", "bilans.bilan_subvention": "finances",
        "bilans.bilans_financeurs": "finances", "bilans.qualite": "finances",
        "bilans.inventaire": "ressources",
        "bilans.bilan_senacs_emploi_create": "rh", "bilans.bilan_senacs_emploi_supprimer": "rh",
        "main.assistant_bilan_financeur": "finances",
    }
    if endpoint in overrides:
        return overrides[endpoint]
    blueprint = (endpoint or "").split(".", 1)[0]
    if blueprint in BLUEPRINT_MODULE:
        return BLUEPRINT_MODULE[blueprint]
    view = current_app.view_functions.get(endpoint)
    if view and view.__module__.startswith("app.main."):
        key = SOURCE_MODULE.get(view.__module__.rsplit(".", 1)[-1])
        if key:
            return key
    return {
        "main.hub_publics": "presences", "main.hub_activites": "presences",
        "main.hub_bilans": "statistiques", "main.hub_ressources": "ressources",
        "main.programme_public_export": "presences", "main.publication_programme": "presences",
        "main.publication_programme_publier": "presences",
    }.get(endpoint)


def url_enabled(url):
    """Filtrer aussi les liens des anciens hubs, générés par url_for."""
    from urllib.parse import urlsplit
    from werkzeug.exceptions import HTTPException
    if not url:
        return False
    try:
        endpoint, _ = current_app.url_map.bind("localhost").match(urlsplit(url).path)
    except HTTPException:
        return True
    key = endpoint_module(endpoint)
    return key is None or module_enabled(key)


def can_manage_modules(user):
    return user.is_authenticated and user.has_perm("admin:rbac") and (
        user.has_role("direction") or user.has_role("admin_tech"))
