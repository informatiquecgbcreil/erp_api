"""HTTP staging contract; the real workbook/service have separate integration tests."""
from copy import deepcopy
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


@pytest.fixture
def historical_ui(app, admin_client, monkeypatch, tmp_path):
    monkeypatch.setattr(app, "instance_path", str(tmp_path))
    calls = {"analyze": [], "apply": [], "ready": False}

    def analyze(path, decisions=None, year=None):
        decisions = decisions or {}
        tranchee = lambda key: key in decisions.get("participants", {})
        calls["analyze"].append({"path": path, "decisions": deepcopy(decisions), "year": year})
        digest = hashlib.sha256(json.dumps(decisions, sort_keys=True).encode()).hexdigest()
        return {
            "source": {"filename": Path(path).name}, "digest": digest,
            "summary": {"sheets": 1, "participants_source": 2, "sessions": 1},
            "parser": {"sheets": [{"name": "Atelier", "classification": "ACTIVITY"}], "anomalies": [{"id": "a1", "message": "Total à contrôler"}], "errors": [],
                       "attendance": [{"person_key": "sheet:5"}, {"person_key": "sheet:6"}]},
            "anomalies": [{"id": "a1", "code": "invalid_day", "message": "Total à contrôler", "blocking": True,
                           "acknowledged": "a1" in decisions.get("acknowledged_anomalies", []),
                           "source_sheet": "Atelier", "source_cell": "J6", "raw_value": 6},
                          {"id": "t1", "code": "unknown_territory", "value": "MOULIN", "blocking": True,
                           "acknowledged": "t1" in decisions.get("acknowledged_anomalies", []),
                           "key": "sheet:5", "source_id": "sheet:5"}],
            "matching": {"rows": [
                {"key": "sheet:5", "classification": "REVIEW", "resolved": tranchee("sheet:5"), "reasons": ["names_without_sufficient_identity_evidence"], "raw": {"nom": "TEST", "prenom": "Luc", "annee_naissance": 1980},
                 "normalized": {"nom": "test", "prenom": "luc", "birth_year": 1980, "birth_date": None, "genre": "Femme", "telephone": None, "email": None, "ville": None, "quartier": None, "adresse": None},
                 "candidates": [{"kind": "participant", "id": 99, "reasons": ["names_without_sufficient_identity_evidence"], "raw": {"nom": "TEST", "prenom": "Luc", "date_naissance": "1980-03-02"}}, {"kind": "source", "key": "sheet:6", "reasons": ["names_without_sufficient_identity_evidence"], "raw": {"nom": "TEST", "prenom": "Luc", "annee_naissance": 1980}}]},
                {"key": "sheet:6", "classification": "REVIEW", "resolved": tranchee("sheet:6"), "reasons": ["names_without_sufficient_identity_evidence"], "raw": {"nom": "TEST", "prenom": "Luc", "annee_naissance": 1980},
                 "normalized": {"nom": "test", "prenom": "luc", "birth_year": 1980, "birth_date": None, "genre": "Femme", "telephone": None, "email": None, "ville": None, "quartier": None, "adresse": None},
                 "candidates": []},
            ]},
            "activities": [{"key": "act:1", "name": "Atelier", "source_sheet": "Atelier", "secteur": decisions.get('activities', {}).get('act:1', {}).get('secteur'), "status": "REVIEW", "match_status": "NEW", "candidates": [{"id": 1, "name": "Atelier existant"}]}],
            "sessions": [{"key": "session:1", "sheet": "Atelier", "source_sheet": "Atelier", "source_cell": "J6", "source_column": 10,
                          "source_slot": "M", "raw_headers": {"J2": "2026-01-01T00:00:00", "J4": "M", "J6": 6}, "anomalies": [],
                          "candidate_dates": [], "date_session": "2026-01-12", "status": "REVIEW", "candidates": [{"id": 7, "date_session": "2026-01-12"}]}],
            "blockers": [] if calls["ready"] else [{"kind": "participant", "key": "sheet:5", "message": "Homonyme à valider"}],
            "ready": calls["ready"], "decisions": decisions,
        }

    def apply(path, **kwargs):
        calls["apply"].append({"path": path, **kwargs})
        if calls.get("apply_error"):
            raise ValueError("Les données ont changé depuis l'aperçu")
        return {"batch_id": 1, "presences_created": 2}

    monkeypatch.setitem(sys.modules, "app.ateliers.historical_import", SimpleNamespace(analyze_import=analyze, apply_import=apply))
    return SimpleNamespace(app=app, client=admin_client, calls=calls, root=tmp_path / "historical_imports")


