"""Client de l'API RH de Recup et synchronisation idempotente dans l'ERP."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

from flask import current_app

from app.extensions import db
from app.models import RecupRhSnapshot, Salarie


class RecupRhError(RuntimeError):
    """Erreur reseau, HTTP ou de contrat provenant de Recup."""


def _config() -> tuple[str, str]:
    base = (current_app.config.get("RECUP_BASE_URL") or "").strip().rstrip("/")
    token = (current_app.config.get("RECUP_TOKEN") or "").strip()
    return base, token


def recup_configure() -> bool:
    base, token = _config()
    return bool(base and token)


def _request(path: str) -> dict:
    base, token = _config()
    if not (base and token):
        raise RecupRhError("Recup non configure (RECUP_BASE_URL / RECUP_TOKEN).")
    req = urllib.request.Request(
        base + path,
        method="GET",
        headers={"x-api-token": token, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RecupRhError(f"HTTP {exc.code} sur {path} : {detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RecupRhError(f"Erreur reseau vers Recup : {exc}") from exc
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RecupRhError(f"Reponse non-JSON de Recup sur {path}.") from exc
    if not isinstance(data, dict):
        raise RecupRhError("Contrat Recup invalide : objet JSON attendu.")
    return data


def health() -> bool:
    data = _request("/api/integration/ping")
    return bool(data.get("ok") and data.get("service") == "recup")


def recuperer_employees(year: int) -> dict:
    return _request("/api/integration/rh/employees?" + urllib.parse.urlencode({"year": int(year)}))


def _int(value, default=0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_datetime(value) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=None)
    except ValueError:
        return None


def _normalized(value: str | None) -> str:
    raw = unicodedata.normalize("NFKD", value or "")
    return " ".join("".join(c for c in raw if not unicodedata.combining(c)).lower().split())


def _find_employee(external_id: str, name: str) -> Salarie | None:
    employee = Salarie.query.filter_by(recup_user_id=external_id).first()
    if employee is not None:
        return employee
    employee = Salarie.query.filter_by(source_ref=external_id).first()
    if employee is not None:
        return employee

    wanted = _normalized(name)
    if not wanted:
        return None
    candidates = []
    for candidate in Salarie.query.filter(Salarie.recup_user_id.is_(None)).all():
        variants = {
            _normalized(f"{candidate.prenom or ''} {candidate.nom}"),
            _normalized(f"{candidate.nom} {candidate.prenom or ''}"),
        }
        if wanted in variants:
            candidates.append(candidate)
    return candidates[0] if len(candidates) == 1 else None


def synchroniser_rh(year: int) -> dict:
    """Upsert des salaries et de leur instantane annuel, sans ecraser le RH manuel."""
    year = int(year)
    data = recuperer_employees(year)
    employees = data.get("employees")
    if not isinstance(employees, list):
        raise RecupRhError("Contrat Recup invalide : liste employees absente.")
    response_year = _int(data.get("year"), year)
    if response_year != year:
        raise RecupRhError(f"Contrat Recup invalide : exercice {response_year} recu au lieu de {year}.")

    generated_at = _parse_datetime(data.get("generatedAt"))
    created = updated = ignored = 0
    seen: set[str] = set()
    try:
        for item in employees:
            if not isinstance(item, dict):
                ignored += 1
                continue
            external_id = str(item.get("externalId") or "").strip()
            name = str(item.get("name") or "").strip()
            if not external_id or not name or external_id in seen:
                ignored += 1
                continue
            seen.add(external_id)

            employee = _find_employee(external_id, name)
            is_new = employee is None
            if is_new:
                employee = Salarie(nom=name, type_contrat="autre", etp=0.0)
                db.session.add(employee)
                created += 1
            else:
                updated += 1
            employee.recup_user_id = external_id
            employee.recup_email = str(item.get("email") or "").strip() or None
            employee.recup_active = bool(item.get("active", True))
            if not employee.source_ref:
                employee.source_ref = external_id
            db.session.flush()

            snapshot = RecupRhSnapshot.query.filter_by(salarie_id=employee.id, year=year).first()
            if snapshot is None:
                snapshot = RecupRhSnapshot(salarie_id=employee.id, year=year)
                db.session.add(snapshot)

            recovery = item.get("recovery") if isinstance(item.get("recovery"), dict) else {}
            mileage = item.get("mileage") if isinstance(item.get("mileage"), dict) else {}
            salary = item.get("salary") if isinstance(item.get("salary"), dict) else None
            snapshot.balance_minutes = _int(recovery.get("balanceMinutes"))
            snapshot.overtime_minutes = _int(recovery.get("overtimeMinutes"))
            snapshot.approved_recovery_minutes = _int(recovery.get("approvedMinutes"))
            snapshot.pending_recovery_minutes = _int(recovery.get("pendingMinutes"))
            snapshot.mileage_expense_count = _int(mileage.get("expenseCount"))
            snapshot.mileage_distance_km = _int(mileage.get("distanceKm"))
            snapshot.mileage_amount_cents = _int(mileage.get("amountCents"))
            snapshot.hourly_unloaded_cents = _int(salary.get("hourlyUnloadedCents")) if salary else None
            snapshot.hourly_loaded_cents = _int(salary.get("hourlyLoadedCents")) if salary else None
            snapshot.worked_weeks_per_year = _int(salary.get("workedWeeksPerYear")) if salary else None
            snapshot.source_generated_at = generated_at
            snapshot.synced_at = datetime.now(timezone.utc).replace(tzinfo=None)

        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    return {
        "year": year,
        "created": created,
        "updated": updated,
        "ignored": ignored,
        "count": len(seen),
        "generated_at": generated_at,
    }
