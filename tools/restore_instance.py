#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import create_app
from app.services.sauvegarde import _restaurer_postgres, _restaurer_uploads, _restaurer_sqlite


def _restore_postgres(src_sql: Path, db_uri: str) -> None:
    # Même chemin que la restauration depuis l'interface : psql localisé
    # (PATH, PSQL_PATH, dossiers PostgreSQL), mot de passe transmis par
    # l'environnement et jamais sur la ligne de commande.
    _restaurer_postgres(src_sql, db_uri)


def _restore_uploads(zip_file: Path, upload_dir: Path) -> None:
    # Extraction durcie : refuse toute entrée qui écrirait hors du dossier cible.
    _restaurer_uploads(zip_file, upload_dir)


def _revenir_a_la_securite(base_securite: str, cause: str):
    """Remet le lot de sécurité (schéma courant) ; maintenance maintenue si
    c'est impossible."""
    from flask import Flask
    from config import Config
    from app.extensions import db
    from app.services import sauvegarde as svc
    secours = Flask("restore-offline-retour", instance_path=Config.INSTANCE_DIR)
    secours.config.from_object(Config)
    db.init_app(secours)
    with secours.app_context():
        try:
            db_file, uploads_file = svc._fichiers_de_base(base_securite, svc.dossier_sauvegardes())
            if db_file is None:
                raise RuntimeError("sauvegarde de sécurité introuvable")
            svc._remplacer_base_et_fichiers(db_file, uploads_file)
            db.session.remove()
            db.engine.dispose()
        except Exception as exc:  # noqa: BLE001
            svc._marquer_restauration({"etat": "echec", "securite": base_securite,
                                       "erreur": f"{cause} ; retour : {exc}", "outil": "ligne de commande"})
            raise RuntimeError(
                f"Restauration en échec ({cause}) et retour impossible ({exc}). Application en maintenance : "
                f"restaurez la sauvegarde de sécurité « {base_securite} » (Administration → Sauvegardes).") from exc
    application = create_app()
    with application.app_context():
        svc._lever_la_maintenance()
    return application


def main() -> int:
    parser = argparse.ArgumentParser(description="Restauration instance")
    parser.add_argument("--db", required=True, help="Chemin .db (SQLite) ou .sql (PostgreSQL)")
    parser.add_argument("--uploads", required=True, help="Chemin du zip uploads")
    args = parser.parse_args()

    db_path, up_path = Path(args.db), Path(args.uploads)
    if not db_path.is_file() or not up_path.is_file():
        raise RuntimeError("Fichiers de sauvegarde introuvables.")
    from config import Config
    db_uri = Config.SQLALCHEMY_DATABASE_URI
    sqlite = db_uri.startswith("sqlite:///") and db_path.suffix.lower() == ".db"
    postgres = db_uri.startswith("postgresql") and db_path.suffix.lower() == ".sql"
    if not (sqlite or postgres):
        raise RuntimeError("Incohérence entre type DB courant et fichier fourni.")

    # Aucun create_app ici : il migrerait et modifierait la destination AVANT
    # de découvrir une archive corrompue. Valider les fichiers en premier.
    from app.services.instance_archive import stage_archive, install_staged, remap_paths
    from app.services.sauvegarde import creer_sauvegarde, _verifier_db_sqlite
    from flask import Flask
    from app.extensions import db
    with tempfile.TemporaryDirectory(prefix="mcs-restore-cli-") as staging:
        manifest = stage_archive(up_path, staging)
        if sqlite and not _verifier_db_sqlite(db_path)[0]:
            raise RuntimeError("Base SQLite invalide : restauration annulée.")
        app = Flask("restore-offline", instance_path=Config.INSTANCE_DIR)
        app.config.from_object(Config)
        db.init_app(app)
        with app.app_context():
            # Effacements RGPD validés pas encore recopiés hors base : la base
            # courante est leur seule trace. Recopie d'abord, sinon refus.
            from sqlalchemy import inspect as _inspect
            if _inspect(db.engine).has_table("effacement_rgpd"):
                from app.services.registre_effacements import exporter_en_attente
                exporter_en_attente()
            securite = creer_sauvegarde()
            # Même garde que le parcours web : maintenance (marque sur disque)
            # jusqu'à la remise en service complète.
            from app.services.sauvegarde import _marquer_restauration
            _marquer_restauration({"etat": "en_cours", "lot": db_path.stem, "securite": securite["base"],
                                   "outil": "ligne de commande"})
            if sqlite:
                _restaurer_sqlite(db_path, db_uri)
            else:
                _restore_postgres(db_path, db_uri)
            roots = {"instance": app.instance_path, "uploads": app.config["APP_UPLOAD_DIR"]}
            install_staged(staging, roots)
            if manifest:
                with db.engine.begin() as connection:
                    remap_paths(connection, manifest["roots"], roots)
            db.session.remove()
            db.engine.dispose()
    # Les migrations portent maintenant sur la base restaurée (create_app les
    # exécute AVANT les traitements des registres). En cas d'échec : retour à
    # la sauvegarde de sécurité, comme le parcours web.
    try:
        restauree = create_app()   # migrations, puis réapplication des effacements confirmés
    except Exception as exc:  # noqa: BLE001
        restauree = _revenir_a_la_securite(securite["base"], str(exc))
        raise RuntimeError(
            f"La base restaurée n'a pas pu être mise à jour ({exc}). L'état d'avant la restauration a été remis "
            f"en place depuis la sauvegarde de sécurité « {securite['base']} » : rien n'a changé.") from exc
    with restauree.app_context():
        from app.services.sauvegarde import _lever_la_maintenance
        _lever_la_maintenance()
    copie = db_path.with_name(db_path.stem + "_registres.json")
    if copie.exists():
        # Copie des registres jointe au lot : fusionnée, jamais écrasante.
        from app.services.registres_transfert import fusionner, lire
        with restauree.app_context():
            fusionner(lire(copie))

    print("Restauration terminée. Lot de sécurité conservé : " + securite["base"])
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        print(f"ERREUR: {e}")
        raise SystemExit(1)
