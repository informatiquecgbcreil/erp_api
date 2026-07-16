"""Adhésions, participation, foyers et barème des tarifs.

- l'année scolaire se calcule correctement (rentrée en septembre) ;
- le tarif « en vigueur » suit la ligne de barème la plus récente ;
- une cotisation fige son montant dû à la création (pas de rétroactivité) ;
- versements, reste dû, solde, relance (sans doublon) ;
- adhésion familiale : rapprochement de foyer, couverture partagée ;
- droits : cotisations:view (lecture), cotisations:edit (mutations),
  cotisations:tarifs (barème, direction/finance seulement).
"""
import datetime as dt
import uuid

import pytest


def _login_role(app, email, role_code, secteur=None):
    from app.extensions import db
    from app.models import Role, User
    with app.app_context():
        u = User.query.filter_by(email=email).first()
        if u is None:
            u = User(email=email, nom=email.split("@")[0], secteur_assigne=secteur)
            u.set_password("pw-test-123")
            u.roles.append(Role.query.filter_by(code=role_code).first())
            db.session.add(u)
            db.session.commit()
    c = app.test_client()
    c.post("/", data={"email": email, "password": "pw-test-123"})
    return c


def _make_participant(app, **kwargs):
    from app.extensions import db
    from app.models import Participant
    with app.app_context():
        suf = uuid.uuid4().hex[:6]
        p = Participant(nom=kwargs.pop("nom", f"Cotis{suf}"), prenom=kwargs.pop("prenom", "Test"), **kwargs)
        db.session.add(p)
        db.session.commit()
        return p.id


# ---------- Service : année scolaire, tarif en vigueur ----------

def test_annee_scolaire_de():
    from app.services.cotisations import annee_scolaire_de, libelle_annee_scolaire
    assert annee_scolaire_de(dt.date(2026, 9, 1)) == 2026
    assert annee_scolaire_de(dt.date(2026, 12, 15)) == 2026
    assert annee_scolaire_de(dt.date(2027, 1, 10)) == 2026
    assert annee_scolaire_de(dt.date(2027, 8, 31)) == 2026
    assert annee_scolaire_de(dt.date(2027, 9, 1)) == 2027
    assert libelle_annee_scolaire(2026) == "2026-2027"


def test_tarif_en_vigueur_prend_la_ligne_la_plus_recente(app):
    from app.extensions import db
    from app.models import TarifBareme
    from app.services.cotisations import tarif_en_vigueur
    with app.app_context():
        annee = 2099  # année dédiée au test, jamais utilisée ailleurs
        db.session.add_all([
            TarifBareme(annee_scolaire=annee, type_tarif="participation", montant=20.0, date_debut=dt.date(annee, 9, 1)),
            TarifBareme(annee_scolaire=annee, type_tarif="participation", montant=15.0, date_debut=dt.date(annee + 1, 1, 1)),
            TarifBareme(annee_scolaire=annee, type_tarif="participation", montant=10.0, date_debut=dt.date(annee + 1, 5, 1)),
        ])
        db.session.commit()

        assert tarif_en_vigueur(annee, "participation", dt.date(annee, 10, 1)).montant == 20.0
        assert tarif_en_vigueur(annee, "participation", dt.date(annee + 1, 2, 1)).montant == 15.0
        assert tarif_en_vigueur(annee, "participation", dt.date(annee + 1, 6, 1)).montant == 10.0
        assert tarif_en_vigueur(annee, "adhesion_individuelle", dt.date(annee, 10, 1)) is None


# ---------- Cotisation : création, montant figé, versements, reste, solde ----------

def test_creer_adhesion_individuelle_fige_le_montant_du(app, admin_client):
    from app.extensions import db
    from app.models import TarifBareme
    from app.services.cotisations import annee_scolaire_courante

    annee = annee_scolaire_courante()
    with app.app_context():
        db.session.add(TarifBareme(annee_scolaire=annee, type_tarif="adhesion_individuelle", montant=25.0, date_debut=dt.date(annee, 9, 1)))
        db.session.commit()

    pid = _make_participant(app)
    r = admin_client.post(f"/participants/{pid}/cotisation/creer", data={"type_cotisation": "adhesion_individuelle"})
    assert r.status_code == 302
    with app.app_context():
        from app.models import Cotisation
        c = Cotisation.query.filter_by(participant_id=pid, type_cotisation="adhesion_individuelle").one()
        assert c.montant_du == 25.0
        assert c.montant_regle == 0
        assert c.reste_du == 25.0
        assert not c.solde

    # Changer le barème APRÈS coup ne doit PAS modifier la cotisation déjà créée.
    with app.app_context():
        db.session.add(TarifBareme(annee_scolaire=annee, type_tarif="adhesion_individuelle", montant=99.0, date_debut=dt.date.today()))
        db.session.commit()
        c = Cotisation.query.filter_by(participant_id=pid, type_cotisation="adhesion_individuelle").one()
        assert c.montant_du == 25.0  # inchangé


