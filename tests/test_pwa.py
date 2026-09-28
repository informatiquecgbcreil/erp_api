"""Application installable (PWA) : icône sur l'écran d'accueil, sans store,
sans aucune donnée gardée sur l'appareil."""
import io
import json
import os
import threading
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[1]


def test_manifeste_installable(client):
    reponse = client.get("/manifest.webmanifest")
    assert reponse.status_code == 200
    assert reponse.mimetype == "application/manifest+json"
    manifeste = json.loads(reponse.get_data(as_text=True))
    assert manifeste["display"] == "standalone" and manifeste["scope"] == "/"
    assert manifeste["start_url"] == "/" and manifeste["name"] and manifeste["short_name"]
    from PIL import Image
    tailles = {}
    for icone in manifeste["icons"]:
        image = client.get(icone["src"])
        assert image.status_code == 200, icone["src"]
        with Image.open(io.BytesIO(image.data)) as im:
            assert f"{im.width}x{im.height}" == icone["sizes"]
        tailles.setdefault(icone["purpose"], set()).add(icone["sizes"])
    assert {"192x192", "512x512"} <= tailles["any"] and "512x512" in tailles["maskable"]


def test_service_worker_ne_garde_aucune_donnee(client):
    reponse = client.get("/sw.js")
    assert reponse.status_code == 200 and reponse.mimetype == "text/javascript"
    assert reponse.headers["Service-Worker-Allowed"] == "/" and reponse.headers["Cache-Control"] == "no-cache"
    code = reponse.get_data(as_text=True)
    # Seules la page hors connexion et l'icône sont mises en cache ; aucune
    # réponse du serveur n'est enregistrée (pas de cache.put).
    assert 'cache.addAll([HORS_LIGNE, "/static/pwa/icone-192.png"])' in code
    assert "cache.put" not in code and ".put(" not in code
    assert 'evenement.request.mode !== "navigate"' in code


def test_page_hors_connexion_sans_donnees(client):
    reponse = client.get("/hors-ligne")
    assert reponse.status_code == 200
    texte = reponse.get_data(as_text=True)
    assert "réseau du centre" in texte and "csrf" not in texte.lower()


def test_page_installer_publique_avec_qr_vers_l_adresse_publique(app, client):
    import segno
    ancien = app.config.get("PUBLIC_BASE_URL")
    app.config["PUBLIC_BASE_URL"] = "https://gestion.cgbcreil.com:8443"
    try:
        page = client.get("/installer").get_data(as_text=True)
        adresse = "https://gestion.cgbcreil.com:8443/installer"
        assert adresse in page
        assert 'data-appareil="ios"' in page and 'data-appareil="android"' in page and 'data-appareil="pc"' in page
        assert "Imprimer l'affiche" not in page, "affiche réservée aux personnes connectées"
        # Le QR code encode bien cette adresse (et non l'adresse par défaut).
        qr = client.get("/launcher/qr", query_string={"u": adresse})
        attendu = io.BytesIO()
        segno.make(adresse, error="M").save(attendu, kind="svg", xmldecl=False, svgclass="qr")
        assert qr.status_code == 200 and qr.data == attendu.getvalue()
    finally:
        app.config["PUBLIC_BASE_URL"] = ancien


def test_affiche_reservee_aux_personnes_connectees(client, admin_client):
    assert client.get("/installer/affiche").status_code in (302, 401)
    page = admin_client.get("/installer/affiche").get_data(as_text=True)
    assert "window.print()" in page and "/installer" in page


def test_toutes_les_pages_annoncent_l_appli(client, admin_client):
    for page in (client.get("/").get_data(as_text=True), admin_client.get("/dashboard").get_data(as_text=True)):
        assert '<link rel="manifest" href="/manifest.webmanifest">' in page
        assert "serviceWorker.register('/sw.js'" in page
        assert 'rel="apple-touch-icon"' in page
    assert "/installer" in client.get("/").get_data(as_text=True)


