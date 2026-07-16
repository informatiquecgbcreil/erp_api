"""Blueprint "main" éclaté en modules par domaine.

Chaque module ci-dessous enregistre ses routes sur le blueprint partagé
défini dans app.main.common. L'import suffit à déclencher l'enregistrement.
"""
from app.main.common import bp  # noqa: F401

from app.main import (  # noqa: F401
    agenda,
    benevoles,
    bilan_global,
    caisse,
    comparaison,
    controle,
    couts,
    dashboard,
    dons,
    guides,
    hart,
    impayes,
    hubs,
    qualite_donnees,
    parcours,
    recherche,
    rh,
    stats,
    subventions,
    suivi_rappels,
    tarifs,
    tresorerie,
)
