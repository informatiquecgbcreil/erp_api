"""Synchronisation « push » avec Google Agenda via l'API Google Calendar.

Complément temps réel du flux iCal (app/services/calendrier.py) : le flux
reste parfait pour Apple/Outlook et pour l'export feuille de temps, mais
Google ne le relit que toutes les ~24 h. Ici, l'application POUSSE chaque
séance dès sa création/modification/suppression :

- chaque personne connecte SON compte Google depuis « Mon agenda »
  (OAuth 2.0, jeton de rafraîchissement stocké en base, révocable) ;
- un calendrier dédié « Séances — <nom> » est créé dans son compte Google
  (jamais le calendrier principal) : superposé dans l'interface Google
  Agenda, désactivable d'un clic, supprimable proprement ;
- le contenu (titre, description : type, horaire, capacité, présences
  saisies, thématiques, secteur…) réutilise EXACTEMENT les réglages du
  flux iCal de la personne — un seul endroit à régler ;
- une empreinte du contenu est mémorisée par séance : on n'appelle Google
  que si quelque chose a réellement changé ;
- une resynchronisation périodique rattrape ce qui aurait été manqué
  (application hors ligne, erreur réseau, import Excel massif…).

Aucune dépendance externe : OAuth et API Calendar en urllib (stdlib),
comme la veille financements. Jamais de nom de participant envoyé à
Google : seuls des agrégats (nombre de présences) sortent.

Configuration (fichier .env) :
    GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET
créés dans Google Cloud Console (voir docs/google-agenda.md).
"""
from __future__ import annotations

import base64
import hashlib
import json
import ssl
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta

from flask import current_app, url_for
from itsdangerous import BadSignature, URLSafeTimedSerializer

from app.extensions import db
from app.models import (
    GoogleAgendaCompte,
    GoogleAgendaEvenement,
    SessionActivite,
    User,
)
from app.services.calendrier import (
    _description_seance,
    _plus_une_heure,
    _rendre_titre,
    _secteur_du_flux,
    _type_seance,
    charger_options,
    sessions_du_flux,
    _presences_par_session,
)
from app.services.public_urls import public_base_url
from app.utils.dates import utcnow

URL_AUTORISATION = "https://accounts.google.com/o/oauth2/v2/auth"
URL_JETON = "https://oauth2.googleapis.com/token"
URL_REVOCATION = "https://oauth2.googleapis.com/revoke"
URL_API = "https://www.googleapis.com/calendar/v3"
PORTEES = "openid email https://www.googleapis.com/auth/calendar"
FUSEAU = "Europe/Paris"
DELAI_HTTP = 20  # secondes par requête vers Google

#: Rattrapage périodique : resynchronisation complète si la dernière date
#: de plus de N heures (filet de sécurité, quasi gratuit grâce aux empreintes).
RATTRAPAGE_HEURES = 6


class GoogleAgendaErreur(Exception):
    """Erreur de dialogue avec Google (réseau, refus, jeton révoqué…)."""


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def est_configure(app=None) -> bool:
    """La synchro n'apparaît que si l'installation a ses identifiants OAuth."""
    app = app or current_app
    return bool(app.config.get("GOOGLE_OAUTH_CLIENT_ID")) and bool(
        app.config.get("GOOGLE_OAUTH_CLIENT_SECRET")
    )


def uri_de_retour() -> str:
    """URL de retour OAuth — DOIT être déclarée telle quelle dans la
    console Google Cloud (« URI de redirection autorisés »)."""
    base = (public_base_url() or "").rstrip("/")
    return f"{base}{url_for('main.google_agenda_retour')}"


def _serialiseur() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="google-agenda-oauth")


def url_autorisation(user) -> str:
    """URL de consentement Google, avec un état signé anti-CSRF."""
    etat = _serialiseur().dumps({"uid": user.id})
    params = {
        "client_id": current_app.config["GOOGLE_OAUTH_CLIENT_ID"],
        "redirect_uri": uri_de_retour(),
        "response_type": "code",
        "scope": PORTEES,
        # offline + consent : Google délivre un refresh_token réutilisable.
        "access_type": "offline",
        "prompt": "consent",
        "state": etat,
    }
    return f"{URL_AUTORISATION}?{urllib.parse.urlencode(params)}"


