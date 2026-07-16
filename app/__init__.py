import logging
import os
from logging.handlers import RotatingFileHandler

from flask import Flask, url_for, request, redirect, session
from flask_login import current_user
from werkzeug.routing import BuildError

from sqlalchemy import text, inspect

from config import Config, DEFAULT_SECRET_KEY
from app.extensions import db, login_manager, csrf, migrate
from app.models import User
from app.services.dashboard_customization import load_dashboard_pref


def _configure_error_logging(app):
    """Journalise avertissements et erreurs dans un fichier avec rotation.

    En production (waitress sous Windows Server), les erreurs 500 sont
    invisibles sans cela : ce fichier est la boîte noire de l'application.
    Flask y écrit automatiquement la trace complète de chaque exception
    non gérée, avec l'URL concernée.

    Emplacement : ERP_LOG_DIR (variable d'environnement) ou, à défaut,
    le dossier instance/logs/. Rotation : 2 Mo x 10 fichiers.
    """
    log_dir = os.environ.get("ERP_LOG_DIR") or os.path.join(app.instance_path, "logs")
    os.makedirs(log_dir, exist_ok=True)

    handler = RotatingFileHandler(
        os.path.join(log_dir, "erreurs.log"),
        maxBytes=2_000_000,
        backupCount=10,
        encoding="utf-8",
    )
    handler.setLevel(logging.WARNING)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    )
    app.logger.addHandler(handler)