def _upload(ui, **fields):
    from app.secteurs import get_secteur_labels
    with ui.app.app_context():
        secteur = get_secteur_labels()[0]
    data = {"secteur": secteur, "xlsx_file": (BytesIO(b"route-test-workbook"), "STATS_2026_par_activite.xlsx"), **fields}
    response = ui.client.post("/admin/import-historical", data=data, content_type="multipart/form-data")
    return response


def _stage(ui):
    response = _upload(ui)
    assert response.status_code == 302
    url = response.headers["Location"]
    stage = ui.root / url.rsplit("/", 1)[1]
    plan = json.loads((stage / "plan.json").read_text(encoding="utf-8"))
    return url, stage, plan


def test_historical_requires_authentication(client):
    assert client.get("/admin/import-historical").status_code == 302


def test_preview_does_not_trigger_background_housekeeping(app, monkeypatch):
    from app.services import purge_rgpd, notifications
    def forbidden():
        raise AssertionError("Le dry-run ne doit pas déclencher cette tâche")
    monkeypatch.setattr(purge_rgpd, "purge_auto_active", forbidden)
    monkeypatch.setattr(notifications, "notifications_actives", forbidden)
    with app.test_request_context("/admin/import-historical"):
        hooks = {f.__name__: f for f in app.before_request_funcs[None]}
        assert hooks["_purge_rgpd_quotidienne"]() is None
        assert hooks["_digest_notifications_quotidien"]() is None


def test_standard_sector_is_required_and_dry_run_is_default(historical_ui):
    response = historical_ui.client.get("/admin/import-excel")
    assert response.status_code == 200
    assert b'name="secteur" required' in response.data
    assert b'value="1" selected' in response.data
    assert historical_ui.client.post("/admin/import-excel", data={"secteur": "INVALID"}).status_code == 400


def test_upload_needs_no_global_sector(historical_ui):
    response = _upload(historical_ui, secteur="")
    assert response.status_code == 302
    assert "secteur" not in historical_ui.calls["analyze"][0]
    assert not historical_ui.calls["apply"]


def test_upload_previews_without_applying_and_retains_filename(historical_ui):
    url, stage, _ = _stage(historical_ui)
    assert len(historical_ui.calls["analyze"]) == 1
    assert not historical_ui.calls["apply"]
    assert (stage / "STATS_2026_par_activite.xlsx").exists()
    response = historical_ui.client.get(url)
    assert response.status_code == 200
    assert "no-store" in response.headers["Cache-Control"]
    assert b"sheet:5" in response.data
    assert b"source:sheet:6" in response.data
    assert b"existing:99" in response.data
    assert b"TEST Luc" in response.data
    assert historical_ui.client.get(url + "/report.json").json["summary"]["sheets"] == 1


def test_owner_scope_and_expiry(historical_ui):
    url, stage, _ = _stage(historical_ui)
    path = stage / "metadata.json"
    original = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**original, "owner_id": 999999}), encoding="utf-8")
    assert historical_ui.client.get(url).status_code == 404
    assert historical_ui.client.get(url + "/report.json").status_code == 404
    path.write_text(json.dumps(original), encoding="utf-8")
    plan_path = stage / "plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan["activities"][0]["secteur"] = "INVALID"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    assert historical_ui.client.get(url).status_code == 403
    plan["activities"][0]["secteur"] = None
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    path.write_text(json.dumps({**original, "expires_at": 1}), encoding="utf-8")
    assert historical_ui.client.get(url).status_code == 410


def test_source_tampering_is_rejected(historical_ui):
    url, stage, plan = _stage(historical_ui)
    (stage / "STATS_2026_par_activite.xlsx").write_bytes(b"different workbook")
    assert historical_ui.client.get(url).status_code == 409
    response = historical_ui.client.post(url + "/apply", data={"digest": plan["digest"], "confirm": "yes"})
    assert response.status_code == 409
    assert not historical_ui.calls["apply"]