def verifier_etat(etat: str) -> int | None:
    """Rejoue l'état signé du retour OAuth ; None si invalide/expiré."""
    try:
        donnees = _serialiseur().loads(etat or "", max_age=600)
        return int(donnees.get("uid"))
    except (BadSignature, ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# HTTP bas niveau (urllib, TLS toujours vérifié — secours certifi)
# ---------------------------------------------------------------------------

def _contexte_certifi() -> ssl.SSLContext | None:
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return None


def _requete_json(url: str, *, methode: str = "GET", corps: bytes | None = None,
                  entetes: dict | None = None, timeout: int = DELAI_HTTP):
    """Appelle Google et renvoie (statut HTTP, JSON décodé ou {}).

    Les statuts d'erreur HTTP sont RENVOYÉS (pas levés) : l'appelant décide
    (404 sur une suppression = déjà parti, 401 = jeton à rafraîchir…).
    Seules les erreurs réseau lèvent GoogleAgendaErreur.
    """
    requete = urllib.request.Request(url, data=corps, method=methode,
                                     headers=entetes or {})

    def _lire(reponse):
        brut = reponse.read()
        if not brut:
            return reponse.status, {}
        try:
            return reponse.status, json.loads(brut.decode("utf-8"))
        except Exception:
            return reponse.status, {}

    try:
        try:
            with urllib.request.urlopen(requete, timeout=timeout) as reponse:
                return _lire(reponse)
        except urllib.error.HTTPError as exc:
            return _lire(exc)
        except urllib.error.URLError as exc:
            # Magasin de certificats système incomplet : on retente avec certifi.
            if isinstance(getattr(exc, "reason", None), ssl.SSLCertVerificationError):
                contexte = _contexte_certifi()
                if contexte is not None:
                    try:
                        with urllib.request.urlopen(requete, timeout=timeout, context=contexte) as reponse:
                            return _lire(reponse)
                    except urllib.error.HTTPError as exc2:
                        return _lire(exc2)
            raise
    except OSError as exc:
        raise GoogleAgendaErreur(f"Google injoignable : {exc}") from exc


def _message_erreur(statut: int, donnees: dict) -> str:
    detail = ""
    if isinstance(donnees, dict):
        err = donnees.get("error")
        if isinstance(err, dict):
            detail = err.get("message") or ""
        elif isinstance(err, str):
            detail = err
            desc = donnees.get("error_description")
            if desc:
                detail = f"{detail} — {desc}"
    return f"Google a répondu {statut}" + (f" : {detail}" if detail else "")


# ---------------------------------------------------------------------------
# Jetons OAuth
# ---------------------------------------------------------------------------

def _appel_jeton(champs: dict) -> dict:
    corps = urllib.parse.urlencode(champs).encode("utf-8")
    statut, donnees = _requete_json(
        URL_JETON, methode="POST", corps=corps,
        entetes={"Content-Type": "application/x-www-form-urlencoded"},
    )
    if statut != 200:
        raise GoogleAgendaErreur(_message_erreur(statut, donnees))
    return donnees


def echanger_code(code: str) -> dict:
    """Échange le code d'autorisation contre les jetons (retour OAuth)."""
    return _appel_jeton({
        "code": code,
        "client_id": current_app.config["GOOGLE_OAUTH_CLIENT_ID"],
        "client_secret": current_app.config["GOOGLE_OAUTH_CLIENT_SECRET"],
        "redirect_uri": uri_de_retour(),
        "grant_type": "authorization_code",
    })


def email_du_id_token(id_token: str | None) -> str | None:
    """Adresse e-mail contenue dans l'id_token OpenID.

    Pas de vérification de signature : le jeton vient d'être reçu en TLS
    directement du serveur de jetons de Google — usage d'affichage.
    """
    try:
        charge = (id_token or "").split(".")[1]
        charge += "=" * (-len(charge) % 4)
        donnees = json.loads(base64.urlsafe_b64decode(charge).decode("utf-8"))
        return donnees.get("email")
    except Exception:
        return None


def _jeton_acces(compte: GoogleAgendaCompte) -> str:
    """Jeton d'accès valide (rafraîchi si expiré, avec 60 s de marge)."""
    if (
        compte.access_token
        and compte.access_token_expire_at
        and compte.access_token_expire_at > utcnow() + timedelta(seconds=60)
    ):
        return compte.access_token
    return _rafraichir_jeton(compte)


def _rafraichir_jeton(compte: GoogleAgendaCompte) -> str:
    try:
        donnees = _appel_jeton({
            "refresh_token": compte.refresh_token,
            "client_id": current_app.config["GOOGLE_OAUTH_CLIENT_ID"],
            "client_secret": current_app.config["GOOGLE_OAUTH_CLIENT_SECRET"],
            "grant_type": "refresh_token",
        })
    except GoogleAgendaErreur as exc:
        # Jeton révoqué côté Google (invalid_grant) : accès à reconnecter.
        raise GoogleAgendaErreur(
            f"Accès Google expiré ou révoqué ({exc}). Reconnecte ton compte depuis Mon agenda."
        ) from exc
    compte.access_token = donnees.get("access_token")
    try:
        duree = int(donnees.get("expires_in") or 3600)
    except Exception:
        duree = 3600
    compte.access_token_expire_at = utcnow() + timedelta(seconds=duree)
    db.session.commit()
    return compte.access_token


def _api(compte: GoogleAgendaCompte, methode: str, chemin: str,
         corps: dict | None = None, *, absent_ok: bool = False) -> dict:
    """Appel API Calendar authentifié ; re-tente une fois après 401."""
    url = f"{URL_API}{chemin}"
    donnees_corps = json.dumps(corps).encode("utf-8") if corps is not None else None
    for tentative in (1, 2):
        entetes = {
            "Authorization": f"Bearer {_jeton_acces(compte)}",
            "Content-Type": "application/json; charset=utf-8",
        }
        statut, donnees = _requete_json(url, methode=methode, corps=donnees_corps, entetes=entetes)
        if statut == 401 and tentative == 1:
            compte.access_token = None  # force le rafraîchissement
            continue
        if absent_ok and statut in (404, 410):
            return {}
        if 200 <= statut < 300:
            return donnees
        raise GoogleAgendaErreur(_message_erreur(statut, donnees))
    raise GoogleAgendaErreur("Google a refusé l'authentification (401).")


def revoquer(compte: GoogleAgendaCompte) -> None:
    """Révoque le refresh_token côté Google (meilleur effort)."""
    try:
        corps = urllib.parse.urlencode({"token": compte.refresh_token}).encode("utf-8")
        _requete_json(URL_REVOCATION, methode="POST", corps=corps,
                      entetes={"Content-Type": "application/x-www-form-urlencoded"})
    except GoogleAgendaErreur:
        pass


# ---------------------------------------------------------------------------
# Connexion / déconnexion
# ---------------------------------------------------------------------------

def creer_calendrier_dedie(compte: GoogleAgendaCompte, nom: str) -> str:
    donnees = _api(compte, "POST", "/calendars", {"summary": nom, "timeZone": FUSEAU})
    calendar_id = donnees.get("id")
    if not calendar_id:
        raise GoogleAgendaErreur("Google n'a pas renvoyé l'identifiant du calendrier créé.")
    return calendar_id


def connecter(user, code: str) -> GoogleAgendaCompte:
    """Retour OAuth : échange le code, (re)crée le compte + calendrier dédié."""
    jetons = echanger_code(code)
    refresh = jetons.get("refresh_token")
    compte = GoogleAgendaCompte.query.filter_by(user_id=user.id).first()
    if not refresh and compte is None:
        raise GoogleAgendaErreur(
            "Google n'a pas fourni de jeton de rafraîchissement. "
            "Retire l'accès de l'application sur myaccount.google.com/permissions puis reconnecte-toi."
        )
    if compte is None:
        compte = GoogleAgendaCompte(user_id=user.id, refresh_token=refresh)
        db.session.add(compte)
    elif refresh:
        compte.refresh_token = refresh
    compte.google_email = email_du_id_token(jetons.get("id_token")) or compte.google_email
    compte.access_token = jetons.get("access_token")
    try:
        duree = int(jetons.get("expires_in") or 3600)
    except Exception:
        duree = 3600
    compte.access_token_expire_at = utcnow() + timedelta(seconds=duree)
    compte.actif = True
    compte.derniere_erreur = None
    if not compte.calendar_id:
        nom = f"Séances — {current_app.config.get('APP_NAME', 'App Gestion')}"
        compte.calendar_id = creer_calendrier_dedie(compte, nom)
    db.session.commit()
    return compte


def deconnecter(compte: GoogleAgendaCompte, *, supprimer_calendrier: bool = True) -> None:
    """Coupe la synchro : supprime le calendrier dédié (option), révoque le
    jeton, efface le compte et les correspondances (cascade)."""
    if supprimer_calendrier and compte.calendar_id:
        try:
            _api(compte, "DELETE", f"/calendars/{urllib.parse.quote(compte.calendar_id)}", absent_ok=True)
        except GoogleAgendaErreur:
            pass  # meilleur effort : la révocation suffit à couper l'accès
    revoquer(compte)
    db.session.delete(compte)
    db.session.commit()


# ---------------------------------------------------------------------------
# Construction des événements
# ---------------------------------------------------------------------------

def _identifiant_evenement(session_id: int) -> str:
    """Identifiant d'événement DÉTERMINISTE (alphabet base32hex imposé par
    Google) : la synchro devient idempotente — rejouer une création ne peut
    jamais produire de doublon dans le calendrier dédié."""
    return f"erpseance{session_id}"


def _dt_iso(d: date, heure: str | None) -> str | None:
    try:
        hh, mm = (heure or "").strip().split(":")[:2]
        return f"{d.isoformat()}T{int(hh):02d}:{int(mm):02d}:00"
    except Exception:
        return None


def corps_evenement(s: SessionActivite, options: dict, *, lien_base: str = "") -> dict:
    """Corps JSON de l'événement Google — mêmes réglages que le flux iCal
    (titre à jetons, lignes de description choisies, lien émargement)."""
    d = s.rdv_date or s.date_session
    heure_debut = s.rdv_debut or s.heure_debut
    heure_fin = s.rdv_fin or s.heure_fin or _plus_une_heure(heure_debut)
    atelier = s.atelier
    nom_atelier = atelier.nom if atelier else f"Atelier #{s.atelier_id}"
    annulee = (s.statut or "").strip().lower() == "annulee"

    titre = _rendre_titre(
        options.get("titre_format") or "{atelier}",
        atelier=nom_atelier, secteur=s.secteur or "",
        type_seance=_type_seance(s), heure=heure_debut or "",
    )
    if getattr(s, "est_evenement", False) and "🎉" not in titre:
        titre = f"🎉 {titre}"
    if annulee:
        titre = f"Annulée · {titre}"

    presences = _presences_par_session([s.id]).get(s.id, 0)
    description = _description_seance(s, atelier, presences, options)
    if annulee:
        description = ("⚠️ Séance annulée.\n" + description).strip()
    lien_base = (lien_base or "").rstrip("/")
    if lien_base and options.get("inclure_lien"):
        lien = f"{lien_base}/activite/session/{s.id}/emargement"
        description = (description + f"\n\nFeuille d'émargement : {lien}").strip()

    debut_iso = _dt_iso(d, heure_debut)
    if debut_iso:
        fin_iso = _dt_iso(d, heure_fin) or debut_iso
        debut = {"dateTime": debut_iso, "timeZone": FUSEAU}
        fin = {"dateTime": fin_iso, "timeZone": FUSEAU}
    else:
        # Pas d'heure : événement « journée entière » (fin exclusive à J+1).
        debut = {"date": d.isoformat()}
        fin = {"date": (d + timedelta(days=1)).isoformat()}

    return {
        "summary": titre,
        "location": (s.secteur or "").strip(),
        "description": description,
        "start": debut,
        "end": fin,
        "status": "confirmed",
        # Une séance annulée ne bloque plus le créneau (libre/occupé).
        "transparency": "transparent" if annulee else "opaque",
        "extendedProperties": {"private": {"erp_seance": str(s.id)}},
    }


def _empreinte(corps: dict) -> str:
    return hashlib.sha256(
        json.dumps(corps, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _session_visible(s: SessionActivite | None, user, options: dict, *, fenetre: bool = True) -> bool:
    """La séance a-t-elle sa place dans le calendrier de cette personne ?

    Mêmes règles que le flux iCal (périmètre secteur, annulées, corbeille).
    ``fenetre=False`` ignore la fenêtre de jours : un événement déjà poussé
    reste maintenu à jour même s'il est sorti de la fenêtre (l'historique
    Google n'est jamais effacé simplement parce qu'il vieillit).
    """
    if s is None or s.is_deleted:
        return False
    atelier = s.atelier
    if atelier is None or atelier.is_deleted:
        return False
    d = s.rdv_date or s.date_session
    if d is None:
        return False
    annulee = (s.statut or "").strip().lower() == "annulee"
    if annulee and not options.get("inclure_annulees", True):
        return False
    secteur = _secteur_du_flux(user)
    if secteur and (s.secteur or "") != secteur:
        if not (options.get("evenements_tous_secteurs") and getattr(s, "est_evenement", False)):
            return False
    if fenetre:
        today = date.today()
        if d < today - timedelta(days=options.get("jours_passe", 30)):
            return False
        if d > today + timedelta(days=options.get("jours_futur", 180)):
            return False
    return True


# ---------------------------------------------------------------------------
# Synchronisation
# ---------------------------------------------------------------------------

def _chemin_evenement(compte: GoogleAgendaCompte, event_id: str) -> str:
    return (
        f"/calendars/{urllib.parse.quote(compte.calendar_id)}"
        f"/events/{urllib.parse.quote(event_id)}"
    )


def _pousser(compte: GoogleAgendaCompte, corps: dict, event_id: str, *, existe: bool) -> None:
    if existe:
        _api(compte, "PUT", _chemin_evenement(compte, event_id), corps)
        return
    try:
        _api(compte, "POST",
             f"/calendars/{urllib.parse.quote(compte.calendar_id)}/events",
             {**corps, "id": event_id})
    except GoogleAgendaErreur as exc:
        # 409 : l'identifiant déterministe existe déjà (création rejouée,
        # ou événement supprimé côté Google) — PUT le remet d'aplomb.
        if "409" not in str(exc):
            raise
        _api(compte, "PUT", _chemin_evenement(compte, event_id), corps)


def synchroniser_session_pour_compte(compte: GoogleAgendaCompte, s: SessionActivite | None,
                                     session_id: int, options: dict | None = None,
                                     *, lien_base: str | None = None) -> str | None:
    """Aligne UNE séance sur le calendrier Google du compte.

    Renvoie "cree", "modifie", "supprime" ou None (rien à faire).
    """
    user = compte.user
    options = options if options is not None else charger_options(user)
    if lien_base is None:
        lien_base = public_base_url()

    correspondance = GoogleAgendaEvenement.query.filter_by(
        compte_id=compte.id, session_id=session_id
    ).first()

    if not _session_visible(s, user, options, fenetre=(correspondance is None)):
        if correspondance is None:
            return None
        _api(compte, "DELETE",
             _chemin_evenement(compte, correspondance.google_event_id), absent_ok=True)
        db.session.delete(correspondance)
        db.session.commit()
        return "supprime"

    corps = corps_evenement(s, options, lien_base=lien_base)
    empreinte = _empreinte(corps)
    if correspondance is not None and correspondance.empreinte == empreinte:
        return None

    event_id = correspondance.google_event_id if correspondance else _identifiant_evenement(session_id)
    _pousser(compte, corps, event_id, existe=correspondance is not None)
    if correspondance is None:
        correspondance = GoogleAgendaEvenement(
            compte_id=compte.id, session_id=session_id, google_event_id=event_id
        )
        db.session.add(correspondance)
        resultat = "cree"
    else:
        resultat = "modifie"
    correspondance.empreinte = empreinte
    db.session.commit()
    return resultat


def synchronisation_complete(compte: GoogleAgendaCompte) -> dict:
    """Resynchronise tout le périmètre de la personne (fenêtre du flux) et
    retire les événements devenus orphelins. Idempotente et quasi gratuite
    quand rien n'a changé (empreintes)."""
    user = compte.user
    options = charger_options(user)
    lien_base = public_base_url()
    today = date.today()
    du = today - timedelta(days=options.get("jours_passe", 30))
    au = today + timedelta(days=options.get("jours_futur", 180))

    bilan = {"cree": 0, "modifie": 0, "supprime": 0, "inchange": 0}
    seances = sessions_du_flux(user, options, du=du, au=au)
    vus = set()
    for s in seances:
        vus.add(s.id)
        resultat = synchroniser_session_pour_compte(
            compte, s, s.id, options, lien_base=lien_base
        )
        bilan[resultat or "inchange"] += 1

    # Correspondances dont la séance a quitté le périmètre (corbeille,
    # changement de secteur, suppression pure) — la simple sortie de la
    # fenêtre de jours, elle, conserve l'événement (historique).
    restantes = (
        GoogleAgendaEvenement.query
        .filter(GoogleAgendaEvenement.compte_id == compte.id)
        .filter(~GoogleAgendaEvenement.session_id.in_(vus) if vus else db.true())
        .all()
    )
    for correspondance in restantes:
        s = db.session.get(SessionActivite, correspondance.session_id)
        resultat = synchroniser_session_pour_compte(
            compte, s, correspondance.session_id, options, lien_base=lien_base
        )
        if resultat:
            bilan[resultat] += 1

    compte.derniere_synchro = utcnow()
    compte.derniere_erreur = None
    db.session.commit()
    return bilan


def _comptes_actifs() -> list[GoogleAgendaCompte]:
    return (
        GoogleAgendaCompte.query
        .join(User, User.id == GoogleAgendaCompte.user_id)
        .filter(GoogleAgendaCompte.actif.is_(True), User.actif.is_(True))
        .all()
    )


def _traiter(session_ids: set[int], complet: bool) -> None:
    """Corps du travailleur d'arrière-plan (déjà sous app_context)."""
    for compte in _comptes_actifs():
        try:
            if complet:
                synchronisation_complete(compte)
            else:
                options = charger_options(compte.user)
                lien_base = public_base_url()
                for session_id in sorted(session_ids):
                    s = db.session.get(SessionActivite, session_id)
                    synchroniser_session_pour_compte(
                        compte, s, session_id, options, lien_base=lien_base
                    )
                compte.derniere_erreur = None
            db.session.commit()
        except GoogleAgendaErreur as exc:
            db.session.rollback()
            compte.derniere_erreur = str(exc)[:2000]
            db.session.commit()
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Google Agenda : échec de synchronisation (compte %s)", compte.id)


# --- File d'attente en mémoire + travailleur unique par processus ----------

_etat_file = {"ids": set(), "complet": False}
_verrou_file = threading.Lock()
_verrou_travail = threading.Lock()


def lancer_synchro_arriere_plan(app, session_ids=None, complet: bool = False) -> None:
    """Empile le travail et lance (si besoin) le thread démon qui draine la
    file. Le rattrapage périodique corrige tout oubli résiduel."""
    with _verrou_file:
        if session_ids:
            _etat_file["ids"].update(int(i) for i in session_ids)
        if complet:
            _etat_file["complet"] = True

    def _tache():
        if not _verrou_travail.acquire(blocking=False):
            return  # un travailleur tourne déjà : il drainera la file
        try:
            with app.app_context():
                while True:
                    with _verrou_file:
                        ids = set(_etat_file["ids"])
                        _etat_file["ids"].clear()
                        faire_complet = _etat_file["complet"]
                        _etat_file["complet"] = False
                    if not ids and not faire_complet:
                        break
                    try:
                        _traiter(ids, faire_complet)
                    except Exception:
                        app.logger.exception("Google Agenda : travailleur en échec")
        finally:
            _verrou_travail.release()

    threading.Thread(target=_tache, name="google-agenda-sync", daemon=True).start()


def planifier_synchro(app, session_ids: set[int]) -> None:
    """Point d'entrée des écouteurs : filtre puis délègue à l'arrière-plan.
    Isolé pour être remplaçable dans les tests (monkeypatch)."""
    if app.config.get("TESTING"):
        return
    if not est_configure(app):
        return
    lancer_synchro_arriere_plan(app, session_ids=session_ids)


# ---------------------------------------------------------------------------
# Écouteurs SQLAlchemy : détection automatique des changements
# ---------------------------------------------------------------------------

_CLE_INFO = "google_agenda_sessions"
_ecouteurs_poses = False
_app_courante = {"app": None}


def _ids_touches(db_session) -> set[int]:
    """Séances concernées par le flush en cours (créations, modifications,
    suppressions de séances ; émargements qui changent le compteur)."""
    from app.models import PresenceActivite  # import local : cycle évité

    ids: set[int] = set()
    for obj in list(db_session.new) + list(db_session.dirty) + list(db_session.deleted):
        if isinstance(obj, SessionActivite) and obj.id is not None:
            ids.add(obj.id)
        elif isinstance(obj, PresenceActivite) and obj.session_id is not None:
            ids.add(obj.session_id)
    return ids


def enregistrer_ecouteurs(app) -> None:
    """Pose (une fois par processus) les écouteurs de session SQLAlchemy.

    after_flush : collecte les séances touchées dans session.info ;
    after_commit : le travail est réellement en base → on pousse en thread.
    Rien n'est envoyé si la synchro n'est pas configurée (coût quasi nul).
    """
    global _ecouteurs_poses
    _app_courante["app"] = app
    if _ecouteurs_poses:
        return
    _ecouteurs_poses = True

    from sqlalchemy import event
    from sqlalchemy.orm import Session as SessionSA

    @event.listens_for(SessionSA, "after_flush")
    def _collecter(db_session, flush_context):
        ids = _ids_touches(db_session)
        if ids:
            db_session.info.setdefault(_CLE_INFO, set()).update(ids)

    @event.listens_for(SessionSA, "after_commit")
    def _pousser_apres_commit(db_session):
        ids = db_session.info.pop(_CLE_INFO, None)
        if not ids:
            return
        app_active = _app_courante["app"]
        if app_active is None:
            return
        try:
            planifier_synchro(app_active, ids)
        except Exception:
            app_active.logger.exception("Google Agenda : planification impossible")


# ---------------------------------------------------------------------------
# Rattrapage périodique (filet de sécurité)
# ---------------------------------------------------------------------------

def lancer_rattrapage_arriere_plan(app) -> bool:
    """Resynchronisation complète des comptes dont la dernière synchro date
    de plus de RATTRAPAGE_HEURES. Appelé depuis un before_request marqueur
    (comme la veille financements) : coût nul le reste du temps."""
    if not est_configure(app):
        return False
    with app.app_context():
        try:
            limite = utcnow() - timedelta(hours=RATTRAPAGE_HEURES)
            en_retard = [
                c for c in _comptes_actifs()
                if c.derniere_synchro is None or c.derniere_synchro < limite
            ]
        except Exception:
            return False  # table pas encore migrée (premier démarrage)
    if not en_retard:
        return False
    lancer_synchro_arriere_plan(app, complet=True)
    return True
