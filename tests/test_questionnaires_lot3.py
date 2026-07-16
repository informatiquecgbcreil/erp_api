"""Lot 3 Projet — Questionnaires d'impact : dépouillement, export, duplication, type/projet."""
import json
import uuid

import pytest
from flask import url_for


@pytest.fixture()
def questionnaire_avec_reponses(app):
    """Questionnaire (échelle + oui/non + texte) avec 3 réponses + séance/présences."""
    with app.app_context():
        from app.extensions import db
        from app.models import (
            Questionnaire, Question, QuestionnaireResponseGroup, QuestionResponse,
            AtelierActivite, SessionActivite, PresenceActivite, Participant,
        )
        suf = uuid.uuid4().hex[:6]
        q = Questionnaire(nom=f"Sat {suf}", type_questionnaire="satisfaction", is_active=True)
        db.session.add(q)
        db.session.flush()
        q_scale = Question(questionnaire_id=q.id, label="Note", kind="scale", position=1)
        q_yn = Question(questionnaire_id=q.id, label="Reviendrez-vous ?", kind="yesno", position=2)
        q_txt = Question(questionnaire_id=q.id, label="Remarques", kind="text", position=3)
        db.session.add_all([q_scale, q_yn, q_txt])

        at = AtelierActivite(nom=f"At {suf}", secteur="Numérique", type_atelier="COLLECTIF")
        db.session.add(at)
        db.session.flush()
        sess = SessionActivite(atelier_id=at.id, secteur="Numérique", session_type="COLLECTIF",
                               date_session=__import__("datetime").date(2019, 5, 5))
        db.session.add(sess)
        db.session.flush()

        # 4 présents sur la séance (dénominateur du taux), 3 réponses
        parts = []
        for i in range(4):
            p = Participant(nom=f"P{i}{suf}", prenom="T")
            db.session.add(p)
            db.session.flush()
            parts.append(p)
            db.session.add(PresenceActivite(session_id=sess.id, participant_id=p.id))

        notes = [5, 4, 3]
        reviens = ["oui", "oui", "non"]
        textes = ["Super", "", "Bof"]
        for i in range(3):
            grp = QuestionnaireResponseGroup(questionnaire_id=q.id, participant_id=parts[i].id,
                                             session_id=sess.id, atelier_id=at.id, secteur="Numérique")
            db.session.add(grp)
            db.session.flush()
            db.session.add_all([
                QuestionResponse(response_group_id=grp.id, question_id=q_scale.id, value_number=notes[i]),
                QuestionResponse(response_group_id=grp.id, question_id=q_yn.id, value_text=reviens[i]),
                QuestionResponse(response_group_id=grp.id, question_id=q_txt.id, value_text=textes[i]),
            ])
        db.session.commit()
        return {"qid": q.id, "scale_id": q_scale.id}


def test_compute_stats(app, questionnaire_avec_reponses):
    from app.models import Questionnaire
    from app.questionnaires.stats import compute_questionnaire_stats

    with app.app_context():
        q = __import__("app.extensions", fromlist=["db"]).db.session.get(Questionnaire, questionnaire_avec_reponses["qid"])
        stats = compute_questionnaire_stats(q)
        assert stats["nb_reponses"] == 3
        assert stats["nb_identifies"] == 3
        assert stats["taux_reponse"] == 75.0   # 3 réponses / 4 présents
        scale_item = next(i for i in stats["questions"] if i["kind"] == "scale")
        assert scale_item["moyenne"] == 4.0      # (5+4+3)/3
        yn_item = next(i for i in stats["questions"] if i["kind"] == "yesno")
        oui = next(d for d in yn_item["distribution"] if d["label"] == "oui")
        assert oui["count"] == 2
        txt_item = next(i for i in stats["questions"] if i["kind"] == "text")
        assert "Super" in txt_item["textes"] and "" not in txt_item["textes"]


def test_page_statistiques_rendue(app, admin_client, questionnaire_avec_reponses):
    with app.app_context():
        with app.test_request_context():
            url = url_for("questionnaires.statistiques", questionnaire_id=questionnaire_avec_reponses["qid"])
    r = admin_client.get(url)
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Dépouillement" in body
    assert "Taux de réponse" in body
    assert "qstat-fill" in body


def test_export_xlsx(app, admin_client, questionnaire_avec_reponses):
    with app.app_context():
        with app.test_request_context():
            url = url_for("questionnaires.export_xlsx", questionnaire_id=questionnaire_avec_reponses["qid"])
    r = admin_client.get(url)
    assert r.status_code == 200
    assert "spreadsheetml" in r.headers.get("Content-Type", "")
    assert len(r.data) > 0


def test_duplication(app, admin_client, questionnaire_avec_reponses):
    from app.extensions import db
    from app.models import Questionnaire, Question

    qid = questionnaire_avec_reponses["qid"]
    with app.app_context():
        with app.test_request_context():
            url = url_for("questionnaires.duplicate", questionnaire_id=qid)
    admin_client.post(url, follow_redirects=True)
    with app.app_context():
        copie = Questionnaire.query.filter(Questionnaire.nom.like("%(copie)%")).first()
        assert copie is not None
        assert copie.is_active is False
        assert copie.type_questionnaire == "satisfaction"
        # mêmes questions copiées, aucune réponse
        assert Question.query.filter_by(questionnaire_id=copie.id).count() == 3
        from app.models import QuestionnaireResponseGroup
        assert QuestionnaireResponseGroup.query.filter_by(questionnaire_id=copie.id).count() == 0


def test_comparaison_avant_apres(app):
    from app.extensions import db
    from app.models import Questionnaire, Question, QuestionnaireResponseGroup, QuestionResponse, Projet
    from app.questionnaires.stats import compute_projet_comparison

    with app.app_context():
        suf = uuid.uuid4().hex[:6]
        proj = Projet(nom=f"Impact {suf}", secteur="Numérique")
        db.session.add(proj)
        db.session.flush()

        def _make(typ, note):
            q = Questionnaire(nom=f"{typ}{suf}", type_questionnaire=typ, projet_id=proj.id)
            db.session.add(q)
            db.session.flush()
            q_s = Question(questionnaire_id=q.id, label="Confiance", kind="scale", position=1)
            db.session.add(q_s)
            db.session.flush()
            grp = QuestionnaireResponseGroup(questionnaire_id=q.id)
            db.session.add(grp)
            db.session.flush()
            db.session.add(QuestionResponse(response_group_id=grp.id, question_id=q_s.id, value_number=note))
            return q

        _make("avant", 2.0)
        _make("apres", 4.0)
        db.session.commit()

        comp = compute_projet_comparison(proj)
        assert comp is not None
        assert comp["moy_avant"] == 2.0
        assert comp["moy_apres"] == 4.0
        assert comp["evolution"] == 2.0


def test_creation_avec_type_et_projet(app, admin_client):
    from app.extensions import db
    from app.models import Questionnaire, Projet

    with app.app_context():
        suf = uuid.uuid4().hex[:6]
        proj = Projet(nom=f"Px {suf}", secteur="Numérique")
        db.session.add(proj)
        db.session.commit()
        pid = proj.id
        with app.test_request_context():
            url = url_for("questionnaires.create")

    nom = f"Q{uuid.uuid4().hex[:6]}"
    admin_client.post(url, data={
        "nom": nom, "is_active": "1",
        "type_questionnaire": "avant", "projet_id": str(pid),
    }, follow_redirects=True)

    with app.app_context():
        q = Questionnaire.query.filter_by(nom=nom).first()
        assert q is not None
        assert q.type_questionnaire == "avant"
        assert q.projet_id == pid
