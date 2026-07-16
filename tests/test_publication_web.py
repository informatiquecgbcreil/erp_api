"""Publication FTPS du programme public (service + page + bouton)."""
import pytest


def test_page_publication_admin(admin_client):
    r = admin_client.get("/programme/publication")
    assert r.status_code == 200
    # En test, aucune config FTP -> message d'aide à la configuration.
    assert "non configurée" in r.get_data(as_text=True)


def test_page_publication_refuse_anonyme(client):
    r = client.get("/programme/publication")
    assert r.status_code in (302, 401, 403)


def test_publier_sans_config_leve_erreur(app):
    from app.services.publication_web import publier_programme

    with app.app_context():
        app.config.update(
            PROGRAMME_FTP_HOST="", PROGRAMME_FTP_USER="", PROGRAMME_FTP_PASSWORD=""
        )
        with pytest.raises(RuntimeError):
            publier_programme()


def test_publier_envoie_le_fichier(app, monkeypatch):
    """Avec une config valide, la page programme est bien envoyée par FTPS."""
    from app.services import publication_web

    envois = {}

    class FauxFTPS:
        def connect(self, host, port, timeout=None):
            envois["host"] = host
            envois["port"] = port

        def login(self, user, password):
            envois["user"] = user

        def prot_p(self):
            envois["securise"] = True

        def cwd(self, dossier):
            envois["dir"] = dossier

        def storbinary(self, cmd, fp):
            envois["cmd"] = cmd
            envois["data"] = fp.read()

        def quit(self):
            envois["quit"] = True

    monkeypatch.setattr(publication_web, "FTP_TLS", lambda: FauxFTPS())

    with app.app_context():
        app.config.update(
            PROGRAMME_FTP_HOST="ftp.example.org",
            PROGRAMME_FTP_USER="u",
            PROGRAMME_FTP_PASSWORD="p",
            PROGRAMME_FTP_DIR="public_html",
            PROGRAMME_FTP_FILENAME="programme.html",
        )
        info = publication_web.publier_programme()

    assert envois["host"] == "ftp.example.org"
    assert envois["securise"] is True
    assert envois["dir"] == "public_html"
    assert envois["cmd"] == "STOR programme.html"
    assert b"Programme des activit" in envois["data"]
    assert info["octets"] > 0
