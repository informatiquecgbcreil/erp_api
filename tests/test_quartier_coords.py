"""Coordonnées de quartier : géocodage auto, placement manuel, et bulle posée
sur le quartier (sans géocoder chaque habitant)."""
import uuid


def test_geocode_quartiers_respecte_le_manuel(app, monkeypatch):
    from app.extensions import db
    from app.models import Quartier
    from app.services import geocodage as g

    with app.app_context():
        auto = Quartier(ville="Creil", nom=f"GA-{uuid.uuid4().hex[:6]}")
        manuel = Quartier(ville="Creil", nom=f"GM-{uuid.uuid4().hex[:6]}",
                          latitude=1.0, longitude=1.0, geo_manuel=True)
        db.session.add_all([auto, manuel])
        db.session.commit()
        aid, mid = auto.id, manuel.id

        monkeypatch.setattr(g, "geocoder",
                            lambda nom, ville: {"lat": 49.26, "lon": 2.47, "score": 0.5,
                                                "precision": "locality", "label": "x"})
        res = g.synchroniser_geocodages_quartiers(force=True)
        assert res["localises"] >= 1
        assert db.session.get(Quartier, aid).latitude == 49.26
        # un placement manuel n'est jamais écrasé
        assert db.session.get(Quartier, mid).latitude == 1.0


def test_repartition_positionne_sur_le_quartier(app):
    from app.extensions import db
    from app.models import Participant, Quartier
    from app.services.cartographie import repartition_par_quartier

    with app.app_context():
        q = Quartier(ville="Creil", nom=f"PQ-{uuid.uuid4().hex[:6]}", is_qpv=True,
                     latitude=49.30, longitude=2.50)
        db.session.add(q)
        db.session.flush()
        # Habitant rattaché au quartier MAIS sans coordonnées individuelles.
        p = Participant(nom="Quartier", prenom="Sansgeo", quartier_id=q.id, created_secteur="Numérique")
        db.session.add(p)
        db.session.commit()

        res = repartition_par_quartier()
        grp = next(g for g in res["quartiers"] if g["id"] == q.id)
        assert grp["lat"] == 49.3 and grp["lon"] == 2.5  # bulle sur le quartier
        assert grp["total"] >= 1 and grp["is_qpv"] is True
        assert res["localises"] >= 1  # compté sans géocodage individuel


def test_edit_enregistre_position_manuelle(app, admin_client):
    from app.extensions import db
    from app.models import Quartier

    nom = f"EQ-{uuid.uuid4().hex[:6]}"
    with app.app_context():
        q = Quartier(ville="Creil", nom=nom)
        db.session.add(q)
        db.session.commit()
        qid = q.id

    r = admin_client.post(
        f"/quartiers/{qid}/edit",
        data={"ville": "Creil", "nom": nom, "latitude": "49,27", "longitude": "2.48"},
        follow_redirects=True,
    )
    assert r.status_code == 200
    with app.app_context():
        q = db.session.get(Quartier, qid)
        assert abs(q.latitude - 49.27) < 1e-6  # virgule décimale acceptée
        assert abs(q.longitude - 2.48) < 1e-6
        assert q.geo_manuel is True
