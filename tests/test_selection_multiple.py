"""Agir sur plusieurs lignes d'un coup.

Le mécanisme existait déjà sur l'annuaire des participants, écrit à la main
dans le gabarit. Il est maintenant posé une fois pour toutes dans
``_selection_multiple.html`` et branché sur les deux listes où la saisie
ligne à ligne coûtait le plus cher :

- **les séances** — annuler une semaine de vacances, corriger une salle
  après un déménagement, vider un lot programmé par erreur ;
- **les dépenses** — imputer un paquet de factures au même financeur,
  reconduire les charges du mois.

Découverte au passage, vérifiée ici : ``SessionActivite.statut`` valait
« realisee » ou « annulee », huit endroits du code respectaient déjà
« annulee » (flux iCal, synchro Google Agenda, saisie en grille,
indicateurs, consommation, transitions), et AUCUN écran ne savait
l'écrire. On ne pouvait que jeter une séance à la corbeille.
"""
import uuid
from datetime import date

import pytest


def _suffixe():
    return uuid.uuid4().hex[:6]


# ---------------------------------------------------------------------------
# Séances
# ---------------------------------------------------------------------------

@pytest.fixture()
def semaine(app):
    """Un atelier et sa semaine de séances — celle qui tombe aux vacances."""
    suf = _suffixe()
    with app.app_context():
        from app.extensions import db
        from app.models import AtelierActivite, SessionActivite

        a = AtelierActivite(secteur="Adultes", nom=f"Atelier hebdo {suf}")
        db.session.add(a)
        db.session.flush()
        ids = []
        for jour in range(20, 25):
            s = SessionActivite(
                atelier_id=a.id, secteur="Adultes", session_type="COLLECTIF",
                date_session=date(2026, 4, jour), heure_debut="09:00", heure_fin="11:00",
            )
            db.session.add(s)
            db.session.flush()
            ids.append(s.id)
        db.session.commit()
        contexte = {"atelier_id": a.id, "seances": ids, "nom": a.nom}

    yield contexte

    # Voir test_duplication : la liste d'étiquetage Transitions plafonne à
    # 200 ateliers, et une fixture qui s'accumule casse un test d'ailleurs.
    with app.app_context():
        from app.extensions import db
        from app.models import AtelierActivite

        for atelier in AtelierActivite.query.filter(
            db.or_(AtelierActivite.nom == contexte["nom"],
                   AtelierActivite.nom.like("Ailleurs %"))
        ).all():
            db.session.delete(atelier)
        db.session.commit()


def test_annuler_une_semaine_en_un_geste(admin_client, app, semaine):
    r = admin_client.post(
        f"/activite/atelier/{semaine['atelier_id']}/sessions/actions",
        data={"action": "annuler", "sid": [str(i) for i in semaine["seances"]]},
    )
    assert r.status_code == 302

    with app.app_context():
        from app.extensions import db
        from app.models import SessionActivite

        for sid in semaine["seances"]:
            s = db.session.get(SessionActivite, sid)
            assert s.statut == "annulee"
            # Annulée n'est pas supprimée : la trace reste, la séance aussi.
            assert s.is_deleted is False


def test_annulation_libere_la_salle(admin_client, app, semaine):
    """Le planning doit rendre la salle : c'est tout l'intérêt d'annuler
    plutôt que de laisser la séance au programme."""
    with app.app_context():
        from app.extensions import db
        from app.models import Espace, Occupation, SessionActivite, Site

        site = Site(nom=f"Centre {_suffixe()}", code=f"C{_suffixe()}")
        db.session.add(site)
        db.session.flush()
        salle = Espace(site_id=site.id, nom=f"Salle {_suffixe()}", reservable=True)
        db.session.add(salle)
        db.session.flush()
        salle_id = salle.id
        for sid in semaine["seances"]:
            db.session.get(SessionActivite, sid).espace_id = salle_id
        db.session.commit()
        assert Occupation.query.filter_by(espace_id=salle_id).count() == 5

    admin_client.post(
        f"/activite/atelier/{semaine['atelier_id']}/sessions/actions",
        data={"action": "annuler", "sid": [str(i) for i in semaine["seances"]]},
    )

    with app.app_context():
        from app.models import Occupation

        assert Occupation.query.filter_by(espace_id=salle_id).count() == 0


def test_remettre_au_programme(admin_client, app, semaine):
    donnees = {"sid": [str(i) for i in semaine["seances"]]}
    admin_client.post(f"/activite/atelier/{semaine['atelier_id']}/sessions/actions",
                      data={**donnees, "action": "annuler"})
    admin_client.post(f"/activite/atelier/{semaine['atelier_id']}/sessions/actions",
                      data={**donnees, "action": "retablir"})

    with app.app_context():
        from app.extensions import db
        from app.models import SessionActivite

        for sid in semaine["seances"]:
            assert db.session.get(SessionActivite, sid).statut == "realisee"