def test_icones_regenerees_a_l_identique(tmp_path, monkeypatch):
    """Les PNG livrés sont ceux que produit tools/generer_icones_pwa.py."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("generer_icones_pwa", RACINE / "tools" / "generer_icones_pwa.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "SORTIE", tmp_path)
    module.main()
    from PIL import Image, ImageChops
    for fichier in ("icone-192.png", "icone-512.png", "icone-maskable-512.png", "apple-touch-icon.png"):
        with Image.open(tmp_path / fichier) as neuf, Image.open(RACINE / "app" / "static" / "pwa" / fichier) as livre:
            assert neuf.size == livre.size and ImageChops.difference(neuf.convert("RGBA"), livre.convert("RGBA")).getbbox() is None, fichier


# ---------------------------------------------------------------------------
# Vrai navigateur : Chrome juge l'application installable, et hors réseau la
# page « hors connexion » s'affiche au lieu d'une erreur.
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def serveur_pwa():
    pytest.importorskip("playwright.sync_api")
    import tempfile
    from werkzeug.serving import make_server
    from config import Config
    from app import create_app
    from tests.test_generateur_browser import _free_port, _wait_ready

    dbfile = os.path.join(tempfile.mkdtemp(prefix="pw-pwa-"), "pwa.db").replace("\\", "/")
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
    port = _free_port()
    serveur = make_server("127.0.0.1", port, app, threaded=True)
    fil = threading.Thread(target=serveur.serve_forever, daemon=True)
    fil.start()
    base = f"http://127.0.0.1:{port}"  # 127.0.0.1 : contexte sécurisé pour Chrome
    try:
        _wait_ready(base)
        yield base
    finally:
        serveur.shutdown()
        fil.join(timeout=5)


def _contexte_persistant(p, dossier):
    """Profil non « incognito » (l'installation y est refusée)."""
    from tests.test_generateur_browser import _CHROME_CANDIDATES, _LAUNCH_ARGS
    try:
        return p.chromium.launch_persistent_context(str(dossier), headless=True, args=_LAUNCH_ARGS)
    except Exception:
        pass
    for chemin in _CHROME_CANDIDATES:
        if os.path.exists(chemin) and "headless_shell" not in chemin:
            try:
                return p.chromium.launch_persistent_context(str(dossier), headless=True, executable_path=chemin, args=_LAUNCH_ARGS)
            except Exception:
                continue
    return None


def test_chrome_juge_l_application_installable_et_gere_le_hors_ligne(serveur_pwa, tmp_path):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        contexte = _contexte_persistant(p, tmp_path / "profil")
        if contexte is None:
            pytest.skip("Chromium indisponible")
        try:
            page = contexte.new_page()
            page.goto(serveur_pwa + "/installer", wait_until="load")
            page.wait_for_function("navigator.serviceWorker.ready.then(() => true)", timeout=15000)
            page.wait_for_function("navigator.serviceWorker.controller !== null || true")
            session = contexte.new_cdp_session(page)
            erreurs = session.send("Page.getInstallabilityErrors")["installabilityErrors"]
            assert erreurs == [], erreurs
            manifeste = session.send("Page.getAppManifest")
            assert not manifeste.get("errors"), manifeste.get("errors")
            # Section PC affichée sur un navigateur de bureau, pas de message « non sécurisé ».
            assert page.is_visible('[data-appareil="pc"]') and not page.is_visible('[data-etat="non-securise"]')
            # Le service worker contrôle les pages : hors réseau, page d'aide au lieu d'une erreur.
            page.reload(wait_until="load")
            page.wait_for_function("navigator.serviceWorker.controller !== null", timeout=15000)
            contexte.set_offline(True)
            page.goto(serveur_pwa + "/dashboard", wait_until="load")
            assert "Le serveur du centre ne répond pas" in page.content()
            contexte.set_offline(False)
            # Aucune page de l'application n'a été mise en cache.
            cles = page.evaluate("""async () => {
                const noms = await caches.keys(); const urls = [];
                for (const n of noms) { for (const r of await (await caches.open(n)).keys()) urls.push(new URL(r.url).pathname); }
                return urls.sort(); }""")
            assert cles == ["/hors-ligne", "/static/pwa/icone-192.png"], cles
        finally:
            contexte.close()
