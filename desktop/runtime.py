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


def postgres_bin(root, c=None):
    """Ne jamais ouvrir un cluster existant avec un autre moteur majeur."""
    version_file = Path(root) / "postgresql/PG_VERSION"
    major = version_file.read_text(encoding="ascii").strip() if version_file.exists() else str((c or {}).get("db_major", 18))
    if major not in {"17", "18"}:
        raise RuntimeError("Version du cluster local non prise en charge : " + major)
    return INSTALL / ("postgresql" if major == "17" else "postgresql18") / "bin"


def adresse_publique(c) -> str:
    """Adresse donnée aux collègues : le nom à certificat reconnu s'il est
    configuré (aucun certificat à installer), sinon l'adresse habituelle."""
    from desktop.certificat_public import nom_public
    if c.get("network") and nom_public(c):
        return f"https://{nom_public(c)}:{int(c['https_port'])}"
    return c["url"]


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
        "ERP_PUBLIC_BASE_URL": adresse_publique(c),
        "MCS_SOURCE_ARCHIVE": str(INSTALL / "sources-Mon-Centre-Social.zip"),
        "KIOSK_PUBLIC_BASE_URL": c.get("kiosk_url") or c["url"],
        "SESSION_COOKIE_SECURE": "1" if c["network"] else "0",
        "PG_DUMP_PATH": str(postgres_bin(root, c) / "pg_dump.exe"),
        "PSQL_PATH": str(postgres_bin(root, c) / "psql.exe"),
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
    pg = postgres_bin(root, c)
    data = root / "postgresql"
    data.mkdir(exist_ok=True)
    if not (data / "PG_VERSION").exists():
        # Le marqueur d'une tentative antérieure ne provisionne pas un nouveau cluster.
        (root / "runtime/provisioned").unlink(missing_ok=True)
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
                creer_base(conn, "moncentresocial")
            conn.execute("REVOKE ALL ON DATABASE moncentresocial FROM PUBLIC")
        (root / "runtime/provisioned").write_text("1", encoding="ascii")


def creer_base(conn, nom) -> None:
    """Base neuve triée à la française (« alain, Bruno, Élodie, Zoé ») par une
    collation ICU fr-FR (mineur de l'audit : cluster en locale C, tri
    « Bruno, Zoé, alain, Élodie »). Sans ICU dans le moteur : création
    classique, comme avant. Les bases existantes ne sont pas modifiées."""
    from psycopg import sql
    try:
        conn.execute(sql.SQL("CREATE DATABASE {} OWNER mcs TEMPLATE template0 ENCODING 'UTF8' "
                             "LOCALE_PROVIDER icu ICU_LOCALE 'fr-FR' LOCALE 'C'").format(sql.Identifier(nom)))
    except Exception:  # noqa: BLE001 — moteur sans ICU : tri binaire, fonctionnel
        conn.execute(sql.SQL("CREATE DATABASE {} OWNER mcs").format(sql.Identifier(nom)))


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


def protect_pending_activation(app, root):
    def pending_activation():
        from flask import request
        if (root / "private/activation.pending").exists() and request.path != "/healthz":
            return "Reprise en cours. Le centre sera disponible après validation de l'installation.", 503
    # Avant CSRF, connexion et tâches quotidiennes : même une requête refusée
    # ne doit pas provoquer de purge, de synchronisation ou d'envoi de mail.
    app.before_request_funcs.setdefault(None, []).insert(0, pending_activation)


def hotes_supplementaires(c) -> list[str]:
    """Noms ou adresses par lesquels des postes joignent AUSSI ce serveur
    (VPN, Tailscale, second réseau) : champ de l'assistant et ERP_LAN_HOSTS
    repris de l'ancienne installation. Ils entrent dans le certificat et dans
    les hôtes de confiance (mineur sécurité : accès par l'adresse Tailscale
    ou VPN refusé en 400)."""
    import ipaddress
    import re
    brut = list(c.get("hotes_supplementaires") or [])
    brut += str((c.get("application_settings") or {}).get("ERP_LAN_HOSTS") or "").split(",")
    from desktop.certificat_public import nom_public
    propres, public = [], nom_public(c)
    for valeur in brut:
        valeur = str(valeur).strip().lower()
        # Le nom à certificat reconnu a son propre bloc (write_caddy) et ne
        # concerne pas l'autorité du centre.
        if not valeur or valeur in propres or valeur == public:
            continue
        try:
            adresse = ipaddress.ip_address(valeur)
            if adresse.version != 4 or adresse.is_loopback or adresse.is_unspecified:
                continue
        except ValueError:
            if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{0,252}", valeur):
                continue
        propres.append(valeur)
    return propres