def test_corbeille_groupee(admin_client, app, semaine):
    deux = [str(i) for i in semaine["seances"][:2]]
    admin_client.post(f"/activite/atelier/{semaine['atelier_id']}/sessions/actions",
                      data={"action": "corbeille", "sid": deux})

    with app.app_context():
        from app.extensions import db
        from app.models import SessionActivite

        assert db.session.get(SessionActivite, semaine["seances"][0]).is_deleted is True
        # Et surtout : les séances NON cochées ne bougent pas.
        assert db.session.get(SessionActivite, semaine["seances"][3]).is_deleted is False


def test_changer_la_salle_en_lot(admin_client, app, semaine):
    with app.app_context():
        from app.extensions import db
        from app.models import Espace, Site

        site = Site(nom=f"Centre {_suffixe()}", code=f"C{_suffixe()}")
        db.session.add(site)
        db.session.flush()
        salle = Espace(site_id=site.id, nom=f"Atelier {_suffixe()}", reservable=True)
        db.session.add(salle)
        db.session.commit()
        salle_id = salle.id

    admin_client.post(
        f"/activite/atelier/{semaine['atelier_id']}/sessions/actions",
        data={"action": "salle", "espace_id": str(salle_id),
              "sid": [str(i) for i in semaine["seances"]]},
    )

    with app.app_context():
        from app.extensions import db
        from app.models import SessionActivite

        for sid in semaine["seances"]:
            assert db.session.get(SessionActivite, sid).espace_id == salle_id


def test_aucune_salle_est_une_reponse_valable(admin_client, app, semaine):
    """« Hors les murs » n'est pas un champ oublié : la valeur 0 détache."""
    admin_client.post(
        f"/activite/atelier/{semaine['atelier_id']}/sessions/actions",
        data={"action": "salle", "espace_id": "0", "sid": [str(semaine["seances"][0])]},
    )
    with app.app_context():
        from app.extensions import db
        from app.models import SessionActivite

        assert db.session.get(SessionActivite, semaine["seances"][0]).espace_id is None


def test_salle_non_choisie_ne_fait_rien(admin_client, app, semaine):
    """Un champ laissé vide se distingue de « aucune salle » : on refuse."""
    r = admin_client.post(
        f"/activite/atelier/{semaine['atelier_id']}/sessions/actions",
        data={"action": "salle", "espace_id": "", "sid": [str(semaine["seances"][0])]},
        follow_redirects=True,
    )
    assert "Choisis une salle" in r.get_data(as_text=True)


def test_rien_de_coche_le_dit(admin_client, semaine):
    r = admin_client.post(
        f"/activite/atelier/{semaine['atelier_id']}/sessions/actions",
        data={"action": "annuler"},
        follow_redirects=True,
    )
    assert "abord au moins une séance" in r.get_data(as_text=True)


def test_une_seance_d_un_autre_atelier_est_ignoree(admin_client, app, semaine):
    """Un id glissé dans le formulaire ne doit pas atteindre une séance
    qu'on n'a pas devant les yeux."""
    with app.app_context():
        from app.extensions import db
        from app.models import AtelierActivite, SessionActivite

        autre = AtelierActivite(secteur="Adultes", nom=f"Ailleurs {_suffixe()}")
        db.session.add(autre)
        db.session.flush()
        intrus = SessionActivite(atelier_id=autre.id, secteur="Adultes",
                                 date_session=date(2026, 4, 20))
        db.session.add(intrus)
        db.session.commit()
        intrus_id = intrus.id

    admin_client.post(
        f"/activite/atelier/{semaine['atelier_id']}/sessions/actions",
        data={"action": "annuler", "sid": [str(intrus_id)]},
    )

    with app.app_context():
        from app.extensions import db
        from app.models import SessionActivite

        assert db.session.get(SessionActivite, intrus_id).statut == "realisee"


def test_coches_presentes_dans_la_liste(admin_client, semaine):
    r = admin_client.get(f"/activite/atelier/{semaine['atelier_id']}/sessions")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'name="sid"' in html
    assert "Annuler (vacances, fermeture)" in html
    assert f"/activite/atelier/{semaine['atelier_id']}/sessions/actions" in html


# ---------------------------------------------------------------------------
# Dépenses
# ---------------------------------------------------------------------------

@pytest.fixture()
def factures(app):
    """Trois factures du même fournisseur, et une enveloppe pour les porter."""
    suf = _suffixe()
    with app.app_context():
        from app.extensions import db
        from app.models import Depense, LigneBudget, Subvention

        sub = Subvention(nom=f"Ville {suf}", secteur="Adultes", annee_exercice=2026)
        db.session.add(sub)
        db.session.flush()
        ligne = LigneBudget(subvention_id=sub.id, nature="charge", compte="606",
                            libelle="Fournitures", montant_base=1000.0, montant_reel=1000.0)
        db.session.add(ligne)
        db.session.flush()

        ids = []
        for n in range(3):
            d = Depense(ligne_budget_id=ligne.id, libelle=f"Facture {suf}-{n}",
                        montant=100.0, date_paiement=date(2026, 3, 15))
            db.session.add(d)
            db.session.flush()
            ids.append(d.id)
        db.session.commit()
        return {"ligne_id": ligne.id, "sub_id": sub.id, "depenses": ids, "nom": sub.nom}


