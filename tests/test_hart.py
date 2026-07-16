"""Échelle de Hart : évaluations dues (pilotées par l'émargement),
statistiques collectives, vues passeport / émargement / pyramide."""
import datetime as dt
import uuid

import pytest
from flask import url_for


def _participant(db, nom=None):
    from app.models import Participant
    p = Participant(nom=nom or f"H{uuid.uuid4().hex[:6]}", prenom="T")
    db.session.add(p)
    db.session.flush()
    return p


def _session(db, secteur="Numérique", jour=None, atelier=None):
    from app.models import AtelierActivite, SessionActivite
    if atelier is None:
        atelier = AtelierActivite(nom=f"At{uuid.uuid4().hex[:6]}", secteur=secteur, type_atelier="COLLECTIF")
        db.session.add(atelier)
        db.session.flush()
    s = SessionActivite(atelier_id=atelier.id, secteur=secteur, session_type="COLLECTIF",
                        date_session=jour or dt.date(2015, 1, 1))
    db.session.add(s)
    db.session.flush()
    return s, atelier


def _presence(db, session, participant):
    from app.models import PresenceActivite
    pr = PresenceActivite(session_id=session.id, participant_id=participant.id)
    db.session.add(pr)
    db.session.flush()
    return pr


def test_evaluation_due_initiale_puis_seuil(app):
    from app.extensions import db
    from app.models import HartEvaluation
    from app.services.hart import evaluation_due, HART_SEUIL_SEANCES

    with app.app_context():
        p = _participant(db)
        s1, at = _session(db, jour=dt.date(2015, 2, 1))
        _presence(db, s1, p)
        db.session.commit()

        # Jamais évalué -> initiale
        due = evaluation_due(p.id)
        assert due is not None and due["motif"] == "initiale"

        # Évaluation initiale posée -> plus rien de dû
        db.session.add(HartEvaluation(participant_id=p.id, niveau=3, type_evaluation="initiale",
                                      date_evaluation=dt.date(2015, 2, 1)))
        db.session.commit()
        assert evaluation_due(p.id) is None

        # 5 séances après l'évaluation -> suivi dû
        for i in range(HART_SEUIL_SEANCES):
            s, _ = _session(db, jour=dt.date(2015, 3, 1 + i), atelier=at)
            _presence(db, s, p)
        db.session.commit()
        due = evaluation_due(p.id)
        assert due is not None and due["motif"] == "suivi"
        assert due["nb_seances"] >= HART_SEUIL_SEANCES


def test_fin_de_parcours_clot_le_cycle(app):
    from app.extensions import db
    from app.models import HartEvaluation
    from app.services.hart import evaluation_due, HART_SEUIL_SEANCES

    with app.app_context():
        p = _participant(db)
        db.session.add(HartEvaluation(participant_id=p.id, niveau=6, type_evaluation="fin_parcours",
                                      date_evaluation=dt.date(2015, 6, 1)))
        s, at = _session(db, jour=dt.date(2015, 7, 1))
        for i in range(HART_SEUIL_SEANCES + 2):
            si, _ = _session(db, jour=dt.date(2015, 7, 2 + i), atelier=at)
            _presence(db, si, p)
        db.session.commit()
        assert evaluation_due(p.id) is None   # fin de parcours = cycle clos


def test_stats_collectives_et_comparaison(app):
    from app.extensions import db
    from app.models import HartEvaluation
    from app.services.hart import stats_collectives, comparaison

    secteur = f"Hart{uuid.uuid4().hex[:5]}"
    with app.app_context():
        p1, p2, p3 = _participant(db), _participant(db), _participant(db)
        s_2015, at = _session(db, secteur=secteur, jour=dt.date(2015, 5, 10))
        for p in (p1, p2, p3):
            _presence(db, s_2015, p)
        # p1 : niveau 2 en 2014 puis 5 en 2015 (progression) ; p2 : 4 ; p3 jamais évalué
        db.session.add_all([
            HartEvaluation(participant_id=p1.id, niveau=2, type_evaluation="initiale", date_evaluation=dt.date(2014, 6, 1)),
            HartEvaluation(participant_id=p1.id, niveau=5, type_evaluation="suivi", date_evaluation=dt.date(2015, 4, 1)),
            HartEvaluation(participant_id=p2.id, niveau=4, type_evaluation="initiale", date_evaluation=dt.date(2015, 3, 1)),
        ])
        # présence 2014 pour la période comparée
        s_2014, _ = _session(db, secteur=secteur, jour=dt.date(2014, 5, 10), atelier=at)
        _presence(db, s_2014, p1)
        db.session.commit()

        stats = stats_collectives(secteur, dt.date(2015, 1, 1), dt.date(2015, 12, 31))
        assert stats["total_actifs"] == 3
        assert stats["total_evalues"] == 2
        assert stats["counts"][5] == 1 and stats["counts"][4] == 1
        assert len(stats["non_evalues"]) == 1
        assert stats["moyenne"] == 4.5
        assert stats["taux_participation"] == 100.0     # niveaux 4 et 5 = participation

        stats_2014 = stats_collectives(secteur, dt.date(2014, 1, 1), dt.date(2014, 12, 31))
        assert stats_2014["counts"][2] == 1             # p1 était au niveau 2
        cmp_ctx = comparaison(stats, stats_2014)
        assert cmp_ctx["deltas"][5] == 1
        assert cmp_ctx["deltas"][2] == -1
        assert cmp_ctx["delta_moyenne"] == 2.5          # 4.5 - 2.0


