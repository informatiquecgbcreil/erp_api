from __future__ import annotations

import re
import unicodedata

from flask import current_app
from app.extensions import db


def _slugify(s: str) -> str:
    s = (s or "").strip()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s or "secteur"


def bootstrap_secteurs_from_config() -> None:
    """
    Initialise la table Secteur depuis config.SECTEURS (non destructif).
    Ne fait rien si la table est déjà peuplée.
    """
    try:
        from app.models import Secteur
        if Secteur.query.count() > 0:
            return
        secteurs = list(current_app.config.get("SECTEURS", []) or [])
        for label in secteurs:
            label = (label or "").strip()
            if not label:
                continue
            code = _slugify(label)
            s = Secteur.query.filter((Secteur.code == code) | (Secteur.label == label)).first()
            if not s:
                db.session.add(Secteur(code=code, label=label, is_active=True))
        db.session.commit()
    except Exception:
        db.session.rollback()
        # Ne jamais casser le boot de l'app à cause des secteurs.
        current_app.logger.exception("bootstrap_secteurs_from_config failed")


def get_secteur_labels(active_only: bool = True) -> list[str]:
    """
    Retourne la liste des labels secteurs (compat avec Projet.secteur, Subvention.secteur, etc.)
    Source prioritaire : DB (Secteur), fallback : config.SECTEURS
    """
    try:
        from app.models import Secteur
        q = Secteur.query
        if active_only:
            q = q.filter_by(is_active=True)
        q = q.order_by(Secteur.label.asc())
        rows = q.all()
        return [s.label for s in rows]
    except Exception:
        return list(current_app.config.get("SECTEURS", []) or [])


def upsert_secteur(label: str, code: str | None = None, is_active: bool = True):
    from app.models import Secteur
    label = (label or "").strip()
    if not label:
        raise ValueError("label vide")
    code = _slugify(code or label)
    s = Secteur.query.filter((Secteur.code == code) | (Secteur.label == label)).first()
    if not s:
        s = Secteur(code=code, label=label, is_active=bool(is_active))
        db.session.add(s)
    else:
        s.code = code
        s.label = label
        s.is_active = bool(is_active)
    db.session.commit()
    return s
