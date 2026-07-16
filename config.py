import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DEFAULT_SECRET_KEY = "Uneapplicationdesuivibudgétairequisimplifielaviedetoutlemondenormalement"


def _charger_dotenv(chemin: str) -> None:
    """Charge le fichier .env (clé=valeur) dans os.environ.

    - Les variables déjà présentes dans l'environnement restent prioritaires.
    - Format supporté: lignes 'CLE=valeur', commentaires '#', lignes vides.
    Permet au service Windows (NSSM/waitress) et aux scripts d'hériter de
    la même configuration sans dépendance externe.
    """
    try:
        with open(chemin, encoding="utf-8") as fh:
            for ligne in fh:
                ligne = ligne.strip()
                if not ligne or ligne.startswith("#") or "=" not in ligne:
                    continue
                cle, _, valeur = ligne.partition("=")
                cle = cle.strip()
                valeur = valeur.strip().strip('"').strip("'")
                if cle and cle not in os.environ:
                    os.environ[cle] = valeur
    except OSError:
        pass


_charger_dotenv(os.path.join(BASE_DIR, ".env"))

# Dossier "data" optionnel (utile si tu pack en exe ou si tu veux stocker ailleurs)
DEFAULT_DATA_DIR = os.path.join(os.path.dirname(BASE_DIR), "data")  # ex: si ton exe est dans C:\AppGestion\app\
DATA_DIR = os.environ.get("APP_DATA_DIR", DEFAULT_DATA_DIR)
os.makedirs(DATA_DIR, exist_ok=True)


