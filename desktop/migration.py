"""Reprise PostgreSQL d'une ancienne installation, sans écrire dans la source.

Exécutée avant le service web, sur une cible gérée et encore vide. Aucune
connexion source ni donnée nominative n'est inscrite dans le rapport.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import os
import shutil
import subprocess

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

SETTINGS = {
    "MAIL_HOST", "MAIL_PORT", "MAIL_USERNAME", "MAIL_PASSWORD", "MAIL_SENDER",
    "MAIL_USE_TLS", "MAIL_TIMEOUT_SECONDS", "GOOGLE_OAUTH_CLIENT_ID",
    "GOOGLE_OAUTH_CLIENT_SECRET", "GOOGLE_OAUTH_REDIRECT_BASE",
    "BACKUP_OFFSITE_DIRS", "BACKUP_RETENTION_LOTS", "KIOSK_PUBLIC_HOST",
    "ERP_LAN_HOSTS", "ORGANIZATION_NAME", "MCS_MODULES",
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
    """Reprise refusée ou interrompue ; ``etape`` distingue copie, migration
    et activation pour ne pas tout recommencer inutilement."""

    def __init__(self, message, etape="copie"):
        super().__init__(message)
        self.etape = etape


def _url_champs(champs):
    """Connexion saisie champ par champ : aucun caractère du mot de passe
    (@ : / % …) ne peut être pris pour un séparateur (audit 4.3)."""
    from sqlalchemy.engine import URL
    base = (champs.get("database") or "").strip()
    if not base:
        return None
    try:
        port = int(champs.get("port") or 5432)
    except (TypeError, ValueError):
        raise MigrationError("Port PostgreSQL invalide.") from None
    return URL.create("postgresql+psycopg", username=(champs.get("user") or "").strip() or None,
                      password=champs.get("password") or None, host=(champs.get("host") or "").strip() or None,
                      port=port, database=base)


def read_source(folder, uri_override="", champs=None):
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
    url = _url_champs(champs or {})
    if url is None:
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
        hote = url.host or ""
        if any(c in hote for c in "@/ ") or (url.password and "@" in (url.database or "")):
            # Mot de passe contenant « @ » non encodé : l'adresse se lit de
            # travers. Rien n'est affiché (le fragment serait un secret).
            raise MigrationError("La connexion inscrite dans le fichier .env est mal formée (un caractère spécial du "
                                 "mot de passe n'est sans doute pas encodé). Saisissez-la dans les champs séparés de "
                                 "l'assistant.")

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


# Documents rattachés hors des dossiers instance/uploads : seuls ces formats
# sont recopiés (pièces jointes, signatures, feuilles d'émargement), jamais un
# fichier système désigné par une valeur inattendue de la base.
EXTERNAL_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".doc", ".docx", ".odt",
                     ".xls", ".xlsx", ".ods", ".csv", ".txt", ".rtf"}
EXTERNAL_MAX_BYTES = 50 * 1024**2
_MARKERS = {"instance": ("instance",), "uploads": ("uploads",)}


def _split(raw):
    import re
    return [p for p in re.split(r"[\\/]+", raw) if p]


def _relocate(raw, roots):
    """Fichier déplacé avec son dossier : retrouve « …/instance/signatures_tmp/x.png »
    sous le dossier instance actuel de la source, quel que soit l'ancien chemin."""
    parts = _split(raw)
    for label, markers in _MARKERS.items():
        root = roots.get(label)
        if not root:
            continue
        positions = [i for i, part in enumerate(parts) if part.casefold() in markers]
        for i in reversed(positions):
            tail = parts[i + 1:]
            if not tail or any(t in {".", ".."} for t in tail):
                continue
            candidate = Path(root).joinpath(*tail)
            if candidate.is_file():
                return label, candidate.resolve()
    return None


