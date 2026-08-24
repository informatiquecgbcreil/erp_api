"""Synchronisation Google Agenda (push temps réel via l'API Calendar).

Aucun test ne parle au vrai Google : les appels API sont interceptés
(monkeypatch de ``_api`` / ``planifier_synchro``) — on vérifie la logique
(contenu poussé, périmètre, idempotence, détection des changements).
"""
import datetime as dt
import uuid


def _user(app, *, email, secteur=None, role="responsable_secteur"):
    from app.extensions import db
    from app.models import Role, User
    with app.app_context():
        u = User.query.filter_by(email=email).first()
        if u is None:
            u = User(email=email, nom=email.split("@")[0], secteur_assigne=secteur)
            u.set_password("pw-test-123")
            u.roles.append(Role.query.filter_by(code=role).first())
            db.session.add(u)
            db.session.commit()
        return u.id


def _seance(app, *, secteur, nom, jour_offset=3, heure_debut="14:00", heure_fin="16:00",
            statut="realisee", modules=()):
    from app.extensions import db
    from app.models import AtelierActivite, PedagogieModule, SessionActivite
    with app.app_context():
        at = AtelierActivite(nom=nom, secteur=secteur, type_atelier="COLLECTIF", capacite_defaut=12)
        db.session.add(at)
        db.session.flush()
        s = SessionActivite(atelier_id=at.id, secteur=secteur, session_type="COLLECTIF",
                            date_session=dt.date.today() + dt.timedelta(days=jour_offset),
                            heure_debut=heure_debut, heure_fin=heure_fin, statut=statut)
        for nom_module in modules:
            m = PedagogieModule.query.filter_by(nom=nom_module).first()
            if m is None:
                m = PedagogieModule(nom=nom_module)
                db.session.add(m)
                db.session.flush()
            s.modules.append(m)
        db.session.add(s)
        db.session.commit()
        return s.id


def _compte(app, user_id):
    from app.extensions import db
    from app.models import GoogleAgendaCompte
    from app.utils.dates import utcnow
    with app.app_context():
        compte = GoogleAgendaCompte.query.filter_by(user_id=user_id).first()
        if compte is None:
            compte = GoogleAgendaCompte(
                user_id=user_id, refresh_token="rt-test", access_token="at-test",
                access_token_expire_at=utcnow() + dt.timedelta(hours=1),
                calendar_id="cal-test@group.calendar.google.com", google_email="test@gmail.com",
            )
            db.session.add(compte)
            db.session.commit()
        return compte.id


# ---------------------------------------------------------------------------
# Contenu poussé
# ---------------------------------------------------------------------------

def test_corps_evenement_horaire_et_description(app):
    from app.extensions import db
    from app.models import SessionActivite
    from app.services import google_agenda as ga

    suf = uuid.uuid4().hex[:6]
    sid = _seance(app, secteur=f"Num{suf}", nom=f"Atelier{suf}", modules=[f"Français oral {suf}"])
    with app.app_context():
        s = db.session.get(SessionActivite, sid)
        options = {
            "titre_format": "{atelier}", "inclure_lien": False,
            "champs_description": ["type", "horaire", "modules", "capacite", "presences", "secteur"],
        }
        corps = ga.corps_evenement(s, options, lien_base="")

    assert corps["summary"] == f"Atelier{suf}"
    assert corps["start"]["timeZone"] == "Europe/Paris"
    assert corps["start"]["dateTime"].endswith("T14:00:00")
    assert corps["end"]["dateTime"].endswith("T16:00:00")
    assert "Séance collective" in corps["description"]
    assert f"Français oral {suf}" in corps["description"]
    assert "Capacité : 12 places" in corps["description"]
    assert corps["transparency"] == "opaque"
    assert corps["extendedProperties"]["private"]["erp_seance"] == str(sid)


def test_corps_evenement_journee_entiere_sans_heure(app):
    from app.extensions import db
    from app.models import SessionActivite
    from app.services import google_agenda as ga

    suf = uuid.uuid4().hex[:6]
    sid = _seance(app, secteur=f"Num{suf}", nom=f"Sortie{suf}", heure_debut=None, heure_fin=None)
    with app.app_context():
        s = db.session.get(SessionActivite, sid)
        corps = ga.corps_evenement(s, {"titre_format": "{atelier}", "champs_description": []}, lien_base="")
        jour = s.date_session

    assert corps["start"] == {"date": jour.isoformat()}
    assert corps["end"] == {"date": (jour + dt.timedelta(days=1)).isoformat()}


