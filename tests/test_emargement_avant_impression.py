"""Le garde-fou du pré-émargement.

L'application permet de pointer les inscrits AVANT la séance, puis de faire
signer — au kiosque, ou par un lien personnel envoyé à chacun. Très bien.
Le revers : une personne pré-émargée qui n'est finalement pas venue reste
sur la liste et ressort sur la feuille imprimée avec une case de signature
VIDE. C'est la première chose qu'un financeur regarde, et rien ne le
rappelait au moment de générer.

Règle de métier qui évite de crier au loup : une personne déclarée
**absente excusée** n'a aucune raison de signer. Sa ligne documente
justement le fait qu'elle n'est pas venue. La compter comme manquante
ferait sonner l'avertissement à chaque séance — et un avertissement qui
sonne toujours n'est plus lu.
"""
import uuid
from datetime import date

import pytest


def _suffixe():
    return uuid.uuid4().hex[:6]


@pytest.fixture()
def seance(app):
    """Une séance pré-émargée : deux personnes pointées, personne n'a signé."""
    suf = _suffixe()
    with app.app_context():
        from app.extensions import db
        from app.models import AtelierActivite, Participant, PresenceActivite, SessionActivite

        atelier = AtelierActivite(secteur="Adultes", nom=f"Preem{suf}")
        db.session.add(atelier)
        db.session.flush()
        s = SessionActivite(atelier_id=atelier.id, secteur="Adultes", session_type="COLLECTIF",
                            date_session=date(2026, 5, 12), heure_debut="14:00", heure_fin="16:00")
        db.session.add(s)
        db.session.flush()

        venue = Participant(nom=f"Avenue{suf}", prenom="Sonia")
        absente = Participant(nom=f"Babin{suf}", prenom="Karim")
        db.session.add_all([venue, absente])
        db.session.flush()
        db.session.add_all([
            PresenceActivite(session_id=s.id, participant_id=venue.id),
            PresenceActivite(session_id=s.id, participant_id=absente.id),
        ])
        db.session.commit()
        contexte = {"session_id": s.id, "atelier_id": atelier.id,
                    "venue_id": venue.id, "absente_id": absente.id, "nom_atelier": atelier.nom}

    yield contexte

    with app.app_context():
        from app.extensions import db
        from app.models import ArchiveEmargement, AtelierActivite

        # Les archives d'émargement D'ABORD : elles référencent la séance
        # sans cascade. SQLite n'applique pas les clés étrangères et
        # laissait des archives orphelines ; PostgreSQL refuse la
        # suppression, et il a raison.
        for archive in ArchiveEmargement.query.filter_by(
                atelier_id=contexte["atelier_id"]).all():
            db.session.delete(archive)
        a = db.session.get(AtelierActivite, contexte["atelier_id"])
        if a is not None:
            db.session.delete(a)
        db.session.commit()


def _signer(app, session_id, participant_id):
    with app.app_context():
        from app.extensions import db
        from app.models import PresenceActivite

        pr = PresenceActivite.query.filter_by(
            session_id=session_id, participant_id=participant_id).one()
        pr.signature_path = "/tmp/signature-de-test.png"
        db.session.commit()


# ---------------------------------------------------------------------------
# Ce qui appelle une décision
# ---------------------------------------------------------------------------

def test_les_non_signees_sont_reperees(app, seance):
    with app.app_context():
        from app.extensions import db
        from app.models import SessionActivite
        from app.services.emargement import presences_sans_signature, resume_signatures

        s = db.session.get(SessionActivite, seance["session_id"])
        assert len(presences_sans_signature(s)) == 2
        assert resume_signatures(s) == {"total": 2, "signees": 0, "manquantes": 2, "excusees": 0}


def test_une_signature_posee_sort_du_compte(app, seance):
    _signer(app, seance["session_id"], seance["venue_id"])
    with app.app_context():
        from app.extensions import db
        from app.models import SessionActivite
        from app.services.emargement import resume_signatures

        s = db.session.get(SessionActivite, seance["session_id"])
        assert resume_signatures(s)["manquantes"] == 1


def test_un_absent_excuse_ne_declenche_pas_lalerte(app, seance):
    """LA règle qui évite de crier au loup : il n'a pas à signer."""
    with app.app_context():
        from app.extensions import db
        from app.models import PresenceActivite, SessionActivite
        from app.services.emargement import presences_sans_signature, resume_signatures

        pr = PresenceActivite.query.filter_by(
            session_id=seance["session_id"], participant_id=seance["absente_id"]).one()
        pr.presence_type = "absent_excuse"
        db.session.commit()

        s = db.session.get(SessionActivite, seance["session_id"])
        assert [p.participant_id for p in presences_sans_signature(s)] == [seance["venue_id"]]
        resume = resume_signatures(s)
        assert resume["manquantes"] == 1
        assert resume["excusees"] == 1


def test_un_retard_doit_signer(app, seance):
    with app.app_context():
        from app.extensions import db
        from app.models import PresenceActivite, SessionActivite
        from app.services.emargement import presences_sans_signature

        pr = PresenceActivite.query.filter_by(
            session_id=seance["session_id"], participant_id=seance["venue_id"]).one()
        pr.presence_type = "retard"
        db.session.commit()
        s = db.session.get(SessionActivite, seance["session_id"])
        assert len(presences_sans_signature(s)) == 2


# ---------------------------------------------------------------------------
# L'écran qui s'interpose
# ---------------------------------------------------------------------------