def chemin_reseau(raw) -> bool:
    """Chemin UNC ou d'espace de noms Windows : \\serveur\partage, //serveur,
    \\?\… Y accéder ouvrirait une connexion réseau avec les identifiants de
    l'administrateur qui lance la reprise (audit 4.2)."""
    texte = str(raw or "").strip()
    return texte.startswith(("\\\\", "//", "\\??\\")) or texte.startswith("\\\\?\\")


def _copiable(path):
    try:
        return (path.suffix.casefold() in EXTERNAL_SUFFIXES and path.is_file()
                and path.stat().st_size <= EXTERNAL_MAX_BYTES)
    except OSError:
        return False


def _autorise(path, autorises):
    """Le chemin RÉSOLU (liens symboliques, jonctions et redirections suivis)
    reste-t-il sous un emplacement autorisé ?"""
    try:
        reel = Path(os.path.realpath(path))
    except (OSError, ValueError):
        return False
    return any(reel == racine or reel.is_relative_to(racine) for racine in autorises)


def plan_documents(connection, source):
    """Inventaire des documents référencés en base, sans rien bloquer.

    - dans instance/uploads et présent : copié avec le dossier ;
    - ancien chemin, fichier retrouvé sous instance/uploads (dossier déplacé) :
      chemin corrigé ;
    - hors de ces dossiers mais lisible : recopié dans instance/documents_repris ;
    - introuvable nulle part : déjà perdu dans la source, référence conservée
      telle quelle et comptée dans le rapport.
    Aucun chemin n'est inscrit dans le rapport, seulement des compteurs.
    """
    from app.services.instance_archive import PATH_COLUMNS
    from sqlalchemy import MetaData, Table, select
    roots = {k: Path(v).resolve() for k, v in source["roots"].items()}
    # Seuls l'ancien dossier de l'application, ses dossiers de documents et
    # les dossiers désignés par l'opérateur peuvent être lus (audit 4.2).
    autorises = [Path(os.path.realpath(source["root"]))] + [Path(os.path.realpath(r)) for r in roots.values()]
    autorises += [Path(os.path.realpath(d)) for d in source.get("dossiers_autorises", []) if d]
    plan = {"relocated": {}, "external": {}, "missing": {}, "refuses": {}}
    inspector = inspect(connection)
    for name in inspector.get_table_names():
        if name == "pending_file_deletion":
            continue  # Un fichier déjà effacé peut attendre le retrait de son ticket.
        columns = PATH_COLUMNS & {c["name"] for c in inspector.get_columns(name)}
        if not columns:
            continue
        table = Table(name, MetaData(), autoload_with=connection)
        for column in sorted(columns):
            missing = refuses = 0
            for raw in connection.execute(select(table.c[column]).distinct()).scalars():
                if not raw or (column.startswith("modele_docx_") and raw.startswith("builtin:")):
                    continue
                if chemin_reseau(raw):
                    refuses += 1  # jamais d'accès réseau, pas même un test d'existence
                    continue
                path = Path(raw)
                try:
                    path = (path if path.is_absolute() else source["root"] / path).resolve()
                except (OSError, ValueError):
                    missing += 1
                    continue
                inside = next((label for label, root in roots.items() if path.is_relative_to(root)), None)
                if inside and path.is_file():
                    continue
                found = _relocate(raw, roots)
                if found and _autorise(found[1], autorises):
                    plan["relocated"][raw] = found
                elif not inside and not _autorise(path, autorises):
                    refuses += 1  # hors des emplacements autorisés : ni lu, ni copié
                elif not inside and _copiable(path):
                    plan["external"][raw] = Path(os.path.realpath(path))
                else:
                    missing += 1
            if missing:
                plan["missing"][f"{name}.{column}"] = missing
            if refuses:
                plan["refuses"][f"{name}.{column}"] = refuses
    return plan


