"""Étape 2 — Inscriptions préalables, listes d'attente, export Brevo.

- jauge : au-delà de la capacité, bascule automatique en liste d'attente ;
- annulation : promotion automatique du plus ancien en attente ;
- doublon refusé (message métier, pas d'erreur technique) ;
- attendus d'une séance = inscrits séance + inscrits atelier entier ;
- « pointer présent » crée la ligne d'émargement (idempotent) ;
- export contacts au format d'import Brevo (EMAIL;NOM;PRENOM;SMS) ;
- pages protégées (permission + anonymes refusés).
"""
import uuid
from datetime import date, timedelta

import pytest


@pytest.fixture()
def contexte(app):
    """Un atelier (capacité 2), une séance à venir, quatre participants."""
    suf = uuid.uuid4().hex[:6]
    with app.app_context():
        from app.extensions import db
        from app.models import AtelierActivite, Participant, SessionActivite

        atelier = AtelierActivite(nom=f"Sortie {suf}", secteur="Familles", capacite_defaut=2)
        db.session.add(atelier)
        db.session.flush()
        session = SessionActivite(
            atelier_id=atelier.id, secteur="Familles", session_type="COLLECTIF",
            date_session=date.today() + timedelta(days=7),
            heure_debut="14:00", heure_fin="17:00", capacite=2,
        )
        participants = [
            Participant(nom=f"Insc{suf}", prenom=f"P{i}",
                        email=f"p{i}.{suf}@exemple.org" if i != 3 else None,
                        telephone="0601020304" if i == 3 else None)
            for i in range(4)
        ]
        db.session.add(session)
        db.session.add_all(participants)
        db.session.commit()
        return {
            "suf": suf,
            "atelier_id": atelier.id,
            "session_id": session.id,
            "participant_ids": [p.id for p in participants],
        }


def _get(app, model, pk):
    from app.extensions import db
    return db.session.get(model, pk)


# ---------------------------------------------------------------------------
# Service : jauge, attente, promotion, doublons
# ---------------------------------------------------------------------------

def test_jauge_puis_liste_attente(app, contexte):
    with app.app_context():
        from app.models import AtelierActivite, Participant, SessionActivite
        from app.services.inscriptions import compteurs, inscrire

        atelier = _get(app, AtelierActivite, contexte["atelier_id"])
        session = _get(app, SessionActivite, contexte["session_id"])
        p = [_get(app, Participant, pid) for pid in contexte["participant_ids"]]

        assert inscrire(p[0], atelier, session).statut == "inscrit"
        assert inscrire(p[1], atelier, session).statut == "inscrit"
        # Capacité 2 atteinte -> liste d'attente, jamais de refus.
        assert inscrire(p[2], atelier, session).statut == "attente"

        etat = compteurs(atelier, session)
        assert etat == {"inscrits": 2, "attente": 1, "capacite": 2,
                        "places_restantes": 0, "complet": True}


def test_annulation_promeut_le_plus_ancien(app, contexte):
    with app.app_context():
        from app.models import AtelierActivite, Participant, SessionActivite
        from app.services.inscriptions import annuler, inscrire

        atelier = _get(app, AtelierActivite, contexte["atelier_id"])
        session = _get(app, SessionActivite, contexte["session_id"])
        p = [_get(app, Participant, pid) for pid in contexte["participant_ids"]]

        i0 = inscrire(p[0], atelier, session)
        inscrire(p[1], atelier, session)
        i2 = inscrire(p[2], atelier, session)   # premier en attente
        i3 = inscrire(p[3], atelier, session)   # second en attente
        assert (i2.statut, i3.statut) == ("attente", "attente")

        promue = annuler(i0)
        assert promue is not None and promue.id == i2.id, "le plus ancien passe inscrit"
        assert i2.statut == "inscrit"
        assert i3.statut == "attente"
        assert i0.statut == "annule"

        # Retirer quelqu'un de la liste d'attente ne promeut personne.
        assert annuler(i3) is None


def test_doublon_refuse_avec_message(app, contexte):
    with app.app_context():
        from app.models import AtelierActivite, Participant, SessionActivite
        from app.services.inscriptions import InscriptionErreur, inscrire

        atelier = _get(app, AtelierActivite, contexte["atelier_id"])
        session = _get(app, SessionActivite, contexte["session_id"])
        p0 = _get(app, Participant, contexte["participant_ids"][0])

        inscrire(p0, atelier, session)
        with pytest.raises(InscriptionErreur, match="déjà"):
            inscrire(p0, atelier, session)
        # Mais l'inscription « atelier entier » reste possible (autre cible).
        assert inscrire(p0, atelier, None).statut == "inscrit"


def test_attendus_et_pointage(app, contexte):
    with app.app_context():
        from app.models import AtelierActivite, Participant, PresenceActivite, SessionActivite
        from app.services.inscriptions import (
            a_pointer_session, attendus_session, inscrire, pointer_present,
        )

        atelier = _get(app, AtelierActivite, contexte["atelier_id"])
        session = _get(app, SessionActivite, contexte["session_id"])
        p = [_get(app, Participant, pid) for pid in contexte["participant_ids"]]

        inscrire(p[0], atelier, session)        # inscrit séance
        inscrire(p[1], atelier, None)           # inscrit atelier entier
        attendus_ids = {i.participant_id for i in attendus_session(session)}
        assert attendus_ids == {p[0].id, p[1].id}, "séance + atelier entier"

        presence = pointer_present(session, p[0].id)
        assert presence.id is not None
        # Idempotent : pas de doublon d'émargement.
        assert pointer_present(session, p[0].id).id == presence.id
        assert PresenceActivite.query.filter_by(
            session_id=session.id, participant_id=p[0].id).count() == 1

        restants = {i.participant_id for i in a_pointer_session(session)}
        assert restants == {p[1].id}