def test_page_pyramide_rendue(app, admin_client):
    from app.extensions import db
    from app.models import HartEvaluation

    secteur = f"Pyr{uuid.uuid4().hex[:5]}"
    with app.app_context():
        p = _participant(db, nom=f"Pyramide{uuid.uuid4().hex[:5]}")
        s, _ = _session(db, secteur=secteur, jour=dt.date(2016, 5, 5))
        _presence(db, s, p)
        db.session.add(HartEvaluation(participant_id=p.id, niveau=7, type_evaluation="initiale",
                                      date_evaluation=dt.date(2016, 5, 5), temoignage="J'organise l'atelier"))
        db.session.commit()
        with app.test_request_context():
            url = url_for("main.hart_collectif", secteur=secteur, annee=2016, niveau=7)

    r = admin_client.get(url)
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Échelle de Hart" in body
    assert "PARTICIPATION EFFECTIVE" in body
    assert "J&#39;organise l&#39;atelier" in body or "J'organise l'atelier" in body   # détail niveau 7


def test_poser_evaluation_via_route(app, admin_client):
    from app.extensions import db
    from app.models import HartEvaluation, AuditLog

    with app.app_context():
        p = _participant(db)
        db.session.commit()
        pid = p.id
        with app.test_request_context():
            url = url_for("main.hart_evaluer", participant_id=pid)

    admin_client.post(url, data={"niveau": "5", "type_evaluation": "mi_parcours",
                                 "temoignage": "Je propose des idées",
                                 "remarque_pro": "Très investie"}, follow_redirects=True)
    with app.app_context():
        ev = HartEvaluation.query.filter_by(participant_id=pid).first()
        assert ev is not None
        assert ev.niveau == 5 and ev.type_evaluation == "mi_parcours"
        assert ev.temoignage == "Je propose des idées"
        assert AuditLog.query.filter_by(action="hart.evaluation").count() >= 1

    # niveau invalide refusé
    admin_client.post(url, data={"niveau": "12"}, follow_redirects=True)
    with app.app_context():
        assert HartEvaluation.query.filter_by(participant_id=pid).count() == 1


def test_bandeau_emargement(app, admin_client):
    from app.extensions import db

    with app.app_context():
        p = _participant(db, nom=f"Nouveau{uuid.uuid4().hex[:5]}")
        s, _ = _session(db, jour=dt.date(2016, 9, 9))
        _presence(db, s, p)
        db.session.commit()
        sid = s.id
        nom = p.nom
        with app.test_request_context():
            url = url_for("activite.emargement", session_id=sid)

    body = admin_client.get(url).get_data(as_text=True)
    assert "Échelle de Hart" in body
    assert nom in body
    assert "première venue" in body


def test_passeport_affiche_hart(app, admin_client):
    from app.extensions import db
    from app.models import HartEvaluation

    with app.app_context():
        p = _participant(db)
        db.session.add(HartEvaluation(participant_id=p.id, niveau=6, type_evaluation="initiale",
                                      date_evaluation=dt.date(2016, 1, 1)))
        db.session.commit()
        pid = p.id
        with app.test_request_context():
            url = url_for("pedagogie.participant_passeport", participant_id=pid)

    body = admin_client.get(url).get_data(as_text=True)
    assert "Échelle de Hart" in body
    assert "niveau 6" in body
