"""Protection de la connexion contre les attaques par force brute.

Règle : après ``LOGIN_MAX_ECHECS`` mots de passe erronés en moins de
``LOGIN_FENETRE_MINUTES`` minutes, les tentatives pour cet email sont
refusées jusqu'à la fin de la fenêtre — même avec le bon mot de passe.
Une connexion réussie remet le compteur à zéro.

Le verrouillage s'applique aussi aux emails inconnus : impossible de
deviner quels comptes existent en observant le comportement.

Tout est journalisé en base (table journal_connexion) pour l'audit, et
les verrouillages sont tracés dans le journal d'erreurs de l'application.
"""
import os
from datetime import timedelta

from flask import current_app
from sqlalchemy.exc import SQLAlchemyError

from app.extensions import db
from app.models import JournalConnexion
from app.utils.dates import utcnow

MAX_ECHECS = int(os.environ.get("LOGIN_MAX_ECHECS", "5"))
FENETRE_MINUTES = int(os.environ.get("LOGIN_FENETRE_MINUTES", "15"))
# Conservation du journal (audit) : 1 an par défaut (recommandation ANSSI).
RETENTION_JOURS = int(os.environ.get("LOGIN_JOURNAL_RETENTION_JOURS", "365"))


def _echecs_recents(email: str) -> list[JournalConnexion]:
    """Échecs dans la fenêtre courante, postérieurs au dernier succès."""
    depuis = utcnow() - timedelta(minutes=FENETRE_MINUTES)
    q = JournalConnexion.query.filter(
        JournalConnexion.email == email,
        JournalConnexion.cree_le >= depuis,
    )
    dernier_succes = (
        q.filter(JournalConnexion.succes.is_(True))
        .order_by(JournalConnexion.cree_le.desc())
        .first()
    )
    echecs = q.filter(JournalConnexion.succes.is_(False))
    if dernier_succes is not None:
        echecs = echecs.filter(JournalConnexion.cree_le > dernier_succes.cree_le)
    return echecs.order_by(JournalConnexion.cree_le.desc()).all()


def minutes_avant_deverrouillage(email: str) -> int:
    """0 si la connexion est autorisée, sinon minutes d'attente restantes."""
    try:
        echecs = _echecs_recents(email)
    except SQLAlchemyError:
        # Journal indisponible (droits PostgreSQL manquants, base en erreur...) :
        # on n'empêche pas la connexion — le mot de passe reste vérifié. Le verrou
        # anti-bruteforce est seulement ignoré tant que la base n'est pas rétablie.
        db.session.rollback()
        current_app.logger.warning(
            "Journal de connexion inaccessible : verrou anti-bruteforce ignoré. "
            "Vérifiez les droits PostgreSQL sur la table journal_connexion.",
            exc_info=True,
        )
        return 0
    if len(echecs) < MAX_ECHECS:
        return 0
    # Le verrou expire quand le plus ancien des MAX_ECHECS derniers échecs
    # sort de la fenêtre.
    plus_ancien = echecs[MAX_ECHECS - 1].cree_le
    fin_verrou = plus_ancien + timedelta(minutes=FENETRE_MINUTES)
    restant = (fin_verrou - utcnow()).total_seconds()
    if restant <= 0:
        return 0
    return max(1, int(restant // 60) + 1)


def enregistrer_echec(email: str, adresse_ip: str | None) -> int:
    """Enregistre un échec. Retourne les minutes de verrouillage (0 si non verrouillé)."""
    try:
        db.session.add(JournalConnexion(email=email, adresse_ip=adresse_ip, succes=False))
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.warning(
            "Impossible d'enregistrer l'échec de connexion (droits/base) : verrou ignoré.",
            exc_info=True,
        )
        return 0
    return minutes_avant_deverrouillage(email)


def enregistrer_succes(email: str, adresse_ip: str | None) -> None:
    """Enregistre une connexion réussie et purge les entrées trop anciennes."""
    try:
        db.session.add(JournalConnexion(email=email, adresse_ip=adresse_ip, succes=True))
        limite = utcnow() - timedelta(days=RETENTION_JOURS)
        JournalConnexion.query.filter(JournalConnexion.cree_le < limite).delete(
            synchronize_session=False
        )
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.warning(
            "Impossible d'enregistrer la connexion réussie (droits/base).",
            exc_info=True,
        )
