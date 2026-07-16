from flask import Blueprint

from app.rbac import can_access_secteur

bp = Blueprint("projets", __name__)


ALLOWED_CR = {"pdf", "doc", "docx", "odt"}

PROJET_JOURNAL_CATEGORIES = {
    "fait_marquant": "Fait marquant",
    "reussite": "Réussite",
    "difficulte": "Difficulté",
    "partenariat": "Partenariat",
    "bilan": "Bilan",
}

PROJET_ACTION_CATEGORIES = {
    "atelier": "Atelier / cycle",
    "evenement": "Événement",
    "permanence": "Permanence",
    "sortie": "Sortie / temps fort",
    "partenariat": "Partenariat",
    "autre": "Autre",
}

PROJET_ACTION_STATUTS = {
    "prevue": "Prévue",
    "en_cours": "En cours",
    "realisee": "Réalisée",
    "annulee": "Annulée",
}



def can_see_secteur(secteur: str) -> bool:
    return can_access_secteur(secteur)


