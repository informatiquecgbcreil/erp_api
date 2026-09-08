"""Read-only XLSX staging for historical activity registers.

This module has no database dependency. Every source column remains a separate
session, and every attendance cell is retained even when its date needs review.
Excel cached totals are an independent check, never a claim of recalculation.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter
from openpyxl.utils.datetime import from_excel


PRESENCE_TOKENS = {"1", "x", "p", "present", "oui", "o", "true"}
ABSENCE_TOKENS = {"", "0", "absent", "a", "non", "n", "false"}
EXCLUDED_SHEETS = {"adultes": "technical", "adultes saveurs": "technical"}
WEEKDAYS = {
    "l": 0, "lu": 0, "lun": 0, "lundi": 0,
    "m": 1, "ma": 1, "mar": 1, "mardi": 1,
    "me": 2, "mer": 2, "mercredi": 2,
    "j": 3, "je": 3, "jeu": 3, "jeudi": 3,
    "v": 4, "ve": 4, "ven": 4, "vendredi": 4,
    "s": 5, "sa": 5, "sam": 5, "samedi": 5,
    "d": 6, "di": 6, "dim": 6, "dimanche": 6,
}
MONTHS = {
    "janvier": 1, "janv": 1, "jan": 1, "fevrier": 2, "fevr": 2, "fev": 2,
    "mars": 3, "avril": 4, "avr": 4, "mai": 5, "juin": 6,
    "juillet": 7, "juil": 7, "aout": 8, "septembre": 9, "sept": 9,
    "octobre": 10, "oct": 10, "novembre": 11, "nov": 11,
    "decembre": 12, "dec": 12,
}
FIELD_ALIASES = {
    "nom": {"nom", "noms"}, "prenom": {"prenom", "prenoms"},
    "annee_naissance": {"annee naissance", "annee de naissance", "annee", "naissance", "date naissance", "date de naissance", "ddn"},
    "sexe": {"sexe", "genre"}, "quartier": {"quartier", "quartiers"},
    "ville": {"ville", "commune", "ville commune"},
    "email": {"email", "e mail", "mail", "adresse mail", "adresse email"},
    "telephone": {"tel", "telephone", "telephone portable", "portable"},
    "activite": {"activite", "activites"}, "age": {"age"},
}


def normalize_label(value: Any) -> str:
    """Comparison form; source strings are always stored separately."""
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(re.sub(r"[^\w]+", " ", text.casefold()).split())


def _json(value):
    return value.isoformat() if isinstance(value, (date, datetime)) else value


def _date(value, epoch=None):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (float, int)) and not isinstance(value, bool) and 20000 <= value <= 100000:
        try:
            return from_excel(value, epoch=epoch).date()
        except (ValueError, OverflowError, AttributeError):
            return None
    if isinstance(value, str):
        for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y-%m-%d", "%d/%m/%y"):
            try:
                return datetime.strptime(value.strip(), fmt).date()
            except ValueError:
                pass
    return None


def _month(value, fallback_year, epoch):
    actual = _date(value, epoch)
    if actual:
        return actual.year, actual.month
    match = re.fullmatch(r"([a-z]+)\s*(\d{4})?", normalize_label(value))
    if match and match[1] in MONTHS and (match[2] or fallback_year):
        return int(match[2] or fallback_year), MONTHS[match[1]]
    return None


def _advance(year_month, delta=1):
    year, month = year_month
    offset = year * 12 + month - 1 + delta
    return offset // 12, offset % 12 + 1


def _presence(value):
    """Return True/False/None (unknown); no positive-number guessing."""
    if value is None or value is False:
        return False
    if value is True:
        return True
    if isinstance(value, (float, int)):
        return True if value == 1 else False if value == 0 else None
    token = normalize_label(value)
    return True if token in PRESENCE_TOKENS else False if token in ABSENCE_TOKENS else None


def _header(ws):
    for row in ws.iter_rows(min_row=1, max_row=min(ws.max_row, 40)):
        fields = {}
        totals = []
        for cell in row:
            token = normalize_label(cell.value)
            if token in {"total", "totaux", "total presences"}:
                totals.append(cell.column)
            for field, aliases in FIELD_ALIASES.items():
                if token in aliases:
                    fields.setdefault(field, cell.column)
        if "nom" in fields and "prenom" in fields:
            return row[0].row, fields, totals
    return None, {}, []


def _anomaly(code, message, sheet, cell=None, blocking=True, **extra):
    return {"code": code, "message": message, "source_sheet": sheet,
            "source_cell": cell, "blocking": blocking, **extra}


def _date_columns(ws, header_row, fields, totals):
    metadata = set(fields.values())
    columns = []
    last_metadata = max(metadata)
    stop = min((col for col in totals if col > last_metadata), default=ws.max_column + 1)
    for col in range(last_metadata + 1, stop):
        # A separator without either a header or any data is not a session.
        header_values = [ws.cell(row, col).value for row in range(max(1, header_row - 4), header_row + 1)]
        body_present = any(ws.cell(row, col).value is not None for row in range(header_row + 1, ws.max_row + 1))
        if any(value is not None for value in header_values) or body_present:
            columns.append(col)
    return columns


def _sessions(ws, header_row, columns, activity_key, year, epoch):
    sessions = []
    current_month = None
    previous_day = None
    previous_candidate = None
    anchor_cell = None
    for col in columns:
        cell = ws.cell(header_row, col)
        headers = {get_column_letter(col) + str(row): _json(ws.cell(row, col).value)
                   for row in range(max(1, header_row - 4), header_row + 1)}
        issues = []
        explicit_month = None
        for row in range(1, header_row):
            month = _month(ws.cell(row, col).value, year, epoch)
            if month:
                explicit_month = month
                anchor_cell = ws.cell(row, col).coordinate
                break
        if explicit_month:
            current_month, previous_day = explicit_month, None
        raw_weekday = ws.cell(header_row - 2, col).value if header_row > 2 else None
        weekday = WEEKDAYS.get(normalize_label(raw_weekday))
        raw_slot = ws.cell(header_row - 1, col).value if header_row > 1 else None
        # Only the dedicated row is interpreted as a slot; M and ME in the
        # weekday row must never become times of day.
        slot = str(raw_slot).strip() if raw_slot is not None and _month(raw_slot, year, epoch) is None else None
        candidate = _date(cell.value, epoch)
        day = None
        if candidate:
            if explicit_month and (candidate.year, candidate.month) != explicit_month:
                issues.append(_anomaly("date_month_conflict", "La date complète contredit le mois affiché.", ws.title, cell.coordinate))
            current_month = candidate.year, candidate.month
            day = candidate.day
        else:
            value = str(cell.value).strip() if cell.value is not None else ""
            if re.fullmatch(r"\d{1,2}(?:\.0+)?", value):
                day = int(float(value))
            if day is None or not 1 <= day <= 31:
                issues.append(_anomaly("invalid_day", "Numéro du jour absent, invalide ou ambigu.", ws.title, cell.coordinate, raw_value=_json(cell.value)))
            elif current_month is None:
                issues.append(_anomaly("missing_month", "Mois ou année introuvable pour le numéro du jour.", ws.title, cell.coordinate, raw_value=_json(cell.value)))
            else:
                if previous_day is not None and day < previous_day:
                    current_month = _advance(current_month)
                    issues.append(_anomaly("implicit_month_rollover", "Changement de mois déduit de l'ordre des jours.", ws.title, cell.coordinate, blocking=False))
                try:
                    candidate = date(*current_month, day)
                except ValueError:
                    issues.append(_anomaly("invalid_calendar_date", "Le jour n'existe pas dans le mois indiqué.", ws.title, cell.coordinate, month=list(current_month), raw_value=_json(cell.value)))
        # An invalid 31 April must not move every following April column to
        # May. A malformed date has no authority over chronological state.
        if candidate and day is not None and 1 <= day <= 31:
            previous_day = day
        alternatives = []
        if candidate and weekday is not None and candidate.weekday() != weekday:
            issues.append(_anomaly("weekday_mismatch", "Le jour de semaine contredit la date reconstruite.", ws.title, cell.coordinate,
                                   candidate_date=candidate.isoformat(), raw_weekday=_json(raw_weekday)))
        if candidate and previous_candidate and candidate < previous_candidate:
            issues.append(_anomaly("non_chronological_date", "La date revient avant la colonne précédente.", ws.title, cell.coordinate,
                                   candidate_date=candidate.isoformat(), previous_date=previous_candidate.isoformat()))
        if raw_weekday is not None and weekday is None and _month(raw_weekday, year, epoch) is None:
            issues.append(_anomaly("unknown_weekday", "Jour de semaine non reconnu.", ws.title, cell.coordinate, raw_weekday=_json(raw_weekday)))
        if any(issue["blocking"] for issue in issues) and day and 1 <= day <= 31 and current_month and weekday is not None:
            # Suggestions are restricted to neighbouring months and MUST be
            # validated. They do not silently repair a wrong Excel header.
            for delta in (-1, 0, 1):
                try:
                    alternative = date(*_advance(current_month, delta), day)
                except ValueError:
                    continue
                if alternative.weekday() == weekday:
                    alternatives.append(alternative.isoformat())
        if candidate and not any(issue["blocking"] for issue in issues):
            previous_candidate = candidate
        blocking = any(issue["blocking"] for issue in issues)
        sessions.append({
            "key": f"{ws.title}!{get_column_letter(col)}", "activity_key": activity_key,
            "date_session": candidate.isoformat() if candidate and not blocking else None,
            "candidate_date": candidate.isoformat() if candidate else None,
            "candidate_dates": alternatives, "source_slot": slot,
            "source_sheet": ws.title, "source_column": col, "source_cell": cell.coordinate,
            "month_anchor_cell": anchor_cell, "raw_headers": headers,
            "status": "REVIEW" if blocking or candidate is None else "READY", "anomalies": issues,
        })
    return sessions


def _numeric(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def parse_workbook(path, year=None):
    """Extract the complete workbook into a JSON-compatible, non-mutating plan.

    ``year`` supplies missing years only; it never overrides explicit dates.
    The caller handles identity matching, sector selection and all writes.
    """
    path = Path(path)
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    digest = hasher.hexdigest()
    wb = load_workbook(path, data_only=False, keep_links=False)
    cached = load_workbook(path, data_only=True, keep_links=False)
    try:
        years = Counter()
        for ws in wb:
            for row in ws.iter_rows(min_row=1, max_row=min(ws.max_row, 6)):
                for cell in row:
                    actual = _date(cell.value, wb.epoch)
                    if actual:
                        years[actual.year] += 1
        inferred = year or (years.most_common(1)[0][0] if years else None)
        if inferred is None:
            match = re.search(r"\b(20\d{2})\b", path.stem.replace("_", " "))
            inferred = int(match[1]) if match else None
        result = {"source": {"filename": path.name, "sha256": digest, "year": inferred},
                  "sheets": [], "activities": [], "sessions": [], "people": [], "attendance": [],
                  "anomalies": [], "errors": [], "counts": {},
                  "presence_tokens": sorted(PRESENCE_TOKENS), "absence_tokens": sorted(ABSENCE_TOKENS)}
        for ws in wb:
            sheet = {"name": ws.title, "classification": "unrecognized", "has_anomalies": False,
                     "activity_key": None, "rows": 0, "sessions": 0, "presences": 0,
                     "dimensions": {"rows": ws.max_row, "columns": ws.max_column},
                     "totals": None, "anomalies": [], "ignored_rows": [], "review_rows": []}
            result["sheets"].append(sheet)
            label = normalize_label(ws.title)
            if label == "total janv a dec 2026" or (
                re.match(r"^(total|totaux|synthese|recapitulatif|recap|bilan)( |$)", label)
                and _header(ws)[0] is None
            ):
                sheet.update(classification="summary", reason="Feuille de synthèse identifiée par son intitulé.")
                continue
            if label in EXCLUDED_SHEETS:
                sheet.update(classification=EXCLUDED_SHEETS[label], reason="Référentiel technique exclu des activités.")
                continue
            header_row, fields, total_columns = _header(ws)
            if header_row is None:
                nonempty = any(cell.value is not None for cell in ws._cells.values())
                sheet.update(classification="unrecognized" if nonempty else "technical",
                             reason="Aucun en-tête NOM / PRENOM reconnu." if nonempty else "Feuille vide.")
                if nonempty:
                    sheet["anomalies"].append(_anomaly("unrecognized_sheet", "Structure de feuille non reconnue : examen nécessaire.", ws.title))
                continue
            columns = _date_columns(ws, header_row, fields, total_columns)
            if not columns:
                sheet.update(classification="technical", reason="Répertoire de personnes sans colonnes de séances.")
                continue
            footer_rows = []
            source_rows = []
            for row in range(header_row + 1, ws.max_row + 1):
                raw = {field: _json(ws.cell(row, col).value) for field, col in fields.items() if field != "age"}
                first = normalize_label(raw.get("nom"))
                if re.match(r"^(total|totaux|nombre|nbre|taux|moyenne|duree|capacite)( |$)", first):
                    footer_rows.append(row)
                    sheet["ignored_rows"].append({"source_row": row, "reason": "footer"})
                    continue
                if first in FIELD_ALIASES["nom"] and normalize_label(raw.get("prenom")) in FIELD_ALIASES["prenom"]:
                    sheet["ignored_rows"].append({"source_row": row, "reason": "repeated_header"})
                    continue
                source_rows.append((row, raw))
            names = Counter(str(raw.get("activite")).strip() for _, raw in source_rows if raw.get("activite"))
            canonical = {normalize_label(name) for name in names}
            name = sorted(names, key=lambda value: (-len(value), value))[0] if len(canonical) == 1 else ws.title.strip()
            activity_key = ws.title
            sheet.update(classification="activity", activity_key=activity_key)
            activity = {"key": activity_key, "name": name, "source_sheet": ws.title,
                        "name_source": "ACTIVITE" if len(canonical) == 1 else "sheet",
                        "source_names": sorted(names), "status": "READY"}
            if len(canonical) > 1:
                activity["status"] = "REVIEW"
                sheet["anomalies"].append(_anomaly("multiple_activity_names", "Plusieurs noms d'activité dans la feuille.", ws.title,
                                                  candidate_names=sorted(names)))
            result["activities"].append(activity)
            sessions = _sessions(ws, header_row, columns, activity_key, inferred, wb.epoch)
            result["sessions"].extend(sessions)
            sheet["sessions"] = len(sessions)
            for session in sessions:
                sheet["anomalies"].extend(session["anomalies"])
            by_column = {session["source_column"]: session for session in sessions}
            column_counts = Counter()
            row_differences, uncached, row_checks = [], [], 0
            for row, raw in source_rows:
                attendance = []
                for col in columns:
                    cell = ws.cell(row, col)
                    value = cached[ws.title].cell(row, col).value if cell.data_type == "f" else cell.value
                    present = _presence(value)
                    if cell.data_type == "f" and value is None:
                        present = None
                    if present is None:
                        sheet["anomalies"].append(_anomaly("unknown_presence_token", "Valeur de présence non reconnue ; cellule conservée pour examen.", ws.title,
                                                          cell.coordinate, raw_value=_json(cell.value), cached_value=_json(value)))
                    if present:
                        attendance.append({"person_key": f"{ws.title}!{row}", "session_key": by_column[col]["key"],
                                           "source_sheet": ws.title, "source_row": row, "source_column": col,
                                           "source_cell": cell.coordinate, "raw_value": _json(cell.value)})
                        column_counts[col] += 1
                sheet["presences"] += len(attendance)
                for col in total_columns:
                    cell = ws.cell(row, col)
                    value = _numeric(cached[ws.title].cell(row, col).value)
                    if value is not None:
                        row_checks += 1
                        if value != len(attendance):
                            row_differences.append({"source_row": row, "source_cell": cell.coordinate,
                                                    "calculated": len(attendance), "excel_total": value})
                    elif cell.data_type == "f":
                        uncached.append(cell.coordinate)
                if not raw.get("nom") or not raw.get("prenom"):
                    reason = "missing_identity" if any(value for field, value in raw.items() if field != "activite") or attendance else "empty"
                    entry = {"source_row": row, "reason": reason, "raw": raw, "attendance": attendance}
                    if reason == "missing_identity":
                        sheet["review_rows"].append(entry)
                        sheet["anomalies"].append(_anomaly("missing_identity", "Nom ou prénom absent : ligne conservée sans création de personne.", ws.title,
                                                          f"A{row}", source_row=row, presence_count=len(attendance)))
                    else:
                        sheet["ignored_rows"].append(entry)
                        continue
                person = {"key": f"{ws.title}!{row}", "activity_key": activity_key,
                          "source_sheet": ws.title, "source_row": row, "raw": raw,
                          "field_cells": {field: ws.cell(row, col).coordinate for field, col in fields.items() if field != "age"}}
                result["people"].append(person)
                result["attendance"].extend(attendance)
                sheet["rows"] += 1
            controls, column_differences = [], []
            for row in footer_rows:
                if normalize_label(ws.cell(row, fields["nom"]).value) not in {"total", "totaux", "total presences"}:
                    continue
                for col in total_columns:
                    original = ws.cell(row, col)
                    value = _numeric(cached[ws.title].cell(row, col).value)
                    controls.append({"source_cell": original.coordinate, "formula": original.value if original.data_type == "f" else None,
                                     "excel_total": value, "cached": original.data_type == "f"})
                    if value is None and original.data_type == "f":
                        uncached.append(original.coordinate)
                for col in columns:
                    value = _numeric(cached[ws.title].cell(row, col).value)
                    if value is not None and value != column_counts[col]:
                        column_differences.append({"source_cell": ws.cell(row, col).coordinate,
                                                   "calculated": column_counts[col], "excel_total": value})
            announced = controls[0]["excel_total"] if len(controls) == 1 else None
            differences = row_differences or column_differences or (announced is not None and announced != sheet["presences"])
            impossible = any(a["code"] == "unknown_presence_token" for a in sheet["anomalies"])
            status = "uninterpretable" if impossible else "difference" if differences else "coherent" if announced is not None else "unavailable"
            sheet["totals"] = {"calculated": sheet["presences"], "excel_total": announced, "status": status,
                               "controls": controls, "rows_checked": row_checks, "row_differences": row_differences,
                               "column_differences": column_differences, "uncached_formula_cells": uncached,
                               "note": "Comparaison aux valeurs Excel enregistrées ; aucune formule n'a été recalculée."}
            if differences:
                sheet["anomalies"].append(_anomaly("total_mismatch", "Écart entre cellules de présence et totaux Excel enregistrés.", ws.title,
                                                  calculated=sheet["presences"], excel_total=announced))
        for sheet in result["sheets"]:
            sheet["has_anomalies"] = bool(sheet["anomalies"])
            result["anomalies"].extend(sheet["anomalies"])
        result["counts"] = {
            "sheets": len(result["sheets"]), "ignored_sheets": sum(s["classification"] != "activity" for s in result["sheets"]),
            "activities": len(result["activities"]), "sessions": len(result["sessions"]),
            "sessions_ready": sum(s["status"] == "READY" for s in result["sessions"]),
            "sessions_review": sum(s["status"] == "REVIEW" for s in result["sessions"]),
            "participants_source": len(result["people"]), "presences": len(result["attendance"]),
            "presence_cells": sum(s["presences"] for s in result["sheets"]),
            "ignored_rows": sum(len(s["ignored_rows"]) for s in result["sheets"]),
            "incomplete_identity_rows": sum(len(s["review_rows"]) for s in result["sheets"]),
            "date_anomalies": sum(len(s["anomalies"]) for s in result["sessions"]),
            "blocking_anomalies": sum(bool(a["blocking"]) for a in result["anomalies"]),
            "errors": len(result["errors"]),
        }
        return result
    finally:
        wb.close()
        cached.close()