def create_app():
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(Config)

    # Instance folder (sqlite db, uploads, etc.)
    os.makedirs(app.instance_path, exist_ok=True)

    _configure_error_logging(app)

    default_secret = app.config.get("SECRET_KEY") == DEFAULT_SECRET_KEY
    is_prod_env = app.config.get("ERP_ENV") == "production"

    if default_secret and is_prod_env:
        raise RuntimeError(
            "SECRET_KEY par défaut interdite en production. Définis SECRET_KEY via variable d'environnement."
        )

    if default_secret and not app.debug:
        app.logger.warning(
            "SECRET_KEY par défaut détectée. Définis SECRET_KEY via variable d'environnement pour la prod."
        )

    # Extensions
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    csrf.init_app(app)
    login_manager.login_view = "auth.login"

    # ------------------------------------------------------------------
    # Jinja helper: safe_url_for
    # ------------------------------------------------------------------
    def safe_url_for(endpoint: str, fallback: str = "#", **values) -> str:
        try:
            return url_for(endpoint, **values)
        except BuildError:
            return fallback

    app.jinja_env.globals["safe_url_for"] = safe_url_for

    from app.services.storage import send_media_file

    @app.route("/media/<path:filename>")
    def media_file(filename):
        return send_media_file(filename, as_attachment=False)

    @app.route("/healthz")
    def healthz():
        return {"status": "ok"}, 200

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    # ------------------------------------------------------------------
    # Blueprints
    # ------------------------------------------------------------------
    from app.auth.routes import bp as auth_bp
    from app.main.routes import bp as main_bp
    from app.budget.routes import bp as budget_bp
    from app.previsionnel.routes import bp as previsionnel_bp
    from app.projets.routes import bp as projets_bp
    from app.admin.routes import bp as admin_bp
    from app.activite import bp as activite_bp
    from app.kiosk import bp as kiosk_bp
    from app.statsimpact.routes import bp as statsimpact_bp
    from app.bilans.routes import bp as bilans_bp
    from app.inventaire.routes import bp as inventaire_bp
    from app.inventaire_materiel.routes import bp as inventaire_materiel_bp
    from app.participants.routes import bp as participants_bp
    from app.launcher import bp as launcher_bp
    from app.pedagogie.routes import bp as pedagogie_bp
    from app.quartiers import bp as quartiers_bp
    from app.partenaires import bp as partenaires_bp
    from app.questionnaires import bp as questionnaires_bp
    from app.transitions import bp as transitions_bp
    from app.insertion.routes import bp as insertion_bp
    from app.setup import bp as setup_bp
    from app.aide import bp as aide_bp

    app.register_blueprint(setup_bp)
    app.register_blueprint(aide_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(budget_bp)
    app.register_blueprint(previsionnel_bp)
    app.register_blueprint(projets_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(activite_bp)
    app.register_blueprint(kiosk_bp)
    app.register_blueprint(statsimpact_bp)
    app.register_blueprint(bilans_bp)
    app.register_blueprint(inventaire_bp)
    app.register_blueprint(inventaire_materiel_bp)
    app.register_blueprint(participants_bp)
    app.register_blueprint(launcher_bp)
    app.register_blueprint(pedagogie_bp)
    app.register_blueprint(quartiers_bp)
    app.register_blueprint(partenaires_bp)
    app.register_blueprint(questionnaires_bp)
    app.register_blueprint(insertion_bp)
    app.register_blueprint(transitions_bp)

    @app.before_request
    def _facade_kiosque_publique():
        """Façade « hors les murs » : par l'hôte public (tunnel), SEUL le
        kiosque répond. Connexion, données et admin restent introuvables
        depuis l'extérieur — l'ERP ne sort pas du réseau local.

        Le test se fait sur le CHEMIN de l'URL (request.path), pas sur
        l'endpoint résolu : quand Flask doit encore rediriger vers la
        version avec « / » final (ex. /kiosk -> /kiosk/), l'endpoint
        n'est pas encore connu à ce stade et vaudrait None — un test sur
        l'endpoint bloquerait alors à tort le kiosque lui-même."""
        hote_public = (app.config.get("KIOSK_PUBLIC_HOST") or "").strip().lower()
        if not hote_public:
            return None
        hote_requete = (request.host or "").split(":", 1)[0].strip().lower()
        if hote_requete != hote_public:
            return None
        chemin = request.path or "/"
        if (
            chemin == "/kiosk" or chemin.startswith("/kiosk/")
            or chemin == "/static" or chemin.startswith("/static/")
            or chemin == "/healthz"
            # Flux calendrier iCal : lu par Google/Apple depuis internet, protégé
            # par un jeton secret dans l'URL (aucune donnée personnelle exposée).
            or chemin.startswith("/calendrier/")
        ):
            return None
        return (
            "<!doctype html><html lang='fr'><meta charset='utf-8'>"
            "<title>Émargement</title>"
            "<body style='font-family:sans-serif;max-width:32em;margin:15vh auto;text-align:center;'>"
            "<h1>🖐️ Cet accès sert uniquement à l'émargement</h1>"
            "<p>Ouvre le lien ou scanne le QR code transmis par l'équipe pour pointer les présences "
            "d'une séance. Le reste de l'application n'est accessible que depuis la structure.</p>"
            "</body></html>",
            403,
        )

    @app.before_request
    def _ensure_initial_setup():
        from app.models import User

        endpoint = (request.endpoint or "")
        if endpoint.startswith("static") or endpoint.startswith("setup.") or endpoint in {"media_file", "healthz"}:
            return None

        if User.query.count() == 0:
            return redirect(url_for("setup.wizard"))

        return None

    # ------------------------------------------------------------------
    # Purge RGPD quotidienne (anonymisation des participants inactifs).
    # Déclenchement paresseux: à la première requête du jour, sans
    # planificateur externe à configurer. Marqueur en mémoire pour que
    # le coût des autres requêtes soit nul.
    # ------------------------------------------------------------------
    _purge_marqueur = {"jour": None}

    @app.before_request
    def _purge_rgpd_quotidienne():
        from datetime import date as _date

        endpoint = (request.endpoint or "")
        if endpoint.startswith("static") or endpoint.startswith("setup.") or endpoint in {"media_file", "healthz"}:
            return None

        from app.services.purge_rgpd import purge_auto_active, purge_quotidienne_si_necessaire

        if not purge_auto_active():
            return None
        aujourd_hui = _date.today()
        if _purge_marqueur["jour"] == aujourd_hui:
            return None
        _purge_marqueur["jour"] = aujourd_hui
        purge_quotidienne_si_necessaire()
        return None

    # ------------------------------------------------------------------
    # Digest de notifications : au plus une fois par jour, même mécanique
    # que la purge (aucun planificateur externe). Inactif tant qu'aucun
    # type n'est coché dans Administration → Notifications.
    # ------------------------------------------------------------------
    _digest_marqueur = {"jour": None}

    @app.before_request
    def _digest_notifications_quotidien():
        from datetime import date as _date

        endpoint = (request.endpoint or "")
        if endpoint.startswith("static") or endpoint.startswith("setup.") or endpoint in {"media_file", "healthz"}:
            return None

        aujourd_hui = _date.today()
        if _digest_marqueur["jour"] == aujourd_hui:
            return None
        _digest_marqueur["jour"] = aujourd_hui

        from app.services.notifications import digest_quotidien_si_necessaire, notifications_actives

        if notifications_actives():
            digest_quotidien_si_necessaire()
        return None

    # ------------------------------------------------------------------
    # RBAC helpers
    # ------------------------------------------------------------------
    from app.rbac import bootstrap_rbac, can

    @app.context_processor
    def _inject_rbac_helpers():
        return {"can": can}

    @app.context_processor
    def _inject_aide_contextuelle():
        # Aide « Comprendre cette page » : pilotée par le registre central
        # app/aide/contenu.py, affichée automatiquement par layout.html.
        from app.aide.contenu import AIDE_PAGES

        return {"AIDE_PAGE": AIDE_PAGES.get(request.endpoint or "")}

    @app.context_processor
    def inject_app_identity():
        from app.services.instance_settings import resolve_identity
        from app.services.storage import media_url

        app_name, organization_name, app_logo_path, organization_logo_path = resolve_identity(
            app.config.get("APP_NAME", "App Gestion"),
            app.config.get("ORGANIZATION_NAME", "Votre structure"),
        )
        ui_mode = (session.get("ui_mode") or "").strip().lower()
        if current_user.is_authenticated:
            try:
                ui_mode = load_dashboard_pref(current_user).get("ui_mode") or ui_mode
            except Exception:
                pass
        if ui_mode not in {"simple", "expert"}:
            ui_mode = "simple"
        valid_device_modes = {"desktop", "mobile", "tablet", "kiosk"}
        requested_mode = (
            (request.args.get("device_mode") or "").strip().lower()
            or (session.get("ui_device_mode") or "").strip().lower()
            or (request.cookies.get("ui_device_mode") or "").strip().lower()
        )
        if requested_mode not in valid_device_modes:
            ua = (request.user_agent.string or "").lower()
            if "ipad" in ua or "tablet" in ua:
                requested_mode = "tablet"
            elif any(token in ua for token in ["iphone", "android", "mobile"]):
                requested_mode = "mobile"
            else:
                requested_mode = "desktop"
        endpoint = (request.endpoint or "").lower()
        if endpoint.startswith("kiosk."):
            requested_mode = "kiosk"
        guide_actif = None
        try:
            if current_user.is_authenticated:
                from app.services.guides import guide_actif_ctx
                guide_actif = guide_actif_ctx()
        except Exception:
            # Un guide cassé ne doit jamais casser une page.
            guide_actif = None
        return {
            "APP_NAME": app_name,
            "ORGANIZATION_NAME": organization_name,
            "APP_LOGO_URL": media_url(app_logo_path) if app_logo_path else None,
            "ORGANIZATION_LOGO_URL": media_url(organization_logo_path) if organization_logo_path else None,
            "media_url": media_url,
            "UI_MODE": ui_mode,
            "UI_SIMPLIFIED": ui_mode == "simple",
            "UI_DEVICE_MODE": requested_mode,
            "GUIDE_ACTIF": guide_actif,
        }

    # ------------------------------------------------------------------
    # ensure_schema : migrations légères SQLite / Postgres
    # ------------------------------------------------------------------
    def ensure_schema():
        dialect = db.engine.dialect.name
        insp = inspect(db.engine)

        def has_table(name):
            try:
                return insp.has_table(name)
            except Exception:
                return False

        def get_cols(table):
            if not has_table(table):
                return set()
            return {c["name"] for c in insp.get_columns(table)}

        def exec_sql(sql):
            db.session.execute(text(sql))

        def add_col(table, col, sql_sqlite, sql_pg):
            if col in get_cols(table):
                return
            if dialect == "sqlite":
                exec_sql(sql_sqlite)
            else:
                exec_sql(sql_pg)

        # --------------------------------------------------------------
        # 0) LEGACY : colonne user.role (OBLIGATOIRE pour le boot)
        # --------------------------------------------------------------
        try:
            add_col(
                "user",
                "role",
                'ALTER TABLE "user" ADD COLUMN role VARCHAR(50) NOT NULL DEFAULT "responsable_secteur"',
                'ALTER TABLE "user" ADD COLUMN IF NOT EXISTS role VARCHAR(50) NOT NULL DEFAULT \'responsable_secteur\'',
            )
            db.session.commit()
        except Exception:
            db.session.rollback()

        # --------------------------------------------------------------
        # 1) Exemple : colonne nature sur ligne_budget
        # --------------------------------------------------------------
        try:
            add_col(
                "ligne_budget",
                "nature",
                "ALTER TABLE ligne_budget ADD COLUMN nature VARCHAR(10) NOT NULL DEFAULT 'charge'",
                "ALTER TABLE ligne_budget ADD COLUMN IF NOT EXISTS nature VARCHAR(10) NOT NULL DEFAULT 'charge'",
            )
            db.session.commit()
        except Exception:
            db.session.rollback()

        # --------------------------------------------------------------
        # 2) Quartiers: description libre
        # --------------------------------------------------------------
        try:
            add_col(
                "quartier",
                "description",
                "ALTER TABLE quartier ADD COLUMN description TEXT",
                "ALTER TABLE quartier ADD COLUMN IF NOT EXISTS description TEXT",
            )
            db.session.commit()
        except Exception:
            db.session.rollback()

        # --------------------------------------------------------------
        # 3) Bilans lourds : médias + frise chronologique
        # --------------------------------------------------------------
        try:
            add_col(
                "bilan_lourd_narratif",
                "photos_json",
                "ALTER TABLE bilan_lourd_narratif ADD COLUMN photos_json TEXT",
                "ALTER TABLE bilan_lourd_narratif ADD COLUMN IF NOT EXISTS photos_json TEXT",
            )
            add_col(
                "bilan_lourd_narratif",
                "timeline_json",
                "ALTER TABLE bilan_lourd_narratif ADD COLUMN timeline_json TEXT",
                "ALTER TABLE bilan_lourd_narratif ADD COLUMN IF NOT EXISTS timeline_json TEXT",
            )
            db.session.commit()
        except Exception:
            db.session.rollback()

    # ------------------------------------------------------------------
    # INIT DB (mode migration industrialisée)
    # ------------------------------------------------------------------
    with app.app_context():
        if app.config.get("DB_AUTO_UPGRADE_ON_START", True):
            try:
                from flask_migrate import upgrade, stamp

                insp_pre = inspect(db.engine)
                tables = set(insp_pre.get_table_names())
                has_legacy_core = {"user", "role", "permission", "atelier_activite"}.issubset(tables)
                if has_legacy_core and "alembic_version" not in tables:
                    app.logger.warning(
                        "Base existante détectée sans alembic_version: stamp(head) avant upgrade."
                    )
                    stamp(revision="head")

                upgrade()
            except Exception:
                app.logger.exception("Echec db upgrade au démarrage")
                raise

        if app.config.get("DB_ENABLE_LEGACY_SCHEMA_PATCH", False):
            ensure_schema()

        insp = inspect(db.engine)
        if insp.has_table("user") and insp.has_table("role") and insp.has_table("permission"):
            bootstrap_rbac()

            from app.secteurs import bootstrap_secteurs_from_config
            bootstrap_secteurs_from_config()

        # str(url) masque déjà le mot de passe (***), mais on évite stdout :
        # une trace de log propre plutôt qu'un print non maîtrisé.
        app.logger.info("Base de données : %s (dialecte %s)", db.engine.url, db.engine.dialect.name)

        @app.context_processor
        def inject_secteurs():
            # Secteurs canoniques pour les formulaires.
            # Source: DB (Secteur) avec fallback config.
            from app.secteurs import get_secteur_labels
            return {"SECTEURS": get_secteur_labels(active_only=True)}


        return app
