"""Carte des partenaires : géocodage (adresse seule) + marqueurs individuels."""
import uuid


def test_sync_partenaire_localise(app, monkeypatch):
    from app.extensions import db
    from app.models import Partenaire
    from app.services import geocodage as g

    with app.app_context():
        p = Partenaire(nom=f"CCAS {uuid.uuid4().hex[:5]}", adresse="Place du Général de Gaulle, Creil")
        db.session.add(p)
        db.session.commit()
        pid = p.id

        monkeypatch.setattr(
            g, "geocoder",
            lambda a, v: {"lat": 49.26, "lon": 2.47, "score": 0.88, "precision": "street", "label": "x"},
        )
        resume = g.synchroniser_geocodages_partenaires(limit=200)
        assert resume["localises"] >= 1

        p = db.session.get(Partenaire, pid)
        assert p.latitude == 49.26 and p.longitude == 2.47
        assert p.geocode_query == "Place du Général de Gaulle, Creil"

        # Idempotent : 2e passage ne retraite pas ce partenaire.
        marqueur = p.geocoded_at
        g.synchroniser_geocodages_partenaires(limit=200)
        p = db.session.get(Partenaire, pid)
        assert p.geocoded_at == marqueur


def test_liste_partenaires(app):
    from app.extensions import db
    from app.models import Partenaire, PartenaireSecteur
    from app.services.cartographie import liste_partenaires

    with app.app_context():
        sect = f"S-{uuid.uuid4().hex[:6]}"
        p = Partenaire(nom=f"Mission Locale {uuid.uuid4().hex[:5]}", adresse="Creil",
                       latitude=49.25, longitude=2.48)
        db.session.add(p)
        db.session.flush()
        db.session.add(PartenaireSecteur(partenaire_id=p.id, secteur=sect))
        # Un partenaire non localisé (sans coordonnées) : compté à part.
        db.session.add(Partenaire(nom=f"Sans geo {uuid.uuid4().hex[:5]}"))
        db.session.commit()

        res = liste_partenaires()
        assert res["non_localises"] >= 1
        ids = [x["id"] for x in res["partenaires"]]
        assert p.id in ids
        point = next(x for x in res["partenaires"] if x["id"] == p.id)
        assert sect in point["secteurs"]
        assert point["fiche_url"].endswith(str(p.id) + "/edit")

        # Filtre secteur
        res_sect = liste_partenaires(secteur=sect)
        assert all(sect in x["secteurs"] for x in res_sect["partenaires"])
        assert any(x["id"] == p.id for x in res_sect["partenaires"])
        assert liste_partenaires(secteur=f"inexistant-{uuid.uuid4().hex}")["localises"] == 0


def test_page_carte_partenaires(admin_client):
    r = admin_client.get("/partenaires/carte")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "vendor/leaflet/leaflet" in body
    assert "Carte des partenaires" in body


def test_carte_partenaires_data_json(admin_client):
    r = admin_client.get("/partenaires/carte/data")
    assert r.status_code == 200
    assert r.is_json
    data = r.get_json()
    assert "partenaires" in data and "non_localises" in data and "centre" in data


def test_carte_partenaires_geocoder_bouton(admin_client, app, monkeypatch):
    from app.services import geocodage as g

    monkeypatch.setattr(
        g, "synchroniser_geocodages_partenaires",
        lambda limit=50: {"localises": 0, "non_localises": 0, "erreurs": 0, "erreur": None, "restants": 0},
    )
    r = admin_client.post("/partenaires/carte/geocoder", follow_redirects=True)
    assert r.status_code == 200
