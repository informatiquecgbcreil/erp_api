"""CLI de migration sur une base SQLite explicitement choisie, sans démarrage ERP.

Exécuter depuis le dépôt : python -m tools.historical_import --help
Les fichiers JSON contiennent des données personnelles : conserver en accès privé.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def _dump(path, result):
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, help="Chemin explicite vers une base SQLite de test/copie/staging")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init-test-db", help="Créer une base de test vide ; refuse un fichier existant")
    for name in ("analyze", "apply"):
        p = commands.add_parser(name)
        p.add_argument("--file", required=True)
        p.add_argument("--year", type=int)
        p.add_argument("--decisions", help="Décisions JSON uniquement, exportées ou rédigées après examen")
        p.add_argument("--output", required=True, help="Rapport JSON privé")
        p.add_argument("--markdown", help="Rapport lisible Markdown privé (analyse uniquement)")
        if name == "apply":
            p.add_argument("--preview", required=True, help="Rapport du dernier dry-run avec ces décisions")
    verify = commands.add_parser("verify")
    verify.add_argument("--batch", required=True)
    verify.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    database = Path(args.database).resolve()
    if args.command == "init-test-db" and database.exists():
        parser.error("La base existe déjà. Initialisation refusée.")
    if args.command != "init-test-db" and not database.is_file():
        parser.error("Base absente. Créer une base de test ou fournir une copie existante.")
    database.parent.mkdir(parents=True, exist_ok=True)
    # Toujours avant import de l'application/configuration ; aucune URL héritée utilisée.
    os.environ["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + database.as_posix()
    os.environ["APP_DATA_DIR"] = str(database.parent / "data")
    os.environ["DB_AUTO_UPGRADE_ON_START"] = "0"
    from flask import Flask
    from app.extensions import db
    from app.models import Secteur
    from app.ateliers.historical_import import analyze_import, apply_import, verify_batch
    app = Flask("historical_migration_cli")
    app.config.update(SQLALCHEMY_DATABASE_URI=os.environ["SQLALCHEMY_DATABASE_URI"],
                      SQLALCHEMY_TRACK_MODIFICATIONS=False)
    db.init_app(app)
    with app.app_context():
        if args.command == "init-test-db":
            from config import Config
            db.create_all()
            for i, label in enumerate(Config.SECTEURS):
                db.session.add(Secteur(code=f"test_{i}", label=label, is_active=True))
            db.session.commit()
            print("Base de test vide créée : " + str(database))
            return 0
        if args.command == "verify":
            result = verify_batch(args.batch)
        else:
            decisions = json.loads(Path(args.decisions).read_text(encoding="utf-8-sig")) if args.decisions else None
            if args.command == "analyze":
                result = analyze_import(args.file, decisions, args.year)
            else:
                preview = json.loads(Path(args.preview).read_text(encoding="utf-8-sig"))
                result = apply_import(args.file, decisions, args.year, expected_digest=preview["digest"])
        _dump(args.output, result)
        if args.command == "analyze" and args.markdown:
            from app.ateliers.historical_report import markdown_report
            output = Path(args.markdown).resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(markdown_report(result), encoding="utf-8")
        print(json.dumps(result.get("summary", result), ensure_ascii=False, indent=2))
        if "ready" in result:
            print("Import autorisable après validation : " + ("oui" if result["ready"] else "non, voir les cas à examiner"))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
