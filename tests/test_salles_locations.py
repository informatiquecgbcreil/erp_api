"""Lot 3 : tarifs, preneurs, mises à disposition.

Le cœur testé ici est le moteur de prix. Une erreur de calcul, ça ne
plante pas — ça facture mal, et on s'en aperçoit six mois plus tard en
recoupant les chiffres. C'est donc le module où les tests valent le plus
cher : chaque règle métier y a son cas, avec des montants vérifiables à
la main.
"""
from datetime import date, timedelta
from uuid import uuid4

import pytest

from app.extensions import db
from app.models import (
    CategoriePreneur,
    Espace,
    MajorationSalle,
    Occupation,
    PrestationSalle,
    Preneur,
    Reservation,
    Site,
    TarifSalle,
)
from app.services.reservations import (
    appliquer_dates,
    bloquants,
    controles,
    dates_recurrentes,
    purger_options_expirees,
    recalculer,
    reference_unique,
)
from app.services.tarifs_salles import calculer, grille_en_vigueur, prix_occurrence, tarif_en_vigueur

# Un jeudi ordinaire, loin des fériés.
JEUDI = date(2026, 10, 15)


@pytest.fixture()
def loc(app):
    """Une salle, deux catégories, une grille : 12 €/h, 35 € la demi-journée,
    60 € la journée pour les associations ; le double pour les entreprises."""
    with app.app_context():
        suffixe = uuid4().hex[:8]
        site = Site(nom="Centre (locations)", code=f"test-loc-{suffixe}")
        db.session.add(site)
        db.session.flush()
        salle = Espace(site=site, nom="Salle polyvalente", louable=True,
                       capacite_usage=40, capacite_reglementaire=60)
        db.session.add(salle)

        asso = CategoriePreneur(code=f"asso_{suffixe}", libelle="Association", ordre=10)
        entreprise = CategoriePreneur(code=f"ent_{suffixe}", libelle="Entreprise", ordre=20)
        partenaire = CategoriePreneur(code=f"part_{suffixe}", libelle="Partenaire",
                                      gratuit_par_defaut=True, ordre=30)
        db.session.add_all([asso, entreprise, partenaire])
        db.session.flush()

        for categorie, (heure, demi, journee) in (
            (asso, (12.0, 35.0, 60.0)),
            (entreprise, (24.0, 70.0, 120.0)),
        ):
            for unite, montant in (("heure", heure), ("demi_journee", demi), ("journee", journee)):
                db.session.add(TarifSalle(
                    espace_id=salle.id, categorie_id=categorie.id, unite=unite,
                    montant=montant, date_debut=date(2020, 1, 1),
                ))

        preneur = Preneur(
            nom="Gym Volontaire", categorie_id=asso.id,
            assurance_rc_fin=date(2030, 12, 31), representant="La présidente",
        )
        db.session.add(preneur)
        db.session.commit()

        ids = {
            "site": site.id, "salle": salle.id, "asso": asso.id,
            "entreprise": entreprise.id, "partenaire": partenaire.id,
            "preneur": preneur.id,
        }
        site_id = site.id

    # Le contexte applicatif est REFERMÉ avant le yield : le garder ouvert
    # pendant le test le ferait réutiliser par les requêtes du client HTTP,
    # qui liraient alors des objets restés en cache dans la session au lieu
    # de l'état réel en base. C'est exactement ce qui se passe en production,
    # où chaque requête a son propre contexte.
    yield ids

    with app.app_context():
        site = db.session.get(Site, site_id)
        for modele in (Reservation, TarifSalle, MajorationSalle, PrestationSalle):
            for objet in modele.query.all():
                db.session.delete(objet)
        db.session.commit()
        if site is not None:
            db.session.delete(site)
        for cid in (ids["asso"], ids["entreprise"], ids["partenaire"]):
            categorie = db.session.get(CategoriePreneur, cid)
            if categorie is not None:
                db.session.delete(categorie)
        p = db.session.get(Preneur, ids["preneur"])
        if p is not None:
            db.session.delete(p)
        db.session.commit()


def _calc(ids, occurrences, categorie=None, prestations=None):
    return calculer(
        ids["salle"], categorie or ids["asso"], occurrences,
        prestations=prestations, date_reference=JEUDI,
    )


# ---------------------------------------------------------------------------
# Lecture de la grille, historisée
# ---------------------------------------------------------------------------