def hotes_de_confiance(c) -> list[str]:
    """Noms acceptés par l'application (sinon « Host … is not trusted », 400) :
    les mêmes que ceux du Caddyfile et du certificat. Un nom ajouté à la main
    dans le Caddyfile seul est refusé ici, et le Caddyfile est de toute façon
    réécrit au démarrage : passer par « Adresses d'accès au serveur »."""
    trusted_hosts = ["127.0.0.1", "localhost", str(c["hostname"]).lower()]
    lan_ip = str(c.get("lan_ip") or "").strip()
    if lan_ip and lan_ip not in trusted_hosts:
        trusted_hosts.append(lan_ip)
    for hote in hotes_supplementaires(c):
        if hote not in trusted_hosts:
            trusted_hosts.append(hote)
    from desktop.certificat_public import nom_public
    if nom_public(c) and nom_public(c) not in trusted_hosts:
        trusted_hosts.append(nom_public(c))
    return trusted_hosts


def web(c):
    if c.get("migration_source") and not c.get("migration_done"):
        raise RuntimeError("La reprise doit être terminée dans l'assistant avant le démarrage.")
    root = configure_environment(c)
    from app import create_app
    from waitress import create_server
    # Audit 6.6 : la mise à jour du schéma se fait dans create_app. Tant
    # qu'elle avance, ce témoin rafraîchi toutes les 10 s dit au superviseur
    # (et à l'installateur) de patienter au lieu de couper en pleine migration.
    temoin = root / "runtime/upgrading"
    fini = threading.Event()

    def _battement():
        while not fini.is_set():
            try:
                temoin.write_text(str(time.time()), encoding="ascii")
            except OSError:
                pass
            fini.wait(10)

    threading.Thread(target=_battement, daemon=True).start()
    try:
        app = create_app()
    finally:
        fini.set()
        temoin.unlink(missing_ok=True)
    protect_pending_activation(app, root)
    trusted_hosts = hotes_de_confiance(c)
    from app.services.public_ingress import hostname
    public_host = hostname(app.config.get("KIOSK_PUBLIC_HOST") or "")
    if public_host:
        trusted_hosts.append(public_host)
    app.config["TRUSTED_HOSTS"] = trusted_hosts
    bootstrap_account(app, c)
    server = create_server(app, host="127.0.0.1", port=int(c["web_port"]), threads=12,
                           clear_untrusted_proxy_headers=True,
                           trusted_proxy="127.0.0.1" if c["network"] else None,
                           # Deux relais au plus : Caddy, et tailscaled derrière le
                           # port kiosque (Funnel). L'adresse retenue reste celle
                           # que Caddy ou tailscaled ont écrite, jamais le visiteur.
                           trusted_proxy_count=2 if c["network"] else None,
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
    """Sauvegarde quotidienne (audit 6.5) : un seul lot par jour (pas de
    nouveau lot à chaque démarrage du service), vérifié, puis recopié vers
    les destinations hors serveur réglées dans l'administration. L'état est
    écrit dans runtime/sauvegarde-etat.json et affiché dans l'administration.
    Code de sortie non nul si le lot n'est pas sain ou si une copie échoue."""
    root = configure_environment(c)
    from datetime import date
    from app import create_app
    from app.services.sauvegarde import (copier_lot_hors_serveur, creer_sauvegarde, destinations_hors_serveur,
                                         enregistrer_etat_sauvegarde, lister_lots, nettoyer_sauvegardes, verifier_lot)
    app = create_app()
    with app.app_context():
        du_jour = [lot for lot in lister_lots() if lot["complet"] and lot["modifie"]
                   and lot["modifie"].date() == date.today()]
        if du_jour:
            base, cree = du_jour[0]["base"], False
        else:
            base, cree = creer_sauvegarde()["base"], True
            nettoyer_sauvegardes()
        verification = verifier_lot(base)
        copies = copier_lot_hors_serveur(base) if verification["ok"] and destinations_hors_serveur() else []
        etat = enregistrer_etat_sauvegarde(base, cree, verification, copies)
    if not etat["ok"]:
        raise SystemExit(1)


def taille_donnees(root) -> int:
    """Octets à sauvegarder (documents + instance + base), pour un délai
    proportionné : une grosse reprise ne doit pas être coupée à 5 minutes."""
    total = 0
    for nom in ("uploads", "instance", "postgresql"):
        for dossier, _sous, fichiers in os.walk(Path(root) / nom):
            for fichier in fichiers:
                try:
                    total += os.path.getsize(os.path.join(dossier, fichier))
                except OSError:
                    pass
    return total


def delai_sauvegarde(root) -> float:
    """15 min de base + 1 min par 100 Mo, au plus 8 h."""
    return min(900 + taille_donnees(root) / (100 * 1024 * 1024) * 60, 8 * 3600)


def tuer_arbre(process) -> None:
    """Arrête le processus ET ses enfants (pg_dump) : un kill seul laissait
    un pg_dump orphelin et un lot partiel."""
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)], capture_output=True,
                       creationflags=CREATE_NO_WINDOW)
    else:
        process.kill()
    try:
        process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        process.kill()


