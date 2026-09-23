"""Reprise PostgreSQL d'une ancienne installation, sans écrire dans la source.

Exécutée avant le service web, sur une cible gérée et encore vide. Aucune
connexion source ni donnée nominative n'est inscrite dans le rapport.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import os
import subprocess

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

SETTINGS = {
    "MAIL_HOST", "MAIL_PORT", "MAIL_USERNAME", "MAIL_PASSWORD", "MAIL_SENDER",
    "MAIL_USE_TLS", "MAIL_TIMEOUT_SECONDS", "GOOGLE_OAUTH_CLIENT_ID",
    "GOOGLE_OAUTH_CLIENT_SECRET", "GOOGLE_OAUTH_REDIRECT_BASE",
    "BACKUP_OFFSITE_DIRS", "BACKUP_RETENTION_LOTS", "KIOSK_PUBLIC_HOST",
    "KIOSK_PUBLIC_BASE_URL", "ORGANIZATION_NAME", "MCS_MODULES",
    "PROGRAMME_FTP_HOST", "PROGRAMME_FTP_PORT", "PROGRAMME_FTP_USER",
    "PROGRAMME_FTP_PASSWORD", "PROGRAMME_FTP_DIR", "PROGRAMME_FTP_FILENAME",
    "PROGRAMME_PUBLIC_URL", "PORTAIL_BASE_URL", "PORTAIL_TOKEN",
    "RECUP_BASE_URL", "RECUP_TOKEN", "GEOCODAGE_BASE_URL", "GEOCODAGE_USER_AGENT",
    "GEOCODAGE_BATCH", "CARTO_TILE_URL", "CARTO_TILE_ATTRIBUTION",
    "BACKUP_ALERT_DAYS", "BACKUP_OFFSITE_RETENTION_LOTS", "BACKUP_OFFSITE_ALERT_DAYS",
    "BENEVOLAT_TAUX_HORAIRE", "LIBREOFFICE_PATH", "PURGE_INACTIFS_ANNEES",
    "PURGE_INACTIFS_AUTO", "LOGIN_MAX_ECHECS", "LOGIN_FENETRE_MINUTES",
    "LOGIN_JOURNAL_RETENTION_JOURS", "PASSWORD_RESET_TOKEN_MAX_AGE_SECONDS",
    "MAX_CONTENT_LENGTH", "WTF_CSRF_TIME_LIMIT",
}


class MigrationError(RuntimeError):
    pass


def read_source(folder, uri_override=""):
    root = Path(folder).resolve(strict=True)
    values = {}
    envfile = root / ".env"
    if envfile.is_file():
        for line in envfile.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    raw = uri_override or values.get("SQLALCHEMY_DATABASE_URI") or values.get("DATABASE_URL")
    if not raw:
        raise MigrationError("Connexion PostgreSQL absente : renseignez-la dans l'assistant.")
    try:
        url = make_url(raw.replace("postgres://", "postgresql://", 1))
        if url.get_backend_name() != "postgresql" or not url.database:
            raise ValueError()
        url = url.set(drivername="postgresql+psycopg")
    except Exception:
        raise MigrationError("La reprise automatique attend une connexion PostgreSQL valide.") from None

    def directory(key, default):
        path = Path(values.get(key) or default)
        return (path if path.is_absolute() else root / path).resolve()

    return {"url": url, "root": root,
            "roots": {"instance": directory("MCS_INSTANCE_DIR", "instance"),
                      "uploads": directory("APP_UPLOAD_DIR", "static/uploads")},
            "settings": {k: v for k, v in values.items() if k in SETTINGS}}


def validate_revision(connection, application):
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    tables = set(inspect(connection).get_table_names())
    if not {"user", "participant", "atelier_activite", "alembic_version"}.issubset(tables):
        raise MigrationError("Base non reconnue ou historique des migrations absent. La source est conservée ; une analyse de son schéma est nécessaire.")
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(application) / "migrations"))
    scripts = ScriptDirectory.from_config(cfg)
    revisions = list(connection.execute(text("SELECT version_num FROM alembic_version")).scalars())
    if not revisions:
        raise MigrationError("Historique des migrations vide : reprise automatique refusée.")
    for revision in revisions:
        try:
            if not scripts.get_revision(revision):
                raise ValueError()
        except Exception:
            raise MigrationError("Cette base provient d'une version inconnue ou plus récente du logiciel.") from None
    return revisions


def fingerprints(connection):
    """Comptage et empreinte de chaque table, indépendants de l'ordre des lignes."""
    result = {}
    quote = connection.dialect.identifier_preparer.quote
    for name in sorted(inspect(connection).get_table_names()):
        total, count = 0, 0
        for row in connection.execution_options(stream_results=True).execute(text("SELECT * FROM " + quote(name))).mappings():
            raw = json.dumps(dict(row), sort_keys=True, ensure_ascii=False, default=str).encode()
            total = (total + int.from_bytes(hashlib.sha256(raw).digest())) % (1 << 256)
            count += 1
        result[name] = {"rows": count, "sha256_sum": f"{total:064x}"}
    return result


