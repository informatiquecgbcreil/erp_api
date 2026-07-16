"""Bouton de synchronisation du portail depuis l'ERP (page correspondances)."""


def test_page_correspondances_affiche_synchronisation(admin_client):
    r = admin_client.get("/pedagogie/portail-competences")
    assert r.status_code == 200
    assert "Synchronisation" in r.get_data(as_text=True)


def test_sync_depuis_erp_succes(admin_client, app, monkeypatch):
    from app.services import portail_apprenants as svc

    monkeypatch.setattr(
        svc, "synchroniser_attempts",
        lambda: {"nouvelles": 2, "recues": 5, "ignorees": 0, "since": "2026-06-24T10:00:00Z"},
    )
    monkeypatch.setitem(app.config, "PORTAIL_BASE_URL", "https://portail.test")
    monkeypatch.setitem(app.config, "PORTAIL_TOKEN", "secret")

    r = admin_client.post("/pedagogie/portail-competences/sync", follow_redirects=True)
    assert r.status_code == 200
    assert "Synchronisation réussie" in r.get_data(as_text=True)


def test_sync_non_configure(admin_client, app, monkeypatch):
    monkeypatch.setitem(app.config, "PORTAIL_BASE_URL", "")
    monkeypatch.setitem(app.config, "PORTAIL_TOKEN", "")

    r = admin_client.post("/pedagogie/portail-competences/sync", follow_redirects=True)
    assert r.status_code == 200
    assert "non configuré" in r.get_data(as_text=True)


def test_sync_erreur_reseau_ne_plante_pas(admin_client, app, monkeypatch):
    from app.services import portail_apprenants as svc

    def boom():
        raise svc.PortailError("réseau indisponible")

    monkeypatch.setattr(svc, "synchroniser_attempts", boom)
    monkeypatch.setitem(app.config, "PORTAIL_BASE_URL", "https://portail.test")
    monkeypatch.setitem(app.config, "PORTAIL_TOKEN", "secret")

    r = admin_client.post("/pedagogie/portail-competences/sync", follow_redirects=True)
    assert r.status_code == 200
    assert "a échoué" in r.get_data(as_text=True)


def test_sync_revient_sur_next(admin_client, app, monkeypatch):
    """Le bouton du tableau de bord revient sur le dashboard via ?next."""
    from app.services import portail_apprenants as svc

    monkeypatch.setattr(svc, "synchroniser_attempts",
                        lambda: {"nouvelles": 0, "recues": 0, "ignorees": 0, "since": None})
    monkeypatch.setitem(app.config, "PORTAIL_BASE_URL", "https://portail.test")
    monkeypatch.setitem(app.config, "PORTAIL_TOKEN", "secret")

    r = admin_client.post("/pedagogie/portail-competences/sync", data={"next": "/dashboard"})
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/dashboard")
