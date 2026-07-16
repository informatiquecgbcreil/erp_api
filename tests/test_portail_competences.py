"""Portail → compétences : page de mapping, calcul de progression, passeport enrichi."""
import datetime as dt
import uuid


def _make_competence():
    """Crée un référentiel + une compétence ; retourne (id, code). À appeler en app_context."""
    from app.extensions import db
    from app.models import Competence, Referentiel

    suf = uuid.uuid4().hex[:6]
    ref = Referentiel(nom=f"Ref portail {suf}")
    db.session.add(ref)
    db.session.flush()
    code = f"PRT-{suf}"
    c = Competence(referentiel_id=ref.id, code=code, nom="Compétence portail test")
    db.session.add(c)
    db.session.commit()
    return c.id, code


def test_page_mapping_accessible(admin_client):
    r = admin_client.get("/pedagogie/portail-competences")
    assert r.status_code == 200
    assert "compétences" in r.get_data(as_text=True).lower()


def test_ajout_mapping(admin_client, app):
    from app.extensions import db
    from app.models import PortailCompetenceMap

    with app.app_context():
        cid, _code = _make_competence()

    r = admin_client.post(
        "/pedagogie/portail-competences/add",
        data={"activity": "Quiz Maths 1", "competence_id": cid, "seuil_pct": 70},
        follow_redirects=True,
    )
    assert r.status_code == 200
    with app.app_context():
        m = PortailCompetenceMap.query.filter_by(activity="Quiz Maths 1", competence_id=cid).first()
        assert m is not None and m.seuil_pct == 70


def test_progression_portail_seuil(app):
    from app.extensions import db
    from app.models import Participant, PortailAttempt, PortailCompetenceMap
    from app.pedagogie.services import progression_portail

    with app.app_context():
        cid, _code = _make_competence()
        p = Participant(nom="Prog", prenom="Portail", type_public="H")
        db.session.add(p)
        db.session.flush()
        pid = p.id
        db.session.add(PortailCompetenceMap(activity="ExoA", competence_id=cid, seuil_pct=80, actif=True))
        db.session.add(PortailCompetenceMap(activity="ExoB", competence_id=cid, seuil_pct=80, actif=True))
        # ExoA réussi (85 %), ExoB échoué (50 %)
        db.session.add(PortailAttempt(attempt_id="pa-A", external_id=str(pid), participant_id=pid,
                                      activity="ExoA", pct=85, score=85, max_score=100,
                                      finished_at=dt.datetime(2026, 6, 20, 10, 0)))
        db.session.add(PortailAttempt(attempt_id="pa-B", external_id=str(pid), participant_id=pid,
                                      activity="ExoB", pct=50, score=50, max_score=100,
                                      finished_at=dt.datetime(2026, 6, 20, 11, 0)))
        db.session.commit()

        prog = progression_portail(pid)
        assert cid in prog
        assert prog[cid]["reussis"] == 1
        assert prog[cid]["total"] == 2
        assert prog[cid]["niveau"] == 2  # 1/2 = 50 % → niveau 2 (acquis)


def test_passeport_competence_enrichie(admin_client, app):
    from app.extensions import db
    from app.models import Participant, PortailAttempt, PortailCompetenceMap

    with app.app_context():
        cid, code = _make_competence()
        p = Participant(nom="Enrichi", prenom="Portail", type_public="H", portail_code="LRN-E")
        db.session.add(p)
        db.session.flush()
        pid = p.id
        db.session.add(PortailCompetenceMap(activity="ExoEnrich", competence_id=cid, seuil_pct=80, actif=True))
        db.session.add(PortailAttempt(attempt_id="pa-E", external_id=str(pid), participant_id=pid,
                                      activity="ExoEnrich", pct=90, score=90, max_score=100,
                                      finished_at=dt.datetime(2026, 6, 21, 9, 0)))
        db.session.commit()

    page = admin_client.get(f"/pedagogie/participant/{pid}/passeport?tab=portail").get_data(as_text=True)
    assert "Compétences alimentées par le portail" in page
    assert code in page  # la compétence enrichie apparaît dans le passeport