def test_le_tarif_en_vigueur_est_le_dernier_sans_depasser_la_date(app, loc):
    """Changer un prix en janvier ne doit pas réécrire novembre."""
    with app.app_context():
        db.session.add(TarifSalle(
            espace_id=loc["salle"], categorie_id=loc["asso"], unite="heure",
            montant=15.0, date_debut=date(2027, 1, 1),
        ))
        db.session.commit()

        avant = tarif_en_vigueur(loc["salle"], loc["asso"], "heure", date(2026, 11, 1))
        apres = tarif_en_vigueur(loc["salle"], loc["asso"], "heure", date(2027, 6, 1))
        assert avant.montant == 12.0, "le tarif futur ne doit pas s'appliquer au passé"
        assert apres.montant == 15.0


def test_grille_absente_avant_la_date_deffet(app, loc):
    with app.app_context():
        assert grille_en_vigueur(loc["salle"], loc["asso"], date(2019, 1, 1)) == {}


# ---------------------------------------------------------------------------
# Le meilleur tarif : LA règle du module
# ---------------------------------------------------------------------------

def test_quatre_heures_facturees_en_demi_journee(app, loc):
    """LE cas d'école : 4 h à 12 € font 48 €, la demi-journée en fait 35.
    Le preneur paie 35, sans avoir eu à le demander."""
    with app.app_context():
        resultat = _calc(loc, [(JEUDI, 9 * 60, 13 * 60)])
        assert resultat["total"] == 35.0
        assert resultat["economie"] == 13.0
        assert "demi-journée" in resultat["lignes"][0]["detail"]
        assert "économie 13.00 €" in resultat["lignes"][0]["detail"]


def test_deux_heures_restent_a_lheure(app, loc):
    """Quand l'heure est plus avantageuse, c'est elle qui gagne."""
    with app.app_context():
        resultat = _calc(loc, [(JEUDI, 9 * 60, 11 * 60)])
        assert resultat["total"] == 24.0
        assert resultat["economie"] == 0.0


def test_heure_commencee_est_due(app, loc):
    """2 h 30 se facturent 3 h : c'est l'usage, et c'est le seul arrondi
    qu'on sait expliquer sans schéma."""
    with app.app_context():
        resultat = _calc(loc, [(JEUDI, 9 * 60, 11 * 60 + 30)])
        assert resultat["total"] == 35.0  # 3 h = 36 € > demi-journée à 35 €
        detail = _calc(loc, [(JEUDI, 9 * 60, 9 * 60 + 90)])
        assert detail["total"] == 24.0   # 1 h 30 -> 2 h = 24 €


def test_journee_entiere_prend_le_forfait_journee(app, loc):
    with app.app_context():
        resultat = _calc(loc, [(JEUDI, 8 * 60, 18 * 60)])  # 10 h
        assert resultat["total"] == 60.0


def test_la_categorie_change_le_prix(app, loc):
    """Une entreprise et une association ne paient pas pareil, sans que
    personne ait à s'en souvenir au comptoir."""
    with app.app_context():
        asso = _calc(loc, [(JEUDI, 9 * 60, 13 * 60)])
        entreprise = _calc(loc, [(JEUDI, 9 * 60, 13 * 60)], categorie=loc["entreprise"])
        assert (asso["total"], entreprise["total"]) == (35.0, 70.0)


def test_sans_categorie_pas_de_tarif(app, loc):
    with app.app_context():
        resultat = calculer(loc["salle"], None, [(JEUDI, 540, 720)])
        assert resultat["total"] == 0.0
        assert resultat["avertissements"]


def test_grille_vide_avertit_au_lieu_de_facturer_zero_en_silence(app, loc):
    """Un trou dans la grille n'est PAS une gratuité : il faut le voir."""
    with app.app_context():
        resultat = _calc(loc, [(JEUDI, 540, 720)], categorie=loc["partenaire"])
        assert resultat["total"] == 0.0
        assert any("Aucun tarif" in a for a in resultat["avertissements"])


def test_forfait_periode_remplace_le_detail(app, loc):
    """Une semaine de location n'est pas sept journées."""
    with app.app_context():
        db.session.add(TarifSalle(
            espace_id=loc["salle"], categorie_id=loc["asso"], unite="semaine",
            montant=250.0, date_debut=date(2020, 1, 1),
        ))
        db.session.commit()
        occurrences = [(JEUDI + timedelta(days=n), 8 * 60, 18 * 60) for n in range(5)]

        resultat = _calc(loc, occurrences)
        assert resultat["total"] == 250.0, "5 journées à 60 € = 300 €, le forfait semaine à 250 € gagne"
        assert resultat["lignes"][0]["type"] == "forfait"
        assert "économie 50.00 €" in resultat["lignes"][0]["detail"]


