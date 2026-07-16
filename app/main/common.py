from flask import Blueprint

from app.rbac import can_access_secteur

bp = Blueprint("main", __name__)


def can_see_secteur(secteur: str) -> bool:
    return can_access_secteur(secteur)