def test_creation_en_double_refusee(app, admin_client):
    pid = _make_participant(app)
    admin_client.post(f"/participants/{pid}/cotisation/creer", data={"type_cotisation": "participation"})
    admin_client.post(f"/participants/{pid}/cotisation/creer", data={"type_cotisation": "participation"})
    with app.app_context():
        from app.models import Cotisation
        assert Cotisation.query.filter_by(participant_id=pid, type_cotisation="participation").count() == 1


def test_versements_reste_du_et_solde(app, admin_client):
    from app.extensions import db
    from app.models import TarifBareme
    from app.services.cotisations import annee_scolaire_courante
    annee = annee_scolaire_courante()
    with app.app_context():
        db.session.add(TarifBareme(annee_scolaire=annee, type_tarif="participation", montant=30.0, date_debut=dt.date(annee, 9, 1)))
        db.session.commit()

    pid = _make_participant(app)
    admin_client.post(f"/participants/{pid}/cotisation/creer", data={"type_cotisation": "participation"})
    with app.app_context():
        from app.models import Cotisation
        cid = Cotisation.query.filter_by(participant_id=pid, type_cotisation="participation").one().id

    admin_client.post(f"/participants/{pid}/cotisation/{cid}/versement",
                      data={"montant": "10", "date_paiement": "2027-01-05", "mode": "especes"})
    with app.app_context():
        from app.models import Cotisation
        c = db.session.get(Cotisation, cid)
        assert c.montant_regle == 10.0
        assert c.reste_du == 20.0
        assert not c.solde

    admin_client.post(f"/participants/{pid}/cotisation/{cid}/versement",
                      data={"montant": "20", "date_paiement": "2027-02-05", "mode": "cheque"})
    with app.app_context():
        c = db.session.get(Cotisation, cid)
        assert c.montant_regle == 30.0
        assert c.reste_du == 0
        assert c.solde


def test_relance_sans_doublon_et_refusee_si_soldee(app, admin_client):
    from app.extensions import db
    from app.models import Cotisation, SuiviRappel
    pid = _make_participant(app)
    admin_client.post(f"/participants/{pid}/cotisation/creer", data={"type_cotisation": "participation"})
    with app.app_context():
        cid = Cotisation.query.filter_by(participant_id=pid, type_cotisation="participation").one().id

    r = admin_client.post(f"/participants/{pid}/cotisation/{cid}/relancer")
    assert r.status_code == 302
    with app.app_context():
        assert SuiviRappel.query.filter(SuiviRappel.categorie == "cotisation",
                                        SuiviRappel.titre.contains(f"#{cid}")).count() == 1
    admin_client.post(f"/participants/{pid}/cotisation/{cid}/relancer")
    with app.app_context():
        assert SuiviRappel.query.filter(SuiviRappel.categorie == "cotisation",
                                        SuiviRappel.titre.contains(f"#{cid}")).count() == 1

    # Soldée : plus de relance possible
    admin_client.post(f"/participants/{pid}/cotisation/{cid}/versement", data={"montant": "1000", "mode": "especes"})
    r = admin_client.post(f"/participants/{pid}/cotisation/{cid}/relancer", follow_redirects=True)
    assert "déjà soldée" in r.get_data(as_text=True)