def test_decisions_assign_sector_after_analysis(historical_ui):
    url, stage, plan = _stage(historical_ui)
    response = historical_ui.client.post(url + "/decisions", data={
        "digest": plan["digest"], "secteur": "FORGED",
        "participants.0": "source:sheet:6", "participants.1": "new",
        "participants.1.group": "famille-test", "activities.0": "existing:1", "activities.0.secteur": "Familles",
        "sessions.0": "date", "sessions.0.date": "2026-02-12",
        "acknowledged_anomalies": "a1",
    })
    assert response.status_code == 302
    last = historical_ui.calls["analyze"][-1]
    assert "secteur" not in last
    assert last["decisions"] == {
        "participants": {"sheet:5": {"action": "source", "source_id": "sheet:6"}, "sheet:6": {"action": "new", "group": "famille-test"}},
        "activities": {"act:1": {"action": "existing", "atelier_id": 1, "secteur": "Familles"}},
        "sessions": {"session:1": {"action": "date", "date_session": "2026-02-12"}},
        "acknowledged_anomalies": ["a1"],
    }
    refreshed = json.loads((stage / "plan.json").read_text(encoding="utf-8"))
    assert refreshed["digest"] != plan["digest"]
    assert historical_ui.client.get(url).status_code == 200
    assert not historical_ui.calls["apply"]


def test_decisions_upload_and_export(historical_ui):
    url, _, plan = _stage(historical_ui)
    decisions = {"participants": {"sheet:5": {"action": "ignore"}}}
    response = historical_ui.client.post(url + "/decisions", data={
        "digest": plan["digest"], "decisions_file": (BytesIO(json.dumps(decisions).encode()), "decisions.json"),
    }, content_type="multipart/form-data")
    assert response.status_code == 302
    assert historical_ui.client.get(url + "/decisions.json").json == decisions


def test_decisions_reject_stale_digest_and_invalid_json(historical_ui):
    url, _, plan = _stage(historical_ui)
    assert historical_ui.client.post(url + "/decisions", data={"digest": "stale"}).status_code == 409
    response = historical_ui.client.post(url + "/decisions", data={
        "digest": plan["digest"], "decisions_file": (BytesIO(b"[]"), "decisions.json"),
    }, content_type="multipart/form-data")
    assert response.status_code == 400
    assert len(historical_ui.calls["analyze"]) == 1


def test_triage_proposes_decisions_and_reruns_the_dry_run(historical_ui):
    url, stage, plan = _stage(historical_ui)
    response = historical_ui.client.post(url + "/triage", data={"digest": plan["digest"]})
    assert response.status_code == 302
    proposees = historical_ui.calls["analyze"][-1]["decisions"]
    # Les deux lignes de même identité forment un dossier unique, rattaché à la fiche.
    assert proposees["participants"] == {
        "sheet:5": {"action": "participant", "participant_id": 99},
        "sheet:6": {"action": "participant", "participant_id": 99}}
    # La colonne datable est proposée ; un nom d'activité muet ne fait deviner aucun secteur.
    assert proposees["sessions"] == {"session:1": {"action": "date", "date_session": "2026-01-06"}}
    assert proposees["activities"] == {}
    assert json.loads((stage / "decisions.json").read_text(encoding="utf-8")) == proposees
    assert json.loads((stage / "plan.json").read_text(encoding="utf-8"))["digest"] != plan["digest"]
    assert not historical_ui.calls["apply"]


def test_triage_never_replaces_a_saved_decision(historical_ui):
    url, stage, plan = _stage(historical_ui)
    saisie = {"participants": {"sheet:5": {"action": "ignore"}}}
    response = historical_ui.client.post(url + "/decisions", data={
        "digest": plan["digest"], "decisions_file": (BytesIO(json.dumps(saisie).encode()), "decisions.json"),
    }, content_type="multipart/form-data")
    assert response.status_code == 302
    courant = json.loads((stage / "plan.json").read_text(encoding="utf-8"))
    assert historical_ui.client.post(url + "/triage", data={"digest": courant["digest"]}).status_code == 302
    proposees = historical_ui.calls["analyze"][-1]["decisions"]
    assert proposees["participants"]["sheet:5"] == {"action": "ignore"}
    assert proposees["participants"]["sheet:6"] == {"action": "participant", "participant_id": 99}


def test_triage_never_acknowledges_an_anomaly(historical_ui):
    url, _, plan = _stage(historical_ui)
    historical_ui.client.post(url + "/triage", data={"digest": plan["digest"]})
    assert historical_ui.calls["analyze"][-1]["decisions"]["acknowledged_anomalies"] == []


