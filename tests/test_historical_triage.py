"""Triage assisté : regroupement, propositions et reprise des arbitrages."""
import csv
import io
import json
import os

import pytest

from app.ateliers.historical_matching import resolve_people
from app.ateliers.historical_triage import (
    COLONNES_CSV, exporter_arbitrages, fusionner_arbitrages, rapport_markdown, trier,
)
from tools.historical_triage import main


def _personne(feuille, ligne, nom, prenom, annee=None, sexe=None, quartier=None):
    return {"key": f"{feuille}!{ligne}", "source_sheet": feuille, "source_row": ligne,
            "activity_key": feuille, "field_cells": {},
            "raw": {"nom": nom, "prenom": prenom, "annee_naissance": annee,
                    "sexe": sexe, "quartier": quartier, "activite": feuille}}


def _seance(feuille, colonne, index, jour=None, semaine=None, mois=None,
            date_session=None, candidates=(), statut="READY", creneau="M"):
    entetes = {f"{colonne}2": mois, f"{colonne}4": semaine, f"{colonne}6": jour}
    return {"key": f"{feuille}!{colonne}", "activity_key": feuille, "source_sheet": feuille,
            "source_cell": f"{colonne}6", "source_column": index, "source_slot": creneau,
            "date_session": date_session, "candidate_date": date_session,
            "candidate_dates": list(candidates), "raw_headers": entetes,
            "anomalies": [], "status": statut, "secteur": None, "session_id": None}


def _activite(nom, statut="NEW", candidats=()):
    return {"key": nom, "name": nom, "source_sheet": nom, "source_names": [nom],
            "name_source": "ACTIVITE", "status": "REVIEW", "match_status": statut,
            "secteur": None, "atelier_id": None, "candidates": list(candidats)}


def _anomalie(identifiant, code, feuille, cellule, message, valeur=None,
              bloquante=True, acquittee=False):
    return {"id": identifiant, "code": code, "source_sheet": feuille, "source_cell": cellule,
            "message": message, "raw_value": valeur, "blocking": bloquante,
            "acknowledged": acquittee}


def _rapport(personnes=(), fiches=(), seances=(), activites=(), presences=(), anomalies=()):
    matching = resolve_people(list(personnes), list(fiches))
    return {"version": "historical-import-1", "source": {"filename": "test.xlsx", "sha256": "a" * 64, "year": 2026},
            "digest": "b" * 64, "database_digest": "c" * 64, "secteurs": [],
            "matching": matching, "sessions": list(seances), "activities": list(activites),
            "anomalies": list(anomalies),
            "parser": {"people": list(personnes), "attendance": [{"person_key": k} for k in presences],
                       "sessions": list(seances), "activities": list(activites),
                       "anomalies": list(anomalies)}}


SECTEURS = ["Numérique", "Familles", "EPE", "Santé Transition",
            "Insertion Sociale et Professionnelle", "Animation Globale"]


def _trier(rapport, **kwargs):
    kwargs.setdefault("secteurs_connus", SECTEURS)
    return trier(rapport, **kwargs)


def _par_cle(dossiers):
    return {d["cle"]: d for d in dossiers}


# --- Personnes ------------------------------------------------------------

def test_lignes_repetees_forment_un_seul_dossier_et_un_groupe_explicite():
    personnes = [_personne("FLE", 7, "MARTIN", "Sophie", 1980, "F"),
                 _personne("ZUMBA", 9, "Martin", "sophie", 1980, "F"),
                 _personne("VACANCES", 4, "MARTIN", "SOPHIE", 1980, "F")]
    triage = _trier(_rapport(personnes, presences=["FLE!7", "FLE!7", "ZUMBA!9"]))
    dossiers = triage["personnes"]
    assert len(dossiers) == 1
    dossier = dossiers[0]
    assert dossier["statut"] == "regrouper"
    assert dossier["lignes"] == ["FLE!7", "VACANCES!4", "ZUMBA!9"]
    assert dossier["presences"] == 3
    assert sorted(dossier["feuilles"]) == ["FLE", "VACANCES", "ZUMBA"]
    groupes = {d["group"] for d in triage["decisions"]["participants"].values()}
    assert len(groupes) == 1
    assert set(triage["decisions"]["participants"]) == set(dossier["lignes"])


