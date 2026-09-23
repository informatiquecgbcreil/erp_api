"""Runtime privé Windows. Aucune dépendance au Python/PATH du poste.

Le service natif transmet la configuration déchiffrée par un tube anonyme.
Les secrets ne figurent jamais dans la ligne de commande ni dans le site web.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from urllib.parse import quote
from urllib.request import urlopen

INSTALL = Path(__file__).resolve().parents[1]
APP = INSTALL / "application"
if not APP.exists():  # Exécution depuis les sources pour la recette.
    APP = INSTALL
sys.path.insert(0, str(APP))
sys.path.insert(0, str(INSTALL))
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def configure_environment(c):
    root = Path(c["data_root"])
    for name in ("instance", "uploads", "logs", "backups", "runtime"):
        (root / name).mkdir(exist_ok=True, parents=True)
    database_name = c.get("db_name", "moncentresocial")
    import re
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", database_name):
        raise ValueError("Nom de base gérée invalide.")
    uri = (f"postgresql+psycopg://mcs:{quote(c['db_password'], safe='')}"
           f"@127.0.0.1:{int(c['db_port'])}/{database_name}")
    values = {
        "ERP_ENV": "production", "APP_NAME": "Mon Centre Social",
        "SECRET_KEY": c["secret_key"], "DATABASE_URL": uri, "SQLALCHEMY_DATABASE_URI": uri,
        "ORGANIZATION_NAME": c["organization"], "MCS_INSTANCE_DIR": str(root / "instance"),
        "APP_DATA_DIR": str(root), "APP_UPLOAD_DIR": str(root / "uploads"),
        "ERP_LOG_DIR": str(root / "logs"), "MCS_BACKUP_DIR": str(root / "backups"),
        "MCS_MODULES": ",".join(c["modules"]), "MCS_SETUP_DISABLED": "1",
        "ERP_PUBLIC_BASE_URL": c["url"],
        "KIOSK_PUBLIC_BASE_URL": c.get("kiosk_url") or c["url"],
        "SESSION_COOKIE_SECURE": "1" if c["network"] else "0",
        "PG_DUMP_PATH": str(INSTALL / "postgresql/bin/pg_dump.exe"),
        "PSQL_PATH": str(INSTALL / "postgresql/bin/psql.exe"),
        "DB_AUTO_UPGRADE_ON_START": "1", "DB_ENABLE_LEGACY_SCHEMA_PATCH": "0",
        "MAIL_HOST": c.get("smtp_host", ""), "MAIL_PORT": str(c.get("smtp_port", 587)),
        "MAIL_USERNAME": c.get("smtp_user", ""), "MAIL_PASSWORD": c.get("smtp_password", ""),
        "MAIL_SENDER": c.get("smtp_sender", ""), "MAIL_USE_TLS": "1",
        "PASSWORD_RESET_ALLOW_DEBUG_LINK": "0", "PYTHONDONTWRITEBYTECODE": "1",
    }
    os.environ.update(values)
    from desktop.migration import SETTINGS
    os.environ.update({k: str(v) for k, v in c.get("application_settings", {}).items() if k in SETTINGS})
    os.chdir(APP)
    return root


def run_tool(args, *, timeout=120):
    # pg_ctl peut laisser un descripteur hérité dans postgres : un PIPE ferait
    # attendre communicate() même après la sortie de pg_ctl sous Windows.
    import tempfile
    with tempfile.TemporaryFile() as output:
        result = subprocess.run([str(x) for x in args], stdout=output, stderr=subprocess.STDOUT,
                                timeout=timeout, creationflags=CREATE_NO_WINDOW)
    if result.returncode:
        # Les arguments et les sorties des outils SQL ne sont pas répercutés.
        raise RuntimeError(f"Échec de {Path(args[0]).name} (code {result.returncode}).")
    return result


def start_database(c, root):
    import psycopg
    from psycopg import sql
    pg = INSTALL / "postgresql/bin"
    data = root / "postgresql"
    data.mkdir(exist_ok=True)
    if not (data / "PG_VERSION").exists():
        pwfile = root / "runtime/init.password"
        try:
            pwfile.write_text(c["db_admin_password"], encoding="utf-8")
            run_tool([pg / "initdb.exe", "-D", data, "-U", "postgres", "-E", "UTF8",
                      "--locale=C", "--auth=scram-sha-256", f"--pwfile={pwfile}"])
        finally:
            pwfile.unlink(missing_ok=True)
        with (data / "postgresql.conf").open("a", encoding="utf-8") as f:
            f.write(f"\nlisten_addresses = '127.0.0.1'\nport = {int(c['db_port'])}\n"
                    "password_encryption = 'scram-sha-256'\nlogging_collector = on\n"
                    "log_rotation_age = '1d'\nlog_rotation_size = '10MB'\n"
                    "log_truncate_on_rotation = on\nlog_filename = 'postgresql-%a.log'\n")
    # Un service arrêté proprement laisse toujours le cluster arrêté.
    run_tool([pg / "pg_ctl.exe", "-D", data, "-l", root / "logs/postgresql-start.log", "-w", "-t", "60", "start"])
    if not (root / "runtime/provisioned").exists():
        with psycopg.connect(host="127.0.0.1", port=c["db_port"], user="postgres",
                             password=c["db_admin_password"], dbname="postgres", autocommit=True) as conn:
            if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname = 'mcs'").fetchone():
                conn.execute(sql.SQL("CREATE ROLE mcs LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD {}")
                             .format(sql.Literal(c["db_password"])))
            if not conn.execute("SELECT 1 FROM pg_database WHERE datname = 'moncentresocial'").fetchone():
                conn.execute("CREATE DATABASE moncentresocial OWNER mcs")
            conn.execute("REVOKE ALL ON DATABASE moncentresocial FROM PUBLIC")
        (root / "runtime/provisioned").write_text("1", encoding="ascii")


def bootstrap_account(app, c):
    from app.extensions import db
    from app.models import User, Role, InstanceSettings
    from app.services.modules import normalize
    with app.app_context():
        if User.query.first():
            return  # Une mise à jour ne change jamais les comptes ni leurs mots de passe.
        user = User(email=c["admin_email"], nom=c["admin_name"])
        user.set_password(c["admin_password"])
        user.roles.append(Role.query.filter_by(code="direction").one())
        db.session.add(user)
        settings = InstanceSettings.query.first() or InstanceSettings()
        settings.app_name = "Mon Centre Social"
        settings.organization_name = c["organization"]
        settings.public_base_url = c["url"]
        settings.enabled_modules_json = json.dumps(normalize(c["modules"]))
        db.session.add_all([user, settings])
        db.session.commit()


def web(c):
    if c.get("migration_source") and not c.get("migration_done"):
        raise RuntimeError("La reprise doit être terminée dans l'assistant avant le démarrage.")
    root = configure_environment(c)
    from app import create_app
    from waitress import create_server
    app = create_app()
    @app.before_request
    def pending_activation():
        from flask import request
        if (root / "private/activation.pending").exists() and request.path != "/healthz":
            return "Reprise en cours. Le centre sera disponible après validation de l'installation.", 503
    trusted_hosts = ["127.0.0.1", "localhost", c["hostname"]]
    lan_ip = str(c.get("lan_ip") or "").strip()
    if lan_ip and lan_ip not in trusted_hosts:
        trusted_hosts.append(lan_ip)
    from app.services.public_ingress import hostname
    public_host = hostname(app.config.get("KIOSK_PUBLIC_HOST") or "")
    if public_host:
        trusted_hosts.append(public_host)
    app.config["TRUSTED_HOSTS"] = trusted_hosts
    bootstrap_account(app, c)
    server = create_server(app, host="127.0.0.1", port=int(c["web_port"]), threads=12,
                           clear_untrusted_proxy_headers=True,
                           trusted_proxy="127.0.0.1" if c["network"] else None,
                           trusted_proxy_headers={"x-forwarded-proto", "x-forwarded-for", "x-forwarded-host"} if c["network"] else set())
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    (root / "runtime/web.ready").write_text("1", encoding="ascii")
    stop = root / "runtime/web.stop"
    try:
        while thread.is_alive() and not stop.exists():
            time.sleep(0.5)
    finally:
        server.close()


def backup(c):
    configure_environment(c)
    from app import create_app
    from app.services.sauvegarde import creer_sauvegarde, nettoyer_sauvegardes
    app = create_app()
    with app.app_context():
        creer_sauvegarde()
        nettoyer_sauvegardes()


def spawn(mode, c, log):
    process = subprocess.Popen([sys.executable, "-B", str(Path(__file__).resolve()), mode],
                               stdin=subprocess.PIPE, stdout=log, stderr=log,
                               creationflags=CREATE_NO_WINDOW)
    child_config = {k: v for k, v in c.items() if k not in {"db_admin_password", "migration_uri"}}
    process.stdin.write(json.dumps(child_config).encode("utf-8"))
    process.stdin.close()
    return process


def write_caddy(c, root):
    # Données préalablement validées par l'assistant, échappées pour Caddyfile.
    hostname = c["hostname"]
    import re
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9.-]{0,252}", hostname):
        raise ValueError("Nom de serveur invalide.")
    storage = json.dumps(str(root / "https/tls").replace("\\", "/"), ensure_ascii=False)
    kiosk_port = int(c["kiosk_http_port"])
    web_port = int(c["web_port"])
    target = root / "https/Caddyfile"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "{\n admin off\n auto_https disable_redirects\n skip_install_trust\n persist_config off\n"
        f" storage file_system {storage}\n}}\n"
        f"https://{hostname}:{int(c['https_port'])} {{\n tls internal\n"
        f" reverse_proxy 127.0.0.1:{web_port}\n}}\n"
        f":{kiosk_port} {{\n"
        " @kiosk path /kiosk /kiosk/* /static /static/* /media/branding /media/branding/* /healthz\n"
        " handle @kiosk {\n"
        f"  reverse_proxy 127.0.0.1:{web_port}\n"
        " }\n"
        " handle {\n"
        "  respond \"Accès réservé à l'émargement sur le réseau local.\" 403\n"
        " }\n"
        "}\n", encoding="utf-8")
    return target


def supervise(c):
    root = configure_environment(c)
    state = root / "runtime"
    for name in ("stop", "web.stop", "ready", "web.ready"):
        (state / name).unlink(missing_ok=True)
    logpath = root / "logs/runtime.log"
    if logpath.exists() and logpath.stat().st_size > 5_000_000:
        logpath.replace(logpath.with_suffix(".previous.log"))
    children = []
    database_started = False
    backup_task = None
    with logpath.open("ab", buffering=0) as log:
        try:
            start_database(c, root)
            database_started = True
            process = spawn("--web", c, log)
            children.append(process)
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("Le démarrage de l'application a échoué. Voir logs/runtime.log.")
                try:
                    with urlopen(f"http://127.0.0.1:{c['web_port']}/healthz", timeout=2) as response:
                        if response.status == 200 and (state / "web.ready").exists():
                            break
                except OSError:
                    pass
                time.sleep(0.5)
            else:
                raise RuntimeError("Le démarrage de l'application a dépassé le délai de 3 minutes.")
            (state / "ready").write_text("1", encoding="ascii")
            last_backup_day = ""
            while not (state / "stop").exists():
                if any(p.poll() is not None for p in children):
                    raise RuntimeError("Un composant s'est arrêté ; Windows relancera le service.")
                day = time.strftime("%Y-%m-%d")
                if day != last_backup_day:
                    last_backup_day = day
                    backup_task = spawn("--backup", c, log)
                    backup_deadline = time.monotonic() + 300
                if backup_task is not None:
                    if backup_task.poll() is not None:
                        if backup_task.returncode != 0:
                            log.write(b"Sauvegarde quotidienne en echec.\n")
                        backup_task = None
                    elif time.monotonic() > backup_deadline:
                        backup_task.kill(); backup_task.wait(); backup_task = None
                        log.write(b"Sauvegarde quotidienne : delai depasse.\n")
                time.sleep(1)
        finally:
            (state / "ready").unlink(missing_ok=True)
            (state / "web.stop").write_text("1", encoding="ascii")
            if backup_task is not None and backup_task.poll() is None:
                backup_task.terminate(); backup_task.wait(timeout=10)
            for p in reversed(children):
                try:
                    p.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    p.terminate()
                    p.wait(timeout=10)
            if database_started or (root / "postgresql/postmaster.pid").exists():
                run_tool([INSTALL / "postgresql/bin/pg_ctl.exe", "-D", root / "postgresql",
                          "-w", "-t", "30", "-m", "fast", "stop"], timeout=45)


def migrate_installation(c):
    """Prépare une base jetable différente à chaque tentative, avant le service."""
    import uuid
    import psycopg
    from psycopg import sql
    from desktop.migration import migrate
    root = configure_environment(c)
    started = False
    try:
        start_database(c, root)
        started = True
        completed = root / "runtime/reprise/complete.json"
        if completed.exists():
            report = json.loads(completed.read_text(encoding="utf-8"))
        else:
            c["db_name"] = "mcs_reprise_" + uuid.uuid4().hex[:16]
            with psycopg.connect(host="127.0.0.1", port=c["db_port"], user="postgres",
                                 password=c["db_admin_password"], dbname="postgres", autocommit=True) as conn:
                conn.execute(sql.SQL("CREATE DATABASE {} OWNER mcs").format(sql.Identifier(c["db_name"])))
                conn.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(c["db_name"])))
            configure_environment(c)
            report = migrate(c, sys.modules[__name__])
        (root / "private/migration-result.json").write_text(json.dumps(report), encoding="utf-8")
    finally:
        if started:
            run_tool([INSTALL / "postgresql/bin/pg_ctl.exe", "-D", root / "postgresql",
                      "-w", "-t", "30", "-m", "fast", "stop"], timeout=45)


if __name__ == "__main__":
    config = json.loads(sys.stdin.buffer.read().decode("utf-8-sig"))
    try:
        {"--supervise": supervise, "--web": web, "--backup": backup, "--migrate": migrate_installation, "--prepare-proxy": lambda c: write_caddy(c, Path(c["data_root"]))}[sys.argv[1]](config)
    except Exception as exc:
        # Le fichier de log est protégé par les ACL, mais ne conserve pas les secrets.
        if sys.argv[1] == "--migrate":
            from desktop.migration import MigrationError
            message = str(exc) if isinstance(exc, MigrationError) else "La reprise a échoué. Vérifiez les connexions, les dossiers et l'espace disponible."
            (Path(config["data_root"]) / "private/migration-error.txt").write_text(message, encoding="utf-8")
            raise SystemExit(1)
        import traceback
        error = traceback.format_exc()
        def secrets(values):
            for key, value in values.items():
                if isinstance(value, dict):
                    yield from secrets(value)
                elif value and any(word in key.lower() for word in ("password", "secret", "migration_uri")):
                    yield str(value)
        for value in secrets(config):
            error = error.replace(value, "[confidentiel]").replace(quote(value, safe=""), "[confidentiel]")
        sys.stderr.write(error)
        sys.exit(1)
