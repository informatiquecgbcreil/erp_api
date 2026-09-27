"""Lecture des montants et nombres saisis par un humain.

Un seul endroit pour transformer « 1 255,50 € » en nombre. Python accepte
``float("nan")`` et ``float("inf")`` et les contrôles ``montant <= 0`` sont
FAUX pour NaN : un « nan » tapé à l'accueil passait tous les garde-fous et
rendait la caisse définitivement incalculable. Ici, une valeur non finie
est toujours refusée (retour ``None`` ou valeur par défaut).

Filet de sécurité : ``installer_garde_nombres`` refuse, au moment d'écrire
en base, toute valeur non finie dans une colonne numérique, quel que soit
le chemin de code qui l'a produite.
"""
from __future__ import annotations

import math
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

# Au-delà, c'est une faute de frappe (ou une tentative) : aucun flux du
# centre n'approche le million d'euros sur une seule ligne.
MONTANT_MAX = Decimal("1000000")

_ESPACES = (" ", " ", " ", "\t")


def lire_decimal(raw) -> Decimal | None:
    """Décimal fini lu depuis une saisie (virgule, espaces, symbole €), sinon None."""
    s = str(raw if raw is not None else "").strip()
    for espace in _ESPACES:
        s = s.replace(espace, "")
    s = s.replace("€", "").replace(",", ".")
    if not s:
        return None
    try:
        d = Decimal(s)
    except (InvalidOperation, ValueError):
        return None
    return d if d.is_finite() else None


def parse_montant(raw, defaut=None, *, negatif: bool = False, maxi: Decimal = MONTANT_MAX):
    """Montant arrondi au centime (float), ou ``defaut`` si la saisie est vide,
    illisible, non finie, négative (sauf ``negatif=True``) ou hors bornes."""
    d = lire_decimal(raw)
    if d is None:
        return defaut
    if abs(d) > maxi:  # avant quantize, qui échoue sur « 1e999 »
        return defaut
    d = d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if abs(d) > maxi or (d < 0 and not negatif):
        return defaut
    return float(d)


def nombre_fini(raw, defaut=None, *, decimales: int | None = None):
    """Nombre fini quelconque (taux, pourcentage, ETP, coordonnées…), ou ``defaut``."""
    d = lire_decimal(raw)
    if d is None:
        return defaut
    valeur = float(d)
    if not math.isfinite(valeur):  # débordement d'un Decimal gigantesque
        return defaut
    return round(valeur, decimales) if decimales is not None else valeur


class NombreNonFini(ValueError):
    """Une valeur NaN ou infinie allait être écrite en base."""


def _non_fini(valeur) -> bool:
    if isinstance(valeur, bool):
        return False
    if isinstance(valeur, float):
        return not math.isfinite(valeur)
    if isinstance(valeur, Decimal):
        return not valeur.is_finite()
    return False


def _refuser_non_finis(session, _flush_context, _instances):
    from sqlalchemy import Float, Numeric, inspect

    for obj in list(session.new) + list(session.dirty):
        try:
            etat = inspect(obj)
        except Exception:
            continue
        valeurs = etat.dict  # seulement ce qui est chargé ou modifié : aucun accès base
        for col in etat.mapper.columns:
            if not isinstance(col.type, (Float, Numeric)):
                continue
            try:
                attr = etat.mapper.get_property_by_column(col).key
            except Exception:
                continue
            if _non_fini(valeurs.get(attr)):
                raise NombreNonFini(
                    f"Valeur non numérique refusée pour {etat.mapper.class_.__name__}.{attr}"
                )


def installer_garde_nombres() -> None:
    """Refuse d'écrire NaN ou ±inf dans une colonne Float/Numeric (toutes sessions)."""
    from sqlalchemy import event
    from sqlalchemy.orm import Session

    if not event.contains(Session, "before_flush", _refuser_non_finis):
        event.listen(Session, "before_flush", _refuser_non_finis)
