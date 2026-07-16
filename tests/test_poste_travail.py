"""Poste de travail « Ma journée » : accueil orienté tâches, par rôle.

- chaque rôle voit ses actions métier (verbes), filtrées par ses permissions ;
- les compteurs live (séances du jour) sont bornés au secteur ;
- le mode simple allège l'accueil (plus de graphiques ni de doublons) ;
- un rôle inconnu retombe sur un choix automatique par permissions.
"""
import datetime as dt
import uuid

import pytest


def _login_role(app, email, role_code, secteur=None):
    with app.app_context():
        from app.extensions import db
        from app.models import Role, User
        u = User.query.filter_by(email=email).first()
        if u is None:
            u = User(email=email, nom=email.split("@")[0], secteur_assigne=secteur)
            u.set_password("pw-test-123")
            u.roles.append(Role.query.filter_by(code=role_code).first())
            db.session.add(u)
            db.session.commit()
    c = app.test_client()
    r = c.post("/", data={"email": email, "password": "pw-test-123"})
    assert r.status_code == 302
    return c


def _user(app, email):
    with app.app_context():
        from app.models import User
        return User.query.filter_by(email=email).first()


# ---------- Construction du poste par rôle ----------

def test_direction_voit_des_actions_de_pilotage(app):
    _login_role(app, "pt-dir@example.org", "direction")
    with app.app_context():
        from app.models import User
        from app.services.poste_travail import build_poste_travail
        u = User.query.filter_by(email="pt-dir@example.org").first()
        with app.test_request_context():
            poste = build_poste_travail(u)
    labels = [a["label"] for a in poste["actions"]]
    assert "Voir ce qu'il y a à traiter" in labels
    assert "Préparer un bilan" in labels
    assert "Gérer l'équipe" in labels
    assert len(poste["actions"]) <= 5


def test_responsable_secteur_voit_l_appel_et_le_compteur_du_jour(app):
    """Le badge « séances aujourd'hui » compte uniquement le secteur de la personne."""
    suf = uuid.uuid4().hex[:6]
    # Secteurs uniques : le compteur « du jour » ne doit pas dépendre des
    # séances créées par d'autres tests de la suite.
    mien, autre = f"PTNum{suf}", f"PTJeun{suf}"
    with app.app_context():
        from app.extensions import db
        from app.models import AtelierActivite, SessionActivite
        today = dt.date.today()
        for secteur, n in ((mien, 2), (autre, 1)):
            at = AtelierActivite(nom=f"PT{secteur}", secteur=secteur, type_atelier="COLLECTIF")
            db.session.add(at)
            db.session.flush()
            for _ in range(n):
                db.session.add(SessionActivite(
                    atelier_id=at.id, secteur=secteur, session_type="COLLECTIF",
                    date_session=today))
        db.session.commit()

    _login_role(app, f"pt-resp-{suf}@example.org", "responsable_secteur", secteur=mien)
    with app.app_context():
        from app.models import User
        from app.services.poste_travail import build_poste_travail
        u = User.query.filter_by(email=f"pt-resp-{suf}@example.org").first()
        with app.test_request_context():
            poste = build_poste_travail(u)
    appel = next(a for a in poste["actions"] if a["key"] == "appel")
    assert appel["badge"] == "2 séances aujourd'hui"
    assert poste["secteur"] == mien


def test_role_inconnu_retombe_sur_les_permissions(app):
    """Un rôle personnalisé (hors gabarits) obtient des actions selon ses droits."""
    with app.app_context():
        from app.extensions import db
        from app.models import Permission, Role
        role = Role.query.filter_by(code="pt_custom").first()
        if role is None:
            role = Role(code="pt_custom", label="Rôle personnalisé")
            db.session.add(role)
            for code in ("dashboard:view", "participants:view", "emargement:view"):
                perm = Permission.query.filter_by(code=code).first()
                if perm:
                    role.permissions.append(perm)
            db.session.commit()

    _login_role(app, "pt-custom@example.org", "pt_custom")
    with app.app_context():
        from app.models import User
        from app.services.poste_travail import build_poste_travail
        u = User.query.filter_by(email="pt-custom@example.org").first()
        with app.test_request_context():
            poste = build_poste_travail(u)
    keys = [a["key"] for a in poste["actions"]]
    # Actions permises seulement : pas de dépenses, pas d'équipe, pas de projets.
    assert "appel" in keys
    assert "retrouver" in keys
    assert "saisir_depense" not in keys
    assert "gerer_equipe" not in keys


def test_admin_tech_voit_la_maintenance(app):
    _login_role(app, "pt-tech@example.org", "admin_tech")
    with app.app_context():
        from app.models import User
        from app.services.poste_travail import build_poste_travail
        u = User.query.filter_by(email="pt-tech@example.org").first()
        with app.test_request_context():
            poste = build_poste_travail(u)
    labels = [a["label"] for a in poste["actions"]]
    assert "Gérer l'équipe" in labels
    assert "Régler les droits d'accès" in labels


# ---------- Rendu de l'accueil ----------

