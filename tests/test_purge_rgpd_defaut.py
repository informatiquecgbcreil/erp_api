"""La purge RGPD automatique ne démarre jamais seule.

Avant : sans réglage, PURGE_INACTIFS_AUTO valait « 1 ». Une installation
neuve ou juste reprise s'armait au premier passage et anonymisait le
lendemain, de façon irréversible, des personnes jugées inactives — dont
certaines encore actives (audit 3.4). Désormais elle est désactivée tant
que la direction ne l'allume pas (Contrôle → Purge RGPD).
"""
import pytest


@pytest.fixture
def sans_reglage_en_base(app):
    from app.extensions import db
    from app.models import InstanceSettings
    with app.app_context():
        reglages = InstanceSettings.query.first()
        ancien = reglages.purge_rgpd_auto if reglages else None
        if reglages:
            reglages.purge_rgpd_auto = None
            db.session.commit()
    yield
    with app.app_context():
        reglages = InstanceSettings.query.first()
        if reglages:
            reglages.purge_rgpd_auto = ancien
            db.session.commit()


def test_desactivee_par_defaut(app, sans_reglage_en_base, monkeypatch):
    from app.services.purge_rgpd import purge_auto_active
    monkeypatch.delenv("PURGE_INACTIFS_AUTO", raising=False)
    with app.app_context():
        assert purge_auto_active() is False


@pytest.mark.parametrize("valeur, attendu", [("1", True), ("true", True), (" 1 ", True),
                                             ("0", False), ("", False), ("non", False)])
def test_variable_d_environnement_explicite(app, sans_reglage_en_base, monkeypatch, valeur, attendu):
    """Une ancienne installation qui l'avait allumée exprès (.env repris) la garde."""
    from app.services.purge_rgpd import purge_auto_active
    monkeypatch.setenv("PURGE_INACTIFS_AUTO", valeur)
    with app.app_context():
        assert purge_auto_active() is attendu


def test_reglage_de_la_page_prioritaire(app, admin_client, monkeypatch):
    from app.services.purge_rgpd import purge_auto_active
    monkeypatch.setenv("PURGE_INACTIFS_AUTO", "1")
    admin_client.post("/controle/purge-rgpd/reglages", data={"annees": "3"})  # case décochée
    with app.app_context():
        assert purge_auto_active() is False
    monkeypatch.setenv("PURGE_INACTIFS_AUTO", "0")
    admin_client.post("/controle/purge-rgpd/reglages", data={"annees": "3", "auto": "1"})
    with app.app_context():
        assert purge_auto_active() is True
    admin_client.post("/controle/purge-rgpd/reglages", data={"annees": "3"})


def test_aucune_purge_declenchee_par_une_requete_sans_activation(app, admin_client, sans_reglage_en_base, monkeypatch):
    from app.services import purge_rgpd
    monkeypatch.delenv("PURGE_INACTIFS_AUTO", raising=False)
    appels = []
    monkeypatch.setattr(purge_rgpd, "purge_quotidienne_si_necessaire", lambda: appels.append(1))
    assert admin_client.get("/dashboard").status_code in (200, 302)
    assert appels == []
