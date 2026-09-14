"""Réinscrire quelqu'un que l'application connaît déjà.

La direction inscription -> fiche participant était bien construite (avec
détection de doublons). La direction inverse n'existait pas : à la rentrée,
l'accueil retapait nom, prénom, adresse, ville, e-mail, téléphone, date de
naissance et genre d'une personne suivie depuis trois ans.

Ce fichier vérifie les trois promesses du bouton « Inscrire pour 2026-2027 » :
- le bulletin arrive prérempli avec ce que la fiche sait déjà ;
- il se rattache tout seul à la fiche à l'enregistrement (pas de doublon) ;
- une personne déjà inscrite cette année-là ouvre SON bulletin au lieu d'en
  créer un second.
"""
import uuid
from datetime import date

import pytest

ANNEE = 2041  # campagne dédiée, à l'écart des autres jeux de tests


def _suffixe():
    return uuid.uuid4().hex[:6]


@pytest.fixture()
def fiche(app):
    """Une fiche participant bien remplie, comme une personne suivie depuis
    des années : c'est tout ce qu'on ne veut plus retaper."""
    suf = _suffixe()
    with app.app_context():
        from app.extensions import db
        from app.models import Participant

        p = Participant(
            nom=f"Dubreuil{suf}",
            prenom="Fatima",
            adresse="12 rue des Usines",
            ville="Creil",
            email=f"fatima.{suf}@example.org",
            telephone="0344000000",
            genre="Femme",
            date_naissance=date(1979, 3, 14),
            created_secteur="Adultes",
        )
        db.session.add(p)
        db.session.commit()
        return {"id": p.id, "nom": p.nom, "prenom": p.prenom, "email": p.email}


def _nettoyer_bulletins(app, nom):
    with app.app_context():
        from app.extensions import db
        from app.models import InscriptionAnnuelle

        for ins in InscriptionAnnuelle.query.filter_by(nom=nom).all():
            db.session.delete(ins)
        db.session.commit()


# ---------------------------------------------------------------------------
# Préremplissage
# ---------------------------------------------------------------------------

def test_prefill_reprend_les_champs_connus(app, fiche):
    """Huit champs retapés à la main, zéro désormais."""
    with app.app_context():
        from app.extensions import db
        from app.models import Participant
        from app.services.inscriptions_annuelles import prefill_depuis_participant

        p = db.session.get(Participant, fiche["id"])
        valeurs = prefill_depuis_participant(p)

    assert valeurs["nom"] == fiche["nom"]
    assert valeurs["prenom"] == "Fatima"
    assert valeurs["adresse"] == "12 rue des Usines"
    assert valeurs["ville"] == "Creil"
    assert valeurs["email"] == fiche["email"]
    assert valeurs["telephone"] == "0344000000"
    assert valeurs["genre"] == "Femme"
    assert valeurs["date_naissance"] == "1979-03-14"
    assert valeurs["secteur_orienteur"] == "Adultes"


def test_prefill_omet_les_champs_vides(app):
    """Une valeur vide n'est pas posée : le gabarit doit pouvoir retomber sur
    son propre défaut plutôt que d'afficher un champ qui a l'air renseigné."""
    with app.app_context():
        from app.extensions import db
        from app.models import Participant
        from app.services.inscriptions_annuelles import prefill_depuis_participant

        p = Participant(nom=f"Nu{_suffixe()}", prenom="Sans")
        db.session.add(p)
        db.session.commit()
        valeurs = prefill_depuis_participant(p)

    assert set(valeurs) == {"nom", "prenom"}
    assert "ville" not in valeurs
    assert "date_naissance" not in valeurs


def test_formulaire_prerempli_a_l_ecran(admin_client, app, fiche):
    """Le bouton de la fiche ouvre un bulletin déjà rempli, et le dit."""
    _nettoyer_bulletins(app, fiche["nom"])
    r = admin_client.get(
        f"/inscriptions-annuelles/nouvelle?participant_id={fiche['id']}&annee={ANNEE}"
    )
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert fiche["nom"] in html
    assert "12 rue des Usines" in html
    assert "0344000000" in html
    # Le bandeau explique d'où viennent les valeurs affichées.
    assert "Prérempli depuis la fiche" in html
    # Et le lien de retour vers la fiche est posé en champ caché.
    assert f'name="participant_id" value="{fiche["id"]}"' in html


