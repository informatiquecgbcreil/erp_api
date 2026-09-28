"""Application installable (PWA) : icône sur l'écran d'accueil ou le bureau,
ouverture plein écran, sans store. Aucune donnée n'est gardée sur l'appareil :
le service worker ne met en cache que la page « hors connexion »."""
from flask import Blueprint

bp = Blueprint("pwa", __name__)

from . import routes  # noqa: F401,E402
