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


class _DatabasePrivacyFilter(logging.Filter):
    """Un diagnostic SQL ne doit pas recopier ses paramètres ou le DETAIL du pilote."""
    def filter(self, record):
        from sqlalchemy.exc import SQLAlchemyError
        import traceback
        error = record.exc_info[1] if record.exc_info else None
        seen = set()
        while error is not None and id(error) not in seen:
            seen.add(id(error))
            if isinstance(error, SQLAlchemyError):
                # hide_parameters ne masque pas le DETAIL PostgreSQL (« clé
                # email=... existe déjà »). Retirer aussi le texte de l'erreur,
                # y compris lorsqu'un appelant l'a recopié dans son message.
                record.msg = "Erreur de base de données (%s). Détails métier masqués."
                record.args = (type(error).__name__,)
                record.exc_text = "".join(
                    f'  File "{frame.filename}", line {frame.lineno}, in {frame.name}\n'
                    for frame in traceback.extract_tb(record.exc_info[2])
                )
                record.exc_info = None
                break
            error = error.__cause__ or error.__context__
        return True


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
    if not any(isinstance(f, _DatabasePrivacyFilter) for f in app.logger.filters):
        # Filtre au niveau du logger : protège également stderr, repris dans
        # logs/runtime.log par le service Windows.
        app.logger.addFilter(_DatabasePrivacyFilter())


from flask.sessions import SecureCookieSessionInterface


class _SessionKiosqueHttp(SecureCookieSessionInterface):
    """Cookie de session « HTTPS uniquement »… sauf pour le kiosque en HTTP.

    En réseau, la distribution Windows sert l'administration en HTTPS
    (SESSION_COOKIE_SECURE=1) et le kiosque des téléphones en simple HTTP sur
    l'adresse du réseau local. Un cookie marqué Secure y est refusé par le
    navigateur : les messages du kiosque (« Code invalide », « Tu es déjà
    émargé(e) », « Merci ! ») ne s'affichaient jamais. Le kiosque n'ouvre
    aucune session de compte : son cookie ne porte que ces messages. Il
    n'est donc non sécurisé que pour les pages du kiosque servies en HTTP ;
    l'administration garde un cookie sécurisé.
    """

    def get_cookie_name(self, app):
        from flask import has_request_context, request
        name = super().get_cookie_name(app)
        if has_request_context() and request.path.startswith("/kiosk"):
            return name + "_kiosk"
        return name

    def get_cookie_secure(self, app):
        securise = super().get_cookie_secure(app)
        if securise:
            from flask import has_request_context, request as _requete

            if has_request_context() and not _requete.is_secure and _requete.blueprint == "kiosk":
                return False
        return securise