def test_espaces_du_nom_ne_separent_pas_la_personne():
    personnes = [_personne("FLE", 7, "EL BAYAD", "Kamelia", 1999, "F"),
                 _personne("ZUMBA", 8, "ELBAYAD", "Kamelia", 1999, "F")]
    triage = _trier(_rapport(personnes))
    assert len(triage["personnes"]) == 1
    dossier = triage["personnes"][0]
    assert dossier["statut"] == "regrouper"
    # L'orthographe retenue est fixée explicitement, pas laissée au tri interne.
    assert triage["decisions"]["participants"]["FLE!7"]["values"]["nom"] in {"EL BAYAD", "ELBAYAD"}
    assert len(dossier["orthographes"]) == 2


def test_fiche_erp_concordante_est_proposee_au_rattachement():
    personnes = [_personne("FLE", 7, "DURAND", "Alice", 1975, "F"),
                 _personne("ZUMBA", 8, "DURAND", "Alice", 1975, "F")]
    fiches = [{"id": 42, "raw": {"nom": "Durand", "prenom": "Alice", "date_naissance": "1975-04-02",
                                 "genre": "Femme", "telephone": None, "email": None,
                                 "ville": None, "quartier": None, "adresse": None}}]
    triage = _trier(_rapport(personnes, fiches))
    dossier = triage["personnes"][0]
    assert dossier["statut"] == "rattacher"
    assert dossier["fiches_erp"][0]["id"] == 42
    assert triage["decisions"]["participants"] == {
        "FLE!7": {"action": "participant", "participant_id": 42},
        "ZUMBA!8": {"action": "participant", "participant_id": 42}}


def test_identite_unique_sans_candidat_ne_produit_aucune_decision():
    triage = _trier(_rapport([_personne("FLE", 7, "SOLO", "Unique", 1990, "F")]))
    assert triage["personnes"][0]["statut"] == "creer"
    assert triage["decisions"]["participants"] == {}


@pytest.mark.parametrize("personnes, categorie", [
    ([_personne("FLE", 7, "BIATRANE", "Naima", 1983, "F"),
      _personne("ZUMBA", 8, "BIATRANE", "Naima", 1993, "F")], "homonymes dans le classeur"),
    ([_personne("FLE", 7, "ALIMY", "Zarlasht", 2003, "F"),
      _personne("ZUMBA", 8, "ALIMY", "Zarlasht", 2003, "M")], "genre contradictoire entre les feuilles"),
    ([_personne("FLE", 7, "SGIR", "Fatima", 1985, "F"),
      _personne("ZUMBA", 8, "SGHIR", "Fatima", 1985, "F")], "orthographe proche"),
    ([_personne("FLE", 7, None, "Fadma", None, None)], "identité incomplète ou année illisible"),
    ([_personne("FLE", 7, "NAILI", "Ilyad", "ADOS", "M")], "identité incomplète ou année illisible"),
])
def test_les_cas_douteux_restent_a_trancher_sans_decision(personnes, categorie):
    triage = _trier(_rapport(personnes))
    assert {d["statut"] for d in triage["personnes"]} == {"arbitrer"}
    assert categorie in {d["categorie"] for d in triage["personnes"]}
    assert triage["decisions"]["participants"] == {}


def test_annee_ou_genre_contredisant_la_fiche_erp_reste_a_trancher():
    fiches = [{"id": 7, "raw": {"nom": "Bah", "prenom": "Hawa", "date_naissance": "1983-05-05",
                                "genre": "Femme", "telephone": None, "email": None,
                                "ville": None, "quartier": None, "adresse": None}}]
    triage = _trier(_rapport([_personne("FLE", 7, "BAH", "Hawa", 1982, "F")], fiches))
    dossier = triage["personnes"][0]
    assert dossier["statut"] == "arbitrer"
    assert dossier["categorie"] == "année du classeur ≠ fiche ERP"
    assert triage["decisions"]["participants"] == {}


