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
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def configure_environment(c):
    root = Path(c["data_root"])
    for name in ("instance", "uploads", "logs", "backups", "runtime"):
        (root / name).mkdir(exist_ok=True, parents=True)
    uri = (f"postgresql+psycopg://mcs:{quote(c['db_password'], safe='')}"
           f"@127.0.0.1:{int(c['db_port'])}/moncentresocial")
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
    root = configure_environment(c)
    from app import create_app
    from waitress import create_server
    app = create_app()
    trusted_hosts = ["127.0.0.1", "localhost", c["hostname"]]
    lan_ip = str(c.get("lan_ip") or "").strip()
    if lan_ip and lan_ip not in trusted_hosts:
        trusted_hosts.append(lan_ip)
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
    process.stdin.write(json.dumps(c).encode("utf-8"))
    process.stdin.close()
    return process


def write_caddy(c, root):
    # Données préalablement validées par l'assistant, échappées pour Caddyfile.
    hostname = c["hostname"]
    import re
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9.-]{0,252}", hostname):
        raise ValueError("Nom de serveur invalide.")
    storage = json.dumps(str(root / "runtime/tls").replace("\\", "/"), ensure_ascii=False)
    kiosk_port = int(c["kiosk_http_port"])
    web_port = int(c["web_port"])
    target = root / "runtime/Caddyfile"
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
            if c["network"]:
                config = write_caddy(c, root)
                proxy = subprocess.Popen([str(INSTALL / "caddy/caddy.exe"), "run", "--config", str(config), "--adapter", "caddyfile"],
                                         stdout=log, stderr=log, creationflags=CREATE_NO_WINDOW)
                children.append(proxy)
                certificate = state / "tls/pki/authorities/local/root.crt"
                for _ in range(60):
                    if proxy.poll() is not None:
                        raise RuntimeError("Le serveur HTTPS n'a pas démarré.")
                    if certificate.exists():
                        break
                    time.sleep(0.5)
                else:
                    raise RuntimeError("Le certificat du centre n'a pas pu être créé.")
                import socket
                import ssl
                tls_context = ssl.create_default_context(cafile=str(certificate))
                for _ in range(60):
                    try:
                        with socket.create_connection(("127.0.0.1", int(c["https_port"])), timeout=2) as connection:
                            with tls_context.wrap_socket(connection, server_hostname=c["hostname"]):
                                break
                    except OSError:
                        if proxy.poll() is not None:
                            raise RuntimeError("Le serveur HTTPS s'est arrêté.")
                        time.sleep(0.5)
                else:
                    raise RuntimeError("La connexion HTTPS n'a pas pu être vérifiée.")
                # Le second point d'entrée est volontairement HTTP et limité
                # par Caddy aux routes kiosque/static/branding/healthz. Cela
                # permet à un téléphone ou une tablette de fonctionner sans
                # installer l'autorité de certification privée de l'ERP.
                for _ in range(60):
                    try:
                        with urlopen(f"http://127.0.0.1:{int(c['kiosk_http_port'])}/healthz", timeout=2) as response:
                            if response.status == 200:
                                break
                    except OSError:
                        if proxy.poll() is not None:
                            raise RuntimeError("Le point d'accès kiosque n'a pas démarré.")
                        time.sleep(0.5)
                else:
                    raise RuntimeError("La connexion kiosque locale n'a pas pu être vérifiée.")
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


if __name__ == "__main__":
    config = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    try:
        {"--supervise": supervise, "--web": web, "--backup": backup}[sys.argv[1]](config)
    except Exception:
        # Le fichier de log est protégé par les ACL, mais ne conserve pas les secrets.
        import traceback
        error = traceback.format_exc()
        for key, value in config.items():
            if ("password" in key or key == "secret_key") and value:
                error = error.replace(str(value), "[confidentiel]").replace(quote(str(value), safe=""), "[confidentiel]")
        sys.stderr.write(error)
        sys.exit(1)
