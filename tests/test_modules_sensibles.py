"""Tests des modules sensibles : participants, projets, budget, activité.

Étend la couverture au-delà du blueprint ``main`` : pages principales,
pages de détail sur des données créées en fixture, création d'un
participant par le formulaire, et cloisonnement par secteur du module
activité.
"""
import pytest

RESP2_EMAIL = "familles2@example.org"
RESP2_PASSWORD = "motdepasse-tests"


@pytest.fixture(scope="module")
def donnees_modules(app):
    """Un participant, un projet, deux ateliers (Familles / Numérique),
    et un second responsable de secteur Familles."""
    with app.app_context():
        from app.extensions import db
        from app.models import AtelierActivite, Participant, Projet, Role, User

        def get_or_create(model, defaults=None, **kwargs):
            obj = model.query.filter_by(**kwargs).first()
            if obj is None:
                obj = model(**kwargs, **(defaults or {}))
                db.session.add(obj)
            return obj

        participant = get_or_create(Participant, nom="Testeur", prenom="Module")
        projet = get_or_create(Projet, nom="Projet test modules", secteur="Familles")
        atelier_familles = get_or_create(AtelierActivite, nom="Atelier test Familles", secteur="Familles")
        atelier_numerique = get_or_create(AtelierActivite, nom="Atelier test Numérique", secteur="Numérique")

        user = User.query.filter_by(email=RESP2_EMAIL).first()
        if user is None:
            user = User(email=RESP2_EMAIL, nom="Responsable Familles 2")
            user.set_password(RESP2_PASSWORD)
            user.secteur_assigne = "Familles"
            user.roles.append(Role.query.filter_by(code="responsable_secteur").first())
            db.session.add(user)

        db.session.commit()
        return {
            "participant_id": participant.id,
            "projet_id": projet.id,
            "atelier_familles_id": atelier_familles.id,
            "atelier_numerique_id": atelier_numerique.id,
        }


PAGES_ADMIN = [
    # participants (données personnelles)
    "/participants/",
    "/participants/new",
    "/participants/duplicates",
    "/participants/search?q=test",
    # projets et finance
    "/projets",
    "/projets/new",
    "/finance",
    "/finance/simple",
    "/finance/secteur",
    # budget / dépenses
    "/depenses",
    "/depense/nouvelle",
    # activité
    "/activite/",
    "/activite/atelier/new?secteur=Familles",
    "/activite/participants",
    "/activite/attestations",
    "/activite/modeles-emargement",
]


@pytest.mark.parametrize("url", PAGES_ADMIN)
def test_page_module_repond_en_admin(admin_client, donnees_modules, url):
    r = admin_client.get(url)
    assert r.status_code == 200, f"GET {url} -> {r.status_code}"


def test_pages_detail_en_admin(admin_client, donnees_modules):
    d = donnees_modules
    detail_urls = [
        f"/participants/{d['participant_id']}/edit",
        f"/participants/{d['participant_id']}/synthese",
        f"/projets/{d['projet_id']}",
        f"/projets/{d['projet_id']}/budget/charges",
        f"/activite/atelier/{d['atelier_familles_id']}/edit",
        f"/activite/atelier/{d['atelier_familles_id']}/sessions",
    ]
    for url in detail_urls:
        r = admin_client.get(url)
        assert r.status_code == 200, f"GET {url} -> {r.status_code}"


@pytest.mark.parametrize("url", ["/participants/", "/projets", "/depenses", "/activite/"])
def test_pages_modules_redirigent_les_anonymes(client, url):
    assert client.get(url).status_code == 302


def test_creation_participant_par_formulaire(app, admin_client):
    r = admin_client.post(
        "/participants/new",
        data={"nom": "Dupont", "prenom": "Camille", "type_public": "H"},
    )
    assert r.status_code == 302, "la création doit rediriger"

    with app.app_context():
        from app.models import Participant

        cree = Participant.query.filter_by(nom="Dupont", prenom="Camille").first()
        assert cree is not None, "le participant créé doit être en base"


def test_cloisonnement_secteur_activite(app, donnees_modules):
    """Un responsable Familles ne peut pas modifier un atelier Numérique."""
    c = app.test_client()
    r = c.post("/", data={"email": RESP2_EMAIL, "password": RESP2_PASSWORD})
    assert r.status_code == 302

    # Atelier de son secteur : accessible
    r = c.get(f"/activite/atelier/{donnees_modules['atelier_familles_id']}/edit")
    assert r.status_code == 200

    # Atelier d'un autre secteur : refus (redirection vers l'accueil activité)
    r = c.get(f"/activite/atelier/{donnees_modules['atelier_numerique_id']}/edit")
    assert r.status_code == 302
    assert "/activite/" in r.headers["Location"]