@pytest.mark.parametrize("personnes, fiches", [
    # Année absente du classeur : il ne reste que le nom face à la fiche.
    ([_personne("FLE", 7, "DURAND", "Alice", None, "F")],
     [{"id": 42, "raw": {"nom": "Durand", "prenom": "Alice", "date_naissance": "1975-04-02",
                         "genre": "Femme", "telephone": None, "email": None,
                         "ville": None, "quartier": None, "adresse": None}}]),
    # Fiche sans aucune naissance connue : même problème dans l'autre sens.
    ([_personne("FLE", 7, "DURAND", "Alice", 1975, "F")],
     [{"id": 42, "raw": {"nom": "Durand", "prenom": "Alice", "date_naissance": None,
                         "annee_naissance": None, "genre": "Femme", "telephone": None,
                         "email": None, "ville": None, "quartier": None, "adresse": None}}]),
    # Deux lignes homonymes du classeur, aucun millésime nulle part.
    ([_personne("FLE", 7, "DURAND", "Alice", None, "F"),
      _personne("ZUMBA", 8, "DURAND", "Alice", None, "F")], []),
])
def test_aucun_rapprochement_n_est_propose_sur_le_seul_nom(personnes, fiches):
    triage = _trier(_rapport(personnes, fiches))
    assert {d["categorie"] for d in triage["personnes"]} == {"aucune année pour étayer le rapprochement"}
    assert triage["decisions"]["participants"] == {}


def test_une_annee_sur_une_seule_ligne_du_dossier_suffit_a_regrouper():
    personnes = [_personne("FLE", 7, "DURAND", "Alice", 1975, "F"),
                 _personne("ZUMBA", 8, "DURAND", "Alice", None, "F")]
    triage = _trier(_rapport(personnes))
    dossier = triage["personnes"][0]
    assert dossier["statut"] == "regrouper" and dossier["annee"] == 1975
    assert len(triage["decisions"]["participants"]) == 2


def test_deux_fiches_erp_homonymes_ne_sont_jamais_departagees():
    fiches = [{"id": 1, "raw": {"nom": "Sacko", "prenom": "Madi", "annee_naissance": 1990, "genre": "Homme",
                                "date_naissance": None, "telephone": None, "email": None,
                                "ville": None, "quartier": None, "adresse": None}},
              {"id": 2, "raw": {"nom": "SACKO", "prenom": "MADI", "annee_naissance": 1990, "genre": "Homme",
                                "date_naissance": None, "telephone": None, "email": None,
                                "ville": None, "quartier": None, "adresse": None}}]
    triage = _trier(_rapport([_personne("FLE", 7, "SACKO", "Madi", 1990, "M")], fiches))
    assert triage["personnes"][0]["categorie"] == "plusieurs fiches ERP homonymes"
    assert triage["decisions"]["participants"] == {}


# --- Séances --------------------------------------------------------------

def test_date_candidate_unique_confirmee_par_le_jour_de_semaine():
    seance = _seance("FLE", "Z", 26, jour=31, semaine="M", mois="2026-04-01T00:00:00",
                     date_session=None, candidates=["2026-03-31"], statut="REVIEW")
    triage = _trier(_rapport(seances=[seance]))
    dossier = triage["seances"][0]
    assert dossier["statut"] == "dater"
    assert triage["decisions"]["sessions"]["FLE!Z"] == {"action": "date", "date_session": "2026-03-31"}


def test_quantieme_double_est_encadre_par_les_colonnes_voisines():
    seances = [_seance("MARCHE", "R", 18, jour=23, semaine="L", date_session="2026-03-23"),
               _seance("MARCHE", "S", 19, jour="30/30", semaine="L", statut="REVIEW"),
               _seance("MARCHE", "T", 20, jour=27, semaine="L", date_session="2026-04-27")]
    triage = _trier(_rapport(seances=seances))
    assert triage["decisions"]["sessions"] == {
        "MARCHE!S": {"action": "date", "date_session": "2026-03-30"}}


