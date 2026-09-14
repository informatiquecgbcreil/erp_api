"""Simplification de l'inventaire : emplacement structuré et déplacement en lot.

Le pont vers le plan des salles existait en base depuis le lot 1, mais la
saisie restait en texte libre : chaque nouveau matériel repartait à la
main et le référentiel se redégradait aussitôt repris. Et ranger quinze
tablettes ailleurs demandait d'ouvrir quinze fiches.
"""
from datetime import date
from uuid import uuid4

import pytest

from app.extensions import db
from app.models import Espace, InventaireItem, Site


@pytest.fixture()
def rangement(app):
    with app.app_context():
        site = Site(nom="Centre (inventaire)", code=f"test-inv-{uuid4().hex[:8]}")
        db.session.add(site)
        db.session.flush()
        atelier = Espace(site=site, nom="Atelier", stockage=True)
        db.session.add(atelier)
        db.session.flush()
        armoire = Espace(site=site, parent=atelier, nom="Armoire sécurisée",
                         type_espace="stockage", reservable=False, stockage=True, securise=True)
        reserve = Espace(site=site, nom="Réserve", type_espace="stockage",
                         reservable=False, stockage=True)
        db.session.add_all([armoire, reserve])
        db.session.commit()
        ids = {"site": site.id, "atelier": atelier.id,
               "armoire": armoire.id, "reserve": reserve.id}
        site_id = site.id

    yield ids

    with app.app_context():
        for item in InventaireItem.query.filter(
            InventaireItem.id_interne.like("TEST-SIMPL%")
        ).all():
            db.session.delete(item)
        reste = db.session.get(Site, site_id)
        if reste is not None:
            db.session.delete(reste)
        db.session.commit()


def _item(n: int, espace_id=None, secteur="Numérique"):
    item = InventaireItem(
        secteur=secteur, id_interne=f"TEST-SIMPL-{uuid4().hex[:6]}-{n}",
        designation=f"Tablette {n}", espace_id=espace_id,
    )
    db.session.add(item)
    return item


def test_le_formulaire_de_saisie_propose_les_emplacements(admin_client, rangement):
    """Sans sélecteur à la saisie, le référentiel se redégrade à chaque ajout."""
    page = admin_client.get("/inventaire/new").get_data(as_text=True)
    assert 'name="espace_id"' in page
    assert "Armoire sécurisée" in page


def test_creer_un_materiel_avec_son_emplacement(admin_client, app, rangement):
    admin_client.post(
        "/inventaire/new",
        data={
            "secteur": "Numérique", "designation": "Tablette neuve",
            "espace_id": str(rangement["armoire"]), "quantite": "1",
        },
        follow_redirects=True,
    )
    with app.app_context():
        item = InventaireItem.query.filter_by(designation="Tablette neuve").one()
        assert item.espace_id == rangement["armoire"]
        # Le texte libre se remplit tout seul, pour rester lisible partout
        # où il est encore affiché.
        assert "Armoire sécurisée" in (item.localisation or "")
        db.session.delete(item)
        db.session.commit()


def test_deplacer_un_lot_en_une_fois(admin_client, app, rangement):
    """Quinze tablettes rangées ailleurs, c'était quinze fiches à ouvrir."""
    with app.app_context():
        items = [_item(n, rangement["atelier"]) for n in range(5)]
        db.session.commit()
        ids = [i.id for i in items]

    reponse = admin_client.post(
        "/inventaire/deplacer",
        data={"item_ids": [str(i) for i in ids], "espace_id": str(rangement["armoire"])},
        follow_redirects=True,
    )
    assert reponse.status_code == 200
    assert "5 matériel(s) rangé(s)" in reponse.get_data(as_text=True)

    with app.app_context():
        for identifiant in ids:
            item = db.session.get(InventaireItem, identifiant)
            assert item.espace_id == rangement["armoire"]
            assert "Armoire sécurisée" in item.localisation


def test_detacher_un_lot_de_son_emplacement(admin_client, app, rangement):
    with app.app_context():
        item = _item(99, rangement["armoire"])
        db.session.commit()
        identifiant = item.id

    admin_client.post(
        "/inventaire/deplacer",
        data={"item_ids": [str(identifiant)], "espace_id": ""},
        follow_redirects=True,
    )
    with app.app_context():
        assert db.session.get(InventaireItem, identifiant).espace_id is None


def test_une_selection_vide_ne_fait_rien(admin_client, rangement):
    reponse = admin_client.post(
        "/inventaire/deplacer",
        data={"espace_id": str(rangement["armoire"])}, follow_redirects=True,
    )
    assert "Coche au moins un matériel" in reponse.get_data(as_text=True)


def test_un_emplacement_inconnu_est_refuse(admin_client, app, rangement):
    with app.app_context():
        item = _item(1, rangement["atelier"])
        db.session.commit()
        identifiant, origine = item.id, item.espace_id

    admin_client.post(
        "/inventaire/deplacer",
        data={"item_ids": [str(identifiant)], "espace_id": "999999"},
        follow_redirects=True,
    )
    with app.app_context():
        assert db.session.get(InventaireItem, identifiant).espace_id == origine


def test_la_liste_propose_la_selection_multiple(admin_client, app, rangement):
    with app.app_context():
        _item(1, rangement["atelier"])
        db.session.commit()
    page = admin_client.get("/inventaire/").get_data(as_text=True)
    assert 'name="item_ids"' in page
    assert "coche-tout" in page
    assert "Ranger ici" in page


def test_les_valeurs_deja_saisies_sont_proposees(admin_client, app, rangement):
    """Un champ libre sans suggestion, c'est « MAIF », « Maif » et « maif »
    dans la même base."""
    with app.app_context():
        item = _item(1)
        item.categorie = "Informatique-test-unique"
        item.marque = "MarqueTestUnique"
        db.session.commit()

    page = admin_client.get("/inventaire/new").get_data(as_text=True)
    assert "<datalist" in page
    assert "Informatique-test-unique" in page
    assert "MarqueTestUnique" in page