def test_correction_montant_du_et_suppression(app, admin_client):
    from app.extensions import db
    from app.models import Cotisation
    pid = _make_participant(app)
    admin_client.post(f"/participants/{pid}/cotisation/creer", data={"type_cotisation": "participation"})
    with app.app_context():
        cid = Cotisation.query.filter_by(participant_id=pid, type_cotisation="participation").one().id

    admin_client.post(f"/participants/{pid}/cotisation/{cid}/montant", data={"montant_du": "5.50"})
    with app.app_context():
        assert db.session.get(Cotisation, cid).montant_du == 5.50

    # Suppression refusée si déjà réglé
    admin_client.post(f"/participants/{pid}/cotisation/{cid}/versement", data={"montant": "5.50", "mode": "especes"})
    admin_client.post(f"/participants/{pid}/cotisation/{cid}/supprimer")
    with app.app_context():
        assert db.session.get(Cotisation, cid) is not None  # toujours là

    # Nouvelle cotisation sans règlement : suppression OK
    admin_client.post(f"/participants/{pid}/cotisation/creer", data={"type_cotisation": "adhesion_individuelle"})
    with app.app_context():
        cid2 = Cotisation.query.filter_by(participant_id=pid, type_cotisation="adhesion_individuelle").one().id
    admin_client.post(f"/participants/{pid}/cotisation/{cid2}/supprimer")
    with app.app_context():
        assert db.session.get(Cotisation, cid2) is None


# ---------- Foyer : rapprochement, adhésion familiale, détachement ----------

def test_rapprocher_creer_adhesion_familiale_couvre_les_deux(app, admin_client):
    from app.extensions import db
    from app.models import Cotisation, Participant, TarifBareme
    from app.services.cotisations import annee_scolaire_courante
    annee = annee_scolaire_courante()
    with app.app_context():
        db.session.add(TarifBareme(annee_scolaire=annee, type_tarif="adhesion_familiale", montant=40.0, date_debut=dt.date(annee, 9, 1)))
        db.session.commit()

    pid_a = _make_participant(app, nom="Traore", prenom="Fatou")
    pid_b = _make_participant(app, nom="Traore", prenom="Ali")

    r = admin_client.post(f"/participants/{pid_a}/foyer/rapprocher", data={"autre_participant_id": pid_b})
    assert r.status_code == 302
    with app.app_context():
        a = db.session.get(Participant, pid_a)
        b = db.session.get(Participant, pid_b)
        assert a.foyer_id is not None
        assert a.foyer_id == b.foyer_id

    admin_client.post(f"/participants/{pid_a}/cotisation/creer", data={"type_cotisation": "adhesion_familiale"})
    with app.app_context():
        a = db.session.get(Participant, pid_a)
        c = Cotisation.query.filter_by(foyer_id=a.foyer_id, type_cotisation="adhesion_familiale").one()
        assert c.montant_du == 40.0

    # La cotisation familiale du foyer apparaît aussi sur la fiche de B (couverture partagée).
    from app.services.cotisations import cotisations_du_participant
    with app.app_context():
        b = db.session.get(Participant, pid_b)
        cotis_b = cotisations_du_participant(b)
        assert any(x.type_cotisation == "adhesion_familiale" for x in cotis_b)


def test_rapprocher_refuse_deux_foyers_differents(app, admin_client):
    pid_a = _make_participant(app)
    pid_b = _make_participant(app)
    pid_c = _make_participant(app)
    admin_client.post(f"/participants/{pid_a}/foyer/rapprocher", data={"autre_participant_id": pid_b})
    # Construit un foyer distinct pour C via un quatrième participant.
    pid_d = _make_participant(app)
    admin_client.post(f"/participants/{pid_c}/foyer/rapprocher", data={"autre_participant_id": pid_d})

    r = admin_client.post(f"/participants/{pid_a}/foyer/rapprocher", data={"autre_participant_id": pid_c}, follow_redirects=True)
    assert "foyers différents" in r.get_data(as_text=True)
    with app.app_context():
        from app.extensions import db
        from app.models import Participant
        a = db.session.get(Participant, pid_a)
        c = db.session.get(Participant, pid_c)
        assert a.foyer_id != c.foyer_id


def test_detacher_le_dernier_membre_nettoie_le_foyer_vide(app, admin_client):
    from app.extensions import db
    from app.models import Foyer, Participant

    pid_a = _make_participant(app)
    pid_b = _make_participant(app)
    admin_client.post(f"/participants/{pid_a}/foyer/rapprocher", data={"autre_participant_id": pid_b})
    with app.app_context():
        foyer_id = db.session.get(Participant, pid_a).foyer_id

    admin_client.post(f"/participants/{pid_a}/foyer/detacher", data={"membre_id": pid_b})
    admin_client.post(f"/participants/{pid_a}/foyer/detacher", data={"membre_id": pid_a})
    with app.app_context():
        assert db.session.get(Participant, pid_a).foyer_id is None
        assert db.session.get(Foyer, foyer_id) is None


