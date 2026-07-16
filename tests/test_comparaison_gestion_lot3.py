"""Gestion Lot 3 — Comparaison N/N-1 & inter-secteurs."""
import datetime as dt
import uuid

from flask import url_for


def _seed(app, secteur, annee, attribue, recu, engage_lignes=0.0):
    """Crée une subvention avec éventuellement une charge engagée (ligne + dépense)."""
    from app.extensions import db
    from app.models import Subvention, LigneBudget, Depense
    s = Subvention(nom=f"S{uuid.uuid4().hex[:6]}", secteur=secteur, annee_exercice=annee,
                   statut_cycle="versee", montant_attribue=attribue, montant_recu=recu)
    db.session.add(s)
    db.session.flush()
    if engage_lignes:
        ligne = LigneBudget(subvention_id=s.id, nature="charge", compte="60",
                            libelle="L", montant_base=engage_lignes, montant_reel=engage_lignes)
        db.session.add(ligne)
        db.session.flush()
        # L'engagé provient des dépenses rattachées, pas du montant réel seul.
        db.session.add(Depense(ligne_budget_id=ligne.id, libelle="Dep", montant=engage_lignes, statut="valide"))
    db.session.commit()
    return s.id


def test_compute_comparaison_deltas(app):
    from app.main.comparaison import compute_comparaison

    tag_secteur = f"Sec{uuid.uuid4().hex[:5]}"
    with app.app_context():
        _seed(app, tag_secteur, 2050, attribue=1000, recu=800)
        _seed(app, tag_secteur, 2051, attribue=1000, recu=1000)

        data = compute_comparaison(2051, scope_secteur=None)
        assert data["n"] == 2051 and data["n_1"] == 2050
        lig = next(l for l in data["lignes_secteur"] if l["secteur"] == tag_secteur)
        assert lig["n"]["recu"] == 1000.0
        assert lig["n_1"]["recu"] == 800.0
        assert lig["delta"]["recu"]["abs"] == 200.0
        assert lig["delta"]["recu"]["pct"] == 25.0


def test_activite_comptee_par_annee(app):
    from app.extensions import db
    from app.models import AtelierActivite, SessionActivite, PresenceActivite, Participant
    from app.main.comparaison import _agrege_annee

    sec = f"Act{uuid.uuid4().hex[:5]}"
    with app.app_context():
        at = AtelierActivite(nom=f"A{uuid.uuid4().hex[:5]}", secteur=sec, type_atelier="COLLECTIF")
        db.session.add(at)
        db.session.flush()
        sess = SessionActivite(atelier_id=at.id, secteur=sec, session_type="COLLECTIF",
                               date_session=dt.date(2052, 4, 4))
        db.session.add(sess)
        db.session.flush()
        for i in range(3):
            p = Participant(nom=f"P{i}{uuid.uuid4().hex[:4]}", prenom="T")
            db.session.add(p)
            db.session.flush()
            db.session.add(PresenceActivite(session_id=sess.id, participant_id=p.id))
        db.session.commit()

        agr = _agrege_annee(2052, scope_secteur=None)
        assert agr[sec]["sessions"] == 1.0
        assert agr[sec]["presences"] == 3.0
        # l'année suivante ne compte pas cette séance
        agr2 = _agrege_annee(2053, scope_secteur=None)
        assert sec not in agr2 or agr2[sec]["sessions"] == 0.0


def test_classement_taux_conso(app):
    from app.main.comparaison import compute_comparaison

    sec = f"Cl{uuid.uuid4().hex[:5]}"
    with app.app_context():
        _seed(app, sec, 2054, attribue=1000, recu=1000, engage_lignes=500)
        data = compute_comparaison(2054, scope_secteur=None)
        row = next(r for r in data["classement"] if r["secteur"] == sec)
        assert row["taux_conso"] == 50.0   # engagé 500 / reçu 1000


def test_page_comparaison_rendue(app, admin_client):
    with app.app_context():
        _seed(app, f"Pg{uuid.uuid4().hex[:5]}", 2055, attribue=500, recu=300)
        with app.test_request_context():
            url = url_for("main.comparaison", annee=2055)
    r = admin_client.get(url)
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Comparaison 2055 / 2054" in body
    assert "Classement inter-secteurs" in body


def test_export_comparaison_xlsx(app, admin_client):
    with app.app_context():
        _seed(app, f"Ex{uuid.uuid4().hex[:5]}", 2056, attribue=500, recu=300)
        with app.test_request_context():
            url = url_for("main.comparaison_export_xlsx", annee=2056)
    r = admin_client.get(url)
    assert r.status_code == 200
    assert "spreadsheetml" in r.headers.get("Content-Type", "")
    assert len(r.data) > 0