def postgresql_en_marche(c, root) -> bool:
    """pg_ctl status : 0 = démarré, 3 = arrêté (mineur Windows : un arrêt de
    PostgreSQL n'était pas vu, l'application répondait en erreur 500)."""
    try:
        resultat = subprocess.run([str(postgres_bin(root, c) / "pg_ctl.exe"), "-D", str(root / "postgresql"), "status"],
                                  capture_output=True, timeout=30, creationflags=CREATE_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return True  # Doute : on ne redémarre pas tout sur un outil qui ne répond pas.
    return resultat.returncode != 3


def temoin_frais(chemin, secondes=60) -> bool:
    try:
        return time.time() - chemin.stat().st_mtime < secondes
    except OSError:
        return False


def tourner_journal(chemin, taille=5_000_000) -> None:
    """Journaux jamais purgés (mineur Windows) : au-delà de 5 Mo, l'ancien
    devient .previous.log et un nouveau commence."""
    try:
        if chemin.exists() and chemin.stat().st_size > taille:
            chemin.replace(chemin.with_suffix(".previous.log"))
    except OSError:
        pass


def spawn(mode, c, log):
    process = subprocess.Popen([sys.executable, "-B", str(Path(__file__).resolve()), mode],
                               stdin=subprocess.PIPE, stdout=log, stderr=log,
                               creationflags=CREATE_NO_WINDOW)
    child_config = {k: v for k, v in c.items()
                    if k not in {"db_admin_password", "migration_uri", "migration_source_db"}}
    process.stdin.write(json.dumps(child_config).encode("utf-8"))
    process.stdin.close()
    return process


#: Plages d'adresses que l'autorité du centre peut certifier : réseaux privés,
#: Tailscale (100.64.0.0/10) et la boucle locale. Jamais une adresse publique
#: de l'Internet, sauf adresse supplémentaire déclarée dans l'assistant.
PLAGES_AUTORISEES = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "127.0.0.0/8")


def fichiers_autorite(root):
    dossier = Path(root) / "https" / "autorite"
    return dossier / "racine.crt", dossier / "racine.key"