def test_chiffre_doubl_hors_quantieme_est_deduit_du_jour_de_semaine():
    seances = [_seance("ALPHA", "AA", 27, jour=4, semaine="L", date_session="2026-05-04"),
               _seance("ALPHA", "AB", 28, jour=66, semaine="ME", statut="REVIEW"),
               _seance("ALPHA", "AC", 29, jour=11, semaine="L", date_session="2026-05-11")]
    triage = _trier(_rapport(seances=seances))
    assert triage["decisions"]["sessions"]["ALPHA!AB"]["date_session"] == "2026-05-06"


def test_quantieme_et_jour_de_semaine_contradictoires_proposent_les_deux_lectures():
    seances = [_seance("LAB", "V", 22, jour=16, semaine="L", date_session="2026-03-16"),
               _seance("LAB", "W", 23, jour=19, semaine="ME", statut="REVIEW"),
               _seance("LAB", "X", 24, jour=23, semaine="L", date_session="2026-03-23")]
    triage = _trier(_rapport(seances=seances))
    dossier = triage["seances"][0]
    assert dossier["statut"] == "arbitrer"
    assert dossier["options"] == ["2026-03-18", "2026-03-19"]
    assert triage["decisions"]["sessions"] == {}


def test_colonne_isolee_reste_bornee_par_le_mois_inscrit():
    seance = _seance("CONCERT", "I", 9, jour=None, semaine="S", mois="2026-01-01T00:00:00",
                     statut="REVIEW", creneau="AM")
    triage = _trier(_rapport(seances=[seance]))
    dossier = triage["seances"][0]
    assert dossier["statut"] == "arbitrer"
    assert dossier["options"] == ["2026-01-03", "2026-01-10", "2026-01-17", "2026-01-24", "2026-01-31"]


def test_seance_deja_datee_n_est_pas_reproposee():
    triage = _trier(_rapport(seances=[_seance("FLE", "I", 9, jour=5, semaine="L", date_session="2026-01-05")]))
    assert triage["seances"] == []
    assert triage["decisions"]["sessions"] == {}


# --- Anomalies bloquantes -------------------------------------------------

def test_seule_une_anomalie_bloquante_et_non_acquittee_reste_a_traiter():
    anomalies = [_anomalie("a1", "invalid_day", "FLE", "Z6", "Jour illisible.", 66),
                 _anomalie("a2", "implicit_month_rollover", "FLE", "Y6", "Mois déduit.", bloquante=False),
                 _anomalie("a3", "missing_identity", "FLE", "A9", "Nom absent.", acquittee=True)]
    triage = _trier(_rapport(anomalies=anomalies))
    assert [d["cle"] for d in triage["anomalies"]] == ["a1"]
    assert triage["resume"]["anomalies_a_acquitter"] == 1
    # Une anomalie n'est jamais acquittée d'office, même quand la date est proposée.
    assert triage["decisions"]["acknowledged_anomalies"] == []


def test_l_anomalie_rappelle_la_date_que_le_triage_propose_pour_sa_colonne():
    seances = [_seance("ALPHA", "AA", 27, jour=4, semaine="L", date_session="2026-05-04"),
               _seance("ALPHA", "AB", 28, jour=66, semaine="ME", statut="REVIEW"),
               _seance("ALPHA", "AC", 29, jour=11, semaine="L", date_session="2026-05-11")]
    anomalies = [_anomalie("a1", "invalid_day", "ALPHA", "AB6", "Jour illisible.", 66)]
    triage = _trier(_rapport(seances=seances, anomalies=anomalies))
    dossier = triage["anomalies"][0]
    assert dossier["cible"] == "ALPHA!AB"
    assert dossier["lecture_proposee"] == "2026-05-06"


def test_une_anomalie_sans_colonne_de_seance_reste_sans_lecture_proposee():
    anomalies = [_anomalie("a1", "missing_identity", "FLE", "A20", "Nom absent.")]
    triage = _trier(_rapport(anomalies=anomalies))
    assert triage["anomalies"][0]["lecture_proposee"] is None


