"""Test de fumée navigateur (Playwright) du générateur de budget.

Couvre précisément la classe de régression que la suite « sans navigateur »
ne peut pas attraper : un gestionnaire JS global qui désactive le bouton de
soumission et fait perdre l'action -> l'export renvoyait HTTP 400.

Le test lance l'app Flask réelle (CSRF activé, comme en production), pilote un
Chromium headless pour se connecter, saisir un montant et cliquer « Exporter
en Excel », puis vérifie qu'un fichier .xlsx est bien téléchargé.

Il se saute proprement si Playwright ou un binaire Chromium n'est pas
disponible (installation : pip install playwright + navigateur).
"""
import os
import socket
import threading
import time
import urllib.request

import pytest

pytest.importorskip("playwright.sync_api")

# Binaires Chromium pré-installés dans l'environnement (fallback hors CI).
_CHROME_CANDIDATES = [
    "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
    "/opt/pw-browsers/chromium_headless_shell-1194/chrome-linux/headless_shell",
]
_LAUNCH_ARGS = ["--no-sandbox", "--disable-dev-shm-usage"]


def _launch(p):
    """Lance Chromium : navigateur géré par Playwright (CI) sinon binaire local."""
    try:
        return p.chromium.launch(headless=True, args=_LAUNCH_ARGS)
    except Exception:
        pass
    for path in _CHROME_CANDIDATES:
        if os.path.exists(path):
            try:
                return p.chromium.launch(headless=True, executable_path=path, args=_LAUNCH_ARGS)
            except Exception:
                continue
    return None


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_ready(base: str, timeout: float = 20.0):
    end = time.time() + timeout
    while time.time() < end:
        try:
            urllib.request.urlopen(base + "/", timeout=1)
            return
        except Exception:
            time.sleep(0.2)
    raise RuntimeError("serveur de test non démarré")


@pytest.fixture(scope="module")
def live_server():
    import tempfile
    from werkzeug.serving import make_server
    from config import Config
    from app import create_app

    tmpdir = tempfile.mkdtemp(prefix="pw-erp-")
    dbfile = os.path.join(tmpdir, "pw.db").replace("\\", "/")

    prev_uri = Config.SQLALCHEMY_DATABASE_URI
    prev_opts = getattr(Config, "SQLALCHEMY_ENGINE_OPTIONS", None)
    Config.SQLALCHEMY_DATABASE_URI = "sqlite:///" + dbfile
    # Serveur multi-thread + SQLite : autorise l'accès inter-thread.
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

    # CSRF actif comme en production (le formulaire porte son csrf_token).
    app.config.update(WTF_CSRF_ENABLED=True)

    with app.app_context():
        from app.extensions import db
        from app.models import Role, User
        if not User.query.filter_by(email="pw@example.org").first():
            u = User(email="pw@example.org", nom="PW")
            u.set_password("pw-pass-123")
            u.roles.append(Role.query.filter_by(code="direction").first())
            db.session.add(u)
            db.session.commit()

    port = _free_port()
    server = make_server("127.0.0.1", port, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    try:
        _wait_ready(base)
        yield base
    finally:
        server.shutdown()
        thread.join(timeout=5)


@pytest.mark.browser
def test_export_generateur_dans_navigateur(live_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = _launch(p)
        if browser is None:
            pytest.skip("Chromium indisponible pour Playwright")
        try:
            page = browser.new_context(accept_downloads=True).new_page()

            # Connexion
            page.goto(live_server + "/", wait_until="domcontentloaded")
            page.fill('input[name="email"]', "pw@example.org")
            page.fill('input[name="password"]', "pw-pass-123")
            page.click('button:has-text("Se connecter")')
            page.wait_for_url("**/dashboard", timeout=15000)

            # Générateur : la structure est rendue par le JS (éditeur)
            page.goto(live_server + "/previsionnel/generateur", wait_until="domcontentloaded")
            page.wait_for_selector(".gen-amt", timeout=10000)
            # éditer : ajouter une ligne dans le premier compte, puis saisir un montant
            page.locator(".gen-add-line").first.click()
            page.locator(".gen-amt").first.fill("500")
            with page.expect_download(timeout=20000) as dl:
                page.click('button:has-text("Exporter en Excel")')
            download = dl.value

            assert download.suggested_filename.endswith(".xlsx")
            path = download.path()
            assert path is not None and os.path.getsize(path) > 100
        finally:
            browser.close()
