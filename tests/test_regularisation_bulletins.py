"""Qui est venu sans bulletin — et comment rattraper.

Le module d'inscription est récent ; les participations, non. Une personne
qui vient depuis un an a des présences mais aucun bulletin, simplement
parce que le module n'existait pas quand elle est arrivée.

Rien ne disait LESQUELLES. Il fallait ouvrir les fiches une par une pour
le découvrir — donc, en pratique, on ne le découvrait pas.
"""
import uuid
from datetime import date

import pytest

ANNEE = 2043  # année scolaire dédiée : 01/09/2043 -> 31/08/2044


def _suffixe():
    return uuid.uuid4().hex[:6]


@pytest.fixture()
def venues(app):
    """Trois personnes venues, avec des assiduités différentes."""
    suf = _suffixe()
    with app.app_context():
        from app.extensions import db
        from app.models import AtelierActivite, Participant, PresenceActivite, SessionActivite

        atelier = AtelierActivite(secteur="Adultes", nom=f"Regul{suf}")
        db.session.add(atelier)
        db.session.flush()

        gens = {}
        for prenom, nb in (("Assidue", 5), ("Moyenne", 2), ("Rare", 1)):
            p = Participant(nom=f"{prenom}{suf}", prenom=prenom, ville="Creil",
                            genre="F", created_secteur="Adultes")
            db.session.add(p)
            db.session.flush()
            gens[prenom] = p.id
            for i in range(nb):
                s = SessionActivite(atelier_id=atelier.id, secteur="Adultes",
                                    session_type="COLLECTIF",
                                    date_session=date(ANNEE, 10, 1 + i))
                db.session.add(s)
                db.session.flush()
                db.session.add(PresenceActivite(session_id=s.id, participant_id=p.id))
        db.session.commit()
        contexte = {"atelier_id": atelier.id, "suffixe": suf, **gens}

    yield contexte

    with app.app_context():
        from app.extensions import db
        from app.models import AtelierActivite, InscriptionAnnuelle

        for ins in InscriptionAnnuelle.query.filter_by(annee_scolaire=ANNEE).all():
            db.session.delete(ins)
        a = db.session.get(AtelierActivite, contexte["atelier_id"])
        if a is not None:
            db.session.delete(a)
        db.session.commit()


# ---------------------------------------------------------------------------
# La liste
# ---------------------------------------------------------------------------

def test_les_plus_assidues_dabord(app, venues):
    """Ce sont elles qui pèsent le plus dans un bilan."""
    with app.app_context():
        from app.services.inscriptions_annuelles import participants_sans_bulletin

        lignes = [l for l in participants_sans_bulletin(ANNEE)
                  if venues["suffixe"] in (l["participant"].nom or "")]

    assert [l["participant"].prenom for l in lignes] == ["Assidue", "Moyenne", "Rare"]
    assert [l["presences"] for l in lignes] == [5, 2, 1]
    assert lignes[0]["derniere"] == date(ANNEE, 10, 5)


def test_une_personne_avec_bulletin_disparait_de_la_liste(app, venues):
    with app.app_context():
        from app.extensions import db
        from app.models import InscriptionAnnuelle, Participant
        from app.services.inscriptions_annuelles import participants_sans_bulletin

        fiche = db.session.get(Participant, venues["Assidue"])
        db.session.add(InscriptionAnnuelle(
            annee_scolaire=ANNEE, date_inscription=date.today(),
            nom=fiche.nom, prenom=fiche.prenom, participant_id=fiche.id))
        db.session.commit()

        restants = [l["participant"].prenom for l in participants_sans_bulletin(ANNEE)
                    if venues["suffixe"] in (l["participant"].nom or "")]
    assert restants == ["Moyenne", "Rare"]


def test_un_bulletin_sans_rattachement_compte_quand_meme(app, venues):
    """Un bulletin saisi AVANT que la fiche existe n'a pas d'identifiant :
    on le reconnaît au nom complet, sinon on proposerait de le recréer."""
    with app.app_context():
        from app.extensions import db
        from app.models import InscriptionAnnuelle, Participant
        from app.services.inscriptions_annuelles import participants_sans_bulletin

        fiche = db.session.get(Participant, venues["Rare"])
        db.session.add(InscriptionAnnuelle(
            annee_scolaire=ANNEE, date_inscription=date.today(),
            nom=fiche.nom.upper(), prenom=fiche.prenom.upper()))  # casse différente
        db.session.commit()

        restants = [l["participant"].prenom for l in participants_sans_bulletin(ANNEE)
                    if venues["suffixe"] in (l["participant"].nom or "")]
    assert "Rare" not in restants


def test_une_seance_annulee_ne_compte_pas(app, venues):
    with app.app_context():
        from app.extensions import db
        from app.models import SessionActivite
        from app.services.inscriptions_annuelles import participants_sans_bulletin

        for s in SessionActivite.query.filter_by(atelier_id=venues["atelier_id"]).all():
            s.statut = "annulee"
        db.session.commit()

        assert [l for l in participants_sans_bulletin(ANNEE)
                if venues["suffixe"] in (l["participant"].nom or "")] == []