def test_acquitter_une_anomalie_l_ajoute_a_la_liste_et_rien_d_autre_n_est_accepte():
    anomalies = [_anomalie("a1", "invalid_day", "FLE", "Z6", "Jour illisible.", 66)]
    triage = _trier(_rapport(anomalies=anomalies))
    lignes = exporter_arbitrages(triage)
    assert [l["type"] for l in lignes] == ["anomalie"]
    lignes[0]["decision"] = "acquitter"
    decisions, refusees = fusionner_arbitrages(triage, lignes)
    assert refusees == [] and decisions["acknowledged_anomalies"] == ["a1"]
    lignes[0]["decision"] = "corrigee"
    decisions, refusees = fusionner_arbitrages(triage, lignes)
    assert decisions["acknowledged_anomalies"] == []
    assert "décision anomalie non reconnue" in refusees[0]["erreur"]


def test_les_anomalies_comptent_dans_les_blocages_restants():
    anomalies = [_anomalie(f"a{i}", "invalid_day", "FLE", f"Z{i}", "Jour illisible.", 66)
                 for i in range(3)]
    triage = _trier(_rapport(activites=[_activite("REUNION DU MARDI")], anomalies=anomalies))
    assert triage["resume"]["blocages_restants"] == 4


# --- Activités ------------------------------------------------------------

def test_secteur_deduit_du_nom_metier_et_confiance_affichee():
    activites = [_activite("FLE ESTHER"), _activite("ANNIVERSAIRE EPE"),
                 _activite("CGB GAMING"), _activite("CTAI LUCIA"), _activite("CONCERT DU NOUVEL AN")]
    triage = _trier(_rapport(activites=activites))
    propose = {d["nom"]: (d["secteur_propose"], d["confiance"]) for d in triage["activites"]}
    assert propose["FLE ESTHER"] == ("Insertion Sociale et Professionnelle", "haute")
    assert propose["ANNIVERSAIRE EPE"] == ("EPE", "haute")
    assert propose["CGB GAMING"] == ("Numérique", "haute")
    assert propose["CTAI LUCIA"] == ("Numérique", "moyenne")
    assert triage["decisions"]["activities"]["FLE ESTHER"] == {"secteur": "Insertion Sociale et Professionnelle"}


def test_activite_sans_mot_cle_reste_a_choisir():
    triage = _trier(_rapport(activites=[_activite("REUNION DU MARDI")]))
    dossier = triage["activites"][0]
    assert dossier["statut"] == "arbitrer" and dossier["secteur_propose"] is None
    assert triage["decisions"]["activities"] == {}


def test_activite_ressemblant_a_une_existante_n_est_jamais_reprise_seule():
    activite = _activite("NUMERIQUE PAR TOUS", statut="REVIEW",
                         candidats=[{"id": 6, "name": "Numérique Par Tous", "secteur": "Numérique"},
                                    {"id": 29, "name": "Numérique Par Tous pro", "secteur": "Numérique"}])
    triage = _trier(_rapport(activites=[activite]))
    assert triage["activites"][0]["statut"] == "arbitrer"
    assert triage["decisions"]["activities"] == {}


def test_deux_feuilles_au_nom_tres_proche_dans_le_meme_secteur_sont_signalees():
    activites = [_activite("ANNIVERSAIRE EPE"), _activite("ANNIVERSAIRES EPE")]
    triage = _trier(_rapport(activites=activites))
    assert {d["statut"] for d in triage["activites"]} == {"arbitrer"}
    assert triage["decisions"]["activities"] == {}


def test_une_ressemblance_moins_forte_est_signalee_sans_bloquer():
    activites = [_activite("ANNIVERSAIRE EPE"), _activite("LES ANNIVERSAIRES EPE")]
    triage = _trier(_rapport(activites=activites))
    dossiers = _par_cle(triage["activites"])
    assert dossiers["ANNIVERSAIRE EPE"]["statut"] == "secteur"
    assert [v["nom"] for v in dossiers["ANNIVERSAIRE EPE"]["voisines"]] == ["LES ANNIVERSAIRES EPE"]
    assert len(triage["decisions"]["activities"]) == 2


