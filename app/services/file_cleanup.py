"""Effacement différé : un rollback conserve les documents, une erreur se retente."""
from pathlib import Path
from flask import current_app, has_app_context
from sqlalchemy import event
from sqlalchemy.orm import Session
from app.extensions import db


def _allowed(path):
    target = Path(path).resolve()
    roots = [Path(current_app.instance_path).resolve(), Path(current_app.config["APP_UPLOAD_DIR"]).resolve()]
    return any(target != root and target.is_relative_to(root) for root in roots)


def schedule(path):
    if not path:
        return
    if not _allowed(path):
        raise ValueError("Le document à effacer se trouve hors des dossiers métier. Vérifiez sa reprise.")
    from app.models import PendingFileDeletion
    db.session.add(PendingFileDeletion(file_path=str(Path(path).resolve())))
    db.session.info["file_cleanup_pending"] = True


def drain():
    from app.models import PendingFileDeletion
    table = PendingFileDeletion.__table__
    with db.engine.begin() as connection:
        for row in connection.execute(table.select().limit(1000)).mappings():
            if not _allowed(row["file_path"]):
                current_app.logger.warning("Effacement de fichier #%s refusé : chemin hors stockage métier.", row["id"])
                continue
            try:
                Path(row["file_path"]).unlink(missing_ok=True)
            except OSError:
                current_app.logger.warning("Effacement de fichier #%s différé : fichier indisponible.", row["id"])
                continue
            connection.execute(table.delete().where(table.c.id == row["id"]))


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
