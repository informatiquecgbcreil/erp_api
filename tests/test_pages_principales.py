"""Tests de non-régression : les pages principales répondent en admin.

Couvre notamment toutes les routes du blueprint ``main`` éclaté en
modules (dashboard, hubs, recherche, subventions, stats, bilan global,
suivi rappels, qualité données, contrôle).
"""
import pytest

PAGES = [
    # dashboard
    "/dashboard",
    "/dashboard/personnaliser",
    # hubs et documents
    "/publics",
    "/activites",
    "/bilans-hub",
    "/ressources",
    "/documents",
    # recherche
    "/recherche?q=test",
    "/api/global-search?q=test",
    # subventions
    "/subventions",
    # stats
    "/stats",
    "/stats-bilans",
    # bilan global
    "/bilan",
    # suivi et qualité
    "/suivi-rappels",
    "/qualite-donnees",
    "/qualite-donnees/export.csv",
    # contrôle et diagnostic
    "/controle",
    "/controle/navigation",
    "/setup-start",
    "/rbac-test",
]


@pytest.mark.parametrize("url", PAGES)
def test_page_repond_en_admin(admin_client, url):
    r = admin_client.get(url)
    assert r.status_code == 200, f"GET {url} -> {r.status_code}"


def test_alias_bilan_global_redirige(admin_client):
    r = admin_client.get("/bilan-global")
    assert r.status_code == 302
    assert "/bilan" in r.headers["Location"]


def test_export_bilan_xlsx(admin_client):
    r = admin_client.get("/bilan/export.xlsx")
    assert r.status_code == 200
    assert "spreadsheet" in r.content_type


def test_export_depenses_csv(admin_client):
    r = admin_client.get("/export/depenses.csv")
    assert r.status_code == 200


def test_etat_vide_explique_et_propose_une_action(admin_client):
    r = admin_client.get("/participants/?q=personne-qui-n-existe-vraiment-pas")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "ui-empty-state" in body
    assert "Aucune personne ne correspond à votre recherche" in body
    assert "Réinitialiser les filtres" in body


def test_menu_expert_utilise_des_mots_comprehensibles(admin_client):
    admin_client.post("/ui-mode", data={"mode": "expert"})
    body = admin_client.get("/dashboard").get_data(as_text=True)
    for label in ["Budgets demandés", "Résultats &amp; bilans", "Droits d’accès", "Vérifier les liens"]:
        assert label in body
    assert ">Audit navigation<" not in body