def test_option_sans_secteurs_laisse_toutes_les_affectations_manuelles():
    triage = _trier(_rapport(activites=[_activite("FLE ESTHER")]), proposer_secteurs=False)
    assert triage["activites"][0]["statut"] == "arbitrer"
    assert triage["decisions"]["activities"] == {}


# --- Export et reprise des arbitrages -------------------------------------

def _rapport_complet():
    personnes = [_personne("FLE", 7, "SGIR", "Fatima", 1985, "F"),
                 _personne("ZUMBA", 8, "SGHIR", "Fatima", 1985, "F"),
                 _personne("FLE", 9, "SOLO", "Unique", 1990, "F")]
    seances = [_seance("CONCERT", "I", 9, jour=None, semaine="S",
                       mois="2026-01-01T00:00:00", statut="REVIEW")]
    activites = [_activite("REUNION DU MARDI")]
    anomalies = [_anomalie("a1", "missing_identity", "FLE", "A9", "Nom absent.")]
    return _rapport(personnes, seances=seances, activites=activites, anomalies=anomalies,
                    presences=["FLE!7", "ZUMBA!8", "FLE!9"])


def test_export_csv_une_ligne_par_dossier_a_trancher():
    triage = _trier(_rapport_complet())
    lignes = exporter_arbitrages(triage)
    assert {l["type"] for l in lignes} == {"personne", "seance", "activite", "anomalie"}
    assert all(set(l) == set(COLONNES_CSV) for l in lignes)
    assert all(l["decision"] == "" for l in lignes)
    # Le dossier déjà proposé n'encombre pas la liste de relecture.
    assert "sgir|fatima" in {l["cle"] for l in lignes}
    assert not any(l["libelle"].startswith("SOLO") for l in lignes)


def test_reprise_des_arbitrages_saisis_au_tableur():
    triage = _trier(_rapport_complet())
    lignes = exporter_arbitrages(triage)
    saisies = {"sgir|fatima": "nouvelle", "sghir|fatima": "groupe:sgir|fatima",
               "CONCERT!I": "2026-01-17", "REUNION DU MARDI": "Animation Globale",
               "a1": "acquitter"}
    for ligne in lignes:
        ligne["decision"] = saisies.get(ligne["cle"], "")
    decisions, refusees = fusionner_arbitrages(triage, lignes)
    assert refusees == []
    groupes = {decisions["participants"][k]["group"] for k in ("FLE!7", "ZUMBA!8")}
    assert len(groupes) == 1
    assert decisions["sessions"]["CONCERT!I"] == {"action": "date", "date_session": "2026-01-17"}
    assert decisions["activities"]["REUNION DU MARDI"] == {"secteur": "Animation Globale"}
    assert decisions["acknowledged_anomalies"] == ["a1"]


def test_le_renvoi_vers_un_autre_dossier_ignore_l_ordre_des_lignes():
    triage = _trier(_rapport_complet())
    lignes = [l for l in exporter_arbitrages(triage) if l["type"] == "personne"]
    lignes.sort(key=lambda l: l["cle"] != "sghir|fatima")  # la cible arrive après le renvoi
    for ligne in lignes:
        ligne["decision"] = {"sghir|fatima": "groupe:sgir|fatima", "sgir|fatima": "nouvelle"}[ligne["cle"]]
    decisions, refusees = fusionner_arbitrages(triage, lignes)
    assert refusees == []
    assert decisions["participants"]["FLE!7"] == decisions["participants"]["ZUMBA!8"]


@pytest.mark.parametrize("cle, choix, extrait", [
    ("sgir|fatima", "peut-être", "décision personne non reconnue"),
    ("sgir|fatima", "fiche:abc", "non numérique"),
    ("sgir|fatima", "groupe:inconnu", "absente du triage"),
    ("CONCERT!I", "le 17", "décision séance non reconnue"),
    ("REUNION DU MARDI", "atelier:x", "non numérique"),
    ("a1", "vu", "décision anomalie non reconnue"),
    ("a1", "acquitter", None),
])
def test_saisies_invalides_sont_refusees_sans_toucher_aux_decisions(cle, choix, extrait):
    triage = _trier(_rapport_complet())
    lignes = exporter_arbitrages(triage)
    for ligne in lignes:
        ligne["decision"] = choix if ligne["cle"] == cle else ""
    decisions, refusees = fusionner_arbitrages(triage, lignes)
    if extrait is None:  # témoin : la même mécanique accepte la saisie valide
        assert refusees == [] and decisions != triage["decisions"]
        return
    assert len(refusees) == 1 and extrait in refusees[0]["erreur"]
    assert decisions == triage["decisions"]


