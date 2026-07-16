"""Tests du moteur d'import de participants (parsing, normalisation, simulation)."""
from app.services.import_participants import (
    analyser,
    detecter_entetes,
    normaliser,
    normaliser_sexe,
    parse_annee,
    parser_feuille,
)


def test_normalisation_casse_accents_espaces():
    # Le cœur de la robustesse aux variations cosmétiques
    assert normaliser("DUPONT") == normaliser("dupont") == normaliser(" Dupont ")
    assert normaliser("EL OIRIRHI") == normaliser("ELOIRIRHI")
    assert normaliser("Müller") == normaliser("MULLER")


def test_parse_annee():
    assert parse_annee("1982") == 1982
    assert parse_annee("ADULTE") is None
    assert parse_annee("INCONNU") is None
    assert parse_annee("") is None
    assert parse_annee("12/03/1990") == 1990
    assert parse_annee(None) is None


def test_normaliser_sexe():
    assert normaliser_sexe("F") == "Femme"
    assert normaliser_sexe("M") == "Homme"
    assert normaliser_sexe("H") == "Homme"  # certaines feuilles notent H
    assert normaliser_sexe("") is None


def _grille(header_row_index, lignes_data):
    """Construit une feuille avec en-tête à une position donnée."""
    entete = ["NOMS", "PRENOMS", "ANNEE NAISSANCE", "AGE", "SEXE", "QUARTIER"]
    lignes = [[""] * 6 for _ in range(header_row_index)]
    lignes.append(entete)
    lignes.extend(lignes_data)
    return lignes


def test_detection_entete_position_variable():
    # En-tête en ligne 5 (comme ZUMBA) puis en ligne 2 (comme CAFE REPAIR)
    for pos in (0, 2, 4):
        lignes = _grille(pos, [["DUPONT", "MARIE", "1980", "44", "F", "ROUHER"]])
        det = detecter_entetes(lignes)
        assert det is not None
        idx, cols = det
        assert idx == pos
        assert cols["nom"] == 0 and cols["prenom"] == 1 and cols["annee"] == 2


def test_feuille_sans_entete_ignoree():
    lignes = [["DUPONT", "MARIE", "1980"], ["MARTIN", "PAUL", "1975"]]
    personnes, raison = parser_feuille("ADULTES", lignes)
    assert personnes == []
    assert raison is not None


def test_parser_ignore_lignes_total_et_vides():
    lignes = _grille(0, [
        ["DUPONT", "MARIE", "1980", "44", "F", "ROUHER"],
        ["", "", "", "", "", ""],
        ["TOTAL", "", "", "", "", ""],
        ["MARTIN", "PAUL", "ADULTE", "", "M", "INCONNU"],
    ])
    personnes, raison = parser_feuille("ZUMBA", lignes)
    assert raison is None
    assert [p.nom for p in personnes] == ["DUPONT", "MARTIN"]
    assert personnes[0].annee == 1980 and personnes[0].sexe == "Femme"
    assert personnes[1].annee is None  # "ADULTE"


def test_simulation_nouveau_rapproche_doublon_ambigu():
    feuilles = {
        "ZUMBA": _grille(0, [
            ["DUPONT", "MARIE", "1980", "", "F", "ROUHER"],   # nouveau
            ["MARTIN", "PAUL", "1975", "", "M", "CAVEE"],     # rapproché (déjà en base)
            ["SANS", "ANNEE", "ADULTE", "", "F", ""],         # ambigu
        ]),
        "YOGA": _grille(0, [
            ["DUPONT", "MARIE", "1980", "", "F", "ROUHER"],   # doublon interne
        ]),
    }
    existantes = {("martin", "paul", 1975)}
    rapport = analyser(feuilles, cles_existantes=existantes)

    assert [p.nom for p in rapport.nouveaux] == ["DUPONT"]
    assert [p.nom for p in rapport.rapproches] == ["MARTIN"]
    assert [p.nom for p in rapport.doublons_internes] == ["DUPONT"]
    assert [p.nom for p in rapport.ambigus] == ["SANS"]
    assert rapport.resume()["total_lignes"] == 4


def test_variations_casse_fusionnees_en_simulation():
    """DUPONT / dupont / 'Dupont ' avec même année = une seule personne nouvelle."""
    feuilles = {
        "A": _grille(0, [["DUPONT", "Marie", "1980", "", "F", ""]]),
        "B": _grille(0, [["dupont", "MARIE", "1980", "", "F", ""]]),
        "C": _grille(0, [[" Dupont ", "marie", "1980", "", "F", ""]]),
    }
    rapport = analyser(feuilles)
    assert len(rapport.nouveaux) == 1
    assert len(rapport.doublons_internes) == 2


def test_feuilles_ignorees_rapportees():
    feuilles = {
        "VALIDE": _grille(0, [["DUPONT", "MARIE", "1980", "", "F", ""]]),
        "RESUME": [["Bla", "bla"], ["chiffres", 42]],
    }
    rapport = analyser(feuilles)
    assert "VALIDE" in rapport.feuilles_traitees
    assert any(nom == "RESUME" for nom, _ in rapport.feuilles_ignorees)
