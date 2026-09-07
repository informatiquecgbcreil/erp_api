"""Identity safety tests: real names/data are not needed or committed here."""
from copy import deepcopy
from datetime import date
from itertools import permutations

import pytest

from app.ateliers.historical_matching import (
    normalize_email, normalize_person, normalize_phone, normalize_territory,
    resolve_participants, resolve_people,
)


def person(key="sheet:2", **fields):
    return {"key": key, "raw": {"nom": "DUPONT", "prenom": "Jean", **fields}}


def existing(identifier=1, **fields):
    return {"id": identifier, "nom": "Dupont", "prenom": "Jean", **fields}


def test_new_without_birth_no_candidate():
    result = resolve_people([person()])
    assert result["rows"][0]["classification"] == "NEW"
    assert result["groups"][0]["values"]["date_naissance"] is None
    assert result["groups"][0]["values"]["ville"] is None


def test_exact_requires_full_date_and_contact():
    result = resolve_people([person(date_naissance="03/04/1985", telephone="06 12 34 56 78")],
                            [existing(date_naissance=date(1985, 4, 3), telephone="+33 6 12 34 56 78")])
    assert result["rows"][0]["classification"] == "EXACT"
    assert result["groups"][0]["participant_id"] == 1


def test_name_case_accents_hyphens_spaces_do_not_define_identity():
    result = resolve_people([
        person("A", nom=" D’Angélo-  Martin ", prenom=" Léa ", annee_naissance=1985, telephone="0612345678"),
        person("B", nom="dangelo martin", prenom="LEA", annee_naissance=1985, telephone="0033612345678"),
    ])
    assert len(result["groups"]) == 1
    assert result["rows"][1]["classification"] == "HIGH_CONFIDENCE"


def test_name_typo_with_common_birth_year_and_phone():
    result = resolve_people([person(nom="DUPON", annee_naissance=1985, telephone="0612345678")],
                            [existing(annee_naissance=1985, telephone="0612345678")])
    row = result["rows"][0]
    assert row["classification"] == "HIGH_CONFIDENCE"
    assert "minor_name_typo" in row["reasons"]


@pytest.mark.parametrize("fields", [{}, {"annee_naissance": 1985},
                                   {"annee_naissance": 1985, "quartier": "ROUHER", "genre": "Homme"}])
def test_real_homonym_never_merged_on_names_or_demographics_alone(fields):
    result = resolve_people([person("A", **fields), person("B", **fields)])
    assert [r["classification"] for r in result["rows"]] == ["REVIEW", "REVIEW"]
    assert not result["groups"]


def test_missing_birth_with_common_dual_contact_is_not_new():
    result = resolve_people([person(telephone="0612345678", email="JEAN@EXAMPLE.ORG")],
                            [existing(annee_naissance=1985, telephone="0612345678", email="jean@example.org")])
    assert result["rows"][0]["classification"] == "HIGH_CONFIDENCE"


def test_missing_birth_insufficient_evidence_reviews_instead_of_recreating():
    result = resolve_people([person(quartier="ROUHER")], [existing(annee_naissance=1985, quartier="ROUHER")])
    assert result["rows"][0]["classification"] == "REVIEW"
    assert result["rows"][0]["candidate_ids"] == [1]
    assert not result["groups"]


def test_conflicting_birth_year_is_review_even_with_contact():
    result = resolve_people([person(annee_naissance=1985, telephone="0612345678")],
                            [existing(annee_naissance=1986, telephone="0612345678")])
    assert result["rows"][0]["classification"] == "REVIEW"
    assert "conflict:birth_year" in result["rows"][0]["reasons"]


def test_same_year_but_conflicting_full_birth_date_is_review():
    result = resolve_people([person(date_naissance="1985-04-03", telephone="0612345678")],
                            [existing(date_naissance="1985-05-04", telephone="0612345678")])
    assert result["rows"][0]["classification"] == "REVIEW"


def test_multiple_existing_candidates_are_not_arbitrarily_selected():
    fields = {"annee_naissance": 1985, "telephone": "0612345678"}
    result = resolve_people([person(**fields)], [existing(1, **fields), existing(2, **fields)])
    assert result["rows"][0]["classification"] == "REVIEW"
    assert result["rows"][0]["candidate_ids"] == [1, 2]


def test_all_sheet_orders_give_same_identity_resolution():
    people = [person("atelier1:2", annee_naissance=1985, telephone="0612345678", email="jean@example.org"),
              person("atelier2:3", telephone="0612345678", email="jean@example.org"),
              person("atelier1:4", annee_naissance=1985, telephone="0612345678", email="jean@example.org")]
    expected = resolve_people(people)
    assert expected["counts"]["new_participants"] == 1
    assert expected["counts"]["internal_duplicates"] == 2
    for order in permutations(people):
        assert resolve_people(order) == expected