def test_triage_refuses_a_stale_preview_and_a_malformed_plan(historical_ui):
    url, stage, plan = _stage(historical_ui)
    assert historical_ui.client.post(url + "/triage", data={"digest": "perime"}).status_code == 409
    casse = json.loads((stage / "plan.json").read_text(encoding="utf-8"))
    for row in casse["matching"]["rows"]:
        row.pop("normalized")
    (stage / "plan.json").write_text(json.dumps(casse), encoding="utf-8")
    response = historical_ui.client.post(url + "/triage", data={"digest": plan["digest"]})
    assert response.status_code == 400
    assert "Rapport d&#39;analyse incomplet" in response.get_data(as_text=True)
    assert len(historical_ui.calls["analyze"]) == 1


def test_triage_button_is_hidden_once_the_batch_is_applied(historical_ui):
    url, _, plan = _stage(historical_ui)
    historical_ui.calls["ready"] = True
    historical_ui.client.post(url + "/decisions", data={"digest": plan["digest"]})
    courant = json.loads((historical_ui.root / url.rsplit("/", 1)[1] / "plan.json").read_text(encoding="utf-8"))
    assert "/triage" in historical_ui.client.get(url).get_data(as_text=True)
    historical_ui.client.post(url + "/apply", data={"digest": courant["digest"], "confirm": "yes"})
    assert "/triage" not in historical_ui.client.get(url).get_data(as_text=True)


def test_preview_groups_lines_into_dossiers_without_embedding_the_plan(historical_ui):
    url, _, _ = _stage(historical_ui)
    page = historical_ui.client.get(url).get_data(as_text=True)
    # Les deux lignes homonymes forment un seul dossier, présenté une fois.
    assert page.count("Provenance, candidats et motifs") == 0
    assert "sheet:5" in page and "sheet:6" in page
    assert "2 lignes source" in page
    # Le plan complet n'est plus recopié dans la page : il reste dans le rapport.
    assert "Toutes les classifications et groupes de participants" not in page
    assert "Toutes les séances reconnues" not in page
    assert "Erreurs de lecture" not in page


def test_preview_filters_and_searches_dossiers(historical_ui):
    url, _, _ = _stage(historical_ui)
    get = lambda suffixe: historical_ui.client.get(url + suffixe).get_data(as_text=True)
    assert "TEST" in get("?q=test")
    assert "Aucun dossier ne correspond" in get("?q=personne-inconnue")
    # Rien n'est réglé tant qu'aucune décision n'est prise.
    assert "Aucun dossier ne correspond" in get("?vue=regles")
    assert "TEST" in get("?vue=tous")
    # Une vue ou une page invalide retombe sur l'affichage utile.
    assert "TEST" in get("?vue=n-importe-quoi&page=nawak")


def test_preview_hides_a_settled_dossier_and_keeps_its_decision(historical_ui):
    url, stage, plan = _stage(historical_ui)
    decisions = {"digest": plan["digest"], "participants.0": "ignore", "participants.1": "ignore"}
    assert historical_ui.client.post(url + "/decisions", data=decisions).status_code == 302
    enregistrees = json.loads((stage / "decisions.json").read_text(encoding="utf-8"))
    assert enregistrees["participants"] == {"sheet:5": {"action": "ignore"}, "sheet:6": {"action": "ignore"}}
    page = historical_ui.client.get(url).get_data(as_text=True)
    assert "Plus aucun rapprochement de participant ne bloque l'import" in page
    assert 'name="participants.0"' not in page
    # Un envoi du formulaire sans les champs masqués ne doit rien effacer.
    courant = json.loads((stage / "plan.json").read_text(encoding="utf-8"))
    assert historical_ui.client.post(url + "/decisions", data={"digest": courant["digest"]}).status_code == 302
    apres = json.loads((stage / "decisions.json").read_text(encoding="utf-8"))
    assert apres["participants"] == enregistrees["participants"]
    assert "TEST" in historical_ui.client.get(url + "?vue=regles").get_data(as_text=True)


