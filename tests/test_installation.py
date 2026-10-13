"""Test du premier démarrage : assistant d'installation puis connexion."""


def test_premier_demarrage_complet(fresh_app, monkeypatch):
    from app.services import territoire

    monkeypatch.setattr(territoire, "localiser_ville_siege", lambda ville, cp: {"lat": 49.43, "lon": 2.08})
    c = fresh_app.test_client()

    # Base vierge : tout redirige vers l'assistant d'installation
    r = c.get("/dashboard")
    assert r.status_code == 302
    assert "/setup/" in r.headers["Location"]

    # L'assistant s'affiche
    assert c.get("/setup/").status_code == 200

    # Création du premier compte admin
    r = c.post(
        "/setup/",
        data={
            "app_name": "App Test",
            "organization_name": "Orga Test",
            "admin_name": "Premier Admin",
            "admin_email": "premier@example.org",
            "admin_password": "motdepasse-tests",
            "territoire_ville": "beauvais",
            "territoire_code_postal": "60000",
        },
    )
    assert r.status_code == 302, "le wizard doit rediriger vers la connexion"

    # La ville siège est réglée dès l'installation, et placée sur la carte.
    with fresh_app.app_context():
        reglage = territoire.territoire()
        assert (reglage.ville_siege, reglage.code_postal) == ("Beauvais", "60000")
        assert reglage.centre_carte == {"lat": 49.43, "lon": 2.08}

    # Connexion avec le compte créé, accès au dashboard
    r = c.post("/", data={"email": "premier@example.org", "password": "motdepasse-tests"})
    assert r.status_code == 302
    assert "/dashboard" in r.headers["Location"]
    assert c.get("/dashboard").status_code == 200