def test_generer_sarrete_et_demande(admin_client, seance):
    r = admin_client.get(f"/activite/session/{seance['session_id']}/generate_collectif")
    assert r.status_code == 200
    page = r.get_data(as_text=True)
    # On ne reçoit PAS un fichier : on reçoit une question.
    assert "avant d'imprimer" in page.lower()
    assert "case de signature vide" in page
    # Avec les noms, pas juste un compteur.
    assert "Sonia" in page and "Karim" in page
    # Et les trois façons d'en sortir.
    assert "et générer la feuille" in page
    assert "lien de signature" in page
    assert "Générer quand même" in page


def _est_un_document(reponse) -> bool:
    """La réponse est-elle le fichier, et non une page HTML ?

    La feuille revient en DOCX ou PDF : un corps binaire qui ne se décode
    même pas en UTF-8. C'est précisément le signe qu'on n'a pas reçu
    l'écran de question.
    """
    return reponse.status_code == 200 and "html" not in (reponse.mimetype or "")


def test_tout_signe_genere_directement(admin_client, app, seance):
    """Rien à signaler : on ne s'interpose pas, on rend le document."""
    _signer(app, seance["session_id"], seance["venue_id"])
    _signer(app, seance["session_id"], seance["absente_id"])
    r = admin_client.get(f"/activite/session/{seance['session_id']}/generate_collectif")
    assert _est_un_document(r) or r.status_code == 302


def test_generer_quand_meme_passe(admin_client, seance):
    """La question a été posée et tranchée : on n'insiste pas."""
    r = admin_client.get(
        f"/activite/session/{seance['session_id']}/generate_collectif?confirme=1")
    assert _est_un_document(r) or r.status_code == 302


# ---------------------------------------------------------------------------
# Le retrait groupé
# ---------------------------------------------------------------------------

def test_retirer_les_non_signees(admin_client, app, seance):
    """Le geste du pré-émargement raté : trois pointés, deux venus."""
    _signer(app, seance["session_id"], seance["venue_id"])

    r = admin_client.post(
        f"/activite/session/{seance['session_id']}/retirer-non-signees",
        follow_redirects=True)
    assert r.status_code == 200

    with app.app_context():
        from app.models import PresenceActivite

        restantes = PresenceActivite.query.filter_by(session_id=seance["session_id"]).all()
        assert [pr.participant_id for pr in restantes] == [seance["venue_id"]]


def test_le_retrait_epargne_les_absents_excuses(app, seance):
    """Leur trace a de la valeur dans le dossier : on ne l'efface pas."""
    with app.app_context():
        from app.extensions import db
        from app.models import PresenceActivite, SessionActivite
        from app.services.emargement import retirer_les_non_signees

        pr = PresenceActivite.query.filter_by(
            session_id=seance["session_id"], participant_id=seance["absente_id"]).one()
        pr.presence_type = "absent_excuse"
        db.session.commit()

        s = db.session.get(SessionActivite, seance["session_id"])
        retires = retirer_les_non_signees(s)

        assert len(retires) == 1
        restantes = PresenceActivite.query.filter_by(session_id=seance["session_id"]).all()
        assert [pr.participant_id for pr in restantes] == [seance["absente_id"]]


def test_le_retrait_est_trace(admin_client, app, seance):
    """Supprimer une ligne d'émargement n'est pas anodin."""
    with app.app_context():
        from app.models import AuditLog

        avant = AuditLog.query.filter_by(action="presence.delete").count()

    admin_client.post(f"/activite/session/{seance['session_id']}/retirer-non-signees",
                      follow_redirects=True)

    with app.app_context():
        from app.models import AuditLog

        lignes = AuditLog.query.filter_by(action="presence.delete").all()
        assert len(lignes) == avant + 2
        assert any("non signée, avant impression" in (l.cible or "") for l in lignes)


def test_rien_a_retirer_le_dit(admin_client, app, seance):
    _signer(app, seance["session_id"], seance["venue_id"])
    _signer(app, seance["session_id"], seance["absente_id"])
    r = admin_client.post(f"/activite/session/{seance['session_id']}/retirer-non-signees",
                          follow_redirects=True)
    assert "Aucune présence à retirer" in r.get_data(as_text=True)


# ---------------------------------------------------------------------------
# Ce qui se voit à l'écran
# ---------------------------------------------------------------------------

def test_lecran_marque_les_lignes_non_signees(admin_client, app, seance):
    _signer(app, seance["session_id"], seance["venue_id"])
    page = admin_client.get(
        f"/activite/session/{seance['session_id']}/emargement").get_data(as_text=True)

    assert "Pas signée" in page, "l'état doit se lire sur la ligne"
    assert "Signée" in page
    assert "ligne-sans-signature" in page, "la ligne elle-même doit se repérer"
    # Le bouton d'impression annonce ce qui attend.
    assert "Générer la feuille PDF" in page


def test_labsence_excusee_saffiche_sans_objet(admin_client, app, seance):
    with app.app_context():
        from app.extensions import db
        from app.models import PresenceActivite

        pr = PresenceActivite.query.filter_by(
            session_id=seance["session_id"], participant_id=seance["absente_id"]).one()
        pr.presence_type = "absent_excuse"
        db.session.commit()

    page = admin_client.get(
        f"/activite/session/{seance['session_id']}/emargement").get_data(as_text=True)
    assert "sans objet" in page