def perimetre_autorite(c):
    """Noms et plages que l'autorité du centre doit pouvoir certifier : nom du
    serveur, autres noms déclarés, adresses privées, adresse du réseau local."""
    import ipaddress
    noms, adresses = {"localhost", str(c["hostname"]).lower()}, [ipaddress.ip_network(p) for p in PLAGES_AUTORISEES]
    for hote in hotes_supplementaires(c):
        try:
            adresse = ipaddress.ip_address(hote)
        except ValueError:
            noms.add(hote)
            continue
        if not any(adresse in plage for plage in adresses):
            adresses.append(ipaddress.ip_network(f"{adresse}/32"))
    lan = str(c.get("lan_ip") or "")
    try:
        adresse = ipaddress.ip_address(lan)
        if not any(adresse in plage for plage in adresses):
            adresses.append(ipaddress.ip_network(f"{adresse}/32"))
    except ValueError:
        pass
    return noms, adresses


def noms_hors_autorite(c, certificat) -> list[str]:
    """Noms ou adresses du centre que l'autorité contrainte existante ne peut
    PAS certifier (NameConstraints fixées à sa création). Un nom ajouté après
    l'installation (ex. « gestion.cgb ») y figure : le navigateur refuserait
    son certificat même si l'autorité est installée sur le poste."""
    from cryptography import x509
    racine = x509.load_pem_x509_certificate(Path(certificat).read_bytes())
    try:
        permis = racine.extensions.get_extension_for_class(x509.NameConstraints).value.permitted_subtrees or []
    except x509.ExtensionNotFound:
        return []
    noms_permis = [n.value.lower().lstrip(".") for n in permis if isinstance(n, x509.DNSName)]
    plages = [n.value for n in permis if isinstance(n, x509.IPAddress)]
    noms, adresses = perimetre_autorite(c)
    manquants = [n for n in sorted(noms)
                 if not any(n == p or n.endswith("." + p) for p in noms_permis)]
    for reseau in adresses:
        if not any(reseau.version == p.version and reseau.subnet_of(p) for p in plages):
            manquants.append(str(reseau.network_address) if reseau.prefixlen == reseau.max_prefixlen else str(reseau))
    return manquants


def _mettre_de_cote_autorite(root):
    """Remplacement consenti : l'ancienne autorité, le certificat
    intermédiaire et les certificats de site qu'elle a signés sont DÉPLACÉS
    (jamais effacés) dans https/autorite/remplacees/<date>. Caddy recrée alors
    l'intermédiaire et les certificats sous la nouvelle autorité ; l'icône
    Windows retire l'ancienne du magasin de confiance du serveur."""
    import shutil
    certificat, cle = fichiers_autorite(root)
    base = certificat.parent / "remplacees" / time.strftime("%Y%m%d-%H%M%S")
    archive, n = base, 1
    while archive.exists():
        archive, n = base.with_name(f"{base.name}_{n}"), n + 1
    archive.mkdir(parents=True)
    stockage = Path(root) / "https" / "tls"
    for source in (certificat, cle,
                   stockage / "pki" / "authorities" / "local" / "intermediate.crt",
                   stockage / "pki" / "authorities" / "local" / "intermediate.key",
                   stockage / "certificates" / "local"):
        if source.exists():
            shutil.move(str(source), str(archive / source.name))