def test_ignorer_une_ligne_est_une_decision_explicite():
    triage = _trier(_rapport_complet())
    lignes = exporter_arbitrages(triage)
    for ligne in lignes:
        ligne["decision"] = "ignorer" if ligne["type"] == "personne" else ""
    decisions, refusees = fusionner_arbitrages(triage, lignes)
    assert refusees == []
    assert decisions["participants"]["FLE!7"] == {"action": "ignore"}


def test_le_triage_ne_modifie_pas_le_rapport_source():
    rapport = _rapport_complet()
    avant = json.dumps(rapport, sort_keys=True, default=str)
    _trier(rapport)
    assert json.dumps(rapport, sort_keys=True, default=str) == avant


def test_les_decisions_proposees_sont_acceptees_par_le_moteur_de_rapprochement():
    personnes = [_personne("FLE", 7, "MARTIN", "Sophie", 1980, "F"),
                 _personne("ZUMBA", 9, "MARTIN", "Sophie", 1980, "F"),
                 _personne("FLE", 8, "DURAND", "Alice", 1975, "F")]
    fiches = [{"id": 42, "raw": {"nom": "Durand", "prenom": "Alice", "date_naissance": "1975-04-02",
                                 "genre": "Femme", "telephone": None, "email": None,
                                 "ville": None, "quartier": None, "adresse": None}}]
    triage = _trier(_rapport(personnes, fiches))
    rejoue = resolve_people(personnes, fiches, decisions=triage["decisions"]["participants"])
    assert rejoue["counts"]["unresolved_review"] == 0
    assert {len(g["source_ids"]) for g in rejoue["groups"]} == {1, 2}
    assert [g["participant_id"] for g in rejoue["groups"] if g["participant_id"]] == [42]


def test_rapport_markdown_resume_le_travail_restant():
    texte = rapport_markdown(_trier(_rapport_complet()))
    assert "# Triage de la migration historique" in texte
    assert "orthographe proche" in texte
    assert "REUNION DU MARDI" in texte
    assert "Anomalies bloquantes à acquitter" in texte
    assert "Nom absent." in texte


# --- Ligne de commande ----------------------------------------------------

@pytest.fixture
def rapport_json(tmp_path):
    chemin = tmp_path / "rapport.json"
    chemin.write_text(json.dumps(_rapport_complet(), default=str), encoding="utf-8")
    return chemin


def test_ligne_de_commande_produit_decisions_compte_rendu_et_tableur(tmp_path, rapport_json):
    decisions, markdown, tableur = (tmp_path / "sortie" / n
                                    for n in ("decisions.json", "triage.md", "arbitrages.csv"))
    assert main(["trier", "--report", str(rapport_json), "--decisions", str(decisions),
                 "--markdown", str(markdown), "--csv", str(tableur)]) == 0
    # Les dossiers de sortie manquants sont créés, contrairement aux entrées.
    assert json.loads(decisions.read_text(encoding="utf-8"))["participants"] == {}
    assert "# Triage de la migration historique" in markdown.read_text(encoding="utf-8")
    entetes = next(csv.reader(io.StringIO(tableur.read_text(encoding="utf-8-sig")), delimiter=";"))
    assert entetes == list(COLONNES_CSV)