def apply_document_plan(connection, plan, source_roots, new_roots):
    """Réécrit les chemins retrouvés et copie les documents externes."""
    from app.services.instance_archive import PATH_COLUMNS
    from sqlalchemy import MetaData, Table, update
    import shutil as _shutil
    mapping = {}
    for raw, (label, found) in plan["relocated"].items():
        relative = found.relative_to(Path(source_roots[label]).resolve())
        mapping[raw] = str(Path(new_roots[label]) / relative)
    if plan["external"]:
        target_dir = Path(new_roots["instance"]) / "documents_repris"
        target_dir.mkdir(parents=True, exist_ok=True)
        for number, (raw, path) in enumerate(sorted(plan["external"].items()), start=1):
            target = target_dir / f"{number:06d}_{path.name}"
            _shutil.copy2(path, target)
            mapping[raw] = str(target)
    if not mapping:
        return
    inspector = inspect(connection)
    for name in inspector.get_table_names():
        columns = PATH_COLUMNS & {c["name"] for c in inspector.get_columns(name)}
        if not columns:
            continue
        table = Table(name, MetaData(), autoload_with=connection)
        for column in columns:
            for old, new in mapping.items():
                connection.execute(update(table).where(table.c[column] == old).values({column: new}))


def reconcile_columns(engine, metadata):
    """Ajoute les colonnes que le logiciel attend et que la base n'a pas.

    Seules des AJOUTS sont faits, jamais de suppression ni de modification :
    colonne facultative ajoutée vide ; colonne obligatoire avec une valeur par
    défaut simple, ajoutée puis remplie avec cette valeur. Une colonne
    obligatoire sans valeur connue bloque la reprise (analyse nécessaire).
    Les contraintes de clé étrangère ne sont pas recréées. Rend la liste
    « table.colonne » ajoutée, pour le rapport.
    """
    from sqlalchemy import inspect as _inspect
    added = []
    with engine.begin() as connection:
        actual = _inspect(connection)
        preparer = connection.dialect.identifier_preparer
        for table in metadata.sorted_tables:
            if not actual.has_table(table.name):
                continue  # Signalé par le contrôle qui suit.
            present = {c["name"] for c in actual.get_columns(table.name)}
            for column in table.columns:
                if column.name in present:
                    continue
                default = column.default.arg if column.default is not None and column.default.is_scalar else None
                if not column.nullable and default is None and column.server_default is None:
                    raise MigrationError(f"Colonne obligatoire absente de l'ancienne base : {table.name}.{column.name}. "
                                         "Une analyse du schéma est nécessaire ; la source est intacte.")
                name = preparer.quote(column.name)
                table_name = preparer.format_table(table)
                sql_type = column.type.compile(dialect=connection.dialect)
                connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {name} {sql_type}"))
                if default is not None:
                    connection.execute(text(f"UPDATE {table_name} SET {name} = :v"), {"v": default})
                    if not column.nullable and connection.dialect.name == "postgresql":
                        connection.execute(text(f"ALTER TABLE {table_name} ALTER COLUMN {name} SET NOT NULL"))
                added.append(f"{table.name}.{column.name}")
    return added


def validate_document_paths(connection, source):
    """Compatibilité : inventaire des documents (ne bloque plus la reprise)."""
    return plan_documents(connection, source)


# Chiffres décimaux complets : un serveur PostgreSQL 10/11 renvoie sinon 15
# chiffres (0.3) quand le serveur cible en renvoie 17 (0.30000000000000004),
# et la comparaison des empreintes refuserait à tort la reprise.
FLOAT_OPTIONS = "-c extra_float_digits=3"


def tool_arguments(executable, url, *arguments):
    """Ligne de commande d'un outil PostgreSQL, SANS mot de passe.

    Le mot de passe passe par PGPASSWORD (environnement du seul processus
    enfant). Les arguments, eux, sont visibles de tout le poste (Gestionnaire
    des tâches, journaux d'audit Windows 4688, antivirus) : ne jamais les y
    mettre. Attention, ``URL.set(password=None)`` de SQLAlchemy signifie
    « inchangé » et conservait le mot de passe dans l'adresse.
    """
    public = url._replace(drivername="postgresql", password=None)
    return [str(executable), *map(str, arguments), "--dbname", public.render_as_string(hide_password=False)]