def test_corps_evenement_seance_annulee(app):
    from app.extensions import db
    from app.models import SessionActivite
    from app.services import google_agenda as ga

    suf = uuid.uuid4().hex[:6]
    sid = _seance(app, secteur=f"Num{suf}", nom=f"Annul{suf}", statut="annulee")
    with app.app_context():
        s = db.session.get(SessionActivite, sid)
        corps = ga.corps_evenement(s, {"titre_format": "{atelier}", "champs_description": []}, lien_base="")

    assert corps["summary"].startswith("Annulée · ")
    assert corps["transparency"] == "transparent"
    assert "annulée" in corps["description"]


# ---------------------------------------------------------------------------
# Périmètre (mêmes règles que le flux iCal)
# ---------------------------------------------------------------------------

def test_session_visible_perimetre(app):
    from app.extensions import db
    from app.models import SessionActivite, User
    from app.services import google_agenda as ga
    from app.services.calendrier import OPTIONS_DEFAUT

    suf = uuid.uuid4().hex[:6]
    secteur = f"Num{suf}"
    uid = _user(app, email=f"scope-{suf}@ex.org", secteur=secteur)
    sid_dedans = _seance(app, secteur=secteur, nom=f"Dedans{suf}")
    sid_ailleurs = _seance(app, secteur=f"Autre{suf}", nom=f"Ailleurs{suf}")
    sid_annulee = _seance(app, secteur=secteur, nom=f"Annulee{suf}", statut="annulee")

    with app.app_context():
        user = db.session.get(User, uid)
        options = dict(OPTIONS_DEFAUT)

        assert ga._session_visible(db.session.get(SessionActivite, sid_dedans), user, options)
        # Autre secteur : hors périmètre pour un responsable de secteur.
        assert not ga._session_visible(db.session.get(SessionActivite, sid_ailleurs), user, options)
        # Annulée : suit le réglage inclure_annulees.
        assert ga._session_visible(db.session.get(SessionActivite, sid_annulee), user, options)
        options["inclure_annulees"] = False
        assert not ga._session_visible(db.session.get(SessionActivite, sid_annulee), user, options)
        # Corbeille : jamais visible.
        s = db.session.get(SessionActivite, sid_dedans)
        s.is_deleted = True
        assert not ga._session_visible(s, user, dict(OPTIONS_DEFAUT))
        db.session.rollback()
        # Fenêtre de jours : ignorée quand fenetre=False (événement déjà poussé).
        s = db.session.get(SessionActivite, sid_dedans)
        s.date_session = dt.date.today() - dt.timedelta(days=400)
        assert not ga._session_visible(s, user, dict(OPTIONS_DEFAUT))
        assert ga._session_visible(s, user, dict(OPTIONS_DEFAUT), fenetre=False)
        db.session.rollback()


# ---------------------------------------------------------------------------
# Synchronisation d'une séance (insert / patch / delete, idempotence)
# ---------------------------------------------------------------------------

class _FauxGoogle:
    """Enregistre les appels API au lieu de parler au vrai Google."""

    def __init__(self):
        self.appels = []

    def __call__(self, compte, methode, chemin, corps=None, absent_ok=False):
        self.appels.append((methode, chemin, corps))
        return {}


