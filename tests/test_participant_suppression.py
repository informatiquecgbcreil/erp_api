"""Suppression définitive d'une fiche participant.

Fonction destructive et sans retour : on vérifie autant ce qu'elle efface
que ce qu'elle refuse de faire, et la trace qu'elle laisse.
"""
import datetime as dt
import uuid


import pytest


@pytest.fixture(autouse=True)
def _nettoyer_les_fiches_de_test(app):
    """La base de test est partagée par toute la session : les fiches que ces
    tests laissent derrière eux (cas de refus de suppression) fausseraient les
    totaux d'autres modules, par exemple les impayés. On efface donc à la fin."""
    yield
    from app.extensions import db
    from app.models import Participant
    from app.services.participant_suppression import supprimer_definitivement

    with app.app_context():
        restantes = Participant.query.filter(Participant.nom.like("DUPONT%")).all()
        for fiche in restantes:
            supprimer_definitivement(fiche)
        if restantes:
            db.session.commit()


def _fiche_avec_historique(app, *, tag, secteur="Numérique"):
    """Un participant et un échantillon de tout ce qui peut s'y rattacher."""
    from app.extensions import db
    from app.models import (
        AtelierActivite, BenevoleHeures, Cotisation, Evaluation, HartEvaluation,
        InscriptionActivite, Paiement, Participant, PasseportNote,
        PresenceActivite, SessionActivite,
    )
    with app.app_context():
        p = Participant(nom=f"DUPONT{tag}", prenom="Erreur", created_secteur=secteur,
                        ville="Creil", genre="Homme", type_public="H",
                        date_naissance=dt.date(1990, 1, 1))
        at = AtelierActivite(nom=f"Atelier{tag}", secteur=secteur, type_atelier="COLLECTIF",
                             capacite_defaut=10)
        db.session.add_all([p, at])
        db.session.flush()
        s = SessionActivite(atelier_id=at.id, secteur=secteur, session_type="COLLECTIF",
                            date_session=dt.date.today(), heure_debut="14:00",
                            heure_fin="16:00", statut="realisee")
        db.session.add(s)
        db.session.flush()
        db.session.add_all([
            PresenceActivite(session_id=s.id, participant_id=p.id),
            InscriptionActivite(participant_id=p.id, atelier_id=at.id, session_id=s.id, statut="inscrit"),
            PasseportNote(participant_id=p.id, session_id=s.id, secteur=secteur,
                          categorie="session", contenu="note de test"),
            HartEvaluation(participant_id=p.id, secteur=secteur, type_evaluation="initiale",
                           date_evaluation=dt.date.today(), niveau=3),
            BenevoleHeures(participant_id=p.id, secteur=secteur, date_action=dt.date.today(), heures=2.0),
        ])
        cot = Cotisation(participant_id=p.id, annee_scolaire="2025-2026", type_cotisation="individuelle",
                         montant_du=10.0, date_reference=dt.date.today())
        db.session.add(cot)
        db.session.flush()
        paiement = Paiement(cotisation_id=cot.id, montant=10.0, date_paiement=dt.date.today())
        db.session.add(paiement)
        db.session.commit()
        return p.id, paiement.id


def test_analyse_liste_l_historique(app):
    from app.extensions import db
    from app.models import Participant
    from app.services.participant_suppression import analyser

    tag = uuid.uuid4().hex[:6]
    pid, _ = _fiche_avec_historique(app, tag=tag)
    with app.app_context():
        resume = analyser(db.session.get(Participant, pid))
    libelles = dict(resume["details"])
    assert resume["total"] >= 6
    assert libelles["présences à des séances"] == 1
    assert libelles["adhésions / cotisations"] == 1
    assert libelles["heures de bénévolat"] == 1


def test_suppression_efface_tout(app, admin_client):
    from app.extensions import db
    from app.models import (
        BenevoleHeures, Cotisation, HartEvaluation, InscriptionActivite,
        Paiement, Participant, PasseportNote, PresenceActivite,
    )

    tag = uuid.uuid4().hex[:6]
    pid, paiement_id = _fiche_avec_historique(app, tag=tag)

    r = admin_client.post(f"/participants/{pid}/delete",
                          data={"confirmation_nom": f"DUPONT{tag}", "motif": "doublon de saisie"})
    assert r.status_code == 302

    with app.app_context():
        assert db.session.get(Participant, pid) is None
        for modele in (PresenceActivite, InscriptionActivite, PasseportNote,
                       HartEvaluation, BenevoleHeures, Cotisation):
            reste = db.session.query(modele).filter_by(participant_id=pid).count()
            assert reste == 0, f"{modele.__name__} : {reste} ligne(s) orpheline(s)"
        # Le paiement rattaché à la cotisation part avec elle.
        assert db.session.get(Paiement, paiement_id) is None


def test_confirmation_par_le_nom_obligatoire(app, admin_client):
    """Sans le nom exact, rien n'est supprimé : un clic seul ne suffit pas."""
    from app.extensions import db
    from app.models import Participant, PresenceActivite

    tag = uuid.uuid4().hex[:6]
    pid, _ = _fiche_avec_historique(app, tag=tag)

    for donnees in ({}, {"confirmation_nom": ""}, {"confirmation_nom": "AUTRE"}):
        r = admin_client.post(f"/participants/{pid}/delete", data=donnees)
        assert r.status_code == 302
        with app.app_context():
            assert db.session.get(Participant, pid) is not None, f"supprimé avec {donnees}"
            assert db.session.query(PresenceActivite).filter_by(participant_id=pid).count() == 1