def autorite_contrainte(c, root):
    """Audit 6.7 : l'autorité créée par Caddy (CA:TRUE, sans restriction)
    pouvait signer un certificat pour n'importe quel site, et elle est
    installée sur les postes. Une installation neuve reçoit désormais une
    autorité dont l'extension NameConstraints (critique) limite la signature
    au nom du serveur, aux autres noms déclarés et aux adresses privées :
    même volée, elle ne permet pas d'usurper un site de l'Internet.

    Une installation existante garde l'autorité déjà déployée sur ses postes
    (la remplacer couperait l'accès HTTPS de tous jusqu'au redéploiement) :
    renvoie None. Sinon (cert, clé), créés une fois puis réutilisés.

    Ses contraintes ne pouvant pas être étendues, un nom ajouté ensuite
    (« Adresses d'accès au serveur ») n'est couvert qu'en la REMPLAÇANT : cela
    n'arrive que sur consentement explicite (``renouveler_autorite``), car
    chaque poste devra réinstaller le certificat du centre."""
    certificat, cle = fichiers_autorite(root)
    if certificat.exists() and cle.exists():
        if not (c.get("renouveler_autorite") and noms_hors_autorite(c, certificat)):
            return certificat, cle
        _mettre_de_cote_autorite(root)
    else:
        ancienne = Path(root) / "https" / "tls" / "pki" / "authorities" / "local" / "root.crt"
        if ancienne.exists():
            return None
    import datetime
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    noms, adresses = perimetre_autorite(c)
    permis = [x509.DNSName(n) for n in sorted(noms)] + [x509.IPAddress(a) for a in adresses]
    cle_privee = ec.generate_private_key(ec.SECP256R1())
    sujet = x509.Name([x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Mon Centre Social"),
                       x509.NameAttribute(NameOID.COMMON_NAME, f"Mon Centre Social - autorite locale {c['hostname']}"[:64])])
    maintenant = datetime.datetime.now(datetime.timezone.utc)
    racine = (x509.CertificateBuilder().subject_name(sujet).issuer_name(sujet)
              .public_key(cle_privee.public_key()).serial_number(x509.random_serial_number())
              .not_valid_before(maintenant - datetime.timedelta(hours=1))
              .not_valid_after(maintenant + datetime.timedelta(days=3650))
              .add_extension(x509.BasicConstraints(ca=True, path_length=1), critical=True)
              .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True,
                                           content_commitment=False, key_encipherment=False, data_encipherment=False,
                                           key_agreement=False, encipher_only=False, decipher_only=False), critical=True)
              .add_extension(x509.NameConstraints(permitted_subtrees=permis, excluded_subtrees=None), critical=True)
              .add_extension(x509.SubjectKeyIdentifier.from_public_key(cle_privee.public_key()), critical=False)
              .sign(cle_privee, hashes.SHA256()))
    certificat.parent.mkdir(parents=True, exist_ok=True)
    temporaire = cle.with_suffix(".new")
    temporaire.write_bytes(cle_privee.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                    serialization.NoEncryption()))
    temporaire.replace(cle)
    certificat.write_bytes(racine.public_bytes(serialization.Encoding.PEM))
    return certificat, cle


