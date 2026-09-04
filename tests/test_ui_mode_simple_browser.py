"""Garde-fous du repli progressif, vérifiés dans un vrai navigateur.

Les trois protections du mode simple sont écrites en JavaScript : aucune suite
« sans navigateur » ne peut les couvrir. Or ce sont elles qui font la
différence entre un écran allégé et un écran qui perd l'utilisateur —
typiquement un formulaire qui refuse de s'enregistrer sans rien dire.

Se saute proprement si Playwright ou Chromium ne sont pas disponibles.
"""
import os
import threading

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_generateur_browser import _free_port, _launch, _wait_ready  # noqa: E402

EMAIL, MOTDEPASSE = "pw-ui@example.org", "pw-pass-123"


@pytest.fixture(scope="module")
def serveur_ui():
    """Application réelle (CSRF actif) avec un participant, pour les filtres."""
    import tempfile
    from werkzeug.serving import make_server
    from config import Config
    from app import create_app

    dbfile = os.path.join(tempfile.mkdtemp(prefix="pw-ui-"), "ui.db").replace("\\", "/")
    prev_uri = Config.SQLALCHEMY_DATABASE_URI
    prev_opts = getattr(Config, "SQLALCHEMY_ENGINE_OPTIONS", None)
    Config.SQLALCHEMY_DATABASE_URI = "sqlite:///" + dbfile
    Config.SQLALCHEMY_ENGINE_OPTIONS = {"connect_args": {"check_same_thread": False}}
    try:
        app = create_app()
    finally:
        Config.SQLALCHEMY_DATABASE_URI = prev_uri
        if prev_opts is None:
            try:
                delattr(Config, "SQLALCHEMY_ENGINE_OPTIONS")
            except Exception:
                Config.SQLALCHEMY_ENGINE_OPTIONS = {}
        else:
            Config.SQLALCHEMY_ENGINE_OPTIONS = prev_opts

    app.config.update(WTF_CSRF_ENABLED=True)
    with app.app_context():
        from app.extensions import db
        from app.models import Participant, Role, User

        if not User.query.filter_by(email=EMAIL).first():
            u = User(email=EMAIL, nom="PW UI")
            u.set_password(MOTDEPASSE)
            u.roles.append(Role.query.filter_by(code="direction").first())
            db.session.add(u)
        if not Participant.query.first():
            db.session.add(Participant(nom="Test", prenom="Filtre", genre="Femme"))
        db.session.commit()

    port = _free_port()
    serveur = make_server("127.0.0.1", port, app, threaded=True)
    fil = threading.Thread(target=serveur.serve_forever, daemon=True)
    fil.start()
    base = f"http://127.0.0.1:{port}"
    try:
        _wait_ready(base)
        yield base
    finally:
        serveur.shutdown()
        fil.join(timeout=5)


def _connecter(page, base: str) -> None:
    page.goto(base + "/", wait_until="domcontentloaded")
    page.fill('input[name="email"]', EMAIL)
    page.fill('input[name="password"]', MOTDEPASSE)
    page.click('button[type="submit"]')
    page.wait_for_load_state("domcontentloaded")


@pytest.mark.browser
def test_garde_fous_du_repli(serveur_ui):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        navigateur = _launch(p)
        if navigateur is None:
            pytest.skip("Chromium indisponible pour Playwright")
        try:
            page = navigateur.new_context().new_page()
            _connecter(page, serveur_ui)

            # --- 1. En mode simple, les blocs secondaires sont bien repliés ---
            page.goto(serveur_ui + "/depense/nouvelle", wait_until="networkidle")
            blocs = page.locator("details.bloc-avance")
            assert blocs.count() >= 2, "le formulaire devrait porter des blocs repliables"
            ouverts = page.evaluate(
                "() => [...document.querySelectorAll('details.bloc-avance')]"
                ".filter(d => d.open).length"
            )
            assert ouverts == 0, "en mode simple, aucun bloc ne doit être ouvert au chargement"

            # --- 2. Rien n'est perdu : les champs repliés sont bien dans le formulaire ---
            assert page.locator('input[name="fournisseur"]').count() == 1, (
                "un champ replié doit rester présent dans le DOM, donc soumis"
            )

            # --- 3. Un champ obligatoire replié rouvre son bloc ---------------
            # Aucun champ requis n'est replié aujourd'hui (un test statique
            # l'interdit) : on en fabrique un pour éprouver le garde-fou
            # lui-même, qui doit rattraper la faute si elle était commise.
            page.evaluate(
                "() => { const c = document.querySelector("
                "'details.bloc-avance input[name=\"fournisseur\"]');"
                " c.required = true; c.value = ''; }"
            )
            # On soumet le formulaire de la dépense, pas le premier de la page
            # (le gabarit commun en porte d'autres : bascule de mode, déconnexion).
            page.evaluate(
                "() => document.querySelector('input[name=\"fournisseur\"]')"
                ".form.requestSubmit()"
            )
            page.wait_for_timeout(300)
            rouvert = page.evaluate(
                "() => !!document.querySelector('details.bloc-avance input[name=\"fournisseur\"]')"
                ".closest('details').open"
            )
            assert rouvert, (
                "un champ obligatoire vide dans un bloc replié doit rouvrir ce bloc, "
                "sinon l'enregistrement échoue sans que l'utilisateur voie pourquoi"
            )

            # --- 4. Un bloc déjà rempli s'ouvre de lui-même -------------------
            # Cas quotidien : filtrer la liste des participants. Sans cela, le
            # filtre actif reste invisible et la liste paraît vide sans raison.
            page.goto(serveur_ui + "/participants/?genre=Femme", wait_until="networkidle")
            page.wait_for_timeout(300)
            filtres_ouverts = page.evaluate(
                "() => [...document.querySelectorAll('details.details-card')]"
                ".some(d => d.open && d.querySelector('select[name=\"genre\"]'))"
            )
            assert filtres_ouverts, (
                "un filtre actif doit rouvrir le bloc « Filtres avancés » : sinon "
                "l'utilisateur voit une liste filtrée sans comprendre pourquoi"
            )
        finally:
            navigateur.close()
