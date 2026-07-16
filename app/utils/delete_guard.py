from __future__ import annotations

from flask import flash
from sqlalchemy.exc import IntegrityError

from app.extensions import db


def commit_delete(entity_label: str, success_message: str, *, success_category: str = "success", blocked_message: str | None = None, blocked_category: str = "danger") -> bool:
    """Commit a deletion-like transaction and convert FK/constraint crashes into user-facing flashes.

    Returns True on success, False when blocked by integrity constraints.
    """
    try:
        db.session.commit()
        flash(success_message, success_category)
        return True
    except IntegrityError:
        db.session.rollback()
        flash(
            blocked_message
            or f"Impossible de supprimer {entity_label} : cet élément est encore utilisé ailleurs dans l'application.",
            blocked_category,
        )
        return False
