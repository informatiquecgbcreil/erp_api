"""Synchronisation Récup vers le module RH de l'ERP."""
import json


def _fake_urlopen(response_dict, capture):
    class FakeResponse:
        def read(self):
            return json.dumps(response_dict).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def open_(request, timeout=None):
        capture["url"] = request.full_url
        capture["headers"] = {key.lower(): value for key, value in request.header_items()}
        return FakeResponse()

    return open_


def _payload():
    return {
        "year": 2026,
        "generatedAt": "2026-07-13T14:00:00Z",
        "count": 1,
        "employees": [
            {
                "externalId": "recup:test-991",
                "name": "Camille Synchronisation",
                "email": "camille-sync@example.org",
                "active": True,
                "recovery": {
                    "balanceMinutes": 210,
                    "overtimeMinutes": 480,
                    "approvedMinutes": 270,
                    "pendingMinutes": 60,
                },
                "mileage": {"expenseCount": 2, "distanceKm": 83, "amountCents": 4391},
                "salary": {
                    "hourlyUnloadedCents": 1450,
                    "hourlyLoadedCents": 2180,
                    "workedWeeksPerYear": 46,
                },
            }
        ],
    }


def test_client_uses_token_and_year(app, monkeypatch):
    from app.services import recup_rh as service

    capture = {}
    monkeypatch.setattr(service.urllib.request, "urlopen", _fake_urlopen(_payload(), capture))
    with app.app_context():
        app.config.update(RECUP_BASE_URL="https://recup.test", RECUP_TOKEN="secret")
        data = service.recuperer_employees(2026)
    assert data["count"] == 1
    assert capture["url"].endswith("/api/integration/rh/employees?year=2026")
    assert capture["headers"]["x-api-token"] == "secret"


def test_sync_is_idempotent_and_preserves_manual_fields(app, monkeypatch):
    from app.extensions import db
    from app.models import RecupRhSnapshot, Salarie
    from app.services import recup_rh as service

    monkeypatch.setattr(service, "recuperer_employees", lambda year: _payload())
    with app.app_context():
        app.config.update(RECUP_BASE_URL="https://recup.test", RECUP_TOKEN="secret")
        Salarie.query.filter_by(recup_user_id="recup:test-991").delete()
        db.session.commit()

        first = service.synchroniser_rh(2026)
        assert first["created"] == 1
        employee = Salarie.query.filter_by(recup_user_id="recup:test-991").one()
        assert employee.nom == "Camille Synchronisation"
        assert employee.etp == 0
        employee.poste = "Animatrice"
        employee.etp = 0.8
        db.session.commit()

        second = service.synchroniser_rh(2026)
        assert second["created"] == 0
        assert second["updated"] == 1
        employee = Salarie.query.filter_by(recup_user_id="recup:test-991").one()
        assert employee.poste == "Animatrice"
        assert employee.etp == 0.8
        assert RecupRhSnapshot.query.filter_by(salarie_id=employee.id, year=2026).count() == 1
        snapshot = RecupRhSnapshot.query.filter_by(salarie_id=employee.id, year=2026).one()
        assert snapshot.balance_minutes == 210
        assert snapshot.mileage_amount_cents == 4391
        assert snapshot.annual_loaded_cost_eur(employee.etp) == 28078.4

        db.session.delete(employee)
        db.session.commit()
