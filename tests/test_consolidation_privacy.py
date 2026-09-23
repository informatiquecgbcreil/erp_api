"""Régressions : effacement, sessions et compteurs, sur SQLite et PostgreSQL."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
import uuid


def test_anonymisation_transactionnelle_et_complete_des_fichiers(app):
    from app.extensions import db
    from app.models import Participant, PasseportPieceJointe, PasseportNote, PendingFileDeletion, ParticipantInsertionParcours
    from app.services.purge_rgpd import anonymiser_participant
    with app.app_context():
        path = Path(app.instance_path) / "passeport_uploads" / (uuid.uuid4().hex + ".txt")
        path.parent.mkdir(parents=True, exist_ok=True); path.write_text("Document nominatif")
        p = Participant(nom="Identité", prenom="Privée", date_naissance=date(1980,5,6), latitude=49.2,
                        longitude=2.4, geocode_query="Adresse précise", created_secteur="Familles")
        db.session.add(p); db.session.flush(); pid=p.id
        db.session.add_all([
            PasseportNote(participant_id=pid, contenu="Observation personnelle", secteur="Familles"),
            PasseportPieceJointe(participant_id=pid, file_path=str(path), original_name="prive.txt"),
            ParticipantInsertionParcours(participant_id=pid, date_debut_titre_sejour=date(2020,1,1)),
        ]); db.session.commit()
        anonymiser_participant(p); db.session.flush()
        assert path.exists(), "Le document reste tant que la transaction n'est pas validée"
        db.session.rollback()
        assert path.exists() and db.session.get(Participant,pid).nom == "Identité"
        assert PendingFileDeletion.query.filter_by(file_path=str(path)).count() == 0
        anonymiser_participant(db.session.get(Participant,pid)); db.session.commit()
        assert not path.exists()
        p=db.session.get(Participant,pid)
        assert p.date_naissance is None and p.annee_naissance == 1980
        assert p.latitude is None and p.longitude is None and p.geocode_query is None
        assert PasseportNote.query.filter_by(participant_id=pid).one().contenu == "Donnée effacée"
        assert ParticipantInsertionParcours.query.filter_by(participant_id=pid).one().date_debut_titre_sejour is None


def test_changer_mot_de_passe_revoque_les_sessions(app):
    from app.extensions import db
    from app.models import User, Role
    email=uuid.uuid4().hex+"@session.test"
    with app.app_context():
        u=User(email=email,nom="Session"); u.set_password("ancien-mot-de-passe")
        u.roles.append(Role.query.filter_by(code="direction").one())
        db.session.add(u); db.session.commit(); uid=u.id
    client=app.test_client()
    assert client.post("/",data={"email":email,"password":"ancien-mot-de-passe"}).status_code == 302
    assert client.get("/dashboard").status_code == 200
    with app.app_context():
        db.session.get(User,uid).set_password("nouveau-mot-de-passe"); db.session.commit()
    assert client.get("/dashboard").status_code == 302


def test_echecs_distants_ne_verrouillent_pas_autre_poste(app):
    from app.services.connexion_securite import enregistrer_echec, minutes_avant_deverrouillage, MAX_ECHECS
    with app.app_context():
        email=uuid.uuid4().hex+"@verrou.test"
        for _ in range(MAX_ECHECS): enregistrer_echec(email,"198.51.100.1")
        assert minutes_avant_deverrouillage(email,"198.51.100.1") > 0
        assert minutes_avant_deverrouillage(email,"198.51.100.2") == 0


def test_numerotation_concurrente_ne_reutilise_aucun_numero(app):
    from app.extensions import db
    from app.services.financial_sequence import next_number
    namespace="test:"+uuid.uuid4().hex
    def issue(_):
        with app.app_context():
            n=next_number(namespace,["ancien-0017"])
            db.session.commit()
            return n
    with ThreadPoolExecutor(max_workers=6) as pool:
        values=list(pool.map(issue,range(10)))
    assert sorted(values) == list(range(18,28))


def test_ical_ne_publie_pas_les_observations_memes_activees():
    from types import SimpleNamespace
    from app.services.calendrier import _description_seance
    s=SimpleNamespace(rdv_date=None,date_session=date.today(),rdv_debut=None,heure_debut=None,
                      rdv_fin=None,heure_fin=None,intention_seance="Identité privée",bilan_qualitatif="Note privée")
    assert _description_seance(s,None,0,{"champs_description":["bilan"]}) == ""