def test_dossier_selection_is_paginated_and_bounded(app):
    from app.admin.historical_routes import DOSSIERS_PAR_PAGE, _selection_dossiers
    dossiers = [{"a_traiter": index % 2 == 0, "lignes": [{}],
                 "recherche": f"dossier {index} fin"} for index in range(500)]
    with app.test_request_context("/"):
        selection = _selection_dossiers(dossiers, "a-traiter", "", 1)
    assert len(selection["dossiers"]) == DOSSIERS_PAR_PAGE
    assert selection["retenus"] == 250 and selection["a_traiter"] == 250
    assert selection["pages"] == -(-250 // DOSSIERS_PAR_PAGE)
    # Une page hors bornes est ramenée dans l'intervalle, jamais vide par accident.
    assert _selection_dossiers(dossiers, "a-traiter", "", 999)["page"] == selection["pages"]
    assert _selection_dossiers(dossiers, "a-traiter", "", -3)["page"] == 1
    assert _selection_dossiers(dossiers, "regles", "", 1)["retenus"] == 250
    assert _selection_dossiers(dossiers, "tous", "", 1)["retenus"] == 500
    assert _selection_dossiers(dossiers, "tous", "dossier 42 fin", 1)["retenus"] == 1


def test_dossiers_survive_a_report_the_triage_cannot_read(app, caplog):
    from app.admin.historical_routes import _dossiers
    infirme = {"matching": {"rows": [{"key": "sheet:1", "classification": "REVIEW",
                                      "raw": {"nom": "TEST", "prenom": "Luc"}}]}}
    with app.test_request_context("/"):
        dossiers = _dossiers(infirme, {})
    assert [d["libelle"] for d in dossiers] == ["TEST Luc"]
    assert dossiers[0]["a_traiter"] is True


def test_territory_anomaly_is_written_in_words_not_dumped(historical_ui):
    url, _, _ = _stage(historical_ui)
    page = historical_ui.client.get(url).get_data(as_text=True)
    assert "Ville ou quartier inconnu du référentiel" in page
    assert "valeur <code>MOULIN</code>" in page  # libellé français, pas la clé « value »
    assert "ligne <code>sheet:5</code>" in page
    # Ni dictionnaire brut, ni libellé sans valeur.
    assert "&#39;code&#39;: &#39;unknown_territory&#39;" not in page
    assert "valeur lue <code></code>" not in page


def test_multi_line_dossier_offers_a_free_grouping_identifier(historical_ui):
    url, _, _ = _stage(historical_ui)
    page = historical_ui.client.get(url).get_data(as_text=True)
    assert "Créer / regrouper une nouvelle personne" in page
    assert "<code>test-luc-1980</code>" in page


def test_a_suggested_identifier_never_collides_with_a_saved_one(app):
    from app.admin.historical_routes import _dossiers
    rapport = {"parser": {"attendance": []}, "matching": {"rows": [
        {"key": "a!1", "classification": "REVIEW", "reasons": [], "candidates": [],
         "raw": {"nom": "TEST", "prenom": "Luc"},
         "normalized": {"nom": "test", "prenom": "luc", "birth_year": None, "birth_date": None,
                        "genre": None, "telephone": None, "email": None, "ville": None,
                        "quartier": None, "adresse": None}},
        {"key": "a!2", "classification": "REVIEW", "reasons": [], "candidates": [],
         "raw": {"nom": "TEST", "prenom": "Luc"},
         "normalized": {"nom": "test", "prenom": "luc", "birth_year": None, "birth_date": None,
                        "genre": None, "telephone": None, "email": None, "ville": None,
                        "quartier": None, "adresse": None}},
    ]}}
    with app.test_request_context("/"):
        libre = _dossiers(rapport, {})[0]["groupe_suggere"]
        occupe = _dossiers(rapport, {"participants": {"z!9": {"action": "new", "group": libre}}})
    assert libre == "test-luc-sans-annee"
    assert occupe[0]["groupe_suggere"] == "test-luc-sans-annee-2"


def test_apply_requires_ready_confirmation_and_current_digest(historical_ui):
    url, _, plan = _stage(historical_ui)
    assert historical_ui.client.post(url + "/apply", data={"digest": plan["digest"]}).status_code == 400
    assert historical_ui.client.post(url + "/apply", data={"digest": plan["digest"], "confirm": "yes"}).status_code == 409
    assert historical_ui.client.post(url + "/apply", data={"digest": "stale", "confirm": "yes"}).status_code == 409
    assert not historical_ui.calls["apply"]


def test_apply_uses_only_staged_values_and_blocks_second_submission(historical_ui):
    historical_ui.calls["ready"] = True
    url, _, plan = _stage(historical_ui)
    response = historical_ui.client.post(url + "/apply", data={
        "digest": plan["digest"], "confirm": "yes", "secteur": "FORGED", "source": "untrusted.xlsx", "decisions": '{"bad":true}',
    })
    assert response.status_code == 302
    assert len(historical_ui.calls["apply"]) == 1
    call = historical_ui.calls["apply"][0]
    assert "secteur" not in call
    assert call["decisions"] == {}
    assert call["actor_id"]
    assert call["expected_digest"] == plan["digest"]
    assert historical_ui.client.get(url).status_code == 200
    assert historical_ui.client.post(url + "/apply", data={"digest": plan["digest"], "confirm": "yes"}).status_code == 409
    assert len(historical_ui.calls["apply"]) == 1


def test_service_error_does_not_mark_stage_applied(historical_ui):
    historical_ui.calls.update(ready=True, apply_error=True)
    url, stage, plan = _stage(historical_ui)
    response = historical_ui.client.post(url + "/apply", data={"digest": plan["digest"], "confirm": "yes"})
    assert response.status_code == 409
    assert not json.loads((stage / "metadata.json").read_text(encoding="utf-8"))["applied"]
    assert not (stage / ".lock").exists()


def test_concurrent_stage_operations_are_blocked(historical_ui):
    url, stage, plan = _stage(historical_ui)
    (stage / ".lock").write_text("busy")
    assert historical_ui.client.post(url + "/decisions", data={"digest": plan["digest"]}).status_code == 409
    assert len(historical_ui.calls["analyze"]) == 1


def test_historical_post_requires_csrf(historical_ui, monkeypatch):
    monkeypatch.setitem(historical_ui.app.config, "WTF_CSRF_ENABLED", True)
    response = _upload(historical_ui)
    assert response.status_code == 400
    assert not historical_ui.calls["analyze"]


def test_historical_requires_import_permission(app):
    from app.extensions import db
    from app.models import User
    with app.app_context():
        user = User(email="no-import-permission@example.org", nom="Sans droit")
        user.set_password("not-used-in-test")
        db.session.add(user)
        db.session.commit()
        uid = user.id
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = str(uid)
        session["_fresh"] = True
    try:
        assert client.get("/admin/import-historical").status_code == 403
    finally:
        with app.app_context():
            db.session.delete(db.session.get(User, uid))
            db.session.commit()


def test_real_workbook_preview_and_large_decision_form(app, admin_client, monkeypatch, tmp_path):
    path = os.environ.get("HISTORICAL_XLSX_PATH")
    if not path:
        pytest.skip("Set HISTORICAL_XLSX_PATH for the real UI integration")
    monkeypatch.setattr(app, "instance_path", str(tmp_path))
    from app.models import Participant, PresenceActivite, HistoricalImportBatch
    with app.app_context():
        before = (Participant.query.count(), PresenceActivite.query.count(), HistoricalImportBatch.query.count())
    with open(path, "rb") as stream:
        response = admin_client.post("/admin/import-historical", data={"xlsx_file": (stream, Path(path).name)}, content_type="multipart/form-data")
    assert response.status_code == 302
    url = response.headers["Location"]
    preview = admin_client.get(url)
    assert preview.status_code == 200
    assert b'activities.65.secteur' in preview.data
    assert b'name="secteur"' not in preview.data
    report = admin_client.get(url + "/report.json").json
    assert report["summary"]["presences_detected"] == 7031
    fields = {"digest": report["digest"]}
    for index, activity in enumerate(report["activities"]):
        fields[f"activities.{index}"] = ""
        fields[f"activities.{index}.secteur"] = "Numérique"  # disposable preview only
    for index, row in enumerate(report["matching"]["rows"]):
        if row["classification"] == "REVIEW":
            fields[f"participants.{index}"] = ""
            fields[f"participants.{index}.group"] = ""
    assert len(fields) > 1000
    response = admin_client.post(url + "/decisions", data=fields)
    assert response.status_code == 302
    report = admin_client.get(url + "/report.json").json
    assert report["summary"]["activities_unassigned"] == 0
    assert report["summary"]["presences_detected"] == 7031
    with app.app_context():
        assert (Participant.query.count(), PresenceActivite.query.count(), HistoricalImportBatch.query.count()) == before