def test_synchronisation_cree_modifie_supprime(app, monkeypatch):
    from app.extensions import db
    from app.models import GoogleAgendaEvenement, SessionActivite, User
    from app.services import google_agenda as ga

    suf = uuid.uuid4().hex[:6]
    secteur = f"Num{suf}"
    uid = _user(app, email=f"sync-{suf}@ex.org", secteur=secteur)
    sid = _seance(app, secteur=secteur, nom=f"Sync{suf}")
    cid = _compte(app, uid)

    faux = _FauxGoogle()
    monkeypatch.setattr(ga, "_api", faux)

    with app.app_context():
        from app.models import GoogleAgendaCompte

        compte = db.session.get(GoogleAgendaCompte, cid)
        s = db.session.get(SessionActivite, sid)

        # 1) Création : POST avec identifiant déterministe + correspondance.
        resultat = ga.synchroniser_session_pour_compte(compte, s, sid, lien_base="")
        assert resultat == "cree"
        methode, chemin, corps = faux.appels[-1]
        assert methode == "POST" and chemin.endswith("/events")
        assert corps["id"] == f"erpseance{sid}"
        correspondance = GoogleAgendaEvenement.query.filter_by(compte_id=cid, session_id=sid).one()
        assert correspondance.google_event_id == f"erpseance{sid}"
        assert correspondance.empreinte

        # 2) Rien n'a changé : aucun appel réseau.
        nb_avant = len(faux.appels)
        assert ga.synchroniser_session_pour_compte(compte, s, sid, lien_base="") is None
        assert len(faux.appels) == nb_avant

        # 3) Modification : PUT sur l'événement existant.
        s.heure_fin = "17:30"
        db.session.commit()
        assert ga.synchroniser_session_pour_compte(compte, s, sid, lien_base="") == "modifie"
        methode, chemin, corps = faux.appels[-1]
        assert methode == "PUT" and chemin.endswith(f"/events/erpseance{sid}")
        assert corps["end"]["dateTime"].endswith("T17:30:00")

        # 4) Corbeille : DELETE + correspondance effacée.
        s.is_deleted = True
        db.session.commit()
        assert ga.synchroniser_session_pour_compte(compte, s, sid, lien_base="") == "supprime"
        methode, chemin, corps = faux.appels[-1]
        assert methode == "DELETE"
        assert GoogleAgendaEvenement.query.filter_by(compte_id=cid, session_id=sid).first() is None


def test_synchronisation_complete_retire_les_orphelins(app, monkeypatch):
    from app.extensions import db
    from app.models import GoogleAgendaCompte, GoogleAgendaEvenement, SessionActivite
    from app.services import google_agenda as ga

    suf = uuid.uuid4().hex[:6]
    secteur = f"Num{suf}"
    uid = _user(app, email=f"full-{suf}@ex.org", secteur=secteur)
    sid = _seance(app, secteur=secteur, nom=f"Full{suf}")
    cid = _compte(app, uid)

    faux = _FauxGoogle()
    monkeypatch.setattr(ga, "_api", faux)
    monkeypatch.setattr(ga, "public_base_url", lambda: "")

    with app.app_context():
        compte = db.session.get(GoogleAgendaCompte, cid)
        bilan = ga.synchronisation_complete(compte)
        assert bilan["cree"] >= 1
        assert compte.derniere_synchro is not None

        # La séance part à la corbeille : la resynchro complète la retire.
        s = db.session.get(SessionActivite, sid)
        s.is_deleted = True
        db.session.commit()
        bilan = ga.synchronisation_complete(compte)
        assert bilan["supprime"] >= 1
        assert GoogleAgendaEvenement.query.filter_by(compte_id=cid, session_id=sid).first() is None


# ---------------------------------------------------------------------------
# Détection automatique des changements (écouteurs SQLAlchemy)
# ---------------------------------------------------------------------------

def test_ecouteurs_detectent_seances_et_presences(app, monkeypatch):
    from app.extensions import db
    from app.models import Participant, PresenceActivite, SessionActivite
    from app.services import google_agenda as ga

    captures = []
    monkeypatch.setattr(ga, "planifier_synchro", lambda application, ids: captures.append(set(ids)))

    suf = uuid.uuid4().hex[:6]
    sid = _seance(app, secteur=f"Num{suf}", nom=f"Ecoute{suf}")
    assert any(sid in ids for ids in captures), "la création de séance doit être détectée"

    with app.app_context():
        p = Participant(nom=f"Doe{suf}", prenom="Jane")
        db.session.add(p)
        db.session.commit()
        captures.clear()
        db.session.add(PresenceActivite(session_id=sid, participant_id=p.id))
        db.session.commit()
    assert any(sid in ids for ids in captures), "un émargement doit resynchroniser sa séance"

    with app.app_context():
        captures.clear()
        s = db.session.get(SessionActivite, sid)
        s.heure_debut = "09:00"
        db.session.commit()
    assert any(sid in ids for ids in captures), "une modification de séance doit être détectée"