def test_forfait_periode_ignore_si_plus_cher(app, loc):
    with app.app_context():
        db.session.add(TarifSalle(
            espace_id=loc["salle"], categorie_id=loc["asso"], unite="semaine",
            montant=400.0, date_debut=date(2020, 1, 1),
        ))
        db.session.commit()
        occurrences = [(JEUDI + timedelta(days=n), 8 * 60, 18 * 60) for n in range(3)]
        assert _calc(loc, occurrences)["total"] == 180.0


# ---------------------------------------------------------------------------
# Majorations
# ---------------------------------------------------------------------------

def test_majoration_soiree_en_pourcentage(app, loc):
    with app.app_context():
        db.session.add(MajorationSalle(
            libelle="Ouverture en soirée", condition="soiree",
            seuil_minute=20 * 60, pourcentage=25.0,
        ))
        db.session.commit()

        jour = _calc(loc, [(JEUDI, 9 * 60, 11 * 60)])
        soir = _calc(loc, [(JEUDI, 19 * 60, 21 * 60)])
        assert jour["total"] == 24.0
        assert soir["total"] == 30.0, "24 € + 25 % = 30 €"


def test_majoration_soiree_des_que_le_creneau_mord_sur_la_soiree(app, loc):
    """Une salle rendue à 23 h mobilise quelqu'un pour fermer, même si elle
    a été prise à 18 h."""
    with app.app_context():
        db.session.add(MajorationSalle(
            libelle="Soirée", condition="soiree", seuil_minute=20 * 60, montant_fixe=40.0,
        ))
        db.session.commit()
        resultat = _calc(loc, [(JEUDI, 18 * 60, 23 * 60)])
        assert any(l["type"] == "majoration" for l in resultat["lignes"])


def test_majoration_week_end(app, loc):
    with app.app_context():
        db.session.add(MajorationSalle(libelle="Samedi", condition="samedi", montant_fixe=50.0))
        db.session.commit()
        samedi = JEUDI + timedelta(days=2)
        assert samedi.weekday() == 5
        assert _calc(loc, [(samedi, 9 * 60, 11 * 60)])["total"] == 74.0
        assert _calc(loc, [(JEUDI, 9 * 60, 11 * 60)])["total"] == 24.0


def test_majoration_ferie(app, loc):
    with app.app_context():
        db.session.add(MajorationSalle(libelle="Jour férié", condition="ferie", pourcentage=100.0))
        db.session.commit()
        assert _calc(loc, [(date(2026, 5, 1), 9 * 60, 11 * 60)])["total"] == 48.0


def test_majoration_desactivee_ne_sapplique_pas(app, loc):
    with app.app_context():
        db.session.add(MajorationSalle(
            libelle="Inactive", condition="samedi", montant_fixe=99.0, actif=False,
        ))
        db.session.commit()
        samedi = JEUDI + timedelta(days=2)
        assert _calc(loc, [(samedi, 9 * 60, 11 * 60)])["total"] == 24.0


# ---------------------------------------------------------------------------
# Prestations
# ---------------------------------------------------------------------------

def test_prestations_sajoutent_au_total(app, loc):
    with app.app_context():
        resultat = _calc(
            loc, [(JEUDI, 9 * 60, 11 * 60)],
            prestations=[
                {"libelle": "Vidéoprojecteur", "montant_unitaire": 15.0, "unite": "forfait", "quantite": 1},
                {"libelle": "Ménage", "montant_unitaire": 30.0, "unite": "forfait", "quantite": 2},
            ],
        )
        assert resultat["total"] == 24.0 + 15.0 + 60.0
        assert sum(1 for l in resultat["lignes"] if l["type"] == "prestation") == 2


def test_prestation_a_zero_ignoree(app, loc):
    with app.app_context():
        resultat = _calc(
            loc, [(JEUDI, 9 * 60, 11 * 60)],
            prestations=[{"libelle": "Sono", "montant_unitaire": 20.0, "unite": "forfait", "quantite": 0}],
        )
        assert resultat["total"] == 24.0


