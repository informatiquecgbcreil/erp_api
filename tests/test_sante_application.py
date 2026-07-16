"""Tests de santé : l'application démarre et expose ses routes."""


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.get_json() == {"status": "ok"}


def test_endpoints_principaux_enregistres(app):
    endpoints = {rule.endpoint for rule in app.url_map.iter_rules()}
    attendus = {
        "auth.login",
        "setup.wizard",
        "main.dashboard",
        "main.subventions_list",
        "main.stats",
        "main.global_search_results",
        "main.suivi_rappels",
        "main.qualite_donnees_transverse",
        "main.bilan_global",
        "main.hub_publics",
        "main.direction_pilotage",
        "main.comite_pilotage",
        "main.journal_metier",
        "main.parcours_metier",
        "main.assistant_bilan_financeur",
        "main.qualite_bilans",
        "admin.matrice_droits",
        "main.controle",
    }
    manquants = attendus - endpoints
    assert not manquants, f"Endpoints manquants : {manquants}"


def test_pas_de_route_en_double(app):
    regles = [(r.rule, tuple(sorted(r.methods - {"HEAD", "OPTIONS"}))) for r in app.url_map.iter_rules()]
    doublons = {r for r in regles if regles.count(r) > 1}
    assert not doublons, f"Routes en double : {doublons}"


def test_chargeur_dotenv(tmp_path, monkeypatch):
    """Le fichier .env est chargé sans écraser l'environnement existant."""
    from config import _charger_dotenv

    fichier = tmp_path / ".env"
    fichier.write_text(
        "# commentaire\nNOUVELLE_VAR_ENV_TEST=valeur1\nDEJA_LA=remplacee\n\nGUILLEMETS=\"abc\"\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DEJA_LA", "originale")
    monkeypatch.delenv("NOUVELLE_VAR_ENV_TEST", raising=False)

    _charger_dotenv(str(fichier))

    import os
    assert os.environ["NOUVELLE_VAR_ENV_TEST"] == "valeur1"
    assert os.environ["DEJA_LA"] == "originale", "l'environnement existant doit rester prioritaire"
    assert os.environ["GUILLEMETS"] == "abc"
    os.environ.pop("NOUVELLE_VAR_ENV_TEST", None)
