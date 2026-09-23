"""Correctifs de sécurité complémentaires à ceux de la distribution Windows.

Repris de la branche Claude-Exe (audit de septembre 2026 et sa relecture
indépendante) pour ce qui n'était pas encore couvert sur main. Voir
docs/SECURITE-DISTRIBUTION-WINDOWS.md, références MCS-13 et suivantes.
"""
import os

import pytest

from tests.conftest import ADMIN_EMAIL, ADMIN_PASSWORD

SANS_DROIT_EMAIL = "sans-droit-complements@example.org"
SANS_DROIT_MDP = "motdepasse-tests"


@pytest.fixture(scope="module")
def compte_sans_droit(app):
    """Un compte actif, connecté, mais sans aucun rôle."""
    with app.app_context():
        from app.extensions import db
        from app.models import User

        if User.query.filter_by(email=SANS_DROIT_EMAIL).first() is None:
            u = User(email=SANS_DROIT_EMAIL, nom="Sans droit")
            u.set_password(SANS_DROIT_MDP)
            db.session.add(u)
            db.session.commit()
    return SANS_DROIT_EMAIL


@pytest.fixture()
def client_sans_droit(app, compte_sans_droit):
    c = app.test_client()
    r = c.post("/", data={"email": SANS_DROIT_EMAIL, "password": SANS_DROIT_MDP})
    assert r.status_code == 302
    return c


def _deposer(app, *parts, contenu=b"piece"):
    from pathlib import Path

    racine = Path(app.config["APP_UPLOAD_DIR"])
    chemin = racine.joinpath(*parts)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_bytes(contenu)
    return "/media/" + "/".join(parts)


# --- MCS-01 verrouillé : contournements connus de /media ----------------------------

@pytest.mark.parametrize("chemin", [
    "branding/..%2fjustifs/D12_20260101_facture.pdf",
    "branding/../justifs/D12_20260101_facture.pdf",
    "branding/..%5cjustifs/D12_20260101_facture.pdf",
    "uploads/branding/../justifs/D12_20260101_facture.pdf",
    "justifs/D12_20260101_facture.pdf",
])
def test_media_facture_jamais_servie_sans_connexion(app, client, chemin):
    _deposer(app, "justifs", "D12_20260101_facture.pdf", contenu=b"FACTURE-SECRETE")
    r = client.get("/media/" + chemin)
    assert r.status_code in (302, 401, 404)
    assert b"FACTURE-SECRETE" not in r.data


# --- MCS-13 : XSS ------------------------------------------------------------------------

def test_pastilles_de_filtre_sans_innerhtml(app):
    gabarit = open(os.path.join(app.root_path, "templates", "layout.html"), encoding="utf-8").read()
    assert "chip.innerHTML" not in gabarit
    assert "chipValue.textContent = value" in gabarit
    # Recherche globale : types échappés, liens internes seulement.
    assert "safeHref(item.url)" in gabarit
    assert 'data-facet-type="${type}"' not in gabarit


def test_legendes_du_tableau_de_bord_echappees(app):
    gabarit = open(os.path.join(app.root_path, "templates", "dashboard.html"), encoding="utf-8").read()
    assert "<strong>${item.label}</strong>" not in gabarit
    assert "escDash(item.label)" in gabarit


def test_infobulle_de_la_carte_echappee(app):
    script = open(os.path.join(app.root_path, "static", "js", "partenaires_carte.js"), encoding="utf-8").read()
    assert "bindTooltip(escapeHtml(p.nom)" in script
    assert "href='\" + p.fiche_url" not in script


# --- MCS-14 : annuaire des participants et kiosque ------------------------------------

def test_recherche_participants_refusee_sans_droit(client_sans_droit):
    assert client_sans_droit.get("/participants/search?q=du").status_code == 403


def test_recherche_participants_ok_avec_droit(admin_client):
    r = admin_client.get("/participants/search?q=du")
    assert r.status_code == 200
    assert "items" in r.get_json()


def test_pin_kiosque_freine_apres_10_echecs(app, client):
    from app.kiosk import routes as kiosque

    kiosque._ECHECS_PIN.reinitialiser()
    try:
        for _ in range(10):
            client.post("/kiosk/", data={"pin": "0000"})
        r = client.post("/kiosk/", data={"pin": "0000"}, follow_redirects=True)
        assert "Trop de codes erronés".encode() in r.data
    finally:
        kiosque._ECHECS_PIN.reinitialiser()


def test_facade_publique_ne_liste_pas_les_seances(app, monkeypatch):
    monkeypatch.setitem(app.config, "PUBLIC_BASE_URL", "http://192.168.1.10:8080")
    from app.kiosk import routes as kiosque

    ancien = app.config.get("KIOSK_PUBLIC_HOST")
    app.config["KIOSK_PUBLIC_HOST"] = "kiosque.exemple.fr"
    try:
        with app.test_request_context("/kiosk/", headers={"Host": "kiosque.exemple.fr"}):
            assert kiosque._via_facade_publique() is True
        with app.test_request_context("/kiosk/", headers={"Host": "192.168.1.10:8080"}):
            assert kiosque._via_facade_publique() is False
    finally:
        app.config["KIOSK_PUBLIC_HOST"] = ancien


