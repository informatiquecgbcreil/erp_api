"""Tâches quotidiennes de conservation, hors des requêtes web (audit 3.5).

Avant : la purge RGPD tournait DANS la première requête du jour, sous le
verrou de la base, en tout ou rien (3 000 fiches = 99 s d'attente pour la
personne qui ouvrait l'application). Désormais :

- la première requête du jour se contente de LANCER la maintenance dans un
  fil d'exécution séparé et repart aussitôt ;
- la maintenance vide la file d'effacement des fichiers, puis, seulement si
  la purge automatique est activée (désactivée par défaut), anonymise par
  lots bornés et applique les durées de conservation ;
- ``flask maintenance`` lance la même chose à la main ou depuis un
  planificateur (installation Linux, tâche planifiée Windows).

Un verrou empêche deux passages simultanés dans le même processus ; la date
de dernière exécution, en base, évite de refaire la purge le même jour.
"""
from __future__ import annotations

import threading

from flask import current_app

_verrou = threading.Lock()


def executer(declenchement: str = "automatique (quotidien)") -> dict:
    """Un passage complet. Ne lève jamais ; rapport par étape."""
    from app.extensions import db
    rapport: dict = {}
    if not _verrou.acquire(blocking=False):
        return {"deja_en_cours": True}
    try:
        try:
            from app.services.file_cleanup import drain
            rapport["fichiers"] = drain()
        except Exception:  # noqa: BLE001
            db.session.rollback()
            current_app.logger.exception("Maintenance : file d'effacement non traitée")
        from app.services import purge_rgpd
        if purge_rgpd.purge_auto_active():
            purge_rgpd.purge_quotidienne_si_necessaire()
            try:
                from app.services.conservation import appliquer
                rapport["conservation"] = appliquer(declenchement)
            except Exception:  # noqa: BLE001
                db.session.rollback()
                current_app.logger.exception("Maintenance : durées de conservation non appliquées")
        return rapport
    finally:
        _verrou.release()


def lancer(app) -> None:
    """Depuis une requête : lance la maintenance sans la faire attendre.
    En test (ou MAINTENANCE_SYNCHRONE), exécution immédiate pour être
    observable."""
    if app.config.get("TESTING") or app.config.get("MAINTENANCE_SYNCHRONE"):
        executer()
        return

    def _fil():
        from app.extensions import db
        with app.app_context():
            try:
                executer()
            finally:
                db.session.remove()

    threading.Thread(target=_fil, name="maintenance-quotidienne", daemon=True).start()


def enregistrer_commande(app) -> None:
    @app.cli.command("maintenance")
    def _commande():
        """Vide la file d'effacement, purge RGPD (si activée) et conservation."""
        import json
        print(json.dumps(executer("commande flask maintenance"), ensure_ascii=False, default=str))