# ---------------------------------------------------------------------------
# Récurrence
# ---------------------------------------------------------------------------

def test_tous_les_mardis_dune_periode():
    dates = dates_recurrentes(date(2026, 9, 1), date(2026, 9, 30), {1})
    assert all(d.weekday() == 1 for d in dates)
    assert len(dates) == 5


def test_la_recurrence_saute_les_feries():
    avec = dates_recurrentes(date(2026, 4, 27), date(2026, 5, 8), {4}, sauter_feries=False)
    sans = dates_recurrentes(date(2026, 4, 27), date(2026, 5, 8), {4}, sauter_feries=True)
    assert date(2026, 5, 1) in avec  # le 1er mai 2026 est un vendredi
    assert date(2026, 5, 1) not in sans


def test_la_recurrence_accepte_des_exclusions():
    dates = dates_recurrentes(
        date(2026, 9, 1), date(2026, 9, 30), {1}, exclusions={date(2026, 9, 15)},
    )
    assert date(2026, 9, 15) not in dates


def test_la_recurrence_est_bornee():
    dates = dates_recurrentes(date(2026, 1, 1), date(2030, 1, 1))
    assert len(dates) <= 400, "garde-fou contre une erreur de date"


# ---------------------------------------------------------------------------
# Réservations
# ---------------------------------------------------------------------------

def _reservation(loc, statut="option", **kwargs):
    r = Reservation(
        reference=reference_unique(JEUDI),
        preneur_id=loc["preneur"], espace_id=loc["salle"], categorie_id=loc["asso"],
        titre="Cours de gym", statut=statut, **kwargs,
    )
    db.session.add(r)
    db.session.flush()
    return r


def test_les_dates_deviennent_des_occupations(app, loc):
    """C'est ce qui fait qu'une location profite du moteur de conflits du
    lot 1 sans une ligne de code de plus."""
    with app.app_context():
        r = _reservation(loc)
        appliquer_dates(r, [JEUDI, JEUDI + timedelta(days=7)], "18:00", "20:00")
        db.session.commit()

        assert len(r.occupations) == 2
        assert all(o.origine == "location" for o in r.occupations)

        from app.services.salles import est_disponible
        assert not est_disponible(
            db.session.get(Espace, loc["salle"]), JEUDI, "19:00", "21:00"
        )


def test_annuler_une_reservation_libere_les_salles(app, loc):
    from app.services.reservations import synchroniser_statut_occupations
    from app.services.salles import est_disponible

    with app.app_context():
        r = _reservation(loc, statut="confirmee")
        appliquer_dates(r, [JEUDI], "18:00", "20:00")
        db.session.commit()

        r.statut = "annulee"
        synchroniser_statut_occupations(r)
        db.session.commit()

        assert est_disponible(db.session.get(Espace, loc["salle"]), JEUDI, "18:00", "20:00")


def test_le_prix_dune_reservation_se_calcule_seul(app, loc):
    with app.app_context():
        r = _reservation(loc)
        appliquer_dates(r, [JEUDI, JEUDI + timedelta(days=7)], "09:00", "13:00")
        db.session.flush()
        recalculer(r)
        db.session.commit()

        assert r.montant_calcule == 70.0, "deux demi-journées à 35 €"
        assert len(r.detail) == 2
        assert r.montant_du == 70.0


def test_la_gratuite_annule_le_du_mais_garde_la_valeur(app, loc):
    """C'est cette valeur qui se valorise au compte de résultat : ce que la
    structure apporte au tissu associatif."""
    with app.app_context():
        r = _reservation(loc, gratuite=True, motif_gratuite="Convention de partenariat")
        appliquer_dates(r, [JEUDI], "09:00", "13:00")
        db.session.flush()
        recalculer(r)
        db.session.commit()

        assert r.montant_calcule == 35.0
        assert r.montant_du == 0.0
        assert r.ecart_au_bareme == -35.0


def test_le_prix_impose_prend_le_pas_et_trace_lecart(app, loc):
    with app.app_context():
        r = _reservation(loc, montant_manuel=20.0, motif_montant_manuel="Tarif négocié")
        appliquer_dates(r, [JEUDI], "09:00", "13:00")
        db.session.flush()
        recalculer(r)
        db.session.commit()

        assert r.montant_du == 20.0
        assert r.ecart_au_bareme == -15.0


