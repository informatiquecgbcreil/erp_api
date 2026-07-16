"""Blueprint "statsimpact" éclaté en modules par domaine.

Chaque module ci-dessous enregistre ses routes sur le blueprint partagé
défini dans app.statsimpact.common. L'import suffit à déclencher
l'enregistrement.
"""
from app.statsimpact.common import bp  # noqa: F401

from app.statsimpact import (  # noqa: F401
    exports_xlsx,
    pages,
    pedagogie,
    tableau_bord,
)
