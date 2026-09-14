"""Contexte de travail : l'application se souvient de l'année consultée.

Quarante-deux écrans demandent une année, cinquante un secteur, et rien
n'était retenu : préparer le bilan 2025 puis cliquer vers les dépenses
ramenait en 2026. Le principe testé ici est celui d'un défaut qui suit,
jamais d'une contrainte : le paramètre explicite l'emporte toujours.
"""
from datetime import date

import pytest

from app.services.contexte import (
    ANNEE_MAX,
    ANNEE_MIN,
    annee_travail,
    memoriser_depuis_la_requete,
    oublier,
    secteur_travail,
)

ANNEE_COURANTE = date.today().year


def test_sans_rien_en_memoire_on_part_de_lannee_courante(app):
    with app.test_request_context("/"):
        oublier()
        assert annee_travail() == ANNEE_COURANTE


def test_une_annee_consultee_est_retenue(app):
    with app.test_request_context("/?year=2024"):
        oublier()
        memoriser_depuis_la_requete()
        assert annee_travail() == 2024


def test_le_parametre_explicite_gagne_toujours(app, admin_client):
    """La souplesse d'abord : mémoriser ne doit jamais imposer."""
    admin_client.get("/dashboard?year=2024")
    reponse = admin_client.get("/dashboard?year=2027")
    assert reponse.status_code == 200
    # Et c'est le dernier consulté qui devient le nouveau défaut.
    with admin_client.session_transaction() as session:
        assert session.get("annee_travail") == 2027


def test_le_contexte_suit_dun_ecran_a_lautre(admin_client):
    """Le cœur du sujet : on choisit une fois, on garde jusqu'à changer."""
    admin_client.get("/dashboard?year=2024")
    with admin_client.session_transaction() as session:
        assert session.get("annee_travail") == 2024

    # Un écran qui ne parle pas d'année ne doit pas réinitialiser le contexte.
    admin_client.get("/participants/")
    with admin_client.session_transaction() as session:
        assert session.get("annee_travail") == 2024


def test_le_secteur_choisi_est_retenu(admin_client):
    admin_client.get("/participants/?secteur=Numérique")
    with admin_client.session_transaction() as session:
        assert session.get("secteur_travail") == "Numérique"


def test_choisir_tous_secteurs_est_un_choix_qui_se_retient(app):
    """Sinon le filtre se remettrait tout seul à l'écran suivant."""
    with app.test_request_context("/?secteur=Numérique"):
        oublier()
        memoriser_depuis_la_requete()
    with app.test_request_context("/?secteur="):
        memoriser_depuis_la_requete()
        assert secteur_travail("repli") == ""


def test_une_annee_absurde_est_ignoree(app):
    with app.test_request_context("/?year=1066"):
        oublier()
        memoriser_depuis_la_requete()
        assert annee_travail() == ANNEE_COURANTE
    with app.test_request_context("/?year=99999"):
        memoriser_depuis_la_requete()
        assert annee_travail() == ANNEE_COURANTE
    with app.test_request_context("/?year=pas-une-annee"):
        memoriser_depuis_la_requete()
        assert annee_travail() == ANNEE_COURANTE


def test_les_bornes_restent_acceptees(app):
    for annee in (ANNEE_MIN, ANNEE_MAX):
        with app.test_request_context(f"/?year={annee}"):
            oublier()
            memoriser_depuis_la_requete()
            assert annee_travail() == annee


def test_un_envoi_de_formulaire_ne_touche_pas_au_contexte(admin_client):
    """Une année passée dans un POST concerne l'enregistrement en cours,
    pas ce qu'on regarde : la confondre déplacerait le contexte à l'insu
    de l'utilisateur.

    Passe par le client et non par des contextes de requête isolés : la
    session ne survit qu'à travers les cookies d'un vrai navigateur.
    """
    admin_client.get("/dashboard?year=2024")
    admin_client.post("/ui-mode?year=2030", data={"mode": "expert"})
    with admin_client.session_transaction() as session:
        assert session.get("annee_travail") == 2024


def test_oublier_repart_de_zero(app):
    with app.test_request_context("/?year=2024&secteur=Familles"):
        oublier()
        memoriser_depuis_la_requete()
        assert annee_travail() == 2024
        oublier()
        assert annee_travail() == ANNEE_COURANTE
        assert secteur_travail("repli") == "repli"


def test_le_repli_fourni_prime_sur_lannee_courante(app):
    with app.test_request_context("/"):
        oublier()
        assert annee_travail(2019) == 2019


# ---------------------------------------------------------------------------
# Le tableau de bord : annoncer sans permettre d'y aller fait perdre du temps
# ---------------------------------------------------------------------------

def test_les_elements_recents_sont_cliquables_et_nommes(admin_client, app):
    """Le bloc affichait « Atelier #17 » — un identifiant de base — et rien
    n'était cliquable. Il annonçait qu'une séance existait puis laissait
    aller la chercher, perdant le temps qu'il devait faire gagner.

    On vérifie la FORME du bloc et non une séance précise : la liste se
    limite aux plus récentes, et dépendre d'un classement rendrait le test
    sensible à ce que font les autres.
    """
    from app.extensions import db
    from app.models import AtelierActivite, SessionActivite

    with app.app_context():
        atelier = AtelierActivite(secteur="Numérique", nom="Atelier repère unique")
        db.session.add(atelier)
        db.session.flush()
        seance = SessionActivite(
            atelier_id=atelier.id, secteur="Numérique",
            date_session=date.today(), heure_debut="14:00", heure_fin="16:00",
        )
        db.session.add(seance)
        db.session.commit()
        ids = (seance.id, atelier.id)

    admin_client.post("/ui-mode", data={"mode": "expert"})
    page = admin_client.get("/dashboard").get_data(as_text=True)

    assert "Atelier #" not in page, "plus aucun identifiant brut à l'écran"
    if "recent-item" in page:
        assert 'a class="recent-item"' in page, "les éléments récents mènent quelque part"

    with app.app_context():
        db.session.delete(db.session.get(SessionActivite, ids[0]))
        db.session.delete(db.session.get(AtelierActivite, ids[1]))
        db.session.commit()


def test_le_raccourci_de_recherche_est_annonce(admin_client):
    """Ctrl+K existait déjà, écrit nulle part : invisible pour qui ne le
    savait pas."""
    page = admin_client.get("/dashboard").get_data(as_text=True)
    assert "Ctrl+K" in page