def validate_document_paths(connection, source):
    """Refuse une bascule qui abandonnerait des documents référencés en base."""
    from app.services.instance_archive import PATH_COLUMNS
    from sqlalchemy import MetaData, Table, select
    inspector = inspect(connection)
    problems = []
    for name in inspector.get_table_names():
        if name == "pending_file_deletion":
            continue  # Un fichier déjà effacé peut attendre le retrait de son ticket.
        columns = PATH_COLUMNS & {c["name"] for c in inspector.get_columns(name)}
        if not columns:
            continue
        table = Table(name, MetaData(), autoload_with=connection)
        for column in sorted(columns):
            counts = {"missing": 0, "outside": 0}
            for raw in connection.execute(select(table.c[column]).distinct()).scalars():
                if not raw or (column.startswith("modele_docx_") and raw.startswith("builtin:")):
                    continue
                path = Path(raw)
                path = (path if path.is_absolute() else source["root"] / path).resolve()
                if not any(path.is_relative_to(root) for root in source["roots"].values()):
                    counts["outside"] += 1
                elif not path.is_file():
                    counts["missing"] += 1
            if any(counts.values()):
                problems.append(f"{name}.{column} : {counts['missing']} absent(s), {counts['outside']} hors dossiers métier")
    if problems:
        # Noms de tables et compteurs uniquement, aucun chemin nominatif.
        raise MigrationError("Reprise arrêtée avant copie : des documents référencés ne peuvent pas être conservés. "
                             "Vérifiez les dossiers instance/uploads de la source. " + "; ".join(problems))