class Config:

    APP_NAME = os.environ.get("APP_NAME", "App Gestion")
    ORGANIZATION_NAME = os.environ.get("ORGANIZATION_NAME", "Votre structure")
    ERP_ENV = os.environ.get("ERP_ENV", "development").strip().lower()

    SECRET_KEY = os.environ.get(
        "SECRET_KEY",
        DEFAULT_SECRET_KEY,
    )
    MAX_CONTENT_LENGTH = int(os.environ.get("MAX_CONTENT_LENGTH", str(10 * 1024 * 1024)))
    WTF_CSRF_TIME_LIMIT = int(os.environ.get("WTF_CSRF_TIME_LIMIT", str(8 * 60 * 60)))

    # --- Cookies de session ---------------------------------------------------
    # HttpOnly: cookie jamais accessible en JavaScript (mitigation vol de session XSS).
    # SameSite=Lax: cookie non envoyé sur les requêtes cross-site (renfort CSRF).
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = os.environ.get("SESSION_COOKIE_SAMESITE", "Lax")
    # Secure: cookie transmis uniquement en HTTPS. Auto-activé si l'URL publique
    # est en https://, sinon surcharger via SESSION_COOKIE_SECURE=1.
    # Ne pas forcer sur un déploiement LAN en HTTP pur: la connexion casserait.
    _public_url = os.environ.get("ERP_PUBLIC_BASE_URL", "").strip().lower()
    SESSION_COOKIE_SECURE = os.environ.get(
        "SESSION_COOKIE_SECURE",
        "1" if _public_url.startswith("https://") else "0",
    ) in {"1", "true", "True", "yes", "YES"}
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SECURE = SESSION_COOKIE_SECURE

    # --- DB -----------------------------------------------------------------
    # Objectif :
    # - Priorité aux variables d'environnement (Postgres ou autre)
    # - Fallback SQLite local si rien n'est défini
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    INSTANCE_DIR = os.path.join(BASE_DIR, "instance")
    os.makedirs(INSTANCE_DIR, exist_ok=True)

    # Fallback SQLite unique (stable, version Flask standard)
    DB_PATH = os.path.join(INSTANCE_DIR, "database.db")
    _default_sqlite_uri = "sqlite:///" + DB_PATH.replace("\\", "/")

    # Priorité aux variables d'environnement (dans cet ordre)
    _db_url = (
        os.environ.get("SQLALCHEMY_DATABASE_URI")
        or os.environ.get("DATABASE_URL")
        or _default_sqlite_uri
    )

    # Compat anciens formats (Heroku-like)
    if _db_url.startswith("postgres://"):
        _db_url = _db_url.replace("postgres://", "postgresql://", 1)

    SQLALCHEMY_DATABASE_URI = _db_url

    # --- Domaines / constantes ----------------------------------------------
    SECTEURS = [
        "Numérique",
        "Familles",
        "EPE",
        "Santé Transition",
        "Insertion Sociale et Professionnelle",
        "Animation Globale",
    ]

    # SMTP optionnel (envoi des feuilles d'émargement)
    MAIL_HOST = os.environ.get("MAIL_HOST", "")
    MAIL_PORT = int(os.environ.get("MAIL_PORT", "587"))
    MAIL_USERNAME = os.environ.get("MAIL_USERNAME", "")
    MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD", "")
    MAIL_USE_TLS = os.environ.get("MAIL_USE_TLS", "1") in {"1", "true", "True", "yes", "YES"}
    MAIL_SENDER = os.environ.get("MAIL_SENDER", "")
    MAIL_TIMEOUT_SECONDS = float(os.environ.get("MAIL_TIMEOUT_SECONDS", "10"))

    # URL publique (LAN) de l'application, utilisée pour générer des QR codes.
    # Exemple : http://erp-cgb:8000 ou http://192.168.1.10:8000
    PUBLIC_BASE_URL = os.environ.get("ERP_PUBLIC_BASE_URL", "")

    # Façade kiosque « hors les murs » : nom d'hôte PUBLIC (tunnel Cloudflare,
    # Tailscale Funnel…) par lequel SEUL l'émargement kiosque doit répondre.
    # Quand une requête arrive par cet hôte, tout le reste de l'application
    # (connexion, données, admin) renvoie 403. Vide = désactivé.
    # Exemple : kiosque-cgb.exemple.fr (sans http:// ni port).
    KIOSK_PUBLIC_HOST = os.environ.get("KIOSK_PUBLIC_HOST", "").strip().lower()

    # Répertoire des uploads (pièces jointes, logos, exports visuels).
    # Par défaut: ./static/uploads (compatible Windows/Linux + hébergement web simple).
    APP_UPLOAD_DIR = os.environ.get("APP_UPLOAD_DIR", os.path.join(BASE_DIR, "static", "uploads"))

    # Migration DB industrialisée
    DB_AUTO_UPGRADE_ON_START = os.environ.get("DB_AUTO_UPGRADE_ON_START", "1") in {"1", "true", "True", "yes", "YES"}
    DB_ENABLE_LEGACY_SCHEMA_PATCH = os.environ.get("DB_ENABLE_LEGACY_SCHEMA_PATCH", "0") in {"1", "true", "True", "yes", "YES"}

    # Réinitialisation mot de passe
    PASSWORD_RESET_TOKEN_MAX_AGE_SECONDS = int(os.environ.get("PASSWORD_RESET_TOKEN_MAX_AGE_SECONDS", "3600"))
    # Affichage du lien de réinitialisation dans la page (dev local sans SMTP).
    # DOIT rester désactivé par défaut: un lien valide affiché à l'écran permet
    # de prendre le contrôle d'un compte. Activer uniquement en local via
    # PASSWORD_RESET_ALLOW_DEBUG_LINK=1.
    PASSWORD_RESET_ALLOW_DEBUG_LINK = os.environ.get("PASSWORD_RESET_ALLOW_DEBUG_LINK", "0") in {"1", "true", "True", "yes", "YES"}

    # --- Publication du programme public (FTPS vers un hébergement) ----------
    # Pousse la page programme.html vers un site (ex: cPanel) en connexion
    # SORTANTE chiffrée (FTP_TLS). Optionnel : tant que l'hôte et les
    # identifiants ne sont pas renseignés, la publication est désactivée.
    PROGRAMME_FTP_HOST = os.environ.get("PROGRAMME_FTP_HOST", "")
    PROGRAMME_FTP_PORT = int(os.environ.get("PROGRAMME_FTP_PORT", "21"))
    PROGRAMME_FTP_USER = os.environ.get("PROGRAMME_FTP_USER", "")
    PROGRAMME_FTP_PASSWORD = os.environ.get("PROGRAMME_FTP_PASSWORD", "")
    PROGRAMME_FTP_DIR = os.environ.get("PROGRAMME_FTP_DIR", "")
    PROGRAMME_FTP_FILENAME = os.environ.get("PROGRAMME_FTP_FILENAME", "programme.html")
    PROGRAMME_PUBLIC_URL = os.environ.get("PROGRAMME_PUBLIC_URL", "")

    # --- Portail des apprenants (intégration REST externe) -------------------
    # Le portail ne stocke aucune donnée personnelle : on lui transmet seulement
    # notre ID interne de stagiaire (externalId). Auth par en-tête x-api-token.
    # Optionnel : tant que l'URL et le jeton ne sont pas renseignés,
    # l'intégration reste désactivée.
    PORTAIL_BASE_URL = os.environ.get("PORTAIL_BASE_URL", "")
    PORTAIL_TOKEN = os.environ.get("PORTAIL_TOKEN", "")

    # --- Recup (integration REST vers le module RH) -------------------------
    # L'ERP tire un instantane annuel en lecture seule. Auth par x-api-token.
    RECUP_BASE_URL = os.environ.get("RECUP_BASE_URL", "")
    RECUP_TOKEN = os.environ.get("RECUP_TOKEN", "")

    # --- Cartographie / géocodage (Base Adresse Nationale) -------------------
    # Géocodage des adresses participants via l'API publique et gratuite de
    # l'État (https://api-adresse.data.gouv.fr) : aucune clé requise. On ne
    # transmet qu'une chaîne d'adresse (jamais l'identité). L'adresse reste
    # FACULTATIVE : sans adresse exploitable, le participant est « non localisé ».
    GEOCODAGE_BASE_URL = os.environ.get("GEOCODAGE_BASE_URL", "https://api-adresse.data.gouv.fr")
    GEOCODAGE_USER_AGENT = os.environ.get("GEOCODAGE_USER_AGENT", "AppGestion-ERP/1.0 (geocodage interne)")
    GEOCODAGE_BATCH = int(os.environ.get("GEOCODAGE_BATCH", "50"))
    # Fond de carte (tuiles). OpenStreetMap par défaut ; surchargeable pour
    # pointer un serveur de tuiles interne (utile en LAN sans Internet côté postes).
    CARTO_TILE_URL = os.environ.get(
        "CARTO_TILE_URL", "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
    )
    CARTO_TILE_ATTRIBUTION = os.environ.get(
        "CARTO_TILE_ATTRIBUTION", "© contributeurs OpenStreetMap"
    )

    # --- Sauvegardes ---------------------------------------------------------
    # Nombre de lots de sauvegarde conservés (rotation automatique) et seuil
    # d'alerte « aucune sauvegarde récente » (en jours).
    BACKUP_RETENTION_LOTS = int(os.environ.get("BACKUP_RETENTION_LOTS", "30"))
    BACKUP_ALERT_DAYS = int(os.environ.get("BACKUP_ALERT_DAYS", "2"))

    # --- Bénévolat -----------------------------------------------------------
    # Taux horaire de valorisation du bénévolat (contributions volontaires,
    # compte 87) — usuellement le SMIC horaire chargé.
    BENEVOLAT_TAUX_HORAIRE = float(os.environ.get("BENEVOLAT_TAUX_HORAIRE", "13"))
