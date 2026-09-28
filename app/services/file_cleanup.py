"""Effacement différé : un rollback conserve les documents, une erreur se retente."""
from pathlib import Path
from flask import current_app, has_app_context
from sqlalchemy import event
from sqlalchemy.orm import Session
from datetime import timedelta

from app.extensions import db
from app.utils.dates import utcnow


def _allowed(path):
    target = Path(path).resolve()
    roots = [Path(current_app.instance_path).resolve(), Path(current_app.config["APP_UPLOAD_DIR"]).resolve()]
    return any(target != root and target.is_relative_to(root) for root in roots)


def schedule(path):
    """Programme l'effacement d'un document après validation de la transaction.

    Un chemin hors des dossiers métier (ancien serveur, dossier déplacé,
    sauvegarde restaurée sans reprise des chemins) n'est jamais effacé, mais
    ne bloque pas non plus l'anonymisation ou la purge qui l'a demandé. Il
    reste inscrit dans la file ``pending_file_deletion`` : l'administrateur y
    retrouve le fichier à traiter à la main (le journal ne cite que le numéro
    de ligne, le chemin pouvant être nominatif).
    """
    if not path:
        return
    try:
        allowed = _allowed(path)
        stored = str(Path(path).resolve())
    except (OSError, ValueError):
        allowed, stored = False, str(path)
    from app.models import PendingFileDeletion
    db.session.add(PendingFileDeletion(file_path=stored))
    if allowed:
        db.session.info["file_cleanup_pending"] = True
    else:
        current_app.logger.warning(
            "Document hors des dossiers métier : conservé et inscrit dans la file "
            "d'effacement (pending_file_deletion) pour contrôle manuel.")


#: Une ligne bloquée (chemin hors stockage, fichier verrouillé) est retentée
#: au plus une fois par jour ; elle ne fait plus obstacle aux suivantes.
DELAI_NOUVEL_ESSAI = timedelta(days=1)
TAILLE_PAGE = 500


def drain(limite: int | None = None) -> dict:
    """Parcourt la file par identifiant croissant (audit 3.6). Rapport :
    {"effaces": n, "bloques": n}. Une seule ligne de résumé au journal."""
    from app.models import PendingFileDeletion
    table = PendingFileDeletion.__table__
    maintenant = utcnow()
    effaces = bloques = vus = 0
    dernier = 0
    with db.engine.begin() as connection:
        while True:
            requete = (table.select()
                       .where(table.c.id > dernier)
                       .where(db.or_(table.c.bloque_le.is_(None), table.c.bloque_le < maintenant - DELAI_NOUVEL_ESSAI))
                       .order_by(table.c.id).limit(TAILLE_PAGE))
            lignes = list(connection.execute(requete).mappings())
            if not lignes:
                break
            for row in lignes:
                dernier = row["id"]
                vus += 1
                motif = None
                if not _allowed(row["file_path"]):
                    motif = "hors_stockage"
                else:
                    try:
                        Path(row["file_path"]).unlink(missing_ok=True)
                    except OSError:
                        motif = "indisponible"
                if motif is None:
                    connection.execute(table.delete().where(table.c.id == row["id"]))
                    effaces += 1
                else:
                    connection.execute(table.update().where(table.c.id == row["id"]).values(
                        bloque_le=maintenant, motif=motif, tentatives=(row.get("tentatives") or 0) + 1))
                    bloques += 1
                if limite is not None and vus >= limite:
                    break
            if limite is not None and vus >= limite:
                break
    if bloques:
        current_app.logger.warning(
            "File d'effacement : %s fichier(s) effacé(s), %s mis de côté pour contrôle manuel "
            "(table pending_file_deletion, colonne motif).", effaces, bloques)
    return {"effaces": effaces, "bloques": bloques}


def en_attente_de_controle() -> int:
    from app.models import PendingFileDeletion
    return PendingFileDeletion.query.filter(PendingFileDeletion.bloque_le.isnot(None)).count()


@event.listens_for(Session, "after_commit")
def _after_commit(session):
    if session.info.pop("file_cleanup_pending", False) and has_app_context():
        try:
            drain()
        except Exception:
            # La file est déjà validée en base : une prochaine exécution reprend.
            current_app.logger.warning("Effacement des fichiers différé après validation SQL.")


@event.listens_for(Session, "after_rollback")
def _after_rollback(session):
    session.info.pop("file_cleanup_pending", None)
