"""Tests d'authentification : accès anonyme, connexion, déconnexion."""
import pytest

from tests.conftest import ADMIN_EMAIL, ADMIN_PASSWORD


@pytest.mark.parametrize(
    "url",
    [
        "/dashboard",
        "/subventions",
        "/stats",
        "/suivi-rappels",
        "/qualite-donnees",
        "/bilan",
        "/admin/users",
    ],
)
def test_pages_protegees_redirigent_les_anonymes(client, url):
    r = client.get(url)
    assert r.status_code == 302, f"{url} devrait rediriger les anonymes"


def test_mauvais_mot_de_passe_refuse(client):
    r = client.post("/", data={"email": ADMIN_EMAIL, "password": "mauvais-mot-de-passe"})
    assert r.status_code == 200
    assert "Identifiants invalides" in r.get_data(as_text=True)


def test_connexion_puis_deconnexion(client):
    r = client.post("/", data={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert r.status_code == 302
    assert "/dashboard" in r.headers["Location"]

    assert client.get("/dashboard").status_code == 200

    r = client.post("/logout")
    assert r.status_code == 302

    r = client.get("/dashboard")
    assert r.status_code == 302, "après déconnexion, le dashboard doit rediriger"