def _diagnostic(stderr, url, cote="source"):
    """Cause principale d'un échec, sans secret, pour orienter la DSI.

    ``cote`` : « source » (ancienne base) ou « destination » (base gérée) :
    un refus de droits sur la destination n'est pas un problème de source.
    """
    text_ = (stderr or b"").decode("utf-8", errors="replace")
    for secret in filter(None, [url.password]):
        text_ = text_.replace(secret, "[confidentiel]")
    lines = [line.strip() for line in text_.splitlines() if line.strip()]
    if not lines:
        return ""
    base = "de la base source" if cote == "source" else "de la base de destination"
    hints = (
        ("password authentication failed", f"Mot de passe {base} refusé."),
        ("server version mismatch", "Version de PostgreSQL source plus récente que les outils fournis."),
        ("has no equivalent in encoding", "Un caractère de la base source n'existe pas dans son encodage "
                                          "(WIN1252 le plus souvent) : faites corriger la donnée dans l'ancienne "
                                          "application, ou demandez une analyse."),
        ("invalid byte sequence", "Octets invalides dans l'encodage de la base source (WIN1252 le plus souvent) : "
                                  "une analyse de la donnée concernée est nécessaire."),
        ("permission denied", f"Droits insuffisants sur les tables {base}."),
        ("could not connect", f"Connexion {base} impossible (serveur arrêté, port ou nom d'hôte)."),
        ("does not exist", "Base, rôle ou extension introuvable."),
    )
    lowered = " ".join(lines).lower()
    for needle, message in hints:
        if needle in lowered:
            return message
    return "Détail technique : " + lines[-1][:300]


def clean_work(work):
    """Supprime dump, archive et dossier de préparation d'une reprise."""
    work = Path(work)
    for name in ("source.dump", "fichiers.zip"):
        (work / name).unlink(missing_ok=True)
    staging = work / "files"
    if staging.is_symlink():
        staging.unlink()
    elif staging.exists():
        shutil.rmtree(staging)


def _discard(work):
    try:
        clean_work(work)
    except OSError:
        pass  # Retenté au début de la tentative suivante.


def pg_tool(executable, url, *arguments, cote="source"):
    env = os.environ.copy()
    env.pop("PGOPTIONS", None)
    if url.password:
        env["PGPASSWORD"] = url.password
    else:
        env.pop("PGPASSWORD", None)
    result = subprocess.run(tool_arguments(executable, url, *arguments),
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            timeout=3600, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode:
        raise MigrationError("Échec de " + Path(executable).stem + ". La source n'a pas été modifiée. "
                             + _diagnostic(result.stderr, url, cote))


def validate_postgres_versions(source, target):
    source_major, target_major = source // 10000, target // 10000
    if not 10 <= source_major <= 18:
        raise MigrationError(f"PostgreSQL source {source_major} détecté. Cette distribution accepte les sources 10 à 18.")
    if target_major not in {17, 18} or source_major > target_major:
        raise MigrationError(f"Copie PostgreSQL {source_major} vers {target_major} refusée : la destination doit être de même version majeure ou plus récente.")


def _moteur_source(url, *, lecture_seule=True):
    # Réglage de SESSION, prioritaire sur celui de la base : la connexion qui
    # met au repos ou libère la source doit pouvoir écrire ce réglage même
    # quand la base est déjà en lecture seule.
    options = ("-c default_transaction_read_only=" + ("on " if lecture_seule else "off ")) + FLOAT_OPTIONS
    return create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 10, "options": options})


def _autres_connexions(connection):
    """Sessions clientes ouvertes sur la base source, hors la nôtre."""
    return list(connection.execute(text(
        "SELECT coalesce(nullif(application_name, ''), 'application sans nom'), count(*) FROM pg_stat_activity "
        "WHERE datname = current_database() AND pid <> pg_backend_pid() AND backend_type = 'client backend' "
        "GROUP BY 1 ORDER BY 2 DESC")))


