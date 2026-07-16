from __future__ import annotations

from app.extensions import db
from app.models import Quartier


def normalize_quartier_for_ville(ville: str | None, quartier_id: str | int | None) -> int | None:
    if not quartier_id:
        return None
    try:
        qid = int(quartier_id)
    except (TypeError, ValueError):
        return None
    quartier = db.session.get(Quartier, qid)
    if not quartier:
        return None
    ville_norm = (ville or "").strip().lower()
    quartier_ville_norm = (quartier.ville or "").strip().lower()
    if ville_norm and quartier_ville_norm and ville_norm != quartier_ville_norm:
        return None
    return quartier.id
