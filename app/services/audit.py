"""Journal d'audit des actions sensibles.

``journaliser()`` enregistre QUI / QUOI / QUAND pour les opérations à fort
enjeu (comptes, rôles, restauration, exports de données personnelles). La
fonction ne lève JAMAIS : un échec d'écriture du journal ne doit pas casser
l'action métier.
"""

from __future__ import annotations

import json

from flask import current_app
from flask_login import current_user

from app.extensions import db


def journaliser(action: str, cible=None, details=None) -> None:
    """Ajoute une entrée au journal d'audit (best-effort, ne lève jamais).

    À appeler APRÈS le commit de l'action métier (cette fonction commit aussi).
    """
    try:
        from app.models import AuditLog

        det = None
        if details is not None:
            det = details if isinstance(details, str) else json.dumps(
                details, ensure_ascii=False, default=str
            )
        db.session.add(
            AuditLog(
                user_id=getattr(current_user, "id", None),
                user_email=getattr(current_user, "email", None),
                action=str(action)[:60],
                cible=(str(cible)[:255] if cible is not None else None),
                details=det,
            )
        )
        db.session.commit()
    except Exception:  # noqa: BLE001
        try:
            db.session.rollback()
        except Exception:  # noqa: BLE001
            pass
        try:
            current_app.logger.exception("Journal d'audit : écriture impossible (%s)", action)
        except Exception:  # noqa: BLE001
            pass