def test_ligne_de_commande_reprend_les_arbitrages_et_signale_les_refus(tmp_path, rapport_json):
    decisions, tableur = tmp_path / "decisions.json", tmp_path / "arbitrages.csv"
    main(["trier", "--report", str(rapport_json), "--decisions", str(decisions), "--csv", str(tableur)])
    lignes = list(csv.DictReader(io.StringIO(tableur.read_text(encoding="utf-8-sig")), delimiter=";"))
    for ligne in lignes:
        ligne["decision"] = {"CONCERT!I": "2026-01-17"}.get(ligne["cle"], "n_importe_quoi")
    with tableur.open("w", encoding="utf-8-sig", newline="") as sortie:
        ecrivain = csv.DictWriter(sortie, fieldnames=COLONNES_CSV, delimiter=";")
        ecrivain.writeheader()
        ecrivain.writerows(lignes)
    # Un refus vaut code de retour non nul : le lot n'est pas prêt.
    assert main(["fusionner", "--report", str(rapport_json), "--csv", str(tableur),
                 "--decisions", str(decisions)]) == 1
    reprises = json.loads(decisions.read_text(encoding="utf-8"))
    assert reprises["sessions"]["CONCERT!I"] == {"action": "date", "date_session": "2026-01-17"}


@pytest.mark.parametrize("arguments, extrait", [
    (["trier", "--report", "{absent}", "--decisions", "{sortie}"], "Rapport d'analyse introuvable"),
    (["trier", "--report", "{dossier}", "--decisions", "{sortie}"], "est un dossier, pas un fichier"),
    (["trier", "--report", "{illisible}", "--decisions", "{sortie}"], "n'est pas du JSON valide"),
    (["fusionner", "--report", "{rapport}", "--csv", "{absent}", "--decisions", "{sortie}"],
     "CSV d'arbitrages introuvable"),
    (["fusionner", "--report", "{rapport}", "--csv", "{illisible}", "--decisions", "{sortie}"],
     "colonnes absentes"),
])
def test_un_chemin_manquant_donne_une_erreur_lisible_pas_une_trace(
        tmp_path, rapport_json, capsys, arguments, extrait):
    illisible = tmp_path / "illisible.txt"
    illisible.write_text("ceci n'est pas un rapport", encoding="utf-8")
    (tmp_path / "vide").mkdir()
    chemins = {"absent": tmp_path / "absent.json", "dossier": tmp_path / "vide",
               "illisible": illisible, "rapport": rapport_json, "sortie": tmp_path / "sortie.json"}
    with pytest.raises(SystemExit) as sortie:
        main([a.format(**{k: str(v) for k, v in chemins.items()}) for a in arguments])
    assert sortie.value.code == 2
    assert extrait in capsys.readouterr().err
    assert not chemins["sortie"].exists()


# --- Intégration sur le vrai rapport, si le fichier privé est fourni -------

@pytest.mark.skipif(not os.environ.get("HISTORICAL_REPORT_PATH"),
                    reason="rapport d'analyse réel non fourni")
def test_triage_du_vrai_rapport_reduit_les_blocages_sans_les_supprimer():
    with open(os.environ["HISTORICAL_REPORT_PATH"], encoding="utf-8-sig") as source:
        rapport = json.load(source)
    triage = _trier(rapport)
    resume = triage["resume"]
    assert resume["dossiers_personnes"] < resume["lignes_source"]
    assert 0 < resume["personnes_a_arbitrer"] < resume["dossiers_personnes"]
    lignes = exporter_arbitrages(triage)
    assert len(lignes) == resume["blocages_restants"]
    tampon = io.StringIO()
    ecrivain = csv.DictWriter(tampon, fieldnames=COLONNES_CSV, delimiter=";")
    ecrivain.writeheader()
    ecrivain.writerows(lignes)
    relues = list(csv.DictReader(io.StringIO(tampon.getvalue()), delimiter=";"))
    decisions, refusees = fusionner_arbitrages(triage, relues)
    assert refusees == [] and decisions == triage["decisions"]
    fiches = [{"id": c["id"], "raw": c["raw"]}
              for ligne in rapport["matching"]["rows"] for c in ligne["candidates"]
              if c["kind"] == "participant"]
    fiches = list({f["id"]: f for f in fiches}.values())
    rejoue = resolve_people(rapport["parser"]["people"], fiches,
                            decisions=triage["decisions"]["participants"])
    assert rejoue["counts"]["unresolved_review"] == resume["lignes_a_arbitrer"]