def test_same_person_in_multiple_activities_and_complementary_rows_share_entity():
    people = [person("atelier1:2", annee_naissance=1985, telephone="0612345678"),
              person("atelier1:3", annee_naissance=1985, telephone="0612345678"),
              person("atelier2:2", annee_naissance=1985, telephone="0612345678")]
    for index, row in enumerate(people):
        row["presences"] = [f"different_session_{index}"]
    original = deepcopy(people)
    result = resolve_people(people)
    assert len({r["group_key"] for r in result["rows"]}) == 1
    assert len(result["groups"][0]["source_ids"]) == 3
    assert people == original  # Matching does not discard presence-bearing rows.


def test_nontransitive_chain_never_merges_disjoint_identity_evidence():
    # A--B share phone and year; B--C share email and year; A--C
    # share names/year only. A transitive union would be unsafe.
    result = resolve_people([
        person("A", annee_naissance=1985, telephone="0612345678"),
        person("B", annee_naissance=1985, telephone="0612345678", email="jean@example.org"),
        person("C", annee_naissance=1985, email="jean@example.org"),
    ])
    assert result["counts"]["REVIEW"] == 3
    assert not result["groups"]


def test_missing_year_bridge_does_not_hide_conflicting_years():
    fields = {"telephone": "0612345678", "email": "jean@example.org"}
    people = [person("A", annee_naissance=1985, **fields), person("B", **fields),
              person("C", annee_naissance=1986, **fields)]
    for order in permutations(people):
        result = resolve_people(order)
        assert result["counts"]["REVIEW"] == 3
        assert not result["groups"]


@pytest.mark.parametrize("source,expected", [
    ("NOGENT SUR OISE", "Nogent-sur-Oise"), ("MONTATAIRE", "Montataire"),
    ("PONT STE MAXENCE", "Pont-Sainte-Maxence"), ("CLERMONT DE L OISE", "Clermont"),
    ("MONCHY ST ELOI", "Monchy-Saint-Éloi"), ("VILLERS ST PAUL", "Villers-Saint-Paul"),
])
def test_external_commune_is_city_never_creil_district(source, expected):
    territory = normalize_territory(None, source)
    assert territory["ville"] == expected
    assert territory["quartier"] is None
    assert not territory["anomalies"]


@pytest.mark.parametrize("source,expected", [("ROUHER", "Rouher"), ("CAVEE", "Cavée"),
                                            ("BAS DE CREIL", "Bas de Creil"), ("CAVEE DE PARIS", "Cavée de Paris")])
def test_known_creil_district_supports_explicit_inference(source, expected):
    territory = normalize_territory(None, source)
    assert territory["ville"] == "Creil"
    assert territory["quartier"] == expected


def test_unknown_territory_is_not_assigned_to_creil():
    territory = normalize_territory(None, "MOULIN")
    assert territory["ville"] is None
    assert territory["quartier"] is None
    assert territory["anomalies"][0]["code"] == "unknown_territory"


def test_territorial_contradiction_retains_explicit_city_and_no_false_district():
    territory = normalize_territory("Creil", "Nogent sur Oise")
    assert territory["ville"] == "Creil"
    assert territory["quartier"] is None
    assert territory["anomalies"][0]["code"] == "city_district_conflict"


def test_differing_known_territories_are_reviewed_not_overwritten():
    fields = {"telephone": "0612345678", "annee_naissance": 1985}
    result = resolve_people([person(ville="Creil", **fields)], [existing(ville="Montataire", **fields)])
    assert result["rows"][0]["classification"] == "REVIEW"


def test_originals_preserved_and_no_fake_full_birth_date():
    row = person(annee_naissance=1985, telephone="06 12 34 56 78", nom=" DuPônt ")
    result = resolve_people([row])
    assert result["rows"][0]["raw"] == row["raw"]
    assert result["groups"][0]["values"]["annee_naissance"] == 1985
    assert result["groups"][0]["values"]["date_naissance"] is None


def test_invalid_birth_and_conflicting_birth_fields_are_reviewed():
    for fields in ({"annee_naissance": 9999}, {"date_naissance": "1985-04-03", "annee_naissance": 1986}):
        assert resolve_people([person(**fields)])["rows"][0]["classification"] == "REVIEW"


def test_contacts_are_normalized_without_inventing_missing_contacts():
    assert normalize_phone("06 12 34 56 78") == "+33612345678"
    assert normalize_phone("0033 6 12 34 56 78") == "+33612345678"
    assert normalize_phone(612345678) == "+33612345678"
    assert normalize_phone("telephone inconnu") is None
    assert normalize_email(" TEST@EXAMPLE.ORG ") == "test@example.org"
    assert normalize_email("bad") is None


