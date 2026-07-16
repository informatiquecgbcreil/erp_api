"""Filtre « période de présence active » : un habitant compte dans chaque
période où il a participé (pas selon son année de création)."""
import datetime as dt
import uuid


def _present(app, quartier_id, *annees, secteur="Numérique"):
    """Crée un participant rattaché au quartier, présent les années données."""
    from app.extensions import db
    from app.models import Participant, AtelierActivite, SessionActivite, PresenceActivite

    p = Participant(nom=f"H{uuid.uuid4().hex[:6]}", prenom="P", quartier_id=quartier_id,
                    created_secteur=secteur)
    db.session.add(p)
    at = AtelierActivite(nom=f"At {uuid.uuid4().hex[:6]}", secteur=secteur)
    db.session.add(at)
    db.session.flush()
    for an in annees:
        s = SessionActivite(atelier_id=at.id, secteur=secteur, date_session=dt.date(an, 6, 15))
        db.session.add(s)
        db.session.flush()
        db.session.add(PresenceActivite(session_id=s.id, participant_id=p.id))
    db.session.commit()
    return p


def test_repartition_filtre_par_presence(app):
    from app.extensions import db
    from app.models import Quartier
    from app.services.cartographie import repartition_par_quartier

    with app.app_context():
        q = Quartier(ville="Creil", nom=f"PR-{uuid.uuid4().hex[:6]}", latitude=49.26, longitude=2.47)
        db.session.add(q)
        db.session.flush()
        # Arrivé en 2018, absent en 2019, revenu en 2020 (années choisies hors
        # de l'année testée par SENACS pour ne pas polluer ses agrégats globaux).
        p = _present(app, q.id, 2018, 2020)
        qid = q.id

        def total():
            res = repartition_par_quartier(date_from=None, date_to=None)
            return next((g["total"] for g in res["quartiers"] if g["id"] == qid), 0)

        def total_periode(a):
            res = repartition_par_quartier(date_from=dt.date(a, 1, 1), date_to=dt.date(a, 12, 31))
            return next((g["total"] for g in res["quartiers"] if g["id"] == qid), 0)

        assert total() == 1                 # toutes périodes : présent
        assert total_periode(2018) == 1     # présent en 2018
        assert total_periode(2019) == 0     # ABSENT en 2019 -> n'apparaît pas
        assert total_periode(2020) == 1     # revenu en 2020


def test_annees_de_presence(app):
    from app.extensions import db
    from app.models import Quartier
    from app.services.cartographie import annees_de_presence

    with app.app_context():
        q = Quartier(ville="Creil", nom=f"AP-{uuid.uuid4().hex[:6]}", latitude=49.26, longitude=2.47)
        db.session.add(q)
        db.session.flush()
        _present(app, q.id, 2017, 2021)
        annees = annees_de_presence()
        assert 2017 in annees and 2021 in annees
        # ordre décroissant
        assert annees == sorted(annees, reverse=True)


def test_carte_data_accepte_dates(admin_client):
    r = admin_client.get("/quartiers/carte/data?date_from=2025-01-01&date_to=2025-12-31")
    assert r.status_code == 200 and r.is_json


def test_stats_annee_de_presence(admin_client, app):
    """La page stats accepte une année précise (présence) et 'tout'."""
    from app.extensions import db
    from app.models import Quartier

    with app.app_context():
        q = Quartier(ville="Creil", nom=f"SP-{uuid.uuid4().hex[:6]}")
        db.session.add(q)
        db.session.commit()
        qid = q.id

    r = admin_client.get(f"/quartiers/stats?quartier_id={qid}&period=2024")
    assert r.status_code == 200
    r2 = admin_client.get(f"/quartiers/stats?quartier_id={qid}&period=tout")
    assert r2.status_code == 200
    # le sélecteur propose bien l'option « Tout l'historique »
    assert "Tout l'historique" in r2.get_data(as_text=True)


def test_carte_popup_lien_stats(admin_client):
    r = admin_client.get("/quartiers/carte")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "data-stats-url" in body
    assert "Période de présence" in body