def test_reference_lisible_et_sequentielle(app, loc):
    with app.app_context():
        premiere = reference_unique(JEUDI)
        assert premiere.startswith("LOC-2026-")
        r = _reservation(loc)
        db.session.commit()
        assert reference_unique(JEUDI) != r.reference


# ---------------------------------------------------------------------------
# Options qui expirent
# ---------------------------------------------------------------------------

def test_une_option_perimee_se_libere_toute_seule(app, loc):
    """Sans ça, le planning se remplit de fantômes : des salles qui semblent
    prises pour des gens qui n'ont jamais rappelé."""
    from app.services.salles import est_disponible

    with app.app_context():
        r = _reservation(loc, option_expire_le=date.today() - timedelta(days=1))
        appliquer_dates(r, [JEUDI], "18:00", "20:00")
        db.session.commit()

        assert purger_options_expirees() == 1
        db.session.refresh(r)
        assert r.statut == "annulee"
        assert "expirée" in (r.notes or "")
        assert est_disponible(db.session.get(Espace, loc["salle"]), JEUDI, "18:00", "20:00")


def test_une_option_encore_valable_nest_pas_touchee(app, loc):
    with app.app_context():
        r = _reservation(loc, option_expire_le=date.today() + timedelta(days=10))
        appliquer_dates(r, [JEUDI], "18:00", "20:00")
        db.session.commit()
        assert purger_options_expirees() == 0
        assert r.statut == "option"


def test_une_reservation_confirmee_nexpire_jamais(app, loc):
    with app.app_context():
        r = _reservation(loc, statut="confirmee", option_expire_le=date.today() - timedelta(days=30))
        appliquer_dates(r, [JEUDI], "18:00", "20:00")
        db.session.commit()
        assert purger_options_expirees() == 0
        assert r.statut == "confirmee"


# ---------------------------------------------------------------------------
# Contrôles avant de s'engager
# ---------------------------------------------------------------------------

def test_assurance_absente_bloque_la_confirmation(app, loc):
    with app.app_context():
        preneur = db.session.get(Preneur, loc["preneur"])
        preneur.assurance_rc_fin = None
        r = _reservation(loc)
        appliquer_dates(r, [JEUDI], "09:00", "13:00")
        db.session.commit()

        messages = [c["message"] for c in bloquants(r)]
        assert any("responsabilité civile" in m for m in messages)

        preneur.assurance_rc_fin = date(2030, 12, 31)
        db.session.commit()


def test_assurance_expirant_avant_la_fin_bloque(app, loc):
    with app.app_context():
        preneur = db.session.get(Preneur, loc["preneur"])
        preneur.assurance_rc_fin = JEUDI - timedelta(days=1)
        r = _reservation(loc)
        appliquer_dates(r, [JEUDI], "09:00", "13:00")
        db.session.commit()
        assert any("assurance" in c["message"].lower() for c in bloquants(r))
        preneur.assurance_rc_fin = date(2030, 12, 31)
        db.session.commit()


def test_effectif_au_dessus_de_la_capacite_reglementaire_bloque(app, loc):
    with app.app_context():
        r = _reservation(loc, effectif=80)
        appliquer_dates(r, [JEUDI], "09:00", "13:00")
        db.session.commit()
        assert any("réglementaire" in c["message"] for c in bloquants(r))


def test_gratuite_sans_motif_bloque(app, loc):
    with app.app_context():
        r = _reservation(loc, gratuite=True)
        appliquer_dates(r, [JEUDI], "09:00", "13:00")
        db.session.commit()
        assert any("motif" in c["message"].lower() for c in bloquants(r))


def test_prix_impose_sans_motif_bloque(app, loc):
    with app.app_context():
        r = _reservation(loc, montant_manuel=5.0)
        appliquer_dates(r, [JEUDI], "09:00", "13:00")
        db.session.commit()
        assert any("motif" in c["message"].lower() for c in bloquants(r))


def test_conflit_de_dates_bloque(app, loc):
    with app.app_context():
        db.session.add(Occupation(
            espace_id=loc["salle"], date_jour=JEUDI, minute_debut=600, minute_fin=780,
            origine="interne", statut="confirme", titre="Atelier déjà là",
        ))
        r = _reservation(loc)
        appliquer_dates(r, [JEUDI], "09:00", "13:00")
        db.session.commit()
        assert any("conflit" in c["message"].lower() for c in bloquants(r))


