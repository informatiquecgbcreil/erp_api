"""Migration historique : analyse pure, décisions explicites et application atomique.

Une Session SQLAlchemy indépendante évite de committer les travaux de l'appelant.
L'analyse ne fait aucun INSERT/UPDATE et termine toujours par rollback.
"""
from __future__ import annotations

import copy
import hashlib
import json
import unicodedata
import re
import uuid
from collections import Counter, defaultdict
from datetime import date
from difflib import SequenceMatcher

from sqlalchemy import select, text
from sqlalchemy.orm import Session, joinedload

from app.extensions import db
from app.models import (
    Participant, Quartier, AtelierActivite, SessionActivite, PresenceActivite,
    HistoricalImportBatch, HistoricalImportSource, Secteur,
)
from app.ateliers.historical_parser import parse_workbook
from app.ateliers.historical_matching import resolve_people

VERSION = "historical-import-1"


class ImportBlocked(ValueError):
    def __init__(self, plan):
        self.plan = plan
        super().__init__(f"Import bloqué : {len(plan['blockers'])} points restent à valider.")


class PreviewStale(ValueError):
    pass


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _hash(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _norm(value):
    value = unicodedata.normalize("NFKD", str(value or "")).casefold()
    value = "".join(c for c in value if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def _snapshot(obj):
    return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}


def _load(session):
    models = (Participant, Quartier, AtelierActivite, SessionActivite, PresenceActivite,
              HistoricalImportBatch, HistoricalImportSource, Secteur)
    data = {}
    for model in models:
        stmt = select(model).order_by(model.id)
        if model is Participant:
            stmt = stmt.options(joinedload(Participant.quartier))
        data[model] = list(session.scalars(stmt))
    return data


def _decisions(value, parsed):
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError("Les décisions doivent être un objet JSON.")
    allowed = {"participants", "activities", "sessions", "acknowledged_anomalies"}
    if set(value) - allowed:
        raise ValueError("Section de décisions inconnue.")
    result = copy.deepcopy(value)
    for section, source in (("participants", "people"), ("activities", "activities"), ("sessions", "sessions")):
        result.setdefault(section, {})
        if not isinstance(result[section], dict):
            raise ValueError(f"Décisions {section} invalides.")
        keys = {r["key"] for r in parsed[source]}
        if set(result[section]) - keys:
            raise ValueError(f"Décision {section} sans source dans ce fichier.")
        if any(not isinstance(d, dict) for d in result[section].values()):
            raise ValueError(f"Décision {section} invalide.")
    result.setdefault("acknowledged_anomalies", [])
    if not isinstance(result["acknowledged_anomalies"], list) or any(
        not isinstance(x, str) for x in result["acknowledged_anomalies"]
    ):
        raise ValueError("Liste des anomalies acquittées invalide.")
    return result


def _plan(parsed, decisions, data):
    labels = {s.label for s in data[Secteur] if s.is_active}
    decisions = _decisions(decisions, parsed)
    blockers = []

    def block(kind, key, message):
        blockers.append({"kind": kind, "key": str(key), "message": message})

    file_hash = parsed["source"]["sha256"]
    batches = {b.id: b for b in data[HistoricalImportBatch] if b.file_hash == file_hash}
    previous = next(iter(batches.values()), None)
    links = {}
    for link in data[HistoricalImportSource]:
        if previous and link.batch_id == previous.id:
            links[(link.kind, link.source_key)] = link
    # Un fichier inchangé donne une preuve d'identité indépendante de son secteur.
    identity_links = defaultdict(set)
    for link in data[HistoricalImportSource]:
        if link.batch_id in batches and link.kind == "participant" and link.participant_id:
            identity_links[link.source_key].add(link.participant_id)
    effective = copy.deepcopy(decisions)
    pids = {p.id for p in data[Participant]}
    for key, ids in identity_links.items():
        if key in {r["key"] for r in parsed["people"]} and len(ids) == 1:
            pid = next(iter(ids))
            if pid in pids:
                effective["participants"].setdefault(key, {"action": "participant", "participant_id": pid})
    if previous:
        # Les choix d'un lot validé sont immuables. Une correction est une autre opération.
        saved = json.loads(previous.decisions_json)
        effective = copy.deepcopy(saved)
        for (kind, key), link in links.items():
            if kind == "participant":
                effective["participants"][key] = ({"action": "participant", "participant_id": link.participant_id}
                    if link.participant_id else {"action": "ignore"})
            elif kind == "activity":
                effective["activities"][key] = ({"action": "existing", "atelier_id": link.atelier_id}
                    if link.atelier_id else {"action": "ignore"})
            elif kind == "session":
                effective["sessions"][key] = ({"action": "existing", "session_id": link.session_id}
                    if link.session_id else {"action": "ignore"})
            expected = {"participant": "participant_id", "activity": "atelier_id", "session": "session_id", "presence": "presence_id"}[kind]
            was_linked = json.loads(link.decision_json).get("target_linked", link.created_target)
            if was_linked and not getattr(link, expected):
                block("provenance", key, "Une cible du lot a été supprimée. Réimport automatique interdit.")
        if any(decisions[k] and decisions[k] not in (saved.get(k, {}), effective.get(k, {})) for k in decisions):
            raise ValueError("Ce fichier a déjà été importé. Les décisions du lot sont immuables.")

    existing = []
    for p in data[Participant]:
        raw = {name: getattr(p, name) for name in (
            "nom", "prenom", "date_naissance", "annee_naissance", "telephone", "email", "ville", "genre", "adresse")}
        raw["quartier"] = p.quartier.nom if p.quartier else None
        existing.append({"id": p.id, "raw": raw})
    for person in parsed["people"]:
        if effective["activities"].get(person["activity_key"], {}).get("action") == "ignore":
            effective["participants"][person["key"]] = {"action": "ignore"}
    matching = resolve_people(parsed["people"], existing, decisions=effective["participants"])
    for row in matching["rows"]:
        if row.get("participant_id") in identity_links.get(row["key"], set()):
            row["original_classification"] = row["classification"]
            row["classification"] = "EXACT"
            row["reasons"] = ["saved_source_identity"]
    matching["counts"].update(Counter(r["classification"] for r in matching["rows"]))
    for category in ("EXACT", "HIGH_CONFIDENCE", "REVIEW", "NEW"):
        matching["counts"][category] = sum(r["classification"] == category for r in matching["rows"])
    for row in matching["rows"]:
        if row["classification"] == "REVIEW" and not row.get("resolved") and not row.get("group_key"):
            block("participant", row["key"], "Correspondance participant à valider.")

    atelier_by_id = {a.id: a for a in data[AtelierActivite] if not a.is_deleted}
    name_index = defaultdict(list)
    for a in atelier_by_id.values():
        name_index[_norm(a.nom)].append(a)
    activities = []
    for source in parsed["activities"]:
        row = copy.deepcopy(source)
        key = row["key"]
        decision = effective["activities"].get(key, {})
        if decision.get("name"):
            if not isinstance(decision["name"], str) or not decision["name"].strip() or len(decision["name"].strip()) > 200:
                raise ValueError("Le nom métier validé doit contenir entre 1 et 200 caractères.")
            row["name"] = decision["name"].strip()
        secteur = decision.get("secteur") or None
        if secteur and secteur not in labels:
            raise ValueError("Secteur d'activité absent ou inactif dans le référentiel.")
        exact = [a for a in name_index[_norm(row["name"])] if a.secteur == secteur]
        candidates = [a for a in atelier_by_id.values() if (not secteur or a.secteur == secteur) and (a in exact or
            SequenceMatcher(None, _norm(a.nom), _norm(row["name"])).ratio() >= .83 or
            (len(_norm(a.nom)) >= 20 and _norm(row["name"]).startswith(_norm(a.nom))))]
        row.update(atelier_id=None, secteur=secteur, status="NEW", group_key=f"new:{secteur}:" + _norm(row["name"]),
                   candidates=[{"id": a.id, "name": a.nom, "secteur": a.secteur} for a in candidates])
        action = decision.get("action")
        if action == "ignore":
            row["status"] = "IGNORE"
        elif action == "existing":
            aid = decision.get("atelier_id")
            if type(aid) is not int or aid not in atelier_by_id:
                raise ValueError("Activité cible absente ou supprimée.")
            target_sector = atelier_by_id[aid].secteur
            if secteur and secteur != target_sector:
                raise ValueError("Le secteur sélectionné contredit celui de l'activité existante. Aucun déplacement automatique.")
            if target_sector not in labels:
                raise ValueError("Le secteur de l'activité existante est inactif.")
            row.update(status="EXACT", atelier_id=aid, group_key=f"existing:{aid}", secteur=target_sector)
        elif action == "new":
            pass
        elif action is not None:
            raise ValueError("Action activité inconnue.")
        elif len(exact) == 1:
            row.update(status="EXACT", atelier_id=exact[0].id, group_key=f"existing:{exact[0].id}")
        elif candidates:
            row["status"] = "REVIEW"
        if source.get("status") == "REVIEW" and action not in ("new", "existing", "ignore"):
            row["status"] = "REVIEW"
        if len(row["name"]) > 200:
            row["status"] = "REVIEW"
            block("activity", key, "Nom trop long : préciser un nom métier de 200 caractères maximum.")
        row["match_status"] = row["status"]
        if row["status"] != "IGNORE" and not row["secteur"]:
            row["status"] = "REVIEW"
            block("sector", key, "Affecter cette activité à un secteur après analyse.")
        elif row["status"] == "REVIEW":
            block("activity", key, "Correspondance activité à valider.")
        activities.append(row)
    # Variantes proches entre feuilles : elles sont proposées, pas créées en double silencieusement.
    for row in activities:
        if row["status"] != "NEW" or effective["activities"].get(row["key"], {}).get("action") == "new":
            continue
        similar = [r for r in activities if r["status"] != "IGNORE" and r["secteur"] == row["secteur"] and r["group_key"] != row["group_key"] and
                   SequenceMatcher(None, _norm(r["name"]), _norm(row["name"])).ratio() >= .9]
        if similar:
            row.update(status="REVIEW", match_status="REVIEW")
            names = ", ".join(r["name"] for r in similar)
            block("activity", row["key"], f"Noms proches dans le classeur ({names}) : confirmer la création distincte ou valider un nom métier commun.")

    amap = {a["key"]: a for a in activities}
    session_by_id = {s.id: s for s in data[SessionActivite] if not s.is_deleted}
    by_activity_date = defaultdict(list)
    for s in session_by_id.values():
        by_activity_date[(s.atelier_id, str(s.date_session))].append(s)
    sessions = []
    used_existing = {}
    for source in parsed["sessions"]:
        row = copy.deepcopy(source)
        key = row["key"]
        a = amap[row["activity_key"]]
        decision = effective["sessions"].get(key, {})
        action = decision.get("action")
        row.update(session_id=None, candidates=[], secteur=a["secteur"])
        if a["status"] == "IGNORE" or action == "ignore":
            row["status"] = "IGNORE"
        elif action == "existing":
            sid = decision.get("session_id")
            target = session_by_id.get(sid) if type(sid) is int else None
            if (not target or target.atelier_id != a["atelier_id"] or target.secteur != a["secteur"]
                    or target.session_type != "COLLECTIF" or target.date_session is None):
                raise ValueError("Séance cible absente ou incompatible avec l'activité et le secteur.")
            if target.id in used_existing:
                raise ValueError("Deux colonnes source distinctes ne peuvent pas partager une séance cible.")
            used_existing[target.id] = key
            row.update(session_id=target.id, date_session=str(target.date_session), status="READY")
        elif action in (None, "date", "new"):
            if decision.get("date_session"):
                try:
                    corrected = date.fromisoformat(decision["date_session"])
                except (ValueError, TypeError):
                    raise ValueError("Date corrigée invalide (AAAA-MM-JJ attendu).") from None
                row.update(date_session=corrected.isoformat(), status="READY")
            elif action == "date":
                raise ValueError("Une correction doit préciser la date.")
            candidates = [s for s in by_activity_date[(a["atelier_id"], row.get("date_session"))]
                          if s.secteur == a["secteur"] and s.session_type == "COLLECTIF" and s.date_session is not None]
            row["candidates"] = [{"id": s.id, "date_session": str(s.date_session),
                                   "source_slot": s.creneau_source, "heure_debut": s.heure_debut,
                                   "heure_fin": s.heure_fin} for s in candidates]
            if candidates and action != "new":
                row["status"] = "REVIEW"
        else:
            raise ValueError("Action séance inconnue.")
        if row["status"] not in ("READY", "IGNORE") or (row["status"] == "READY" and not row.get("date_session")):
            row["status"] = "REVIEW"
            block("session", key, "Date ou rapprochement de séance à valider.")
        sessions.append(row)

    anomalies = []
    for original in list(parsed.get("anomalies", [])) + list(matching.get("territory_anomalies", [])):
        item = copy.deepcopy(original) if isinstance(original, dict) else {"message": str(original)}
        item["id"] = _hash(original)[:24]
        item["acknowledged"] = item["id"] in effective["acknowledged_anomalies"]
        anomalies.append(item)
        if item.get("blocking", True) and not item["acknowledged"]:
            block("anomaly", item["id"], item.get("message") or item.get("reason") or "Anomalie source à examiner.")
    for i, error in enumerate(parsed.get("errors", [])):
        block("error", i, str(error))

    rmap = {r["key"]: r for r in matching["rows"]}
    smap = {s["key"]: s for s in sessions}
    existing_pairs = {(p.session_id, p.participant_id) for p in data[PresenceActivite]}
    unique_pairs = set()
    duplicates = already = pending = ignored = 0
    for cell in parsed["attendance"]:
        p, s = rmap[cell["person_key"]], smap[cell["session_key"]]
        if s["status"] == "IGNORE" or effective["participants"].get(p["key"], {}).get("action") == "ignore":
            ignored += 1
            continue
        if not p.get("group_key") or s["status"] != "READY":
            pending += 1
            continue
        pair = (s["session_id"] or s["key"], p["group_key"])
        if pair in unique_pairs:
            duplicates += 1
            continue
        unique_pairs.add(pair)
        if s["session_id"] and p.get("participant_id") and (s["session_id"], p["participant_id"]) in existing_pairs:
            already += 1
    counts = matching["counts"]
    summary = {
        "sheets": len(parsed["sheets"]),
        "ignored_sheets": [s["name"] for s in parsed["sheets"] if s["classification"] != "activity"],
        "activities_recognized": len(activities),
        "activities_unassigned": sum(not r["secteur"] and r["status"] != "IGNORE" for r in activities),
        "activities_existing": len({r["atelier_id"] for r in activities if r["atelier_id"]}),
        "activities_new": len({r["group_key"] for r in activities if r["match_status"] == "NEW"}),
        "sessions_detected": len(sessions), "sessions_review": sum(s["status"] == "REVIEW" for s in sessions),
        "participants_source": len(parsed["people"]),
        "participants_exact": counts.get("EXACT", 0), "participants_high_confidence": counts.get("HIGH_CONFIDENCE", 0),
        "participants_review": counts.get("REVIEW", 0),
        "participants_new": sum(not g.get("participant_id") for g in matching["groups"]),
        "presences_detected": len(parsed["attendance"]), "presences_existing": already,
        "presences_new": len(unique_pairs) - already, "presences_pending": pending,
        "presences_ignored": ignored, "internal_duplicate_presences": duplicates,
        "internal_duplicate_people": counts.get("internal_duplicates", 0),
        "territory_anomalies": len(matching.get("territory_anomalies", [])),
        "date_anomalies": sum(sum(a.get("blocking", True) for a in s.get("anomalies", [])) for s in parsed["sessions"]),
        "ignored_rows": sum(len(s.get("ignored_rows", [])) for s in parsed["sheets"]),
        "errors": len(parsed.get("errors", [])), "blockers": len(blockers),
    }
    result = dict(version=VERSION, source=parsed["source"], secteurs=sorted({a["secteur"] for a in activities if a["secteur"]}), parser=parsed,
                  matching=matching, activities=activities, sessions=sessions, anomalies=anomalies,
                  summary=summary, blockers=blockers, ready=not blockers,
                  decisions=effective, already_imported=previous.id if previous else None)
    result["database_digest"] = _hash({m.__tablename__: [_snapshot(o) for o in rows] for m, rows in data.items()})
    result["digest"] = _hash(result)
    return result


def analyze_import(path, decisions=None, year=None):
    """Analyse intégrale, zéro écriture métier ou staging en base, rollback garanti."""
    parsed = parse_workbook(path, year=year)
    with Session(db.engine, autoflush=False) as session:
        try:
            return _plan(parsed, decisions, _load(session))
        finally:
            session.rollback()


def _provenance(session, batch, kind, source, **values):
    expected = {"participant": "participant_id", "activity": "atelier_id", "session": "session_id", "presence": "presence_id"}[kind]
    decision = dict(values.pop("decision", {}), target_linked=bool(values.get(expected)))
    session.add(HistoricalImportSource(
        batch_id=batch.id, kind=kind, source_key=source["key"],
        source_sheet=source.get("source_sheet"), source_row=source.get("source_row"),
        source_column=source.get("source_column"), source_cell=source.get("source_cell"),
        raw_json=_json(source), decision_json=_json(decision), **values))


def _write(session, plan, actor_id=None):
    """Unique point d'écriture ; toutes les relations sont créées dans la transaction appelante."""
    batch = HistoricalImportBatch(id=str(uuid.uuid4()), file_hash=plan["source"]["sha256"],
        filename=plan["source"]["filename"], secteurs_json=_json(plan["secteurs"]), actor_id=actor_id,
        plan_digest=plan["digest"], decisions_json=_json(plan["decisions"]), summary_json=_json(plan["summary"]))
    session.add(batch)
    session.flush()
    created = Counter()
    people = {}
    amap = {a["key"]: a for a in plan["activities"]}
    source_sectors = {p["key"]: amap[p["activity_key"]]["secteur"] for p in plan["parser"]["people"]}
    quartiers = {(_norm(q.ville), _norm(q.nom)): q for q in session.scalars(select(Quartier))}
    for group in plan["matching"]["groups"]:
        pid = group.get("participant_id")
        if pid:
            people[group["key"]] = (pid, False)
            continue
        v = group["values"]
        qid = None
        if v.get("quartier") and _norm(v.get("ville")) == "creil":
            qkey = ("creil", _norm(v["quartier"]))
            q = quartiers.get(qkey)
            if not q:
                # La provenance territoriale de ce quartier est portée par les lignes participant.
                q = Quartier(ville="Creil", nom=v["quartier"], is_qpv=False)
                session.add(q)
                session.flush()
                quartiers[qkey] = q
                created["quartiers"] += 1
            qid = q.id
        birthday = date.fromisoformat(str(v["date_naissance"])) if v.get("date_naissance") else None
        person_sectors = {source_sectors[k] for k in group["source_keys"] if source_sectors[k]}
        p = Participant(nom=v["nom"], prenom=v["prenom"], date_naissance=birthday,
            annee_naissance=v.get("annee_naissance"), ville=v.get("ville"), quartier_id=qid,
            telephone=v.get("telephone"), email=v.get("email"), genre=v.get("genre"), adresse=v.get("adresse"),
            created_secteur=next(iter(person_sectors)) if len(person_sectors) == 1 else None, created_by_user_id=actor_id)
        session.add(p)
        session.flush()
        people[group["key"]] = (p.id, True)
        created["participants"] += 1
    rows = {r["key"]: r for r in plan["matching"]["rows"]}
    person_ids = {}
    for source in plan["parser"]["people"]:
        row = rows[source["key"]]
        pid, is_new = people.get(row.get("group_key"), (None, False))
        person_ids[source["key"]] = pid
        _provenance(session, batch, "participant", source, participant_id=pid, created_target=is_new,
                    decision=plan["decisions"]["participants"].get(source["key"], {"classification": row["classification"]}))
    ateliers = {}
    activity_ids = {}
    for source in plan["activities"]:
        aid = source["atelier_id"]
        is_new = False
        if source["status"] != "IGNORE" and not aid:
            if source["group_key"] not in ateliers:
                a = AtelierActivite(nom=source["name"], secteur=source["secteur"], type_atelier="COLLECTIF")
                session.add(a)
                session.flush()
                ateliers[source["group_key"]] = a.id
                created["activities"] += 1
            aid, is_new = ateliers[source["group_key"]], True
        activity_ids[source["key"]] = aid
        _provenance(session, batch, "activity", source, atelier_id=aid, created_target=is_new,
                    decision=plan["decisions"]["activities"].get(source["key"], {}))
    session_ids = {}
    for source in plan["sessions"]:
        sid, is_new = source["session_id"], False
        if source["status"] != "IGNORE" and not sid:
            s = SessionActivite(atelier_id=activity_ids[source["activity_key"]], secteur=source["secteur"],
                session_type="COLLECTIF", date_session=date.fromisoformat(source["date_session"]),
                creneau_source=source.get("source_slot") or None, statut="realisee")
            session.add(s)
            session.flush()
            sid, is_new = s.id, True
            created["sessions"] += 1
        session_ids[source["key"]] = sid
        _provenance(session, batch, "session", source, session_id=sid,
                    atelier_id=activity_ids[source["activity_key"]], created_target=is_new,
                    decision=plan["decisions"]["sessions"].get(source["key"], {}))
    presences = {(p.session_id, p.participant_id): p.id for p in session.scalars(select(PresenceActivite))}
    new_pairs = set()
    for cell in plan["parser"]["attendance"]:
        source = dict(cell, key=cell["person_key"] + ":" + str(cell["source_column"]))
        sid, pid = session_ids[cell["session_key"]], person_ids[cell["person_key"]]
        prid = None
        if sid and pid:
            pair = (sid, pid)
            prid = presences.get(pair)
            if not prid:
                p = PresenceActivite(session_id=sid, participant_id=pid)
                session.add(p)
                session.flush()
                prid = p.id
                presences[pair] = prid
                new_pairs.add(pair)
                created["presences"] += 1
        _provenance(session, batch, "presence", source, participant_id=pid, session_id=sid,
                    presence_id=prid, created_target=(sid, pid) in new_pairs,
                    decision={"action": "linked" if prid else "ignored"})
    session.flush()
    return {"batch_id": batch.id, "created": dict(created), "summary": plan["summary"], "already_imported": False}


def apply_import(path, decisions=None, year=None, expected_digest=None, actor_id=None):
    """Recalcule le plan sous transaction, refuse tout aperçu périmé, puis commit unique."""
    if not expected_digest:
        raise ValueError("Un dry-run et son empreinte de prévisualisation sont obligatoires.")
    parsed = parse_workbook(path, year=year)
    with Session(db.engine, autoflush=False) as session:
        try:
            dialect = db.engine.dialect.name
            if dialect == "sqlite":
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            elif dialect == "postgresql":
                session.execute(text("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE"))
                session.execute(text("SELECT pg_advisory_xact_lock(728419026)"))
            else:
                raise ValueError("Migration validée uniquement pour SQLite et PostgreSQL.")
            plan = _plan(parsed, decisions, _load(session))
            if plan["digest"] != expected_digest:
                raise PreviewStale("Le fichier, les décisions ou la base ont changé. Relancer le dry-run.")
            if not plan["ready"]:
                raise ImportBlocked(plan)
            if plan["already_imported"]:
                session.rollback()
                return {"batch_id": plan["already_imported"], "created": {}, "summary": plan["summary"], "already_imported": True}
            result = _write(session, plan, actor_id)
            session.commit()
            return result
        except BaseException:
            session.rollback()
            raise


def verify_batch(batch_id):
    """Contrôle les références et retourne les cellules dont une cible n'existe plus."""
    with Session(db.engine) as session:
        try:
            batch = session.get(HistoricalImportBatch, batch_id)
            if not batch:
                raise ValueError("Lot introuvable.")
            missing = []
            refs = list(session.scalars(select(HistoricalImportSource).where(HistoricalImportSource.batch_id == batch_id)))
            targets = {name: {v for v in session.scalars(select(model.id))} for name, model in
                       (("participant_id", Participant), ("atelier_id", AtelierActivite),
                        ("session_id", SessionActivite), ("presence_id", PresenceActivite))}
            for row in refs:
                for name, ids in targets.items():
                    value = getattr(row, name)
                    if value and value not in ids:
                        missing.append({"source_key": row.source_key, "target": name, "id": value})
                expected = {"participant": "participant_id", "activity": "atelier_id", "session": "session_id", "presence": "presence_id"}[row.kind]
                if json.loads(row.decision_json).get("target_linked", row.created_target) and not getattr(row, expected):
                    missing.append({"source_key": row.source_key, "target": expected, "id": None})
            return {"batch_id": batch.id, "source": batch.filename, "secteurs": json.loads(batch.secteurs_json),
                    "source_records": len(refs), "missing_targets": missing, "ok": not missing,
                    "summary": json.loads(batch.summary_json)}
        finally:
            session.rollback()