def test_manual_group_unites_review_rows_without_discarding_sources():
    result = resolve_people([person("A"), person("B")], decisions={
        "A": {"action": "new", "group": "confirmed-person"},
        "B": {"action": "new", "group": "confirmed-person"},
    })
    assert len(result["groups"]) == 1
    assert result["groups"][0]["source_ids"] == ["A", "B"]
    assert result["counts"]["REVIEW"] == 2
    assert result["counts"]["unresolved_review"] == 0


def test_manual_existing_and_distinct_person_decisions():
    result = resolve_people([person("A"), person("B"), person("C")], [existing()], decisions={
        "A": {"action": "participant", "participant_id": 1},
        "B": {"action": "new"}, "C": {"action": "ignore"},
    })
    assert len(result["groups"]) == 2
    assert result["counts"]["ignored"] == 1
    assert result["counts"]["unresolved_review"] == 0


def test_manual_source_reference_can_target_explicitly_created_person():
    result = resolve_people([person("A"), person("B")], decisions={
        "A": {"action": "source", "source_id": "B"}, "B": {"action": "new"},
    })
    assert len(result["groups"]) == 1


@pytest.mark.parametrize("decisions", [
    {"missing": {"action": "new"}}, {"A": {"action": "participant", "participant_id": 999}},
    {"A": {"action": "participant", "participant_id": True}},
    {"A": {"action": "new", "group": "", }},
    {"A": {"action": "new", "participant_id": 1}},
    {"A": {"action": "participant", "participant_id": 1, "group": "same"}},
    {"A": {"action": "source", "source_id": "B"}, "B": {"action": "source", "source_id": "A"}},
])
def test_invalid_decisions_reject_without_partial_effects(decisions):
    people = [person("A"), person("B")]
    original = deepcopy(people)
    with pytest.raises(ValueError):
        resolve_people(people, [existing()], decisions=decisions)
    assert people == original


def test_partial_manual_split_of_automatic_group_rejected():
    fields = {"telephone": "0612345678", "annee_naissance": 1985}
    with pytest.raises(ValueError, match="partielle"):
        resolve_people([person("A", **fields), person("B", **fields)], decisions={"A": {"action": "new"}})


def test_both_input_output_contract_aliases_are_supported():
    result = resolve_participants([{"id": "A", "raw": {"nom": "Dupont", "prenom": "Jean", "sexe": "H", "annee_naissance": 1985}}])
    assert result["entities"] == result["groups"]
    assert result["rows"][0]["source_id"] == result["rows"][0]["key"] == "A"
    assert result["groups"][0]["values"]["genre"] == "Homme"


def test_incomplete_identity_preserved_for_review_and_needs_correction_to_create():
    row = person("A", nom="", prenom="Jean")
    assert resolve_people([row])["counts"]["unresolved_review"] == 1
    with pytest.raises(ValueError, match="nom et un prénom"):
        resolve_people([row], decisions={"A": {"action": "new"}})
    result = resolve_people([row], decisions={"A": {"action": "new", "values": {"nom": "Dupont"}}})
    assert result["groups"][0]["values"]["nom"] == "Dupont"
    assert result["rows"][0]["raw"]["nom"] == ""
    assert result["rows"][0]["decision"]["values"]["nom"] == "Dupont"


def test_incomplete_identity_can_bind_to_existing_or_ignore_without_invention():
    row = person("A", nom="", prenom="Jean")
    result = resolve_people([row], [existing()], decisions={"A": {"action": "participant", "participant_id": 1}})
    assert result["groups"][0]["participant_id"] == 1
    result = resolve_people([row], decisions={"A": {"action": "ignore"}})
    assert result["counts"]["unresolved_review"] == 0


def test_manual_identity_group_does_not_arbitrarily_choose_conflicting_birth_year():
    people = [person("A", annee_naissance=1985), person("B", annee_naissance=1986)]
    result = resolve_people(people, decisions={key: {"action": "new", "group": "same"} for key in ("A", "B")})
    assert result["groups"][0]["values"]["annee_naissance"] is None
    assert "birth_year" in result["groups"][0]["value_conflicts"]


@pytest.mark.parametrize("values", [{"nom": ""}, {"nom": "?"}, {"nom": 1}, {"ville": "Creil"}, {"nom": "a" * 121}])
def test_invalid_name_corrections_rejected(values):
    with pytest.raises(ValueError):
        resolve_people([person("A")], decisions={"A": {"action": "new", "values": values}})