def test_confirmation_insensible_a_la_casse(app, admin_client):
    from app.extensions import db
    from app.models import Participant

    tag = uuid.uuid4().hex[:6]
    pid, _ = _fiche_avec_historique(app, tag=tag)
    r = admin_client.post(f"/participants/{pid}/delete",
                          data={"confirmation_nom": f"  dupont{tag}  "})
    assert r.status_code == 302
    with app.app_context():
        assert db.session.get(Participant, pid) is None


def test_suppression_journalisee_avec_instantane(app, admin_client):
    """Après coup, il ne reste que le journal : il doit tout dire."""
    import json
    from app.extensions import db
    from app.models import AuditLog

    tag = uuid.uuid4().hex[:6]
    pid, _ = _fiche_avec_historique(app, tag=tag)
    admin_client.post(f"/participants/{pid}/delete",
                      data={"confirmation_nom": f"DUPONT{tag}", "motif": "doublon de saisie"})

    with app.app_context():
        entree = (AuditLog.query.filter_by(action="participant.delete")
                  .order_by(AuditLog.id.desc()).first())
        assert entree is not None, "aucune trace au journal"
        assert f"DUPONT{tag}" in (entree.cible or "")
        assert entree.user_email, "l'auteur de la suppression doit être identifié"
        details = json.loads(entree.details)
        assert details["participant"]["id"] == pid
        assert details["participant"]["nom"] == f"DUPONT{tag}"
        assert details["participant"]["date_naissance"] == "1990-01-01"
        assert details["motif"] == "doublon de saisie"
        assert details["total_elements_supprimes"] >= 6
        assert details["historique_supprime"]["présences à des séances"] == 1


def test_secteur_ne_supprime_pas_une_personne_suivie_ailleurs(app):
    """Garde-fou existant conservé : hors de son périmètre, on n'efface pas."""
    from app.extensions import db
    from app.models import (
        AtelierActivite, Participant, PresenceActivite, Role, SessionActivite, User,
    )

    tag = uuid.uuid4().hex[:6]
    pid, _ = _fiche_avec_historique(app, tag=tag, secteur=f"Autre{tag}")

    # Une présence dans un secteur qui n'est pas le sien.
    with app.app_context():
        p = db.session.get(Participant, pid)
        email = f"resp-{tag}@ex.org"
        u = User(email=email, nom="Resp", secteur_assigne=f"Mien{tag}")
        u.set_password("pw-test-123")
        u.roles.append(Role.query.filter_by(code="responsable_secteur").first())
        db.session.add(u)
        db.session.commit()

    client = app.test_client()
    client.post("/", data={"email": f"resp-{tag}@ex.org", "password": "pw-test-123"})
    r = client.post(f"/participants/{pid}/delete", data={"confirmation_nom": f"DUPONT{tag}"})
    assert r.status_code == 302
    with app.app_context():
        assert db.session.get(Participant, pid) is not None, "la fiche aurait dû être protégée"


def test_sans_permission_interdit(app):
    from app.extensions import db
    from app.models import Participant, Permission, Role, User

    tag = uuid.uuid4().hex[:6]
    pid, _ = _fiche_avec_historique(app, tag=tag)

    email = f"nodel-{tag}@ex.org"
    with app.app_context():
        role = Role(code=f"nodel{tag}", label="Sans suppression")
        db.session.add(role)
        db.session.flush()
        for code in ("dashboard:view", "participants:view", "participants:edit"):
            perm = Permission.query.filter_by(code=code).first()
            if perm is not None:
                role.permissions.append(perm)
        u = User(email=email, nom="NoDel", secteur_assigne="Numérique")
        u.set_password("pw-test-123")
        u.roles.append(role)
        db.session.add(u)
        db.session.commit()
        assert not u.has_perm("participants:delete")

    client = app.test_client()
    client.post("/", data={"email": email, "password": "pw-test-123"})
    r = client.post(f"/participants/{pid}/delete", data={"confirmation_nom": f"DUPONT{tag}"})
    assert r.status_code == 403
    with app.app_context():
        assert db.session.get(Participant, pid) is not None


def test_ecran_annonce_ce_qui_sera_detruit(app, admin_client):
    """Avant de décider, on doit voir l'inventaire et retaper le nom."""
    tag = uuid.uuid4().hex[:6]
    pid, _ = _fiche_avec_historique(app, tag=tag)

    page = admin_client.get(f"/participants/{pid}/edit").get_data(as_text=True)
    assert 'id="suppression"' in page
    assert "Supprimer définitivement" in page
    assert 'name="confirmation_nom"' in page
    # L'historique est annoncé, pas caché.
    assert "présences à des séances" in page
    assert "adhésions / cotisations" in page
    assert "anonymisation" in page.lower()


def test_fiche_vierge_annonce_l_absence_d_impact(app, admin_client):
    from app.extensions import db
    from app.models import Participant

    tag = uuid.uuid4().hex[:6]
    with app.app_context():
        p = Participant(nom=f"DUPONT{tag}", prenom="Vierge", created_secteur="Numérique",
                        ville="Creil", type_public="H")
        db.session.add(p)
        db.session.commit()
        pid = p.id

    page = admin_client.get(f"/participants/{pid}/edit").get_data(as_text=True)
    assert "Aucune présence ni donnée d'activité" in page


def test_les_listes_ne_suppriment_plus_en_un_clic(app, admin_client):
    """La suppression exige une confirmation par le nom : les anciens boutons
    des listes, qui postaient directement, renvoient vers la fiche."""
    tag = uuid.uuid4().hex[:6]
    _fiche_avec_historique(app, tag=tag)

    page = admin_client.get("/participants/", follow_redirects=True).get_data(as_text=True)
    assert "/delete" not in page, "une liste poste encore une suppression directe"