def test_formulaire_vierge_sans_participant(admin_client):
    """Sans participant, la saisie reste ce qu'elle était : un bulletin vide."""
    r = admin_client.get(f"/inscriptions-annuelles/nouvelle?annee={ANNEE}")
    assert r.status_code == 200
    assert "Prérempli depuis la fiche" not in r.get_data(as_text=True)


def test_bouton_present_sur_la_fiche(admin_client, app, fiche):
    """Le geste doit être visible là où on est : sur la fiche de la personne."""
    r = admin_client.get(f"/participants/{fiche['id']}/synthese")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Inscrire pour" in html
    assert f"participant_id={fiche['id']}" in html


# ---------------------------------------------------------------------------
# Rattachement automatique
# ---------------------------------------------------------------------------

def test_enregistrement_rattache_la_fiche(admin_client, app, fiche):
    """Enregistrer le bulletin le relie à la fiche : aucun doublon à créer,
    aucun second geste de rattachement à faire."""
    _nettoyer_bulletins(app, fiche["nom"])
    r = admin_client.post(
        f"/inscriptions-annuelles/nouvelle?annee={ANNEE}",
        data={
            "annee": ANNEE,
            "participant_id": fiche["id"],
            "nom": fiche["nom"],
            "prenom": fiche["prenom"],
            "ville": "Creil",
        },
        follow_redirects=True,
    )
    assert r.status_code == 200

    with app.app_context():
        from app.models import InscriptionAnnuelle, Participant

        ins = InscriptionAnnuelle.query.filter_by(nom=fiche["nom"], annee_scolaire=ANNEE).one()
        assert ins.participant_id == fiche["id"]
        # Et la fiche n'a pas été dupliquée au passage.
        assert Participant.query.filter_by(nom=fiche["nom"]).count() == 1


def test_deja_inscrit_ouvre_son_bulletin(admin_client, app, fiche):
    """Deuxième clic sur le bouton : on ouvre le bulletin existant plutôt que
    d'en ouvrir un second — le doublon se paierait au moment du bilan."""
    _nettoyer_bulletins(app, fiche["nom"])
    admin_client.post(
        f"/inscriptions-annuelles/nouvelle?annee={ANNEE}",
        data={
            "annee": ANNEE,
            "participant_id": fiche["id"],
            "nom": fiche["nom"],
            "prenom": fiche["prenom"],
        },
        follow_redirects=True,
    )

    r = admin_client.get(
        f"/inscriptions-annuelles/nouvelle?participant_id={fiche['id']}&annee={ANNEE}"
    )
    assert r.status_code == 302
    assert "/inscriptions-annuelles/" in r.headers["Location"]
    assert "/nouvelle" not in r.headers["Location"]

    with app.app_context():
        from app.models import InscriptionAnnuelle

        assert InscriptionAnnuelle.query.filter_by(nom=fiche["nom"], annee_scolaire=ANNEE).count() == 1


def test_bulletin_existant_reconnait_le_nom_sans_rattachement(app, fiche):
    """Un bulletin saisi à l'accueil AVANT que la fiche existe n'a pas encore
    de participant_id : on le retrouve quand même par le nom complet, sinon
    on crée le doublon qu'on prétend éviter."""
    _nettoyer_bulletins(app, fiche["nom"])
    with app.app_context():
        from app.extensions import db
        from app.models import InscriptionAnnuelle, Participant
        from app.services.inscriptions_annuelles import bulletin_existant

        db.session.add(InscriptionAnnuelle(
            annee_scolaire=ANNEE,
            date_inscription=date.today(),
            # Casse différente : la secrétaire n'écrit pas comme l'import.
            nom=fiche["nom"].upper(),
            prenom="FATIMA",
        ))
        db.session.commit()

        p = db.session.get(Participant, fiche["id"])
        assert bulletin_existant(p, ANNEE) is not None
        # Une autre année n'est pas concernée.
        assert bulletin_existant(p, ANNEE + 1) is None


def test_participant_inconnu_ne_casse_pas_la_saisie(admin_client, app):
    """Un id fantaisiste dans l'URL ne doit pas planter l'accueil : on ouvre
    un bulletin vide, ce qui reste le geste utile."""
    r = admin_client.get(f"/inscriptions-annuelles/nouvelle?participant_id=99999999&annee={ANNEE}")
    assert r.status_code == 200
    assert "Prérempli depuis la fiche" not in r.get_data(as_text=True)
