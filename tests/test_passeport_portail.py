"""Affichage des résultats du portail dans le passeport + création manuelle du profil."""
import datetime as dt


def test_passeport_affiche_section_portail(admin_client, app):
    from app.extensions import db
    from app.models import Participant

    with app.app_context():
        p = Participant(nom="Passe", prenom="Port", type_public="H")
        db.session.add(p)
        db.session.commit()
        pid = p.id

    page = admin_client.get(f"/pedagogie/participant/{pid}/passeport").get_data(as_text=True)
    assert "Activités en ligne" in page
    # sans code portail : on annonce l'absence de profil (bouton si configuré)
    assert "pas encore de profil" in page


def test_passeport_affiche_une_tentative(admin_client, app):
    from app.extensions import db
    from app.models import Participant, PortailAttempt

    with app.app_context():
        p = Participant(nom="AvecRes", prenom="Ultats", type_public="H", portail_code="LRN-1")
        db.session.add(p)
        db.session.flush()
        db.session.add(
            PortailAttempt(
                attempt_id="att-passeport-1",
                external_id=str(p.id),
                participant_id=p.id,
                activity="Quiz X",
                activity_type="quiz",
                theme="Lecture",
                score=7,
                max_score=10,
                pct=70,
                duration_ms=90000,
                finished_at=dt.datetime(2026, 6, 20, 14, 30),
            )
        )
        db.session.commit()
        pid = p.id

    page = admin_client.get(f"/pedagogie/participant/{pid}/passeport?tab=portail").get_data(as_text=True)
    assert "Quiz X" in page
    assert "Lecture" in page
    assert "7/10" in page


def test_creer_profil_portail_depuis_passeport(admin_client, app, monkeypatch):
    from app.extensions import db
    from app.models import Participant
    from app.services import portail_apprenants as svc

    monkeypatch.setattr(svc, "creer_apprenant", lambda ext: {"code": "LRN-NEW", "created": True})
    monkeypatch.setitem(app.config, "PORTAIL_BASE_URL", "https://portail.test")
    monkeypatch.setitem(app.config, "PORTAIL_TOKEN", "secret")

    with app.app_context():
        p = Participant(nom="Manuel", prenom="Profil", type_public="H")
        db.session.add(p)
        db.session.commit()
        pid = p.id

    r = admin_client.post(f"/pedagogie/participant/{pid}/passeport/portail", follow_redirects=True)
    assert r.status_code == 200
    with app.app_context():
        assert db.session.get(Participant, pid).portail_code == "LRN-NEW"
