"""Géocodage des adresses + carte agrégée par quartier.

Garde-fous : l'adresse reste facultative, une erreur réseau ne plante pas,
le géocodage est idempotent, et l'agrégation n'expose que des quartiers.
"""
import uuid


def test_colonnes_geo_nullable(app):
    """Un participant SANS adresse reste valide (colonnes géo nullable)."""
    with app.app_context():
        from app.extensions import db
        from app.models import Participant

        p = Participant(nom="Sans", prenom="Adresse")
        db.session.add(p)
        db.session.commit()
        assert p.latitude is None and p.longitude is None
        assert p.geocode_query is None


def test_construire_requete(app):
    from app.services.geocodage import construire_requete

    with app.app_context():
        assert construire_requete("12 rue X", "Creil") == "12 rue X Creil"
        assert construire_requete(None, "Creil") == "Creil"
        assert construire_requete("  ", "  ") == ""
        assert construire_requete("ab", "") == ""  # < 3 caractères


def test_geocoder_parse(app, monkeypatch):
    from app.services import geocodage as g

    fake = {
        "features": [
            {
                "geometry": {"coordinates": [2.475, 49.258]},
                "properties": {"score": 0.97, "type": "housenumber", "label": "12 Rue X 60100 Creil"},
            }
        ]
    }
    with app.app_context():
        monkeypatch.setattr(g, "_requete_ban", lambda q, timeout=10: fake)
        res = g.geocoder("12 rue X", "Creil")
        assert res["lat"] == 49.258 and res["lon"] == 2.475
        assert res["precision"] == "housenumber"

        # Sans adresse exploitable : None, et on n'appelle pas la BAN.
        monkeypatch.setattr(g, "_requete_ban", lambda q, timeout=10: (_ for _ in ()).throw(AssertionError("ne doit pas appeler")))
        assert g.geocoder(None, "") is None


def test_sync_localise_et_idempotent(app, monkeypatch):
    from app.extensions import db
    from app.models import Participant
    from app.services import geocodage as g

    with app.app_context():
        p = Participant(nom="Geo", prenom="Test", adresse="1 rue Test", ville="Creil")
        db.session.add(p)
        db.session.commit()
        pid = p.id

        monkeypatch.setattr(
            g, "geocoder",
            lambda a, v: {"lat": 49.25, "lon": 2.47, "score": 0.9, "precision": "street", "label": "x"},
        )
        resume = g.synchroniser_geocodages(limit=200)
        assert resume["localises"] >= 1

        p = db.session.get(Participant, pid)
        assert p.latitude == 49.25 and p.longitude == 2.47
        assert p.geocode_query == "1 rue Test Creil"
        assert p.geocoded_at is not None

        # 2e passage : ce participant ne doit plus être retraité.
        marqueur = p.geocoded_at
        g.synchroniser_geocodages(limit=200)
        p = db.session.get(Participant, pid)
        assert p.geocoded_at == marqueur  # inchangé → non re-géocodé


def test_sync_erreur_reseau_ne_plante_pas(app, monkeypatch):
    from app.extensions import db
    from app.models import Participant
    from app.services import geocodage as g

    with app.app_context():
        p = Participant(nom="Net", prenom="Down", adresse="1 rue Y", ville="Creil")
        db.session.add(p)
        db.session.commit()
        pid = p.id

        def boom(a, v):
            raise g.GeocodageError("réseau indisponible", retryable=True)

        monkeypatch.setattr(g, "geocoder", boom)
        resume = g.synchroniser_geocodages(limit=200)
        assert resume["erreurs"] >= 1

        p = db.session.get(Participant, pid)
        assert p.latitude is None  # laissé pour réessai
        assert p.geocode_query is None


def test_repartition_par_quartier(app):
    from app.extensions import db
    from app.models import Participant, Quartier
    from app.services.cartographie import repartition_par_quartier

    with app.app_context():
        qv = Quartier(ville="Creil", nom=f"Q-{uuid.uuid4().hex[:5]}", is_qpv=True)
        db.session.add(qv)
        db.session.flush()
        p1 = Participant(nom="A", prenom="x", created_secteur="Numérique", type_public="H",
                         quartier_id=qv.id, latitude=49.25, longitude=2.47)
        p2 = Participant(nom="B", prenom="y", created_secteur="Numérique", type_public="H",
                         quartier_id=qv.id, latitude=49.27, longitude=2.49)
        p3 = Participant(nom="C", prenom="z", created_secteur="Familles")  # non localisé
        db.session.add_all([p1, p2, p3])
        db.session.commit()

        res = repartition_par_quartier()
        assert res["total"] >= 3
        assert res["non_localises"] >= 1
        grp = next(g for g in res["quartiers"] if g["id"] == qv.id)
        assert grp["total"] == 2
        assert abs(grp["lat"] - 49.26) < 0.001  # centroïde = moyenne
        assert grp["is_qpv"] is True
        assert grp["par_secteur"].get("Numérique") == 2


def test_repartition_filtre_secteur(app):
    from app.extensions import db
    from app.models import Participant, Quartier
    from app.services.cartographie import repartition_par_quartier

    with app.app_context():
        sect = f"S-{uuid.uuid4().hex[:6]}"
        qv = Quartier(ville="Creil", nom=f"QF-{uuid.uuid4().hex[:5]}")
        db.session.add(qv)
        db.session.flush()
        p = Participant(nom="F", prenom="x", created_secteur=sect,
                        latitude=49.2, longitude=2.4, quartier_id=qv.id)
        db.session.add(p)
        db.session.commit()

        assert repartition_par_quartier(secteur=sect)["localises"] == 1
        assert repartition_par_quartier(secteur=f"inexistant-{uuid.uuid4().hex}")["localises"] == 0


def test_page_carte(admin_client):
    r = admin_client.get("/quartiers/carte")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "vendor/leaflet/leaflet" in body  # assets auto-hébergés (LAN)
    assert "Carte des habitants" in body


def test_carte_data_json(admin_client):
    r = admin_client.get("/quartiers/carte/data")
    assert r.status_code == 200
    assert r.is_json
    data = r.get_json()
    assert "quartiers" in data and "non_localises" in data and "centre" in data


def test_carte_geocoder_bouton(admin_client, app, monkeypatch):
    from app.services import geocodage as g

    monkeypatch.setattr(
        g, "synchroniser_geocodages",
        lambda limit=50: {"localises": 0, "non_localises": 0, "erreurs": 0, "erreur": None, "restants": 0},
    )
    r = admin_client.post("/quartiers/carte/geocoder", follow_redirects=True)
    assert r.status_code == 200