def write_caddy(c, root):
    # Données préalablement validées par l'assistant, échappées pour Caddyfile.
    hostname = c["hostname"]
    import re
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9.-]{0,252}", hostname):
        raise ValueError("Nom de serveur invalide.")
    storage = json.dumps(str(root / "https/tls").replace("\\", "/"), ensure_ascii=False)
    https_port = int(c["https_port"])
    # Adresse IPv4 du réseau local dans le certificat : les postes (et la
    # vérification de l'assistant) passent par elle sans dépendre du DNS, qui
    # peut renvoyer plusieurs adresses (VPN, Tailscale, IPv6 de lien local).
    sites = [f"https://{hostname}:{https_port}"]
    for hote in hotes_supplementaires(c):
        site = f"https://{hote}:{https_port}"
        if site not in sites:
            sites.append(site)
    import ipaddress
    try:
        lan = ipaddress.IPv4Address(str(c.get("lan_ip") or ""))
        if not lan.is_loopback and not lan.is_unspecified and f"https://{lan}:{https_port}" not in sites:
            sites.append(f"https://{lan}:{https_port}")
    except ValueError:
        pass
    # Nom à certificat reconnu (Let's Encrypt) : bloc à part avec ses fichiers.
    # Certificat absent, expiré ou incohérent : le nom retombe sur l'autorité
    # du centre, l'accès n'est jamais coupé.
    from desktop.certificat_public import certificat_valide, nom_public
    bloc_public = ""
    if nom_public(c):
        valide = certificat_valide(c, root)
        site_public = f"https://{nom_public(c)}:{https_port}"
        if valide is None:
            if site_public not in sites:
                sites.append(site_public)
        else:
            chemins = [json.dumps(str(f).replace("\\", "/"), ensure_ascii=False) for f in valide[:2]]
            bloc_public = (f"{site_public} {{\n tls {chemins[0]} {chemins[1]}\n"
                           f" reverse_proxy 127.0.0.1:{int(c['web_port'])}\n}}\n")
    kiosk_port = int(c["kiosk_http_port"])
    web_port = int(c["web_port"])
    pki = ""
    autorite = autorite_contrainte(c, root)
    if autorite is not None:
        cert_pem = json.dumps(str(autorite[0]).replace("\\", "/"), ensure_ascii=False)
        cle_pem = json.dumps(str(autorite[1]).replace("\\", "/"), ensure_ascii=False)
        pki = f" pki {{\n  ca local {{\n   root {{\n    cert {cert_pem}\n    key {cle_pem}\n   }}\n  }}\n }}\n"
    target = root / "https/Caddyfile"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "{\n admin off\n auto_https disable_redirects\n skip_install_trust\n persist_config off\n"
        f" storage file_system {storage}\n"
        # HTTP/3 (UDP) était annoncé alors que le pare-feu n'ouvre que TCP.
        " servers {\n  protocols h1 h2\n }\n"
        + pki +
        # Tunnel Funnel : tailscaled (boucle locale) transmet l'adresse du
        # visiteur ; Caddy la garde au lieu de la remplacer par 127.0.0.1, et
        # les compteurs anti-abus du kiosque distinguent les visiteurs.
        f" servers :{kiosk_port} {{\n  trusted_proxies static 127.0.0.1/32 ::1/128\n }}\n}}\n"
        f"{', '.join(sites)} {{\n tls internal\n"
        f" reverse_proxy 127.0.0.1:{web_port}\n}}\n"
        + bloc_public +
        f":{kiosk_port} {{\n"
        " @kiosk path /kiosk /kiosk/* /static /static/* /media/branding /media/branding/* /healthz /sources\n"
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
    for name in ("stop", "web.stop", "ready", "web.ready", "database.ready"):
        (state / name).unlink(missing_ok=True)
    logpath = root / "logs/runtime.log"
    for journal in (logpath, root / "logs/postgresql-start.log"):
        tourner_journal(journal)
    children = []
    database_started = False
    backup_task = None
    with logpath.open("ab", buffering=0) as log:
        try:
            start_database(c, root)
            database_started = True
            (state / "database.ready").write_text("1", encoding="ascii")
            if c.get("migration_source") and not c.get("migration_done"):
                # PostgreSQL reste sous son identité de service, y compris à
                # l'import. L'assistant élevé ne lance jamais initdb/pg_ctl.
                # Aucun serveur HTTP ni travail quotidien avant la reprise.
                while not (state / "stop").exists():
                    time.sleep(0.5)
                return
            process = spawn("--web", c, log)
            children.append(process)
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline or temoin_frais(state / "upgrading"):
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
            prochain_controle_pg = time.monotonic() + 30
            while not (state / "stop").exists():
                if any(p.poll() is not None for p in children):
                    raise RuntimeError("Un composant s'est arrêté ; Windows relancera le service.")
                if time.monotonic() > prochain_controle_pg:
                    prochain_controle_pg = time.monotonic() + 30
                    if not postgresql_en_marche(c, root):
                        raise RuntimeError("PostgreSQL s'est arrêté ; Windows relancera le service.")
                day = time.strftime("%Y-%m-%d")
                if day != last_backup_day:
                    last_backup_day = day
                    backup_task = spawn("--backup", c, log)
                    backup_deadline = time.monotonic() + delai_sauvegarde(root)
                if backup_task is not None:
                    if backup_task.poll() is not None:
                        if backup_task.returncode != 0:
                            log.write(b"Sauvegarde quotidienne en echec (voir Administration > Sauvegardes).\n")
                        backup_task = None
                    elif time.monotonic() > backup_deadline:
                        tuer_arbre(backup_task); backup_task = None
                        log.write(b"Sauvegarde quotidienne : delai depasse, lot partiel non restaurable.\n")
                time.sleep(1)
        finally:
            (state / "ready").unlink(missing_ok=True)
            (state / "database.ready").unlink(missing_ok=True)
            (state / "web.stop").write_text("1", encoding="ascii")
            if backup_task is not None and backup_task.poll() is None:
                tuer_arbre(backup_task)
            for p in reversed(children):
                try:
                    p.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    p.terminate()
                    p.wait(timeout=10)
            if database_started or (root / "postgresql/postmaster.pid").exists():
                run_tool([postgres_bin(root, c) / "pg_ctl.exe", "-D", root / "postgresql",
                          "-w", "-t", "30", "-m", "fast", "stop"], timeout=45)