def pg_tool(executable, url, *arguments):
    env = os.environ.copy()
    env.pop("PGOPTIONS", None)
    if url.password:
        env["PGPASSWORD"] = url.password
    else:
        env.pop("PGPASSWORD", None)
    public_uri = url.set(drivername="postgresql", password=None).render_as_string(hide_password=False)
    result = subprocess.run([str(executable), *map(str, arguments), "--dbname", public_uri],
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            timeout=3600, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode:
        raise MigrationError("Échec de " + Path(executable).stem + ". La source n'a pas été modifiée.")


def migrate(c, runtime):
    """Appel administrateur, ancien service arrêté, nouveau service non démarré."""
    source = read_source(c["migration_source"], c.get("migration_uri", ""))
    root = Path(c["data_root"])
    target_url = make_url(os.environ["SQLALCHEMY_DATABASE_URI"])
    if (source["url"].host, source["url"].port or 5432, source["url"].database) == (target_url.host, target_url.port or 5432, target_url.database):
        raise MigrationError("La base source et la destination doivent être distinctes.")
    for old in source["roots"].values():
        if old == root or old.is_relative_to(root) or root.is_relative_to(old):
            raise MigrationError("Les dossiers source et destination doivent être distincts.")
    work = root / "runtime" / "reprise"
    work.mkdir(parents=True, exist_ok=True)
    completed = work / "complete.json"
    if completed.exists():
        return json.loads(completed.read_text(encoding="utf-8"))
    target = create_engine(target_url, hide_parameters=True)
    src = create_engine(source["url"], hide_parameters=True,
                        connect_args={"connect_timeout": 10, "options": "-c default_transaction_read_only=on"})
    try:
        with target.connect() as connection:
            if inspect(connection).get_table_names():
                raise MigrationError("La destination contient déjà des données. Aucune base existante ne sera écrasée ; reprenez avec une destination vide.")
        from app.services.instance_archive import create_archive, stage_archive, install_staged, remap_paths
        pg = runtime.INSTALL / "postgresql" / "bin"
        extension = ".exe" if os.name == "nt" else ""
        with src.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
            with connection.begin():
                version = int(connection.execute(text("SHOW server_version_num")).scalar_one())
                if not 100000 <= version < 180000:
                    raise MigrationError("Cette distribution accepte les sources PostgreSQL 10 à 17. Utilisez une distribution adaptée à la version source.")
                revisions = validate_revision(connection, runtime.APP)
                validate_document_paths(connection, source)
                snapshot = connection.execute(text("SELECT pg_export_snapshot()")).scalar_one()
                before = fingerprints(connection)
                if not before["user"]["rows"]:
                    raise MigrationError("La base source ne contient aucun compte : utilisez une installation neuve.")
                pg_tool(pg / ("pg_dump" + extension), source["url"], "--format=custom", "--no-owner",
                        "--no-privileges", "--snapshot=" + snapshot, "--file", work / "source.dump")
        excluded = [source["roots"]["instance"] / "logs", source["root"] / "backups"]
        manifest = create_archive(work / "fichiers.zip", source["roots"], excluded)
        staging = work / "files"
        stage_archive(work / "fichiers.zip", staging)
        pg_tool(pg / ("pg_restore" + extension), target_url, "--single-transaction", "--exit-on-error",
                "--no-owner", "--no-privileges", work / "source.dump")
        with target.connect() as connection:
            if fingerprints(connection) != before:
                raise MigrationError("Les données restaurées ne correspondent pas à la source. Bascule refusée.")
            accounts = list(connection.execute(text('SELECT id, email, password_hash FROM "user" ORDER BY id')))
        new_roots = {"instance": root / "instance", "uploads": root / "uploads"}
        install_staged(staging, new_roots)
        with target.begin() as connection:
            remap_paths(connection, manifest["roots"], new_roots, source_directory=source["root"])
        # Config a été importée avec les chemins de la cible, jamais de la source.
        from app import create_app
        app = create_app()
        from app.extensions import db
        from app.models import InstanceSettings
        with app.app_context():
            with db.engine.connect() as connection:
                after_accounts = list(connection.execute(text('SELECT id, email, password_hash FROM "user" ORDER BY id')))
                if accounts != after_accounts:
                    raise MigrationError("Les comptes n'ont pas été conservés intégralement.")
                # Vérifie toutes les colonnes utilisées par le logiciel sans lire de données.
                actual = inspect(connection)
                for table in db.metadata.sorted_tables:
                    if not actual.has_table(table.name):
                        raise MigrationError("Schéma incomplet après migration : table " + table.name)
                    if {x.name for x in table.columns} - {x["name"] for x in actual.get_columns(table.name)}:
                        raise MigrationError("Schéma incomplet après migration : colonnes de " + table.name)
            settings = InstanceSettings.query.first()
            modules = json.loads(settings.enabled_modules_json) if settings and settings.enabled_modules_json else None
            if settings:
                settings.public_base_url = c["url"]
                db.session.commit()
            db.session.remove()
            db.engine.dispose()
        report = {"format": 1, "database": source["url"].database, "db_name": c["db_name"], "revisions_source": revisions,
                  "tables": {k: v["rows"] for k, v in before.items()}, "files": len(manifest["files"]),
                  "accounts_preserved": True, "modules": modules, "settings": source["settings"]}
        # Ce fichier contient des paramètres privés, dans le dossier protégé runtime.
        temp = completed.with_suffix(".new")
        temp.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
        temp.replace(completed)
        return report
    except MigrationError:
        raise
    except Exception:
        raise MigrationError("La reprise n'a pas abouti. La source est intacte ; vérifiez connexion, version, espace disque et droits sur les fichiers.") from None
    finally:
        src.dispose()
        target.dispose()
