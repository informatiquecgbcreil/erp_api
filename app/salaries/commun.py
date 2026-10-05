"""Helpers partagés par les pages de l'espace salarié."""
from __future__ import annotations

from functools import wraps
from urllib.parse import urlsplit

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user

from app.services import espace_salarie as es


def ma_fiche_ou_explication(fn):
    """La page exige une fiche salarié reliée au compte ; sinon on explique."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        salarie = es.salarie_de(current_user)
        if salarie is None:
            return render_template("salaries/fiche_manquante.html"), 200
        return fn(salarie, *args, **kwargs)
    return wrapper


def retour(defaut: str):
    """Revenir à la page d'origine (même site) ou à la page par défaut."""
    cible = (request.form.get("next") or request.referrer or "").strip()
    try:
        morceaux = urlsplit(cible)
    except ValueError:
        return redirect(defaut)
    if cible and morceaux.netloc in ("", request.host) and morceaux.path.startswith("/") \
            and not morceaux.path.startswith("//"):
        chemin = morceaux.path + (f"?{morceaux.query}" if morceaux.query else "")
        return redirect(chemin)
    return redirect(defaut)


def erreur(message: str, defaut: str):
    flash(message, "danger")
    return retour(defaut)


def interdit_sauf(condition: bool):
    if not condition:
        abort(403)


def url_espace() -> str:
    return url_for("salaries.espace")