def test_limiteur():
    from app.utils.limiteur import Limiteur

    lim = Limiteur(maximum=3, fenetre_secondes=60)
    assert [lim.autoriser("ip") for _ in range(4)] == [True, True, True, False]
    assert lim.autoriser("autre-ip") is True


# --- MCS-15 : lien de réinitialisation et en-tête Host -------------------------------------

def test_lien_de_reinitialisation_ignore_l_en_tete_host(app):
    from app.auth.routes import _build_external_reset_link

    with app.test_request_context("/password-reset", headers={"Host": "site-pirate.fr"}):
        from app.models import InstanceSettings

        row = InstanceSettings.query.first()
        ancien = row.public_base_url if row else None
        if row:
            row.public_base_url = None
        ancien_cfg = app.config.get("PUBLIC_BASE_URL")
        app.config["PUBLIC_BASE_URL"] = ""
        try:
            assert _build_external_reset_link("jeton") is None
        finally:
            app.config["PUBLIC_BASE_URL"] = ancien_cfg
            if row:
                row.public_base_url = ancien

    with app.test_request_context("/password-reset", headers={"Host": "127.0.0.1:8000"}):
        lien = _build_external_reset_link("jeton")
        assert lien and "site-pirate" not in lien


# --- MCS-16 : veille financements ------------------------------------------------------------

@pytest.mark.parametrize("url,attendu", [
    ("https://aides-territoires.beta.gouv.fr/api/", True),
    ("http://exemple.fr/flux.rss", True),
    ("file:///C:/ProgramData/MonCentreSocial/private/configuration.dpapi", False),
    ("ftp://exemple.fr/flux", False),
    ("javascript:alert(1)", False),
    ("", False),
])
def test_veille_seules_les_adresses_web_sont_lues(url, attendu):
    from app.services.veille_financements import url_source_valide

    assert url_source_valide(url) is attendu


def test_veille_telechargement_refuse_file():
    from app.services.veille_financements import _telecharger

    with pytest.raises(ValueError):
        _telecharger("file:///etc/passwd")


# --- MCS-17 : redirections ouvertes (filet global) --------------------------------------------

@pytest.mark.parametrize("cible", [
    "https://site-pirate.fr/", "//site-pirate.fr", "/\\site-pirate.fr", "////site-pirate.fr",
    "https:site-pirate.fr", "http:/site-pirate.fr", "https://site-pirate.fr@/",
])
def test_redirection_externe_bloquee(admin_client, cible):
    from urllib.parse import urlsplit

    r = admin_client.post("/guides/quitter", data={"next": cible})
    assert r.status_code == 302
    location = r.headers["Location"]
    lue = location.replace("\\", "/")
    assert not lue.startswith("//")
    morceaux = urlsplit(lue)
    assert morceaux.netloc in ("", "localhost"), location
    assert not (morceaux.scheme and not morceaux.netloc), location


def test_redirection_interne_conservee(admin_client):
    r = admin_client.post("/guides/quitter", data={"next": "/participants/"})
    assert r.headers["Location"].endswith("/participants/")


def test_referrer_policy_compatible_avec_les_cartes(client):
    r = client.get("/healthz")
    assert r.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
    assert r.headers.get("X-Frame-Options") == "SAMEORIGIN"


# --- MCS-18 : pages publiques annexes ------------------------------------------------------------

def test_setup_start_exige_connexion(client):
    assert client.get("/setup-start").status_code in (302, 401)


def test_qr_code_limite_a_l_application(client):
    pirate = client.get("/launcher/qr?u=https://site-pirate.fr/")
    defaut = client.get("/launcher/qr")
    assert pirate.status_code == 200 and pirate.mimetype == "image/svg+xml"
    # L'adresse étrangère est ignorée : même QR code que celui du kiosque.
    assert pirate.data == defaut.data


def test_qr_code_de_l_adresse_kiosque_accepte(app, client):
    """Le QR code d'émargement pointe sur l'adresse kiosque du réseau local
    (KIOSK_PUBLIC_BASE_URL, sans certificat) : il doit rester possible."""
    ancien = app.config.get("KIOSK_PUBLIC_BASE_URL")
    app.config["KIOSK_PUBLIC_BASE_URL"] = "http://192.168.1.20:8080"
    try:
        u = "http://192.168.1.20:8080/kiosk/session/jeton"
        r = client.get("/launcher/qr", query_string={"u": u})
        assert r.status_code == 200
        assert r.data != client.get("/launcher/qr?u=https://site-pirate.fr/").data
    finally:
        app.config["KIOSK_PUBLIC_BASE_URL"] = ancien


# --- MCS-19 : inventaire, outils -------------------------------------------------------------------

def test_inventaire_exige_un_droit(client_sans_droit):
    assert client_sans_droit.post("/inventaire/from_depense/1").status_code in (403, 404)
    assert client_sans_droit.post("/factures/1/validate").status_code in (403, 404)


def test_comptes_de_test_refuses_en_production(monkeypatch):
    import runpy

    monkeypatch.setenv("ERP_ENV", "production")
    with pytest.raises(SystemExit):
        runpy.run_path(
            os.path.join(os.path.dirname(os.path.dirname(__file__)), "tools", "create_test_users.py"),
            run_name="__main__",
        )


def test_connexion_admin_toujours_possible(app):
    c = app.test_client()
    r = c.post("/", data={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert r.status_code == 302