def test_detacher_un_membre_specifique_depuis_lautre_fiche(app, admin_client):
    from app.extensions import db
    from app.models import Participant

    pid_a = _make_participant(app)
    pid_b = _make_participant(app)
    admin_client.post(f"/participants/{pid_a}/foyer/rapprocher", data={"autre_participant_id": pid_b})

    # Depuis la fiche de A, on détache B (membre_id explicite).
    r = admin_client.post(f"/participants/{pid_a}/foyer/detacher", data={"membre_id": pid_b})
    assert r.status_code == 302
    with app.app_context():
        b = db.session.get(Participant, pid_b)
        assert b.foyer_id is None
        # A reste dans le foyer : seul B en a été détaché.
        a = db.session.get(Participant, pid_a)
        assert a.foyer_id is not None


def test_recherche_famille_exclut_les_membres_deja_lies(app, admin_client):
    pid_a = _make_participant(app, nom="Kone", prenom="Awa")
    pid_b = _make_participant(app, nom="Konepartner", prenom="Ismael")
    admin_client.post(f"/participants/{pid_a}/foyer/rapprocher", data={"autre_participant_id": pid_b})

    body = admin_client.get(f"/participants/{pid_a}/synthese?q_famille=kone").get_data(as_text=True)
    # B est déjà membre : n'apparaît plus dans les résultats de recherche.
    assert "foyer_rapprocher" not in body or "Konepartner" not in body.split("Rapprocher un membre")[-1]


# ---------- Droits : view / edit / tarifs ----------

def test_lecture_seule_sans_edit_naffiche_aucun_bouton_de_mutation(app):
    """finance a cotisations:edit ; on simule un rôle avec view seul via
    un rôle personnalisé pour vérifier que la mutation est bien coupée."""
    from app.extensions import db
    from app.models import Permission, Role
    with app.app_context():
        role = Role.query.filter_by(code="cotis_lecture").first()
        if role is None:
            role = Role(code="cotis_lecture", label="Lecture cotisations")
            db.session.add(role)
            for code in ("dashboard:view", "participants:view", "participants:view_all", "cotisations:view"):
                perm = Permission.query.filter_by(code=code).first()
                if perm:
                    role.permissions.append(perm)
            db.session.commit()

    c = _login_role(app, "cotis-lecture@example.org", "cotis_lecture")
    pid = _make_participant(app)
    body = c.get(f"/participants/{pid}/synthese").get_data(as_text=True)
    assert "Adhésion" in body  # la section est visible (cotisations:view)
    assert "cotisation_creer" not in body  # mais aucune action de mutation

    r = c.post(f"/participants/{pid}/cotisation/creer", data={"type_cotisation": "participation"})
    assert r.status_code == 403


def test_sans_cotisations_view_section_absente(app):
    c = _login_role(app, "cotis-tech@example.org", "admin_tech")
    pid = _make_participant(app)
    body = c.get(f"/participants/{pid}/synthese").get_data(as_text=True)
    assert "🎫 Adhésion" not in body


def test_tarifs_reserves_a_direction_finance(app, admin_client):
    # direction : accès OK
    r = admin_client.get("/tarifs")
    assert r.status_code == 200

    # responsable_secteur : a cotisations:view/edit mais PAS cotisations:tarifs
    c = _login_role(app, "cotis-resp@example.org", "responsable_secteur", secteur="Jeunesse")
    r = c.get("/tarifs")
    assert r.status_code == 403
    r = c.post("/tarifs/ligne", data={"annee": "2099", "type_tarif": "participation", "montant": "5", "date_debut": "2099-09-01"})
    assert r.status_code == 403


def test_page_tarifs_ajout_et_suppression(app, admin_client):
    from app.extensions import db
    from app.models import TarifBareme
    annee = 2098
    r = admin_client.post("/tarifs/ligne", data={
        "annee": str(annee), "type_tarif": "adhesion_individuelle",
        "montant": "22,50", "date_debut": f"{annee}-09-01", "commentaire": "tarif de rentrée",
    })
    assert r.status_code == 302
    with app.app_context():
        ligne = TarifBareme.query.filter_by(annee_scolaire=annee, type_tarif="adhesion_individuelle").one()
        assert ligne.montant == 22.5
        lid = ligne.id

    body = admin_client.get(f"/tarifs?annee={annee}").get_data(as_text=True)
    assert "22.50" in body or "22,50" in body

    admin_client.post(f"/tarifs/ligne/{lid}/supprimer", data={"annee": str(annee)})
    with app.app_context():
        assert db.session.get(TarifBareme, lid) is None
