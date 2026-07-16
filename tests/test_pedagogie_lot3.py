"""Lot 3 Terrain — évaluation pédagogique : recherche de compétence + marquage portail."""
import uuid

from flask import url_for


def test_passeport_recherche_competence(admin_client, app):
    from app.extensions import db
    from app.models import Participant, Referentiel, Competence

    with app.app_context():
        p = Participant(nom=f"Pass{uuid.uuid4().hex[:6]}", prenom="T")
        db.session.add(p)
        ref = Referentiel(nom=f"Ref{uuid.uuid4().hex[:5]}")
        db.session.add(ref)
        db.session.flush()
        db.session.add(Competence(referentiel_id=ref.id, code="C1", nom="Compétence test"))
        db.session.commit()
        pid = p.id
        with app.test_request_context():
            url = url_for("pedagogie.participant_passeport", participant_id=pid)

    r = admin_client.get(url + "?tab=evaluate")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "compSearch" in body            # champ de recherche de compétence
    assert "Filtrer" in body