def _identifiant_installation(c) -> str:
    """Identifiant stable et non secret de CETTE installation."""
    import hashlib
    return hashlib.sha256(("mcs-installation:" + c["secret_key"]).encode()).hexdigest()[:16]


def _marque_tentative(c) -> str:
    return f"mcs-reprise {_identifiant_installation(c)} "


def _connexion_admin(c):
    import psycopg
    return psycopg.connect(host="127.0.0.1", port=c["db_port"], user="postgres",
                           password=c["db_admin_password"], dbname="postgres", autocommit=True)


def bases_de_tentative(conn, c) -> list[str]:
    """Bases créées par une tentative de reprise DE CETTE installation,
    reconnues à leur commentaire (jamais au seul préfixe du nom)."""
    lignes = conn.execute(
        "SELECT d.datname FROM pg_database d JOIN pg_shdescription s ON s.objoid = d.oid "
        "AND s.classoid = 'pg_database'::regclass WHERE s.description LIKE %s",
        (_marque_tentative(c) + "%",)).fetchall()
    return sorted(nom for (nom,) in lignes)


def nettoyer_tentatives(conn, c, garder: set[str]) -> list[str]:
    """Supprime les copies abandonnées : bases de tentative de cette
    installation, sauf celles à garder, et seulement si personne n'y est
    connecté (aucun DROP forcé). Rend les noms supprimés (audit 6.4)."""
    from psycopg import sql
    supprimees = []
    for nom in bases_de_tentative(conn, c):
        if nom in garder:
            continue
        occupee = conn.execute("SELECT 1 FROM pg_stat_activity WHERE datname = %s", (nom,)).fetchone()
        if occupee:
            continue
        conn.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(nom)))
        supprimees.append(nom)
    return supprimees


def migrate_installation(c):
    """Prépare une base distincte sur le service PostgreSQL, web encore fermé."""
    import time
    import uuid
    from psycopg import sql
    from desktop.migration import migrate
    root = configure_environment(c)
    completed = root / "private/reprise-complete.json"
    if completed.exists():
        report = json.loads(completed.read_text(encoding="utf-8"))
    else:
        with _connexion_admin(c) as conn:
            # Une nouvelle tentative remplace les copies précédentes (données
            # personnelles hors sauvegardes et hors purge) : elles sont
            # supprimées, pas accumulées.
            nettoyer_tentatives(conn, c, garder=set())
            c["db_name"] = "mcs_reprise_" + uuid.uuid4().hex[:16]
            creer_base(conn, c["db_name"])
            conn.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(c["db_name"])))
            conn.execute(sql.SQL("COMMENT ON DATABASE {} IS {}").format(
                sql.Identifier(c["db_name"]), sql.Literal(_marque_tentative(c) + time.strftime("%Y-%m-%d %H:%M:%S"))))
        configure_environment(c)
        report = migrate(c, sys.modules[__name__])
    (root / "private/migration-result.json").write_text(json.dumps(report), encoding="utf-8")


def verifier_source(c):
    """Activation : refuse une copie périmée (source modifiée depuis)."""
    configure_environment(c)
    from desktop.migration import verifier_source as verifier
    verifier(c)


def liberer_source(c):
    """Retour arrière : la base source redevient modifiable."""
    configure_environment(c)
    from desktop.migration import liberer_source as liberer, read_source
    liberer(read_source(c["migration_source"], c.get("migration_uri", ""), c.get("migration_source_db"))["url"])


def nettoyer_apres_activation(c):
    """Après la bascule : supprime les copies de tentatives abandonnées."""
    configure_environment(c)
    with _connexion_admin(c) as conn:
        return nettoyer_tentatives(conn, c, garder={c.get("db_name", "")})


