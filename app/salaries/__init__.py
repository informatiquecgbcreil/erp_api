"""Espace salarié (intégration de l'application « Récup »).

Heures supplémentaires et récupérations, frais kilométriques, profils
salariaux et coffre-fort de documents. Module « rh » (voir
app/services/modules.py) : désactiver le module masque tout l'espace.
"""
from flask import Blueprint

bp = Blueprint("salaries", __name__, url_prefix="/salarie")

from . import accueil  # noqa: E402,F401
from . import recup  # noqa: E402,F401
from . import km  # noqa: E402,F401
from . import salaire  # noqa: E402,F401
from . import coffre  # noqa: E402,F401
from . import exports  # noqa: E402,F401
