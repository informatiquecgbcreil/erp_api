"""Tests de la purge RGPD (anonymisation des participants inactifs)."""
import uuid
from datetime import timedelta

import pytest


def _creer_participant(app, *, anciennete_jours: int, nom_prefixe="Purge"):
    """Crée un participant dont la fiche date d'il y a `anciennete_jours`."""
    with app.app_context():
        from app.extensions import db
        from app.models import Participant
        from app.utils.dates import utcnow

        ancien = utcnow() - timedelta(days=anciennete_jours)
        p = Participant(
            nom=f"{nom_prefixe}-{uuid.uuid4().hex[:8]}",
            prenom="Test",
            created_secteur="Familles",
            created_at=ancien,
            updated_at=ancien,
        )
        db.session.add(p)
        db.session.commit()
        return p.id


def test_detection_des_inactifs(app):
    vieux_id = _creer_participant(app, anciennete_jours=4 * 365)
    recent_id = _creer_participant(app, anciennete_jours=30)

    with app.app_context():
        from app.services.purge_rgpd import participants_inactifs

        ids = {item["participant"].id for item in participants_inactifs(annees=3)}
        assert vieux_id in ids, "un participant inactif depuis 4 ans doit être détecté"
        assert recent_id not in ids, "un participant récent ne doit PAS être détecté"


def test_une_presence_recente_protege_de_la_purge(app):
    pid = _creer_participant(app, anciennete_jours=4 * 365)

    with app.app_context():
        from app.extensions import db
        from app.models import AtelierActivite, PresenceActivite, SessionActivite
        from app.services.purge_rgpd import participants_inactifs

        atelier = AtelierActivite(nom=f"Atelier purge {uuid.uuid4().hex[:6]}", secteur="Familles")
        db.session.add(atelier)
        db.session.flush()
        session = SessionActivite(atelier_id=atelier.id, secteur="Familles")
        db.session.add(session)
        db.session.flush()
        db.session.add(PresenceActivite(session_id=session.id, participant_id=pid))
        db.session.commit()

        ids = {item["participant"].id for item in participants_inactifs(annees=3)}
        assert pid not in ids, "une présence récente doit protéger la fiche de la purge"


def test_purge_anonymise_et_journalise(app):
    import os

    pid = _creer_participant(app, anciennete_jours=4 * 365)

    with app.app_context():
        from app.extensions import db
        from app.models import Participant
        from app.services.purge_rgpd import derniere_purge, purger_participants_inactifs

        nombre = purger_participants_inactifs(annees=3, declenchement="test")
        assert nombre >= 1

        p = db.session.get(Participant, pid)
        assert p.nom == "ANONYME"
        assert p.prenom == f"P{pid}"
        assert p.email is None and p.telephone is None and p.adresse is None
        assert derniere_purge() is not None, "la purge doit être horodatée"

        # Une fiche déjà anonymisée n'est pas reprise au tour suivant
        assert purger_participants_inactifs(annees=3, declenchement="test") == 0

    chemin = os.path.join(os.environ["ERP_LOG_DIR"], "erreurs.log")
    with open(chemin, encoding="utf-8") as fh:
        assert f"#{pid} anonymisé" in fh.read(), "chaque anonymisation doit être journalisée"


def test_page_controle_purge(app, admin_client):
    pid = _creer_participant(app, anciennete_jours=4 * 365, nom_prefixe="PageCtrl")

    r = admin_client.get("/controle/purge-rgpd")
    assert r.status_code == 200
    page = r.get_data(as_text=True)
    assert "PageCtrl" in page, "la fiche en attente doit être listée"
    assert "Anonymiser maintenant" in page

    # Lancement manuel par le bouton
    r = admin_client.post("/controle/purge-rgpd/lancer")
    assert r.status_code == 302

    with app.app_context():
        from app.extensions import db
        from app.models import Participant

        assert db.session.get(Participant, pid).nom == "ANONYME"


def test_page_purge_refusee_aux_anonymes(client):
    assert client.get("/controle/purge-rgpd").status_code == 302
    assert client.post("/controle/purge-rgpd/lancer").status_code == 302


def test_premiere_activation_sarme_sans_purger(app):
    """Au premier déclenchement automatique, rien n'est effacé : l'équipe a
    une journée pour vérifier la liste avant la première exécution réelle."""
    pid = _creer_participant(app, anciennete_jours=4 * 365, nom_prefixe="Armement")

    with app.app_context():
        from app.extensions import db
        from app.models import Participant, TachePlanifiee
        from app.services.purge_rgpd import NOM_TACHE, derniere_purge, purge_quotidienne_si_necessaire

        TachePlanifiee.query.filter_by(nom=NOM_TACHE).delete()
        db.session.commit()

        purge_quotidienne_si_necessaire()

        assert db.session.get(Participant, pid).nom != "ANONYME", (
            "la première activation ne doit rien effacer"
        )
        assert derniere_purge() is not None, "mais elle doit s'armer pour demain"


def test_declenchement_quotidien_automatique(app):
    """La première requête du jour déclenche la purge sans intervention."""
    pid = _creer_participant(app, anciennete_jours=4 * 365, nom_prefixe="Quotidien")

    # On simule « la dernière purge date d'hier » et un marqueur mémoire vierge :
    # plus simple et plus fiable que de créer une application dédiée.
    with app.app_context():
        from app.extensions import db
        from app.models import TachePlanifiee
        from app.services.purge_rgpd import NOM_TACHE
        from app.utils.dates import utcnow

        tache = TachePlanifiee.query.filter_by(nom=NOM_TACHE).first()
        if tache is None:
            tache = TachePlanifiee(nom=NOM_TACHE)
            db.session.add(tache)
        tache.derniere_execution = utcnow() - timedelta(days=1)
        db.session.commit()

        from app.services.purge_rgpd import purge_quotidienne_si_necessaire

        purge_quotidienne_si_necessaire()

        from app.models import Participant

        assert db.session.get(Participant, pid).nom == "ANONYME"

        # Relance le même jour : ne refait rien (horodatage du jour)
        autre = _creer_participant(app, anciennete_jours=4 * 365)
        purge_quotidienne_si_necessaire()
        assert db.session.get(Participant, autre).nom != "ANONYME"