def test_dashboard_affiche_le_poste_de_travail(app):
    c = _login_role(app, "pt-dash@example.org", "direction")
    r = c.get("/dashboard")
    body = r.get_data(as_text=True)
    assert r.status_code == 200
    assert "Bonjour" in body
    assert "poste-card" in body


def test_mode_simple_allege_l_accueil(app):
    """En mode simple : pas de graphiques ni de « Que voulez-vous faire ? » ;
    en mode expert, tout revient."""
    c = _login_role(app, "pt-simple@example.org", "direction")

    # Mode simple (défaut)
    body = c.get("/dashboard").get_data(as_text=True)
    assert "poste-card" in body
    assert "Que voulez-vous faire ?" not in body
    assert 'id="chartBudget"' not in body
    assert "À traiter en premier" in body

    # Bascule en expert
    r = c.post("/ui-mode", data={"mode": "expert"})
    assert r.status_code == 302
    body = c.get("/dashboard").get_data(as_text=True)
    assert "Que voulez-vous faire ?" in body
    assert 'id="chartBudget"' in body
    assert "poste-card" in body


def test_dashboard_affiche_mes_actions_et_signaux_du_jour(app):
    c = _login_role(app, "pt-signaux@example.org", "responsable_secteur", secteur="Numérique")
    body = c.get("/dashboard").get_data(as_text=True)
    assert "Mes actions du jour" in body
    assert "Séances aujourd" in body
    assert "Présences à compléter" in body
    assert "Fiches à compléter" in body
    assert "Rappels ouverts" in body


def test_compteurs_sans_secteur_ne_deviennent_jamais_globaux(app):
    """Un compte borné mais mal configuré voit une alerte, pas les totaux des autres secteurs."""
    suf = uuid.uuid4().hex[:6]
    with app.app_context():
        from app.extensions import db
        from app.models import AtelierActivite, SessionActivite

        atelier = AtelierActivite(nom=f"PTGlobal{suf}", secteur=f"Autre{suf}", type_atelier="COLLECTIF")
        db.session.add(atelier)
        db.session.flush()
        db.session.add(SessionActivite(
            atelier_id=atelier.id,
            secteur=f"Autre{suf}",
            session_type="COLLECTIF",
            date_session=dt.date.today(),
        ))
        db.session.commit()

    _login_role(app, f"pt-sans-secteur-{suf}@example.org", "responsable_secteur", secteur=None)
    with app.app_context():
        from app.models import User
        from app.services.poste_travail import build_poste_travail

        user = User.query.filter_by(email=f"pt-sans-secteur-{suf}@example.org").first()
        with app.test_request_context():
            poste = build_poste_travail(user)

    assert any(signal["key"] == "missing_scope" for signal in poste["signals"])
    assert not any(signal["key"] == "today_sessions" for signal in poste["signals"])


def test_date_du_rendez_vous_prime_sur_date_session(app):
    """Une ancienne date_session ne doit pas faire compter un rendez-vous prévu demain aujourd'hui."""
    suf = uuid.uuid4().hex[:6]
    secteur = f"PTRdv{suf}"
    with app.app_context():
        from app.extensions import db
        from app.models import AtelierActivite, SessionActivite

        atelier = AtelierActivite(nom=f"PTRdv{suf}", secteur=secteur, type_atelier="COLLECTIF")
        db.session.add(atelier)
        db.session.flush()
        db.session.add(SessionActivite(
            atelier_id=atelier.id,
            secteur=secteur,
            session_type="COLLECTIF",
            date_session=dt.date.today(),
            rdv_date=dt.date.today() + dt.timedelta(days=1),
        ))
        db.session.commit()

    _login_role(app, f"pt-rdv-{suf}@example.org", "responsable_secteur", secteur=secteur)
    with app.app_context():
        from app.models import User
        from app.services.poste_travail import build_poste_travail

        user = User.query.filter_by(email=f"pt-rdv-{suf}@example.org").first()
        with app.test_request_context():
            poste = build_poste_travail(user)

    signal = next(row for row in poste["signals"] if row["key"] == "today_sessions")
    assert signal["value"] == 0


def test_dashboard_ne_montre_pas_les_noms_sans_droit_participants(app):
    suf = uuid.uuid4().hex[:8]
    nom_secret = f"NomInvisible{suf}"
    role_code = f"pt_dash_only_{suf}"
    email = f"pt-dash-only-{suf}@example.org"
    with app.app_context():
        from app.extensions import db
        from app.models import Participant, Permission, Role

        role = Role(code=role_code, label="Tableau seul")
        permission = Permission.query.filter_by(code="dashboard:view").first()
        role.permissions.append(permission)
        db.session.add_all([role, Participant(nom=nom_secret, prenom="Test", created_secteur="Numérique")])
        db.session.commit()

    c = _login_role(app, email, role_code, secteur="Numérique")
    c.post("/ui-mode", data={"mode": "expert"})
    body = c.get("/dashboard").get_data(as_text=True)
    assert nom_secret not in body