# ---------------------------------------------------------------------------
# Routes / page Mon agenda
# ---------------------------------------------------------------------------

def test_page_mon_agenda_sans_configuration(app, admin_client):
    app.config["GOOGLE_OAUTH_CLIENT_ID"] = ""
    app.config["GOOGLE_OAUTH_CLIENT_SECRET"] = ""
    r = admin_client.get("/mon-agenda")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Synchro Google Agenda" in body
    assert "pas encore configurée" in body


def test_connexion_redirige_vers_google(app, admin_client):
    app.config["GOOGLE_OAUTH_CLIENT_ID"] = "client-test.apps.googleusercontent.com"
    app.config["GOOGLE_OAUTH_CLIENT_SECRET"] = "secret-test"
    try:
        r = admin_client.get("/mon-agenda/google/connecter")
        assert r.status_code == 302
        cible = r.headers["Location"]
        assert cible.startswith("https://accounts.google.com/o/oauth2/v2/auth")
        assert "access_type=offline" in cible
        assert "calendar" in cible

        # La page propose alors le bouton de connexion.
        r = admin_client.get("/mon-agenda")
        assert "Connecter mon agenda Google" in r.get_data(as_text=True)
    finally:
        app.config["GOOGLE_OAUTH_CLIENT_ID"] = ""
        app.config["GOOGLE_OAUTH_CLIENT_SECRET"] = ""


def test_retour_oauth_etat_invalide(app, admin_client):
    app.config["GOOGLE_OAUTH_CLIENT_ID"] = "client-test.apps.googleusercontent.com"
    app.config["GOOGLE_OAUTH_CLIENT_SECRET"] = "secret-test"
    try:
        r = admin_client.get("/mon-agenda/google/retour?state=falsifie&code=abc", follow_redirects=True)
        assert r.status_code == 200
        assert "invalide ou expiré" in r.get_data(as_text=True)
    finally:
        app.config["GOOGLE_OAUTH_CLIENT_ID"] = ""
        app.config["GOOGLE_OAUTH_CLIENT_SECRET"] = ""


def test_deconnexion_supprime_calendrier_et_compte(app, monkeypatch):
    from app.extensions import db
    from app.models import GoogleAgendaCompte, User
    from app.services import google_agenda as ga

    suf = uuid.uuid4().hex[:6]
    uid = _user(app, email=f"deco-{suf}@ex.org", secteur=f"Num{suf}")
    cid = _compte(app, uid)

    faux = _FauxGoogle()
    monkeypatch.setattr(ga, "_api", faux)
    monkeypatch.setattr(ga, "revoquer", lambda compte: None)

    with app.app_context():
        compte = db.session.get(GoogleAgendaCompte, cid)
        ga.deconnecter(compte)
        assert db.session.get(GoogleAgendaCompte, cid) is None
    assert faux.appels and faux.appels[0][0] == "DELETE"


def test_flux_ics_inclut_les_thematiques(app, client):
    """Le champ « modules » enrichit aussi le flux iCal existant."""
    from app.extensions import db
    from app.models import User
    from app.services.calendrier import sauvegarder_options, token_ou_creer, OPTIONS_DEFAUT

    suf = uuid.uuid4().hex[:6]
    secteur = f"Num{suf}"
    uid = _user(app, email=f"ics-{suf}@ex.org", secteur=secteur)
    _seance(app, secteur=secteur, nom=f"Ics{suf}", modules=[f"Cuisine durable {suf}"])
    with app.app_context():
        user = db.session.get(User, uid)
        sauvegarder_options(user, dict(OPTIONS_DEFAUT))
        token = token_ou_creer(user)

    r = client.get(f"/calendrier/{token}.ics")
    assert r.status_code == 200
    # Dépliage RFC 5545 : les lignes > 75 octets sont repliées avec "\r\n ".
    corps_deplie = r.get_data(as_text=True).replace("\r\n ", "")
    assert f"Cuisine durable {suf}" in corps_deplie
