"""Intégration « Portail des apprenants » : client HTTP, code à l'inscription, synchro."""
import json

import pytest


def _faux_urlopen(reponse_dict, capture):
    """Remplace urllib.request.urlopen : capture la requête, renvoie un JSON."""

    class FauxResp:
        def read(self):
            return json.dumps(reponse_dict).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _open(req, timeout=None):
        capture["url"] = req.full_url
        capture["method"] = req.get_method()
        capture["headers"] = {k.lower(): v for k, v in req.header_items()}
        capture["body"] = req.data
        return FauxResp()

    return _open


def test_health_ok(app, monkeypatch):
    from app.services import portail_apprenants as svc

    cap = {}
    monkeypatch.setattr(svc.urllib.request, "urlopen", _faux_urlopen({"ok": True}, cap))
    with app.app_context():
        app.config.update(PORTAIL_BASE_URL="https://portail.test", PORTAIL_TOKEN="secret")
        assert svc.health() is True
    assert cap["url"].endswith("/api/health")
    assert cap["headers"]["x-api-token"] == "secret"


def test_creer_apprenant_envoie_external_id_et_token(app, monkeypatch):
    from app.services import portail_apprenants as svc

    cap = {}
    monkeypatch.setattr(
        svc.urllib.request, "urlopen", _faux_urlopen({"code": "ABC123", "created": True}, cap)
    )
    with app.app_context():
        app.config.update(PORTAIL_BASE_URL="https://portail.test", PORTAIL_TOKEN="secret")
        rep = svc.creer_apprenant(42)

    assert rep["code"] == "ABC123"
    assert cap["method"] == "POST"
    assert cap["url"].endswith("/api/integration/learners")
    assert json.loads(cap["body"].decode()) == {"externalId": "42"}
    assert cap["headers"]["x-api-token"] == "secret"


def test_assurer_code_stocke_et_idempotent(app, monkeypatch):
    from app.extensions import db
    from app.models import Participant
    from app.services import portail_apprenants as svc

    appels = {"n": 0}

    def faux_creer(external_id):
        appels["n"] += 1
        return {"code": "LRN-XYZ", "created": True}

    monkeypatch.setattr(svc, "creer_apprenant", faux_creer)
    with app.app_context():
        app.config.update(PORTAIL_BASE_URL="https://portail.test", PORTAIL_TOKEN="secret")
        p = Participant(nom="Test", prenom="Portail", type_public="H")
        db.session.add(p)
        db.session.commit()

        assert svc.assurer_code_portail(p) is True
        assert p.portail_code == "LRN-XYZ"
        # déjà un code -> idempotent, aucun nouvel appel
        assert svc.assurer_code_portail(p) is True
        assert appels["n"] == 1


def test_assurer_code_noop_si_non_configure(app):
    from app.extensions import db
    from app.models import Participant
    from app.services import portail_apprenants as svc

    with app.app_context():
        app.config.update(PORTAIL_BASE_URL="", PORTAIL_TOKEN="")
        p = Participant(nom="Non", prenom="Config", type_public="H")
        db.session.add(p)
        db.session.commit()
        assert svc.assurer_code_portail(p) is False
        assert p.portail_code is None


def test_assurer_code_ne_plante_pas_sur_erreur(app, monkeypatch):
    from app.extensions import db
    from app.models import Participant
    from app.services import portail_apprenants as svc

    def faux_creer(external_id):
        raise svc.PortailError("boom réseau")

    monkeypatch.setattr(svc, "creer_apprenant", faux_creer)
    with app.app_context():
        app.config.update(PORTAIL_BASE_URL="https://portail.test", PORTAIL_TOKEN="secret")
        p = Participant(nom="Erreur", prenom="Reseau", type_public="H")
        db.session.add(p)
        db.session.commit()
        assert svc.assurer_code_portail(p) is False  # pas d'exception
        assert p.portail_code is None


def test_synchroniser_upsert_dedup_et_curseur(app, monkeypatch):
    from app.extensions import db
    from app.models import Participant, PortailAttempt, PortailSyncState
    from app.services import portail_apprenants as svc

    with app.app_context():
        app.config.update(PORTAIL_BASE_URL="https://portail.test", PORTAIL_TOKEN="secret")
        # état de départ propre
        PortailAttempt.query.delete()
        etat0 = db.session.get(PortailSyncState, 1)
        if etat0:
            etat0.last_since = None
        db.session.commit()

        p = Participant(nom="Sync", prenom="Test", type_public="H")
        db.session.add(p)
        db.session.commit()
        pid = p.id

        lot1 = {
            "generatedAt": "2026-06-16T10:00:00Z",
            "attempts": [
                {"id": "att-1", "externalId": str(pid), "activity": "Quiz A",
                 "activityType": "quiz", "theme": "Maths", "score": 8, "maxScore": 10,
                 "pct": 80, "durationMs": 120000, "finishedAt": "2026-06-16T09:30:00Z"},
                {"id": "att-2", "externalId": None, "activity": "Anonyme"},  # ignoré
            ],
        }
        monkeypatch.setattr(svc, "recuperer_attempts", lambda since: lot1)
        r1 = svc.synchroniser_attempts()
        assert r1["nouvelles"] == 1
        assert r1["ignorees"] == 1
        att = PortailAttempt.query.filter_by(attempt_id="att-1").first()
        assert att is not None and att.participant_id == pid and att.theme == "Maths"
        assert db.session.get(PortailSyncState, 1).last_since == "2026-06-16T10:00:00Z"

        # 2e passage : since inclusive -> att-1 revient (mise à jour, pas doublon) + att-3
        capture = {}

        def lot2(since):
            capture["since"] = since
            return {
                "generatedAt": "2026-06-16T11:00:00Z",
                "attempts": [
                    {"id": "att-1", "externalId": str(pid), "score": 9, "maxScore": 10, "pct": 90},
                    {"id": "att-3", "externalId": str(pid), "activity": "Quiz B"},
                ],
            }

        monkeypatch.setattr(svc, "recuperer_attempts", lot2)
        r2 = svc.synchroniser_attempts()
        assert capture["since"] == "2026-06-16T10:00:00Z"  # renvoie le since mémorisé
        assert r2["nouvelles"] == 1  # seulement att-3
        assert PortailAttempt.query.filter_by(attempt_id="att-1").count() == 1  # pas de doublon
        assert PortailAttempt.query.filter_by(attempt_id="att-1").first().score == 9  # mis à jour
        assert db.session.get(PortailSyncState, 1).last_since == "2026-06-16T11:00:00Z"


def test_synchroniser_erreur_reseau_ne_perd_pas_le_curseur(app, monkeypatch):
    from app.extensions import db
    from app.models import PortailSyncState
    from app.services import portail_apprenants as svc

    def boom(since):
        raise svc.PortailError("réseau indisponible")

    with app.app_context():
        app.config.update(PORTAIL_BASE_URL="https://portail.test", PORTAIL_TOKEN="secret")
        etat = db.session.get(PortailSyncState, 1)
        if etat is None:
            etat = PortailSyncState(id=1)
            db.session.add(etat)
        etat.last_since = "2026-06-01T00:00:00Z"
        db.session.commit()

        monkeypatch.setattr(svc, "recuperer_attempts", boom)
        with pytest.raises(svc.PortailError):
            svc.synchroniser_attempts()

        assert db.session.get(PortailSyncState, 1).last_since == "2026-06-01T00:00:00Z"