def mettre_au_repos(url):
    """Mise au repos EXPLICITE et RÉVERSIBLE de la base source (audit C4).

    ``ALTER DATABASE … SET default_transaction_read_only = on`` : toute
    NOUVELLE connexion (ancienne application relancée par Windows, tâche
    planifiée, autre poste) ne peut plus écrire. Réversible par
    ``liberer_source`` (échec, abandon) ; conservée après la bascule pour
    qu'une ancienne application relancée par erreur ne puisse plus écrire.
    Exige d'être propriétaire de la base ; sinon la reprise continue avec
    le seul contrôle des connexions et celui de l'empreinte à l'activation.
    Rend (mise_au_repos_obtenue, connexions_restantes).
    """
    moteur = _moteur_source(url, lecture_seule=False)
    obtenue = False
    try:
        with moteur.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            nom = connection.execute(text("SELECT current_database()")).scalar_one()
            quote = connection.dialect.identifier_preparer.quote
            try:
                connection.execute(text(f"ALTER DATABASE {quote(nom)} SET default_transaction_read_only = on"))
                obtenue = True
            except Exception:
                obtenue = False
            return obtenue, _autres_connexions(connection)
    finally:
        moteur.dispose()


def liberer_source(url):
    """Rend la base source à nouveau modifiable (retour arrière)."""
    moteur = _moteur_source(url, lecture_seule=False)
    try:
        with moteur.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            nom = connection.execute(text("SELECT current_database()")).scalar_one()
            quote = connection.dialect.identifier_preparer.quote
            connection.execute(text(f"ALTER DATABASE {quote(nom)} RESET default_transaction_read_only"))
    finally:
        moteur.dispose()


def _refuser_si_connexions(connexions, au_repos):
    if not connexions:
        return
    total = sum(n for _, n in connexions)
    noms = ", ".join(f"{nom} ({n})" for nom, n in connexions[:5])
    repos = (" La base source a été mise en lecture seule pour les nouvelles connexions ; elle redevient "
             "modifiable si vous abandonnez la reprise.") if au_repos else ""
    raise MigrationError(f"{total} connexion(s) encore ouverte(s) sur la base source ({noms}). Arrêtez l'ancienne "
                         "application sous toutes ses formes (service, tâche planifiée, console, autre poste) puis "
                         "relancez l'assistant." + repos)


def empreinte_fichier(root):
    return Path(root) / "runtime" / "reprise" / "source-empreinte.json"


def verifier_source(c):
    """Avant d'ouvrir la copie : la source n'a-t-elle pas changé depuis ?

    Compare les effectifs et empreintes de toutes les tables à ceux relevés
    lors de la copie. Une différence (ancienne application relancée, saisie
    tardive) rend la copie périmée : l'activation est refusée. Une source
    injoignable ne permet pas de le vérifier : refus aussi.
    """
    chemin = empreinte_fichier(c["data_root"])
    if not chemin.exists():
        raise MigrationError("Empreinte de la copie introuvable : la reprise doit être recommencée.", "activation")
    attendu = json.loads(chemin.read_text(encoding="utf-8"))
    source = read_source(c["migration_source"], c.get("migration_uri", ""), c.get("migration_source_db"))
    moteur = _moteur_source(source["url"])
    try:
        with moteur.connect() as connection:
            actuel = fingerprints(connection)
    except MigrationError:
        raise
    except Exception:
        raise MigrationError("La base source est injoignable : impossible de vérifier que la copie est encore à "
                             "jour. Redémarrez son serveur PostgreSQL puis relancez l'assistant.", "activation") from None
    finally:
        moteur.dispose()
    if actuel != attendu:
        raise MigrationError("La base source a été modifiée depuis la copie (ancienne application relancée ou saisie "
                             "tardive) : la copie est périmée, la reprise va être recommencée.", "perimee")
    return True


