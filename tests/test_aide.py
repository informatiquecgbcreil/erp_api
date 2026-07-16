"""Tests de l'aide intégrée : registre contextuel, bandeau, notice."""


def test_chaque_cle_du_registre_correspond_a_une_vraie_page(app):
    """Verrou anti-divergence : si une page est renommée ou supprimée,
    l'aide qui la référence doit être mise à jour (sinon la CI casse)."""
    from app.aide.contenu import AIDE_PAGES

    endpoints = set(app.view_functions.keys())
    inconnus = sorted(set(AIDE_PAGES) - endpoints)
    assert not inconnus, f"Aide référençant des pages inexistantes : {inconnus}"


def test_contenu_aide_complet(app):
    """Chaque entrée d'aide a un titre et un résumé non vides."""
    from app.aide.contenu import AIDE_PAGES, NOTICE

    for endpoint, aide in AIDE_PAGES.items():
        assert aide.get("titre"), f"{endpoint}: titre manquant"
        assert aide.get("resume"), f"{endpoint}: résumé manquant"

    assert len(NOTICE) >= 8, "la notice doit couvrir tous les grands modules"
    for chapitre in NOTICE:
        assert chapitre["id"] and chapitre["titre"] and chapitre["sections"], (
            f"chapitre incomplet : {chapitre.get('titre')}"
        )
        for titre, paragraphes in chapitre["sections"]:
            assert titre and paragraphes, f"section vide dans {chapitre['titre']}"


def test_couverture_phase2(app):
    """La phase 2 couvre largement les modules secondaires et ajoute des chapitres."""
    from app.aide.contenu import AIDE_PAGES, NOTICE

    assert len(AIDE_PAGES) >= 60, "le registre doit couvrir l'essentiel des pages métier"

    # Quelques modules secondaires clés doivent être documentés
    for endpoint in [
        "pedagogie.index", "partenaires.orientations", "quartiers.index",
        "questionnaires.index", "insertion.index", "previsionnel.index",
        "bilans.bilan_secteur", "admin.droits", "admin.instance_settings",
    ]:
        assert endpoint in AIDE_PAGES, f"page secondaire non documentée : {endpoint}"

    ids = {c["id"] for c in NOTICE}
    assert {"pedagogie", "partenaires", "configuration"} <= ids


def test_pas_de_doublon_dans_le_registre():
    """Le fichier source ne doit pas redéfinir deux fois la même clé."""
    import ast

    from app.aide import contenu

    arbre = ast.parse(open(contenu.__file__, encoding="utf-8").read())
    for noeud in ast.walk(arbre):
        if isinstance(noeud, ast.Assign) and any(
            getattr(t, "id", None) == "AIDE_PAGES" for t in noeud.targets
        ):
            cles = [k.value for k in noeud.value.keys if isinstance(k, ast.Constant)]
            doublons = {c for c in cles if cles.count(c) > 1}
            assert not doublons, f"clés en double dans AIDE_PAGES : {doublons}"


def test_notice_accessible_et_complete(admin_client):
    r = admin_client.get("/aide/")
    assert r.status_code == 200
    page = r.get_data(as_text=True)
    assert "Notice d'utilisation" in page
    for attendu in ["Démarrer", "participants", "émargement", "SENACS", "Purge RGPD", "sauvegarde"]:
        assert attendu.lower() in page.lower(), f"la notice doit parler de : {attendu}"


def test_notice_refusee_aux_anonymes(client):
    assert client.get("/aide/").status_code == 302


def test_bandeau_comprendre_cette_page(admin_client):
    r = admin_client.get("/dashboard")
    page = r.get_data(as_text=True)
    assert '<section class="aide-page' in page
    assert "À quoi sert cette page ?" in page
    assert "Le tableau de bord" in page


def test_bandeau_absent_sur_page_non_referencee(admin_client):
    r = admin_client.get("/rbac-test")
    assert r.status_code == 200
    assert '<section class="aide-page' not in r.get_data(as_text=True)


def test_bouton_aide_dans_entete(admin_client):
    page = admin_client.get("/dashboard").get_data(as_text=True)
    assert "/aide/" in page
    assert "❓ Aide" in page


def test_infobulles_sur_pages_cles(app, admin_client):
    with app.app_context():
        from app.extensions import db
        from app.models import Subvention

        sub = Subvention.query.filter_by(nom="Sub test aide").first()
        if sub is None:
            sub = Subvention(nom="Sub test aide", secteur="Familles", annee_exercice=2026)
            db.session.add(sub)
            db.session.commit()
        sub_id = sub.id

    page = admin_client.get(f"/subvention/{sub_id}/pilotage").get_data(as_text=True)
    assert "sollicité auprès du financeur" in page, "info-bulle du montant demandé attendue"

    page = admin_client.get("/participants/new").get_data(as_text=True)
    assert "statistiques CAF/SENACS" in page, "info-bulle du type de public attendue"

    page = admin_client.get("/quartiers/").get_data(as_text=True)
    assert "Quartier Prioritaire" in page, "info-bulle QPV attendue"
