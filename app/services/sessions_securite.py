"""Sessions de l'équipe : fin après inactivité, durée maximale, et vraie
fermeture à la déconnexion (mineur sécurité de l'audit : « session valable
31 jours, aucune déconnexion après inactivité, cookie encore valide après
déconnexion »).

Le cookie reste signé côté navigateur ; on y ajoute un identifiant de
session, l'heure d'ouverture et l'heure de dernière activité. « Se
déconnecter » inscrit l'identifiant dans ``session_revoquee`` : un cookie
copié avant la déconnexion n'ouvre plus rien.
"""
from __future__ import annotations

import secrets
import time
from datetime import timedelta

from flask import current_app, session

from app.extensions import db
from app.utils.dates import utcnow

CLE_SID, CLE_DEBUT, CLE_VU = "_mcs_sid", "_mcs_debut", "_mcs_vu"


def _reglages() -> tuple[int, int]:
    return (int(current_app.config.get("SESSION_INACTIVITE_MINUTES") or 120),
            int(current_app.config.get("SESSION_DUREE_MAX_HEURES") or 12))


def ouvrir() -> None:
    """À la connexion : nouvelle session, jamais l'identifiant d'une autre."""
    maintenant = int(time.time())
    session[CLE_SID] = secrets.token_urlsafe(24)
    session[CLE_DEBUT] = maintenant
    session[CLE_VU] = maintenant


def motif_de_fin() -> str | None:
    """Pourquoi cette session ne vaut plus rien, ou None si elle est valable.
    Met à jour l'heure de dernière activité (au plus une fois par minute)."""
    from app.models import SessionRevoquee
    maintenant = int(time.time())
    inactivite, duree_max = _reglages()
    if CLE_SID not in session:
        # Session ouverte avant cette version : on l'adopte, sans la couper.
        ouvrir()
        return None
    if maintenant - int(session.get(CLE_DEBUT) or 0) > duree_max * 3600:
        return "La session a dépassé sa durée maximale : reconnectez-vous."
    if maintenant - int(session.get(CLE_VU) or 0) > inactivite * 60:
        return "Session fermée après une période d'inactivité : reconnectez-vous."
    try:
        if db.session.get(SessionRevoquee, session[CLE_SID]) is not None:
            return "Cette session a été fermée : reconnectez-vous."
    except Exception:  # noqa: BLE001 — table absente pendant une mise à jour
        db.session.rollback()
    if maintenant - int(session.get(CLE_VU) or 0) >= 60:
        session[CLE_VU] = maintenant
    return None


def fermer() -> None:
    """À la déconnexion : l'identifiant de session est révoqué côté serveur."""
    from app.models import SessionRevoquee
    sid = session.get(CLE_SID)
    if not sid:
        return
    _inactivite, duree_max = _reglages()
    try:
        SessionRevoquee.query.filter(SessionRevoquee.expire_le < utcnow()).delete(synchronize_session=False)
        if db.session.get(SessionRevoquee, sid) is None:
            db.session.add(SessionRevoquee(sid=sid, expire_le=utcnow() + timedelta(hours=duree_max + 1)))
        db.session.commit()
    except Exception:  # noqa: BLE001 — la déconnexion locale a lieu quand même
        db.session.rollback()
        current_app.logger.warning("Révocation de session impossible (table absente ?).")