def essai_restauration(c):
    """Restaure réellement le dernier lot dans une base jetable, vérifie,
    puis supprime la base (mode élevé : il faut le compte administrateur de
    PostgreSQL). Rapport dans private/essai-restauration.json."""
    import uuid
    from psycopg import sql
    root = configure_environment(c)
    from app import create_app
    from app.services.sauvegarde import lister_lots, restaurer_a_blanc
    app = create_app()
    nom = "mcs_essai_restauration_" + uuid.uuid4().hex[:12]
    with app.app_context():
        lots = [lot for lot in lister_lots() if lot["complet"]]
        if not lots:
            raise RuntimeError("Aucun lot complet à tester.")
        with _connexion_admin(c) as conn:
            creer_base(conn, nom)
        try:
            uri = (f"postgresql+psycopg://mcs:{quote(c['db_password'], safe='')}"
                   f"@127.0.0.1:{int(c['db_port'])}/{nom}")
            rapport = restaurer_a_blanc(lots[0]["base"], uri)
        finally:
            with _connexion_admin(c) as conn:
                conn.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(nom)))
    rapport["date"] = time.strftime("%Y-%m-%d %H:%M:%S")
    (root / "private/essai-restauration.json").write_text(json.dumps(rapport, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(rapport, ensure_ascii=False))
    if not rapport.get("ok"):
        raise SystemExit(1)


def verifier_autorite(c):
    """Code 3 : l'autorité contrainte ne couvre pas tous les noms déclarés
    (l'assistant « Adresses d'accès » demande alors l'accord de la
    direction pour la remplacer). Code 0 : rien à remplacer."""
    certificat, cle = fichiers_autorite(Path(c["data_root"]))
    if certificat.exists() and cle.exists():
        manquants = noms_hors_autorite(c, certificat)
        if manquants:
            print(", ".join(manquants))
            raise SystemExit(3)


def certificat_public(c):
    """Obtient ou renouvelle le certificat reconnu. Code 10 : nouveau
    certificat (le proxy doit relire le Caddyfile) ; code 1 : échec, message
    en clair sur la sortie (le certificat précédent reste en place)."""
    from desktop.certificat_public import ErreurCertificat, executer
    try:
        code = executer(c, forcer=bool(c.get("certificat_public_forcer")))
    except ErreurCertificat as exc:
        print(str(exc))
        raise SystemExit(1)
    if code:
        raise SystemExit(code)


MODES = {
    "--supervise": supervise, "--web": web, "--backup": backup, "--migrate": migrate_installation,
    "--prepare-proxy": lambda c: write_caddy(c, Path(c["data_root"])),
    "--verify-source": verifier_source, "--release-source": liberer_source,
    "--cleanup-attempts": nettoyer_apres_activation, "--restore-test": essai_restauration,
    "--verifier-autorite": verifier_autorite, "--certificat-public": certificat_public,
}

if __name__ == "__main__":
    config = json.loads(sys.stdin.buffer.read().decode("utf-8-sig"))
    try:
        MODES[sys.argv[1]](config)
    except Exception as exc:
        # Le fichier de log est protégé par les ACL, mais ne conserve pas les secrets.
        if sys.argv[1] in {"--migrate", "--verify-source", "--release-source"}:
            from desktop.migration import MigrationError
            message = str(exc) if isinstance(exc, MigrationError) else "La reprise a échoué. Vérifiez les connexions, les dossiers et l'espace disponible."
            etape = getattr(exc, "etape", "copie") if isinstance(exc, MigrationError) else "copie"
            (Path(config["data_root"]) / "private/migration-error.txt").write_text(message, encoding="utf-8")
            (Path(config["data_root"]) / "private/migration-error-etape.txt").write_text(etape, encoding="utf-8")
            raise SystemExit(3 if etape == "perimee" else 1)
        import traceback
        error = traceback.format_exc()
        def secrets(values):
            for key, value in values.items():
                if isinstance(value, dict):
                    yield from secrets(value)
                elif value and any(word in key.lower() for word in ("password", "secret", "migration_uri", "token", "key")):
                    yield str(value)
        for value in secrets(config):
            error = error.replace(value, "[confidentiel]").replace(quote(value, safe=""), "[confidentiel]")
        sys.stderr.write(error)
        sys.exit(1)