def test_convention_interdisant_la_sous_location_bloque(app, loc):
    with app.app_context():
        site = db.session.get(Site, loc["site"])
        site.regime_sous_location = "interdite"
        r = _reservation(loc)
        appliquer_dates(r, [JEUDI], "09:00", "13:00")
        db.session.commit()
        assert any("interdit" in c["message"].lower() for c in bloquants(r))
        site.regime_sous_location = "autorisee"
        db.session.commit()


def test_une_reservation_saine_na_aucun_bloquant(app, loc):
    with app.app_context():
        site = db.session.get(Site, loc["site"])
        site.regime_sous_location = "autorisee"
        r = _reservation(loc, effectif=20)
        appliquer_dates(r, [JEUDI], "09:00", "13:00")
        db.session.commit()
        assert bloquants(r) == []


# ---------------------------------------------------------------------------
# TVA
# ---------------------------------------------------------------------------

def test_mention_de_tva_par_defaut(app, loc):
    with app.app_context():
        site = db.session.get(Site, loc["site"])
        assert "293 B" in site.mention_tva_affichee
        site.tva_applicable = True
        assert "293 B" not in site.mention_tva_affichee


# ---------------------------------------------------------------------------
# Écrans
# ---------------------------------------------------------------------------

def test_les_ecrans_du_lot3_repondent(admin_client, loc):
    for url in (
        "/salles/reservations", "/salles/reservation/nouvelle",
        "/salles/preneurs", "/salles/preneur/nouveau",
        f"/salles/preneur/{loc['preneur']}/modifier",
        "/salles/tarifs", f"/salles/tarifs?espace_id={loc['salle']}",
        "/salles/categories", "/salles/prestations",
    ):
        assert admin_client.get(url).status_code == 200, url


def test_parcours_complet_de_reservation(admin_client, app, loc):
    """Le parcours réel de l'accueil, du formulaire au prix affiché."""
    reponse = admin_client.post(
        "/salles/reservation/nouvelle",
        data={
            "preneur_id": str(loc["preneur"]), "espace_id": str(loc["salle"]),
            "titre": "Cours de gym", "effectif": "25",
            "date_debut": JEUDI.isoformat(),
            "date_fin": (JEUDI + timedelta(days=21)).isoformat(),
            "jour_semaine": "3", "sauter_feries": "1",
            "heure_debut": "18:00", "heure_fin": "20:00",
            "poser_option": "1",
            "option_expire_le": (date.today() + timedelta(days=15)).isoformat(),
        },
        follow_redirects=True,
    )
    assert reponse.status_code == 200

    with app.app_context():
        r = Reservation.query.filter_by(titre="Cours de gym").one()
        assert len(r.occupations) == 4, "quatre jeudis"
        assert r.montant_calcule == 4 * 24.0, "2 h à 12 € par séance"
        assert r.statut == "option"

    page = admin_client.get(f"/salles/reservation/{r.id}").get_data(as_text=True)
    assert "96.00" in page
    assert "293 B" in page


def test_confirmer_refuse_si_un_controle_bloque(admin_client, app, loc):
    with app.app_context():
        preneur = db.session.get(Preneur, loc["preneur"])
        preneur.assurance_rc_fin = None
        r = _reservation(loc)
        appliquer_dates(r, [JEUDI], "09:00", "13:00")
        db.session.commit()
        rid = r.id

    reponse = admin_client.post(
        f"/salles/reservation/{rid}/statut",
        data={"statut": "confirmee"}, follow_redirects=True,
    )
    assert "Impossible de confirmer" in reponse.get_data(as_text=True)

    with app.app_context():
        assert db.session.get(Reservation, rid).statut == "option"
        db.session.get(Preneur, loc["preneur"]).assurance_rc_fin = date(2030, 12, 31)
        db.session.commit()


def test_prix_impose_sans_motif_refuse_a_lenregistrement(admin_client, app, loc):
    with app.app_context():
        r = _reservation(loc)
        appliquer_dates(r, [JEUDI], "09:00", "13:00")
        db.session.commit()
        rid = r.id

    reponse = admin_client.post(
        f"/salles/reservation/{rid}/reglements",
        data={"montant_manuel": "10"}, follow_redirects=True,
    )
    assert "motivé" in reponse.get_data(as_text=True)
    with app.app_context():
        assert db.session.get(Reservation, rid).montant_manuel is None
