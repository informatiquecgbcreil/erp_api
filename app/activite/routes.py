"""Blueprint "activite" éclaté en modules par domaine.

Chaque module ci-dessous enregistre ses routes sur le blueprint partagé
défini dans app/activite/__init__.py. L'import suffit à déclencher
l'enregistrement.
"""
from app.activite import (  # noqa: F401
    archives,
    ateliers,
    emargement,
    emargement_modeles,
    helpers,
    inscriptions,
    participants_secteur,
    saisie_grille,
    sessions_gestion,
)
