"""Cohérence des droits (retour de revue externe) :
- P1 : participants:view_all doit ouvrir /participants/ (403 avant correction) ;
- P2 : la pédagogie en lecture seule ne doit pas pouvoir modifier ;
- point 9 : les compétences de session exigent pedagogie:edit ;
- P7 : les dons ont leurs permissions propres (plus subventions:*)."""
import uuid

import pytest
from flask import url_for


def _login_role(app, email, role_code, secteur=None):
    with app.app_context():
        from app.extensions import db
        from app.models import Role, User
        u = User.query.filter_by(email=email).first()
        if u is None:
            u = User(email=email, nom=email.split("@")[0], secteur_assigne=secteur)
            u.set_password("pw-test-123")
            u.roles.append(Role.query.filter_by(code=role_code).first())
            db.session.add(u)
            db.session.commit()
    c = app.test_client()
    r = c.post("/", data={"email": email, "password": "pw-test-123"})
    assert r.status_code == 302
    return c


# ---------- P1 : accès à la liste des participants ----------

def test_responsable_secteur_ouvre_la_liste_participants(app):
    """responsable_secteur a participants:view_all mais pas participants:view :
    l'équivalence doit lui ouvrir /participants/ (la carte du menu lui est visible)."""
    c = _login_role(app, "resp-part@example.org", "responsable_secteur", secteur="Numérique")
    with app.app_context():
        with app.test_request_context():
            url = url_for("participants.list_participants")
    assert c.get(url).status_code == 200


# ---------- P2 : pédagogie lecture seule ----------

def test_lecture_seule_pedagogie_ne_modifie_pas(app):
    """admin_tech a pedagogie:view sans pedagogie:edit : consultation OK,
    création/modification refusées (403)."""
    from app.models import Referentiel

    c = _login_role(app, "tech-pedago@example.org", "admin_tech")
    with app.app_context():
        with app.test_request_context():
            url_list = url_for("pedagogie.referentiels_list")
            url_note = None

    # GET : consultation autorisée
    assert c.get(url_list).status_code == 200
    # POST : création refusée
    r = c.post(url_list, data={"action": "create_referentiel", "nom": "Interdit"})
    assert r.status_code == 403
    with app.app_context():
        assert Referentiel.query.filter_by(nom="Interdit").count() == 0


def test_lecture_seule_ne_pose_pas_de_note_passeport(app):
    from app.extensions import db
    from app.models import Participant, PasseportNote

    c = _login_role(app, "tech-note@example.org", "admin_tech")
    with app.app_context():
        p = Participant(nom=f"Pn{uuid.uuid4().hex[:6]}", prenom="T")
        db.session.add(p)
        db.session.commit()
        pid = p.id
        with app.test_request_context():
            url = url_for("pedagogie.participant_passeport_note", participant_id=pid)

    r = c.post(url, data={"contenu": "note interdite"})
    assert r.status_code == 403
    with app.app_context():
        assert PasseportNote.query.filter_by(participant_id=pid).count() == 0


def test_direction_modifie_toujours_la_pedagogie(app, admin_client):
    """Non-régression : un rôle avec pedagogie:edit continue de créer."""
    from app.models import Referentiel

    nom = f"RefDir{uuid.uuid4().hex[:6]}"
    with app.app_context():
        with app.test_request_context():
            url = url_for("pedagogie.referentiels_list")
    admin_client.post(url, data={"action": "create_referentiel", "nom": nom}, follow_redirects=True)
    with app.app_context():
        assert Referentiel.query.filter_by(nom=nom).count() == 1


# ---------- point 9 : compétences de session ----------

def test_skills_session_exigent_pedagogie_edit(app):
    from app.extensions import db
    from app.models import AtelierActivite, SessionActivite

    c = _login_role(app, "tech-skill@example.org", "admin_tech")
    with app.app_context():
        at = AtelierActivite(nom=f"Sk{uuid.uuid4().hex[:6]}", secteur="Numérique", type_atelier="COLLECTIF")
        db.session.add(at)
        db.session.flush()
        s = SessionActivite(atelier_id=at.id, secteur="Numérique", session_type="COLLECTIF")
        db.session.add(s)
        db.session.commit()
        sid = s.id
        with app.test_request_context():
            url = url_for("activite.session_skill_add", session_id=sid)

    assert c.post(url, data={"competence_id": "1"}).status_code == 403


# ---------- P7 : permissions dons dédiées ----------

def test_dons_reserves_aux_roles_dons(app, admin_client):
    """responsable_secteur (qui a subventions:edit) n'accède plus aux dons ;
    finance et direction y accèdent via dons:view."""
    with app.app_context():
        with app.test_request_context():
            url = url_for("main.dons_registre")

    resp = _login_role(app, "resp-dons@example.org", "responsable_secteur", secteur="Numérique")
    assert resp.get(url).status_code == 403

    fin = _login_role(app, "fin-dons@example.org", "finance")
    assert fin.get(url).status_code == 200

    assert admin_client.get(url).status_code == 200     # direction