def test_une_autre_annee_scolaire_nest_pas_concernee(app, venues):
    with app.app_context():
        from app.services.inscriptions_annuelles import participants_sans_bulletin

        assert [l for l in participants_sans_bulletin(ANNEE + 1)
                if venues["suffixe"] in (l["participant"].nom or "")] == []


def test_bornes_de_lannee_scolaire():
    from app.services.inscriptions_annuelles import bornes_annee_scolaire

    assert bornes_annee_scolaire(2026) == (date(2026, 9, 1), date(2027, 8, 31))


# ---------------------------------------------------------------------------
# Le rattrapage
# ---------------------------------------------------------------------------

def test_la_creation_groupee_reprend_la_fiche(app, venues):
    with app.app_context():
        from app.extensions import db
        from app.models import InscriptionAnnuelle, Participant
        from app.services.inscriptions_annuelles import regulariser_depuis_les_fiches

        fiches = [db.session.get(Participant, venues[p]) for p in ("Assidue", "Moyenne")]
        crees, _ = regulariser_depuis_les_fiches(ANNEE, fiches)

        assert len(crees) == 2
        bulletin = InscriptionAnnuelle.query.filter_by(
            annee_scolaire=ANNEE, participant_id=venues["Assidue"]).one()
        assert bulletin.ville == "Creil"
        assert bulletin.genre == "F"
        assert "créé après coup" in (bulletin.commentaire or "")
        assert bulletin.participant_id == venues["Assidue"]


def test_la_creation_groupee_ninvente_ni_reglement_ni_adhesion(app, venues):
    """Ce sont des actes administratifs : ils demandent une personne, pas un
    bouton. Un bulletin qui affirmerait un règlement jamais encaissé
    fausserait les comptes ET la relation avec l'adhérent."""
    with app.app_context():
        from app.extensions import db
        from app.models import Cotisation, InscriptionAnnuelle, Participant
        from app.services.inscriptions_annuelles import regulariser_depuis_les_fiches

        avant_cotisations = Cotisation.query.count()
        fiche = db.session.get(Participant, venues["Assidue"])
        regulariser_depuis_les_fiches(ANNEE, [fiche])

        bulletin = InscriptionAnnuelle.query.filter_by(
            annee_scolaire=ANNEE, participant_id=fiche.id).one()

        assert bulletin.reglement_statut == "rien"
        assert bulletin.reglement_confirme is False
        assert bulletin.cotisation_id is None
        assert list(bulletin.ateliers) == []
        assert Cotisation.query.count() == avant_cotisations


def test_une_personne_deja_inscrite_est_ignoree(app, venues):
    with app.app_context():
        from app.extensions import db
        from app.models import InscriptionAnnuelle, Participant
        from app.services.inscriptions_annuelles import regulariser_depuis_les_fiches

        fiche = db.session.get(Participant, venues["Rare"])
        regulariser_depuis_les_fiches(ANNEE, [fiche])
        crees, avertissements = regulariser_depuis_les_fiches(ANNEE, [fiche])

        assert crees == []
        assert any("déjà un bulletin" in m for m in avertissements)
        assert InscriptionAnnuelle.query.filter_by(
            annee_scolaire=ANNEE, participant_id=fiche.id).count() == 1


# ---------------------------------------------------------------------------
# Les écrans
# ---------------------------------------------------------------------------

def test_lecran_liste_et_compte(admin_client, venues):
    page = admin_client.get(
        f"/inscriptions-annuelles/a-regulariser?annee={ANNEE}").get_data(as_text=True)
    assert "À régulariser" in page
    assert f"Assidue{venues['suffixe']}" in page
    assert "Créer les bulletins manquants" in page
    # Le bouton « Inscrire » de chaque ligne mène au bulletin prérempli.
    assert f"participant_id={venues['Assidue']}" in page


def test_la_campagne_annonce_le_nombre(admin_client, venues):
    page = admin_client.get(
        f"/inscriptions-annuelles/?annee={ANNEE}").get_data(as_text=True)
    assert "À régulariser" in page


def test_creation_groupee_depuis_lecran(admin_client, app, venues):
    r = admin_client.post(
        f"/inscriptions-annuelles/a-regulariser/creer?annee={ANNEE}",
        data={"pid": [str(venues["Assidue"]), str(venues["Moyenne"])]},
        follow_redirects=True)
    assert r.status_code == 200
    assert "bulletin(s) créé(s)" in r.get_data(as_text=True)

    with app.app_context():
        from app.models import InscriptionAnnuelle

        assert InscriptionAnnuelle.query.filter_by(annee_scolaire=ANNEE).count() == 2


def test_rien_de_coche_le_dit(admin_client, venues):
    r = admin_client.post(f"/inscriptions-annuelles/a-regulariser/creer?annee={ANNEE}",
                          data={}, follow_redirects=True)
    assert "abord au moins une personne" in r.get_data(as_text=True)