def _journal_reprise(root, exc):
    """Trace technique nettoyée (type, pile) pour la DSI, jamais de secret."""
    import traceback
    try:
        chemin = Path(root) / "logs" / "migration.log"
        chemin.parent.mkdir(parents=True, exist_ok=True)
        pile = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        import re
        pile = re.sub(r"(postgresql(?:\+psycopg)?://[^:/@\s]*):[^@\s]*@", r"\1:[confidentiel]@", pile)
        pile = re.sub(r"(?i)(password|pwd|secret)\s*[=:]\s*\S+", r"\1=[confidentiel]", pile)
        with chemin.open("a", encoding="utf-8") as f:
            import time
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + type(exc).__name__ + "\n" + pile + "\n")
    except OSError:
        pass


def migrate(c, runtime):
    """Appel administrateur, ancien service arrêté, nouveau service non démarré."""
    source = read_source(c["migration_source"], c.get("migration_uri", ""), c.get("migration_source_db"))
    source["dossiers_autorises"] = c.get("migration_dossiers_autorises") or []
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
    target = create_engine(target_url, hide_parameters=True, connect_args={"options": FLOAT_OPTIONS})
    src = _moteur_source(source["url"])
    etape = "copie"
    au_repos = False
    try:
        with target.connect() as connection:
            if inspect(connection).get_table_names():
                raise MigrationError("La destination contient déjà des données. Aucune base existante ne sera écrasée ; reprenez avec une destination vide.")
        from app.services.instance_archive import create_archive, stage_archive, install_staged, remap_paths
        pg = runtime.postgres_bin(root, c)
        extension = ".exe" if os.name == "nt" else ""
        # Une tentative précédente ne doit rien laisser dans la copie de travail.
        clean_work(work)
        # Plus personne ne doit écrire dans la source entre la copie et
        # l'ouverture de la nouvelle application (audit C4).
        au_repos, connexions = mettre_au_repos(source["url"])
        _refuser_si_connexions(connexions, au_repos)
        with src.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
            with connection.begin():
                version = int(connection.execute(text("SHOW server_version_num")).scalar_one())
                with target.connect() as destination:
                    target_version = int(destination.execute(text("SHOW server_version_num")).scalar_one())
                validate_postgres_versions(version, target_version)
                revisions = validate_revision(connection, runtime.APP)
                documents = plan_documents(connection, source)
                # Dernier contrôle, dans la transaction du cliché : une session
                # ouverte juste avant la mise au repos pourrait encore écrire.
                _refuser_si_connexions(_autres_connexions(connection), au_repos)
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
                "--no-owner", "--no-privileges", work / "source.dump", cote="destination")
        with target.connect() as connection:
            if fingerprints(connection) != before:
                raise MigrationError("Les données restaurées ne correspondent pas à la source. Bascule refusée.")
            accounts = list(connection.execute(text('SELECT id, email, password_hash FROM "user" ORDER BY id')))
        new_roots = {"instance": root / "instance", "uploads": root / "uploads"}
        install_staged(staging, new_roots)
        with target.begin() as connection:
            remap_paths(connection, manifest["roots"], new_roots, source_directory=source["root"])
            apply_document_plan(connection, documents, source["roots"], new_roots)
        # Empreinte de la source au moment du cliché : l'activation la
        # recompare pour refuser une copie devenue périmée.
        empreinte_fichier(root).write_text(json.dumps(before), encoding="utf-8")

        etape = "migration"
        # Migrations du schéma sur la CIBLE seulement ; lectures de démarrage
        # différées jusqu'à ce que les colonnes manquantes soient complétées
        # (dérives de schéma, audit 4.1).
        os.environ["MCS_SKIP_BOOTSTRAP"] = "1"
        try:
            from app import create_app
            app = create_app()
        finally:
            os.environ.pop("MCS_SKIP_BOOTSTRAP", None)
        from app.extensions import db
        from app.models import InstanceSettings
        with app.app_context():
            # Bases anciennes dont le schéma a dérivé de l'historique Alembic
            # (colonnes ajoutées à la main ou par l'ancien correctif de schéma).
            completed_columns = reconcile_columns(db.engine, db.metadata)
            from app.rbac import bootstrap_rbac
            from app.secteurs import bootstrap_secteurs_from_config
            bootstrap_rbac()
            bootstrap_secteurs_from_config()
            with db.engine.connect() as connection:
                after_accounts = list(connection.execute(text('SELECT id, email, password_hash FROM "user" ORDER BY id')))
                if accounts != after_accounts:
                    raise MigrationError("Les comptes n'ont pas été conservés intégralement.", "migration")
                # Vérifie toutes les colonnes utilisées par le logiciel sans lire de données.
                actual = inspect(connection)
                for table in db.metadata.sorted_tables:
                    if not actual.has_table(table.name):
                        raise MigrationError("Schéma incomplet après migration : table " + table.name, "migration")
                    if {x.name for x in table.columns} - {x["name"] for x in actual.get_columns(table.name)}:
                        raise MigrationError("Schéma incomplet après migration : colonnes de " + table.name, "migration")
            settings = InstanceSettings.query.first()
            modules = json.loads(settings.enabled_modules_json) if settings and settings.enabled_modules_json else None
            organization = (settings.organization_name if settings else None) or source["settings"].get("ORGANIZATION_NAME") or "Structure reprise"
            source["settings"].setdefault("ORGANIZATION_NAME", organization)
            if settings:
                settings.public_base_url = c["url"]
                db.session.commit()
            db.session.remove()
            db.engine.dispose()
        report = {"format": 2, "database": source["url"].database, "db_name": c["db_name"], "revisions_source": revisions,
                  "tables": {k: v["rows"] for k, v in before.items()}, "files": len(manifest["files"]),
                  "accounts_preserved": True, "organization": organization,
                  "colonnes_completees": completed_columns,
                  "source_mise_au_repos": au_repos,
                  "documents": {"copies_avec_les_dossiers": len(manifest["files"]),
                                "retrouves_apres_deplacement": len(documents["relocated"]),
                                "recopies_hors_dossiers": len(documents["external"]),
                                "refuses_hors_emplacements_autorises": documents["refuses"],
                                "introuvables_dans_la_source": documents["missing"]},
                  "modules": modules, "settings": source["settings"],
                  "reglages_importes": sorted(source["settings"])}
        # Ce fichier contient des paramètres privés, dans le dossier protégé runtime.
        temp = completed.with_suffix(".new")
        temp.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
        temp.replace(completed)
        # Copies complètes de la base et des documents : inutiles une fois la
        # cible vérifiée (la source reste intacte), à ne pas laisser traîner hors
        # rotation des sauvegardes et hors effacement RGPD.
        clean_work(work)
        return report
    except MigrationError as exc:
        _journal_reprise(root, exc)
        _discard(work)
        if au_repos:
            _liberer_sans_erreur(source["url"])
        if exc.etape == "copie" and etape == "migration":
            exc.etape = "migration"
        raise
    except Exception as exc:
        _journal_reprise(root, exc)
        _discard(work)
        if au_repos:
            _liberer_sans_erreur(source["url"])
        from sqlalchemy.exc import OperationalError
        detail = ""
        if isinstance(exc, OperationalError):
            # Connexion refusée, mot de passe, base introuvable : message utile, sans secret.
            detail = " " + _diagnostic(str(getattr(exc, "orig", None) or exc).encode(), source["url"])
        if etape == "migration":
            raise MigrationError("La copie est faite, mais la mise à jour de son schéma a échoué (" + type(exc).__name__
                                 + "). La source est intacte ; le détail technique est dans logs/migration.log.",
                                 "migration") from None
        raise MigrationError("La reprise n'a pas abouti. La source est intacte ; vérifiez connexion, version, espace disque et droits sur les fichiers." + detail.rstrip()) from None
    finally:
        src.dispose()
        target.dispose()


def _liberer_sans_erreur(url):
    try:
        liberer_source(url)
    except Exception:
        pass  # Signalé par l'assistant lors du retour arrière.
