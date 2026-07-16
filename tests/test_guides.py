"""Guides pas à pas : un fil conducteur sur les écrans existants.

- la liste des guides est filtrée par les permissions ;
- le bandeau suit la personne de page en page (session) ;
- avancer/reculer/terminer/quitter fonctionnent ;
- on ne peut pas démarrer un guide sans les droits de ses écrans.
"""
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


def test_liste_filtree_par_permissions(app):
    # direction : tout ; admin_tech : pas participants:edit ni emargement:edit
    c = _login_role(app, "gd-dir@example.org", "direction")
    body = c.get("/guides").get_data(as_text=True)
    assert "accueille une nouvelle personne" in body
    assert "appel d" in body and "une séance" in body
    assert "Je prépare un bilan" in body

    c = _login_role(app, "gd-tech@example.org", "admin_tech")
    body = c.get("/guides").get_data(as_text=True)
    assert "accueille une nouvelle personne" not in body
    assert "une séance" not in body
    assert "Je prépare un bilan" in body  # admin_tech a stats:view


def test_demarrer_conduit_a_la_premiere_etape_et_le_bandeau_suit(app):
    c = _login_role(app, "gd-suit@example.org", "direction")
    r = c.post("/guides/accueillir/demarrer")
    # Étape 1 : la liste des participants (vérifier l'existant)
    assert r.status_code == 302
    assert "/participants/" in r.headers["Location"]
    # Le bandeau apparaît aussi sur une AUTRE page : il suit la personne.
    body = c.get("/dashboard").get_data(as_text=True)
    assert "Étape 1 sur 4" in body
    assert "pas déjà une fiche" in body
    c.post("/guides/quitter")


def test_avancer_reculer_et_etape_sans_page(app):
    c = _login_role(app, "gd-nav@example.org", "direction")
    c.post("/guides/accueillir/demarrer")

    # Étape 2 : création de fiche (page cible)
    r = c.post("/guides/suivant")
    assert "/participants/new" in r.headers["Location"]

    # Étape 3 : « compléter la fiche » n'a pas de page cible → on reste où on est
    r = c.post("/guides/suivant", data={"next": "/participants/new"})
    assert r.headers["Location"].endswith("/participants/new")
    body = c.get("/dashboard").get_data(as_text=True)
    assert "Étape 3 sur 4" in body

    # Retour à l'étape 2
    r = c.post("/guides/precedent")
    assert "/participants/new" in r.headers["Location"]
    body = c.get("/dashboard").get_data(as_text=True)
    assert "Étape 2 sur 4" in body
    c.post("/guides/quitter")


def test_terminer_le_guide_vide_la_session(app):
    c = _login_role(app, "gd-fin@example.org", "direction")
    c.post("/guides/faire_appel/demarrer")
    c.post("/guides/suivant")
    c.post("/guides/suivant")
    # Dernière étape → terminer
    r = c.post("/guides/suivant", data={"next": "/dashboard"}, follow_redirects=True)
    body = r.get_data(as_text=True)
    assert "Guide terminé" in body
    assert "guide-bandeau" not in body


def test_quitter_le_guide(app):
    c = _login_role(app, "gd-quit@example.org", "direction")
    c.post("/guides/accueillir/demarrer")
    assert "guide-bandeau" in c.get("/dashboard").get_data(as_text=True)
    c.post("/guides/quitter")
    assert "guide-bandeau" not in c.get("/dashboard").get_data(as_text=True)


def test_demarrage_refuse_sans_les_droits(app):
    """admin_tech n'a pas participants:edit : le guide accueillir lui est refusé."""
    c = _login_role(app, "gd-refus@example.org", "admin_tech")
    r = c.post("/guides/accueillir/demarrer", follow_redirects=True)
    assert "pas disponible avec tes droits" in r.get_data(as_text=True)
    assert "guide-bandeau" not in c.get("/dashboard").get_data(as_text=True)
