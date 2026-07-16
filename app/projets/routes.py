"""Blueprint "projets" éclaté en modules par domaine.

Chaque module ci-dessous enregistre ses routes sur le blueprint partagé
défini dans app.projets.common. L'import suffit à déclencher
l'enregistrement.
"""
from app.projets.common import bp  # noqa: F401

from app.projets import (  # noqa: F401
    budget_aap,
    finance_exports,
    finance_secteur,
    helpers_projet,
    objectifs_routes,
    projet_detail,
    projets_crud,
)