# ---------------------------------------------------------------------------
# Export Brevo
# ---------------------------------------------------------------------------

def test_contacts_brevo(app, contexte):
    with app.app_context():
        from app.models import AtelierActivite, Participant, SessionActivite
        from app.services.inscriptions import contacts_brevo, inscrire

        atelier = _get(app, AtelierActivite, contexte["atelier_id"])
        session = _get(app, SessionActivite, contexte["session_id"])
        p = [_get(app, Participant, pid) for pid in contexte["participant_ids"]]

        inscrire(p[0], atelier, session)   # email
        inscrire(p[3], atelier, None)      # téléphone seul -> exportable via SMS
        contacts = contacts_brevo(atelier, session)
        assert {c["PRENOM"] for c in contacts} == {"P0", "P3"}
        par_prenom = {c["PRENOM"]: c for c in contacts}
        assert par_prenom["P0"]["EMAIL"].endswith("@exemple.org")
        assert par_prenom["P3"]["SMS"] == "0601020304"


def test_export_brevo_csv(admin_client, app, contexte):
    with app.app_context():
        from app.models import AtelierActivite, Participant
        from app.services.inscriptions import inscrire

        atelier = _get(app, AtelierActivite, contexte["atelier_id"])
        p0 = _get(app, Participant, contexte["participant_ids"][0])
        inscrire(p0, atelier, None)

    r = admin_client.get(f"/activite/atelier/{contexte['atelier_id']}/inscriptions/export-brevo.csv")
    assert r.status_code == 200
    assert "csv" in r.content_type
    contenu = r.get_data(as_text=True)
    assert "EMAIL;NOM;PRENOM;SMS" in contenu
    assert f"p0.{contexte['suf']}@exemple.org" in contenu


# ---------------------------------------------------------------------------
# Pages et permissions
# ---------------------------------------------------------------------------

def test_pages_inscriptions(admin_client, contexte):
    r = admin_client.get(f"/activite/atelier/{contexte['atelier_id']}/inscriptions")
    assert r.status_code == 200
    page = r.get_data(as_text=True)
    assert "Inscriptions ·" in page
    assert "liste d'attente" in page

    r = admin_client.get(f"/activite/session/{contexte['session_id']}/inscriptions")
    assert r.status_code == 200
    assert "séance du" in r.get_data(as_text=True)


def test_inscription_via_formulaire(admin_client, app, contexte):
    pid = contexte["participant_ids"][0]
    r = admin_client.post("/activite/inscriptions/creer", data={
        "atelier_id": contexte["atelier_id"],
        "session_id": contexte["session_id"],
        "participant_id": pid,
    }, follow_redirects=True)
    assert r.status_code == 200
    assert "inscrit·e ✅" in r.get_data(as_text=True)

    with app.app_context():
        from app.models import InscriptionActivite
        assert InscriptionActivite.query.filter_by(
            session_id=contexte["session_id"], participant_id=pid, statut="inscrit"
        ).count() == 1


def test_emargement_affiche_les_inscrits_a_pointer(admin_client, app, contexte):
    with app.app_context():
        from app.models import AtelierActivite, Participant, SessionActivite
        from app.services.inscriptions import inscrire

        atelier = _get(app, AtelierActivite, contexte["atelier_id"])
        session = _get(app, SessionActivite, contexte["session_id"])
        p0 = _get(app, Participant, contexte["participant_ids"][0])
        inscrire(p0, atelier, session)

    r = admin_client.get(f"/activite/session/{contexte['session_id']}/emargement")
    assert r.status_code == 200
    page = r.get_data(as_text=True)
    assert "pas encore pointé" in page
    assert f"Insc{contexte['suf']} P0" in page


def test_pointer_depuis_emargement(admin_client, app, contexte):
    with app.app_context():
        from app.models import AtelierActivite, Participant, SessionActivite
        from app.services.inscriptions import inscrire

        atelier = _get(app, AtelierActivite, contexte["atelier_id"])
        session = _get(app, SessionActivite, contexte["session_id"])
        p0 = _get(app, Participant, contexte["participant_ids"][0])
        inscrire(p0, atelier, session)

    r = admin_client.post(
        f"/activite/session/{contexte['session_id']}/inscriptions/pointer",
        data={"participant_id": contexte["participant_ids"][0], "retour": "emargement"},
        follow_redirects=True,
    )
    assert r.status_code == 200
    assert "présent·e ✅" in r.get_data(as_text=True)

    with app.app_context():
        from app.models import PresenceActivite
        assert PresenceActivite.query.filter_by(
            session_id=contexte["session_id"],
            participant_id=contexte["participant_ids"][0],
        ).count() == 1


def test_refus_anonymes(client, contexte):
    assert client.get(
        f"/activite/atelier/{contexte['atelier_id']}/inscriptions"
    ).status_code in (302, 401, 403)
    assert client.post("/activite/inscriptions/creer", data={}).status_code in (302, 401, 403)


def test_permission_inscriptions_dans_rbac(app):
    """Les nouvelles permissions existent et sont attribuées aux bons rôles."""
    with app.app_context():
        from app.models import Permission, Role

        assert Permission.query.filter_by(code="inscriptions:view").first() is not None
        assert Permission.query.filter_by(code="inscriptions:edit").first() is not None
        direction = Role.query.filter_by(code="direction").first()
        codes = {p.code for p in direction.permissions}
        assert {"inscriptions:view", "inscriptions:edit"} <= codes