def create_app():
    app = Flask(__name__, instance_relative_config=True, instance_path=Config.INSTANCE_DIR)
    app.config.from_object(Config)
    app.session_interface = _SessionKiosqueHttp()

    # Instance folder (sqlite db, uploads, etc.)
    os.makedirs(app.instance_path, exist_ok=True)

    _configure_error_logging(app)

    default_secret = app.config.get("SECRET_KEY") in {
        DEFAULT_SECRET_KEY, "change-me-local-dev", "remplacer-par-une-cle-tres-longue-et-aleatoire",
        "une-cle-longue-aleatoire",
    }
    is_prod_env = app.config.get("ERP_ENV") == "production"

    if is_prod_env and (default_secret or not app.config.get("SECRET_KEY") or len(app.config["SECRET_KEY"]) < 32):
        raise RuntimeError(
            "SECRET_KEY par défaut interdite en production. Définis SECRET_KEY via variable d'environnement."
        )

    if default_secret and not app.debug:
        app.logger.warning(
            "SECRET_KEY par défaut détectée. Définis SECRET_KEY via variable d'environnement pour la prod."
        )

    # Extensions
    db.init_app(app)
    # Dossier des migrations en chemin ABSOLU : Flask-Migrate le cherche sinon
    # par rapport au dossier courant, qui n'est pas celui du code quand
    # l'application est lancée par une tâche planifiée ou un service
    # (dossier courant C:\Windows\System32).
    migrate.init_app(app, db, directory=os.path.join(os.path.dirname(app.root_path), "migrations"))
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

    # Référentiel du genre. Posé en GLOBALE et non en context_processor :
    # une macro importée (« {% import "_genre.html" %} ») ne voit pas le
    # contexte de la page, mais voit les globales. Le libellé dépend de
    # l'âge (fille / femme, garçon / homme) : les gabarits ont besoin de la
    # fonction, pas d'une liste figée.
    from app.services.genre import (
        choix as _genre_choix,
        libelle as _genre_libelle,
        libelle_participant as _genre_de,
        normaliser as _genre_code,
    )

    app.jinja_env.globals["genre_choix"] = _genre_choix
    app.jinja_env.globals["genre_code"] = _genre_code
    app.jinja_env.globals["genre_libelle"] = _genre_libelle
    app.jinja_env.globals["genre_de"] = _genre_de

    from app.services.storage import send_media_file

    @app.route("/media/<path:filename>")
    def media_file(filename):
        from app.services.storage import authorize_public_media
        authorize_public_media(filename)
        return send_media_file(filename, as_attachment=False)

    @app.route("/healthz")
    def healthz():
        return {"status": "ok"}, 200

    @app.route("/sources")
    def source_archive():
        from flask import abort, send_file
        from pathlib import Path
        archive = app.config.get("SOURCE_ARCHIVE")
        if not archive or not Path(archive).is_file():
            abort(404)
        # Chemin fixé au déploiement, jamais issu d'un paramètre de requête.
        return send_file(archive, as_attachment=True, download_name="sources-Mon-Centre-Social.zip")

    @app.context_processor
    def _inject_source_offer():
        return {"SOURCE_URL": url_for("source_archive") if app.config.get("SOURCE_ARCHIVE")
                else "https://github.com/informatiquecgbcreil/erp_api"}

    @login_manager.user_loader
    def load_user(user_id):
        import hmac
        try:
            identifiant, _ = user_id.split(".", 1)
            user = db.session.get(User, int(identifiant))
        except (ValueError, AttributeError, TypeError):
            return None
        return user if user and user.is_active and hmac.compare_digest(user.get_id(), user_id) else None

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
    from app.veille_financements import bp as veille_bp
    from app.inscriptions_annuelles import bp as inscriptions_annuelles_bp
    from app.salles import bp as salles_bp

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
    app.register_blueprint(veille_bp)
    app.register_blueprint(inscriptions_annuelles_bp)
    app.register_blueprint(salles_bp)

    from app.services.modules import endpoint_module, module_enabled, can_manage_modules

    @app.before_request
    def _enforce_module_scope():
        from flask import abort
        import posixpath
        static_path = posixpath.normpath(str((request.view_args or {}).get("filename", "")).replace("\\", "/"))
        if request.endpoint == "static" and (static_path == "uploads" or static_path.startswith("uploads/")):
            abort(404)
        key = endpoint_module(request.endpoint or "")
        if key and not module_enabled(key):
            # Personne connectée : une page qui explique (outil non activé,
            # où l'activer) plutôt qu'une erreur « introuvable » déroutante.
            # Visiteur anonyme ou kiosque : rien à expliquer.
            if request.blueprint == "kiosk" or not current_user.is_authenticated:
                abort(404)
            from flask import render_template as _rendu
            from app.services.modules import CATALOG as _catalogue

            libelle, description = _catalogue.get(key, (key, ""))
            return _rendu("module_inactif.html", libelle=libelle, description=description,
                          peut_activer=can_manage_modules(current_user)), 403

    @app.after_request
    def _response_security(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        # Origine seule vers les autres sites (jamais les adresses internes,
        # qui peuvent contenir un identifiant de participant). « same-origin »
        # ne transmettait rien du tout, et les serveurs de tuiles
        # OpenStreetMap refusent alors les cartes (partenaires, quartiers).
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        if request.endpoint == "media_file":
            response.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
            response.headers["Cache-Control"] = "private, no-store"
        return response

    # Seules routes autorisées à renvoyer le navigateur vers un autre site.
    _REDIRECTIONS_EXTERNES_AUTORISEES = {"main.google_agenda_connecter"}

    def _hotes_autorises() -> set[str]:
        """Hôtes vers lesquels une redirection reste « interne »."""
        from urllib.parse import urlsplit as _us

        hotes = {(request.host or "").lower()}
        for cle in ("PUBLIC_BASE_URL", "KIOSK_PUBLIC_BASE_URL"):
            valeur = (app.config.get(cle) or "").strip()
            if valeur:
                hotes.add(_us(valeur).netloc.lower())
        kiosque = (app.config.get("KIOSK_PUBLIC_HOST") or "").strip().lower()
        if kiosque:
            hotes.add(kiosque)
        hotes.discard("")
        return hotes

    @app.after_request
    def _bloquer_redirections_ouvertes(reponse):
        """Filet de sécurité contre les redirections ouvertes.

        Une vingtaine d'écrans renvoient vers l'adresse reçue dans un champ
        « next » / « retour » ou dans l'en-tête Referer. Un lien piégé pouvait
        ainsi faire atterrir un utilisateur connecté sur un site imitant
        l'application (hameçonnage). Plutôt que de compter sur chaque écran,
        on vérifie ici toute redirection : si elle sort de l'application
        (autre hôte, « //site », « /\\site », « https:site », schéma exotique,
        caractère de contrôle), elle est remplacée par l'accueil.
        """
        if reponse.status_code not in (301, 302, 303, 307, 308):
            return reponse
        if (request.endpoint or "") in _REDIRECTIONS_EXTERNES_AUTORISEES:
            return reponse
        cible = (reponse.headers.get("Location") or "").strip()
        if not cible:
            return reponse
        from urllib.parse import urlsplit

        # Les navigateurs lisent « \\ » comme « / » : « /\\site.fr » == « //site.fr ».
        normalisee = cible.replace("\\", "/")
        if any(ord(c) < 33 or ord(c) == 127 for c in normalisee):
            externe = True
        else:
            morceaux = urlsplit(normalisee)
            hotes = _hotes_autorises()
            if morceaux.scheme:
                # « https:site.fr » devient « https:///site.fr » une fois encodé :
                # schéma sans hôte, que le navigateur complète vers site.fr.
                externe = (morceaux.scheme.lower() not in {"http", "https"}
                           or morceaux.netloc.lower() not in hotes)
            elif morceaux.netloc:
                externe = morceaux.netloc.lower() not in hotes
            else:
                # « ////site.fr » : chemin commençant par « // », lu comme un hôte.
                externe = morceaux.path.startswith("//")
        if externe:
            app.logger.warning("Redirection externe bloquée vers %r (page %s)", cible, request.path)
            reponse.headers["Location"] = "/"
        return reponse

    @app.context_processor
    def _inject_modules():
        return {"module_enabled": module_enabled,
                "can_manage_modules": can_manage_modules(current_user)}

    # Blocage automatique des salles : une séance ou un créneau d'agenda
    # qui porte une salle crée son occupation tout seul, quel que soit le
    # chemin d'écriture (saisie, grille hebdo, import Excel, reprise
    # historique…). Un point d'écoute unique plutôt que sept appels
    # dispersés qu'on finirait par oublier d'ajouter au huitième.
    from app.services.salles import enregistrer_synchronisation_auto
    enregistrer_synchronisation_auto()

    # Recherche accent-insensible : le « lower() » de SQLite ne connaît que
    # l'ASCII, si bien qu'« Étienne » ne se trouvait pas lui-même. On donne
    # à la base la même normalisation que celle appliquée à ce qu'on tape.
    from app.services.recherche_texte import installer as installer_recherche_texte
    installer_recherche_texte()

    @app.before_request
    def _memoriser_contexte_de_travail():
        """Retient l'année et le secteur consultés, pour les proposer par
        défaut à l'écran suivant.

        Placé ici plutôt que dans chaque route : un écran qui ne parle ni
        d'année ni de secteur laisse le contexte intact, et aucune route
        n'a à s'en préoccuper. Le paramètre explicite gagne toujours — on
        ne fait que remplacer « repartir de l'année courante » par
        « reprendre là où on en était ».
        """
        from app.services.contexte import memoriser_depuis_la_requete

        try:
            memoriser_depuis_la_requete()
        except Exception:  # noqa: BLE001 - un confort ne casse jamais une page
            app.logger.debug("Contexte de travail : mémorisation ignorée", exc_info=True)
        return None

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
        from app.services.public_ingress import is_public_ingress
        if not is_public_ingress():
            return None
        chemin = request.path or "/"
        if (
            chemin == "/kiosk" or chemin.startswith("/kiosk/")
            or chemin == "/static" or chemin.startswith("/static/")
            or chemin in {"/healthz", "/sources"}
            or chemin.startswith("/media/branding/")
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
        if endpoint.startswith("static") or endpoint.startswith("setup.") or endpoint in {"media_file", "healthz", "source_archive"}:
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
        if endpoint.startswith("admin.historical_"):
            return None  # Une prévisualisation de migration ne déclenche aucune purge.
        if endpoint.startswith("static") or endpoint.startswith("setup.") or endpoint in {"media_file", "healthz", "source_archive"}:
            return None

        from app.services.purge_rgpd import purge_auto_active, purge_quotidienne_si_necessaire

        if not purge_auto_active():
            return None
        from app.utils.dates import utcnow
        aujourd_hui = utcnow().date()
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
        if endpoint.startswith("admin.historical_"):
            return None
        if endpoint.startswith("static") or endpoint.startswith("setup.") or endpoint in {"media_file", "healthz", "source_archive"}:
            return None

        from app.utils.dates import utcnow
        aujourd_hui = utcnow().date()
        if _digest_marqueur["jour"] == aujourd_hui:
            return None
        _digest_marqueur["jour"] = aujourd_hui

        from app.services.notifications import digest_quotidien_si_necessaire, notifications_actives

        if notifications_actives():
            digest_quotidien_si_necessaire()
        return None

    # ------------------------------------------------------------------
    # Veille financements : collecte automatique tous les 3 jours, même
    # mécanique paresseuse que la purge RGPD (aucun planificateur externe).
    # Le marqueur mémoire limite le test à une fois par jour et par
    # processus ; le service vérifie ensuite en base si les 3 jours sont
    # réellement écoulés, puis collecte dans un thread d'arrière-plan pour
    # ne jamais ralentir la requête en cours.
    # ------------------------------------------------------------------
    _veille_marqueur = {"jour": None}

    @app.before_request
    def _veille_financements_periodique():
        from datetime import date as _date

        endpoint = (request.endpoint or "")
        if endpoint.startswith("admin.historical_"):
            return None
        if endpoint.startswith("static") or endpoint.startswith("setup.") or endpoint in {"media_file", "healthz", "source_archive"}:
            return None

        # Jamais de collecte réseau pendant les tests, ni si désactivée.
        if app.config.get("TESTING") or not app.config.get("VEILLE_AUTO", True) or not module_enabled("finances"):
            return None

        aujourd_hui = _date.today()
        if _veille_marqueur["jour"] == aujourd_hui:
            return None
        _veille_marqueur["jour"] = aujourd_hui

        from app.services.veille_financements import lancer_rafraichissement_arriere_plan

        try:
            lancer_rafraichissement_arriere_plan(app)
        except Exception:
            app.logger.exception("Veille financements : échec du déclenchement")
        return None

    # ------------------------------------------------------------------
    # Google Agenda : synchro push temps réel. Les écouteurs SQLAlchemy
    # détectent séances/présences modifiées et poussent en arrière-plan
    # dès le commit ; un rattrapage périodique (même mécanique paresseuse
    # que la veille) resynchronise tout si quelque chose a été manqué.
    # Coût nul tant que GOOGLE_OAUTH_CLIENT_ID/SECRET ne sont pas définis.
    # ------------------------------------------------------------------
    from app.services.google_agenda import enregistrer_ecouteurs as _ga_ecouteurs

    _ga_ecouteurs(app)
    _google_agenda_marqueur = {"heure": None}

    @app.before_request
    def _google_agenda_rattrapage_periodique():
        from datetime import datetime as _datetime

        endpoint = (request.endpoint or "")
        if endpoint.startswith("admin.historical_"):
            return None
        if endpoint.startswith("static") or endpoint.startswith("setup.") or endpoint in {"media_file", "healthz", "source_archive"}:
            return None
        if app.config.get("TESTING") or not app.config.get("GOOGLE_AGENDA_AUTO", True) or not module_enabled("presences"):
            return None

        heure_courante = _datetime.now().strftime("%Y-%m-%d %H")
        if _google_agenda_marqueur["heure"] == heure_courante:
            return None
        _google_agenda_marqueur["heure"] = heure_courante

        from app.services.google_agenda import lancer_rattrapage_arriere_plan

        try:
            lancer_rattrapage_arriere_plan(app)
        except Exception:
            app.logger.exception("Google Agenda : échec du déclenchement du rattrapage")
        return None

    # ------------------------------------------------------------------
    # RBAC helpers
    # ------------------------------------------------------------------
    from app.rbac import bootstrap_rbac, can

    @app.context_processor
    def _inject_rbac_helpers():
        return {"can": can}

    @app.context_processor
    def _inject_salles():
        """Liste des salles occupables, pour les listes déroulantes.

        Exposée comme fonction plutôt que comme valeur : elle n'est
        interrogée que par les gabarits qui en ont besoin (formulaire
        d'atelier, formulaire de séance), pas sur chaque page. Évite
        surtout de trimballer la même variable dans une dizaine d'appels
        à ``render_template`` répartis dans plusieurs modules.
        """
        def salles_occupables():
            try:
                from app.services.salles import espaces_reservables

                return espaces_reservables()
            except Exception:  # noqa: BLE001 - un formulaire ne doit jamais tomber pour ça
                return []

        def emplacements_stockage():
            """Les lieux où l'on range du matériel : salles ET armoires."""
            try:
                from app.services.salles import espaces_stockage

                return espaces_stockage()
            except Exception:  # noqa: BLE001
                return []

        def valeurs_deja_saisies(modele: str, champ: str, limite: int = 200):
            """Les valeurs distinctes déjà enregistrées dans une colonne.

            Sert à proposer une liste au lieu de laisser retaper. Un champ
            libre sans suggestion, c'est « MAIF », « Maif » et « maif » dans
            la même base, et trois lignes dans le moindre regroupement.
            """
            from app import models as _modeles

            try:
                classe = getattr(_modeles, modele, None)
                colonne = getattr(classe, champ, None)
                if colonne is None:
                    return []
                lignes = (
                    db.session.query(colonne)
                    .filter(colonne.isnot(None), colonne != "")
                    .distinct().order_by(colonne).limit(limite).all()
                )
                return [v for (v,) in lignes]
            except Exception:  # noqa: BLE001 - une suggestion n'empêche pas de saisir
                return []

        return {
            "salles_occupables": salles_occupables,
            "emplacements_stockage": emplacements_stockage,
            "valeurs_deja_saisies": valeurs_deja_saisies,
        }

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
                from flask_migrate import upgrade

                insp_pre = inspect(db.engine)
                tables = set(insp_pre.get_table_names())
                has_legacy_core = {"user", "role", "permission", "atelier_activite"}.issubset(tables)
                if has_legacy_core and "alembic_version" not in tables:
                    raise RuntimeError(
                        "Base existante sans historique Alembic : démarrage refusé. "
                        "Faites analyser une copie du schéma avant la reprise ; aucun marquage automatique n'est effectué."
                    )

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
