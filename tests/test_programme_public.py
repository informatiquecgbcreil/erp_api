"""Export public du programme : page HTML autonome, sans données nominatives."""
import uuid
from datetime import date, timedelta

import pytest


@pytest.fixture()
def atelier_collectif_a_venir(app):
    suffixe = uuid.uuid4().hex[:8]
    with app.app_context():
        from app.extensions import db
        from app.models import AtelierActivite, SessionActivite

        atelier = AtelierActivite(nom=f"Atelier public {suffixe}", secteur="Familles")
        db.session.add(atelier)
        db.session.flush()
        s = SessionActivite(
            atelier_id=atelier.id,
            secteur="Familles",
            session_type="COLLECTIF",
            date_session=date.today() + timedelta(days=3),
            heure_debut="10:00",
            heure_fin="12:00",
        )
        db.session.add(s)
        db.session.commit()
        return {"atelier": atelier.nom}


def test_export_programme_public_admin(admin_client, atelier_collectif_a_venir):
    r = admin_client.get("/programme-public.html")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("Content-Type", "")
    page = r.get_data(as_text=True)
    assert "Programme des activités" in page
    assert atelier_collectif_a_venir["atelier"] in page, "l'atelier à venir doit figurer au programme"


def test_export_programme_public_refuse_anonyme(client):
    r = client.get("/programme-public.html")
    assert r.status_code in (302, 401, 403)


def test_service_exclut_rendez_vous_individuels(app):
    """Le programme public ne doit lister que les ateliers collectifs."""
    from app.services.programme_public import sessions_a_venir

    with app.app_context():
        from app.extensions import db
        from app.models import AtelierActivite, SessionActivite

        atelier = AtelierActivite(nom=f"RDV privé {uuid.uuid4().hex[:6]}", secteur="Familles")
        db.session.add(atelier)
        db.session.flush()
        db.session.add(
            SessionActivite(
                atelier_id=atelier.id,
                secteur="Familles",
                session_type="INDIVIDUEL_MENSUEL",
                rdv_date=date.today() + timedelta(days=2),
            )
        )
        db.session.commit()

        groupes = sessions_a_venir(jours=60)
        noms = {s["atelier"] for g in groupes for s in g["sessions"]}
        assert atelier.nom not in noms, "les rendez-vous individuels ne doivent pas être publiés"
