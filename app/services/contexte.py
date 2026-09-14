"""Contexte de travail : l'année et le secteur qu'on est en train de regarder.

L'application demande une année sur 42 écrans et un secteur sur 50, et
n'en retenait aucun. On prépare le bilan 2025, on clique vers les
dépenses, et on se retrouve en 2026 : il faut rechoisir. À chaque écran,
toute la journée, pendant toute la durée d'un dossier.

Le principe retenu ne retire aucune souplesse : **le paramètre explicite
gagne toujours**. On mémorise simplement le dernier choix pour s'en servir
comme valeur par défaut à l'écran suivant, au lieu de repartir de l'année
courante. Le sélecteur reste là, on peut toujours changer, et changer
devient le nouveau défaut.
"""
from __future__ import annotations

from datetime import date

from flask import request, session

#: Bornes de bon sens, reprises de celles déjà appliquées dans les hubs.
ANNEE_MIN, ANNEE_MAX = 2000, 2100

CLE_ANNEE = "annee_travail"
CLE_SECTEUR = "secteur_travail"


def _borner(annee) -> int | None:
    try:
        valeur = int(annee)
    except (TypeError, ValueError):
        return None
    return valeur if ANNEE_MIN <= valeur <= ANNEE_MAX else None


def memoriser_depuis_la_requete() -> None:
    """Retient l'année et le secteur passés dans l'URL, s'ils sont valides.

    Branché en amont de chaque requête : il n'y a donc rien à appeler dans
    les routes, et un écran qui n'en parle pas ne perturbe pas le contexte.
    """
    if request.method != "GET":
        return

    brut = request.args.get("year") or request.args.get("annee")
    annee = _borner(brut) if brut else None
    if annee is not None:
        session[CLE_ANNEE] = annee

    if "secteur" in request.args:
        secteur = (request.args.get("secteur") or "").strip()
        # Un secteur vide est un choix : « tous secteurs ». On le retient
        # aussi, sinon le filtre se remettrait tout seul au suivant.
        session[CLE_SECTEUR] = secteur


def annee_travail(defaut: int | None = None) -> int:
    """L'année sur laquelle on travaille : la dernière consultée, sinon
    l'année courante."""
    annee = _borner(session.get(CLE_ANNEE))
    if annee is not None:
        return annee
    return _borner(defaut) or date.today().year


def secteur_travail(defaut: str = "") -> str:
    """Le secteur sur lequel on travaille, ou le repli fourni."""
    valeur = session.get(CLE_SECTEUR)
    if valeur is None:
        return defaut
    return (valeur or "").strip()


def oublier() -> None:
    """Repart de zéro — utile après une déconnexion ou un changement de rôle."""
    session.pop(CLE_ANNEE, None)
    session.pop(CLE_SECTEUR, None)