def test_imputer_un_lot_a_un_financeur(admin_client, app, factures):
    r = admin_client.post("/depenses/actions", data={
        "action": "imputer",
        "ligne_budget_id": str(factures["ligne_id"]),
        "did": [str(i) for i in factures["depenses"]],
    })
    assert r.status_code == 302

    with app.app_context():
        from app.extensions import db
        from app.models import Depense

        for did in factures["depenses"]:
            dep = db.session.get(Depense, did)
            assert len(dep.affectations) == 1
            assert dep.affectations[0].subvention_id == factures["sub_id"]
            assert dep.total_affecte == 100.0
            assert dep.statut_affectation == "ok"


def test_imputation_ne_compte_pas_deux_fois(admin_client, app, factures):
    """Re-imputer une dépense déjà financée la ferait compter deux fois dans
    le bilan du financeur."""
    donnees = {
        "action": "imputer",
        "ligne_budget_id": str(factures["ligne_id"]),
        "did": [str(i) for i in factures["depenses"]],
    }
    admin_client.post("/depenses/actions", data=donnees)
    r = admin_client.post("/depenses/actions", data=donnees, follow_redirects=True)
    assert "déjà entièrement financées" in r.get_data(as_text=True)

    with app.app_context():
        from app.extensions import db
        from app.models import Depense

        dep = db.session.get(Depense, factures["depenses"][0])
        assert dep.total_affecte == 100.0


def test_budget_insuffisant_bloque_le_lot_entier(admin_client, app, factures):
    """Imputer la moitié d'une sélection puis s'arrêter faute de crédits
    laisserait un travail à moitié fait, et invisible."""
    with app.app_context():
        from app.extensions import db
        from app.models import LigneBudget

        ligne = db.session.get(LigneBudget, factures["ligne_id"])
        ligne.montant_reel = 150.0  # de quoi porter une facture et demie
        db.session.commit()

    r = admin_client.post("/depenses/actions", data={
        "action": "imputer",
        "ligne_budget_id": str(factures["ligne_id"]),
        "did": [str(i) for i in factures["depenses"]],
    }, follow_redirects=True)
    assert "Budget insuffisant" in r.get_data(as_text=True)

    with app.app_context():
        from app.extensions import db
        from app.models import Depense

        # Rien n'a été écrit : ni la première, ni la dernière.
        for did in factures["depenses"]:
            assert len(db.session.get(Depense, did).affectations) == 0


def test_regulariser_une_ligne_pleine_reste_possible(admin_client, app, factures):
    """Piège comptable : une dépense sans aucune affectation pèse DÉJÀ 100 %
    sur sa ligne (compatibilité « legacy » de LigneBudget.engage). L'imputer
    sur cette même ligne ne consomme rien de plus. Comparer son montant au
    « reste disponible » refuserait donc une régularisation gratuite — c'est
    exactement ce qui se passe sur une enveloppe consommée au centime près.
    """
    with app.app_context():
        from app.extensions import db
        from app.models import LigneBudget

        ligne = db.session.get(LigneBudget, factures["ligne_id"])
        ligne.montant_reel = 300.0  # budget consommé au centime près
        db.session.commit()
        assert ligne.reste == 0.0

    r = admin_client.post("/depenses/actions", data={
        "action": "imputer",
        "ligne_budget_id": str(factures["ligne_id"]),
        "did": [str(i) for i in factures["depenses"]],
    }, follow_redirects=True)
    assert "Budget insuffisant" not in r.get_data(as_text=True)

    with app.app_context():
        from app.extensions import db
        from app.models import Depense, LigneBudget

        for did in factures["depenses"]:
            assert len(db.session.get(Depense, did).affectations) == 1
        # Et l'engagement de la ligne n'a pas bougé d'un centime.
        assert db.session.get(LigneBudget, factures["ligne_id"]).engage == 300.0


def test_reconduire_un_lot(admin_client, app, factures):
    admin_client.post("/depenses/actions", data={
        "action": "reconduire",
        "did": [str(i) for i in factures["depenses"]],
    })

    with app.app_context():
        from app.models import Depense

        copies = Depense.query.filter_by(ligne_budget_id=factures["ligne_id"]).all()
        assert len(copies) == 6
        nouvelles = [d for d in copies if d.id not in factures["depenses"]]
        assert all(d.date_paiement == date(2026, 4, 15) for d in nouvelles)


def test_ligne_non_choisie_refuse_l_imputation(admin_client, factures):
    r = admin_client.post("/depenses/actions", data={
        "action": "imputer",
        "did": [str(factures["depenses"][0])],
    }, follow_redirects=True)
    assert "Choisis la ligne de financement" in r.get_data(as_text=True)


def test_coches_presentes_dans_la_liste_des_depenses(admin_client, factures):
    r = admin_client.get("/depenses")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'name="did"' in html
    assert "Imputer la sélection" in html
    assert "Reconduire au mois suivant" in html
