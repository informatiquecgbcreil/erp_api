"""Helpers de dates partagés."""
from datetime import datetime, timezone


def utcnow() -> datetime:
    """Heure courante UTC naïve.

    Remplaçant de ``datetime.utcnow()`` (déprécié, suppression prévue dans
    une future version de Python). Retourne la même valeur naïve qu'avant
    pour rester compatible avec les datetimes déjà stockés en base.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)
