"""Journal d'audit des actions sensibles.

``journaliser()`` enregistre QUI / QUOI / QUAND pour les opérations à fort
enjeu (comptes, rôles, restauration, exports de données personnelles). La
fonction ne lève JAMAIS : un échec d'écriture du journal ne doit pas casser
l'action métier.
"""

from __future__ import annotations

import json
import re

from flask import current_app
from flask_login import current_user

from app.extensions import db


#: Clés de détails qui portent une identité : effacées à l'anonymisation.
CLES_IDENTIFIANTES = {"nom", "prenom", "email", "telephone", "date_naissance", "adresse",
                      "participant_nom", "label", "nom_complet", "etiquette"}


_EXACT = re.compile(r"participant\s?#(\d+)\b", re.IGNORECASE)
_PARENTHESE = re.compile(r"\(#(\d+)\)")


def participant_de_la_cible(action, cible):
    """Identifiant de fiche désigné sans ambiguïté par la cible, sinon None
    (même règle que la migration e4f7a2c9b168)."""
    if not cible:
        return None
    trouve = _EXACT.search(cible)
    if trouve:
        return int(trouve.group(1))
    if (action or "").startswith("participant."):
        trouve = _PARENTHESE.search(cible)
        if trouve:
            return int(trouve.group(1))
    return None


def _participant(action, cible, participant_id):
    if participant_id is not None:
        return int(participant_id)
    return participant_de_la_cible(action, str(cible) if cible is not None else None)


def enregistrer(action: str, cible=None, details=None, participant_id=None):
    """Ajoute une trace dans la transaction métier ; aucun commit implicite."""
    from app.models import AuditLog
    row = AuditLog(user_id=getattr(current_user, "id", None),
                   user_email=getattr(current_user, "email", None),
                   action=action[:60], cible=str(cible)[:255] if cible is not None else None,
                   details=json.dumps(details, ensure_ascii=False, default=str) if details is not None else None,
                   participant_id=_participant(action, cible, participant_id))
    db.session.add(row)
    return row


def effacer_identite(participant_id: int) -> int:
    """Anonymisation ou suppression d'une fiche : ses lignes du journal,
    retrouvées par identifiant (jamais en cherchant un nom dans le texte),
    ne gardent que « participant #id » et perdent les détails identifiants.
    Dans la transaction de l'appelant ; renvoie le nombre de lignes."""
    from app.models import AuditLog
    nombre = 0
    for row in AuditLog.query.filter_by(participant_id=participant_id).all():
        row.cible = f"participant #{participant_id}"
        if row.details:
            try:
                valeur = json.loads(row.details)
            except ValueError:
                valeur = None
            if isinstance(valeur, dict):
                for cle in CLES_IDENTIFIANTES & set(valeur):
                    valeur[cle] = "effacé"
                row.details = json.dumps(valeur, ensure_ascii=False, default=str)
            else:
                row.details = None
        nombre += 1
    return nombre


def journaliser(action: str, cible=None, details=None, participant_id=None) -> None:
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
                participant_id=_participant(action, cible, participant_id),
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
