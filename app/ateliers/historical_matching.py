"""Pure, conservative global identity resolution for historical migrations.

No query, persistence, fuzzy automatic merge on names alone, or mutation of
source dictionaries happens here. See docs/historical-import.md for the
contract and deliberately strict matching rules.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from datetime import date, datetime
import re

from app.services.import_participants import nettoyer_affichage, normaliser, normaliser_sexe


RULES_VERSION = "historical-identity-v1"
RULES = {
    "EXACT": "Noms normalisés identiques, date complète et contact fiables concordants ; aucun concurrent ni contradiction.",
    "HIGH_CONFIDENCE": "Noms identiques/proches, année et contact communs ; ou noms identiques et deux contacts communs. Aucun concurrent ni contradiction.",
    "REVIEW": "Homonymie sans preuve forte, contradiction, candidat concurrent, chaîne non complètement concordante ou identité invalide.",
    "NEW": "Aucun candidat crédible ; ou représentant d'un groupe nouveau fortement concordant.",
}

_DISTRICTS = {
    normaliser(k): v for k, v in {
        "ROUHER": "Rouher", "CAVEE": "Cavée", "BAS DE CREIL": "Bas de Creil",
        "HAUTS DE CREIL": "Hauts de Creil", "CAVEE DE PARIS": "Cavée de Paris",
        "CAVEE DE SENLIS": "Cavée de Senlis",
    }.items()
}
_CITIES = {
    normaliser(k): v for k, v in {
        "CREIL": "Creil", "NOGENT SUR OISE": "Nogent-sur-Oise",
        "MONTATAIRE": "Montataire", "PONT STE MAXENCE": "Pont-Sainte-Maxence",
        "PONT SAINTE MAXENCE": "Pont-Sainte-Maxence", "CLERMONT": "Clermont",
        "CLERMONT DE L OISE": "Clermont", "CAUFFRY": "Cauffry", "LIANCOURT": "Liancourt",
        "MONCHY ST ELOI": "Monchy-Saint-Éloi", "MONCHY SAINT ELOI": "Monchy-Saint-Éloi",
        "VILLERS ST PAUL": "Villers-Saint-Paul", "VILLERS SAINT PAUL": "Villers-Saint-Paul",
        "MOGNEVILLE": "Mogneville", "VILLERS SOUS ST LEU": "Villers-sous-Saint-Leu",
        "VILLERS SOUS SAINT LEU": "Villers-sous-Saint-Leu", "VERNEUIL EN HALATTE": "Verneuil-en-Halatte",
        "COYE LA FORET": "Coye-la-Forêt", "SENLIS": "Senlis", "BERTHECOURT": "Berthecourt",
        "MOUY": "Mouy", "GOUVIEUX": "Gouvieux",
    }.items()
}
_MISSING = {"", "inconnu", "inconnue", "nr", "nc", "na", "nonrenseigne", "nonrenseignee"}


def normalize_territory(ville=None, quartier=None):
    """Recognise a finite reviewed vocabulary; never assume a missing city.

    Unknown values remain available in ``raw`` and anomalies. An explicitly
    labelled unknown city can be retained, but never inferred from QUARTIER.
    """
    raw_city, raw_district = nettoyer_affichage(ville), nettoyer_affichage(quartier)
    cv, cq = normaliser(raw_city), normaliser(raw_district)
    cv = "" if cv in _MISSING else cv
    cq = "" if cq in _MISSING else cq
    city = _CITIES.get(cv, raw_city if cv else None)
    district = None
    anomalies = []
    if cv and cv not in _CITIES:
        anomalies.append({"code": "unknown_city", "value": raw_city})
    if cq in _DISTRICTS:
        if cv and normaliser(city) != "creil":
            anomalies.append({"code": "city_district_conflict", "ville": raw_city, "quartier": raw_district})
        else:
            city, district = "Creil", _DISTRICTS[cq]
    elif cq in _CITIES:
        proposed = _CITIES[cq]
        if cv and normaliser(city) != normaliser(proposed):
            # A known external commune in QUARTIER is never attached to Creil.
            anomalies.append({"code": "city_district_conflict", "ville": raw_city, "quartier": raw_district})
        else:
            city = proposed
    elif cq:
        anomalies.append({"code": "unknown_territory", "value": raw_district})
    return {"ville": city, "quartier": district, "anomalies": anomalies,
            "raw": {"ville": ville, "quartier": quartier}}


def normalize_phone(value):
    raw = nettoyer_affichage(value)
    if not raw:
        return None
    # Excel sometimes stores a telephone as an integer: accept a 9-digit
    # French national number with its leading zero lost, but not decimals.
    if re.search(r"[A-Za-z]", raw):
        return None
    if re.fullmatch(r"\d+\.0", raw):
        raw = raw[:-2]
    digits = re.sub(r"\D", "", raw)
    if digits.startswith("00"):
        digits = digits[2:]
    elif len(digits) == 10 and digits.startswith("0"):
        digits = "33" + digits[1:]
    elif len(digits) == 9 and digits[0] in "123456789":
        digits = "33" + digits
    return "+" + digits if 8 <= len(digits) <= 15 and not digits.startswith("0") else None


def normalize_email(value):
    value = nettoyer_affichage(value).casefold()
    return value if re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value) else None


def _first(raw, *keys):
    return next((raw[k] for k in keys if raw.get(k) is not None and raw.get(k) != ""), None)


def _birth(raw):
    full = _first(raw, "date_naissance", "birth_date", "birth", "ddn")
    year_value = _first(raw, "annee_naissance", "birth_year", "annee", "year")
    parsed = None
    invalid = []
    if isinstance(full, datetime):
        parsed = full.date()
    elif isinstance(full, date):
        parsed = full
    elif full is not None:
        value = str(full).strip()
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y"):
            try:
                parsed = datetime.strptime(value, fmt).date()
                break
            except ValueError:
                pass
        if parsed is None:
            if re.fullmatch(r"\d{4}(?:\.0)?", value) and year_value is None:
                year_value = full
            elif normaliser(full) not in _MISSING:
                invalid.append("invalid_birth_date")
    # Some workbooks label the full date column ANNEE.
    if isinstance(year_value, str):
        if "T" in year_value:
            try:
                year_value = datetime.fromisoformat(year_value).date()
            except ValueError:
                pass
    if isinstance(year_value, str):
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y"):
            try:
                year_value = datetime.strptime(year_value.strip(), fmt).date()
                break
            except ValueError:
                pass
    if isinstance(year_value, (date, datetime)):
        from_year = year_value.date() if isinstance(year_value, datetime) else year_value
        if parsed and parsed != from_year:
            invalid.append("contradictory_birth_date")
        parsed = parsed or from_year
        year_value = from_year.year
    year = None
    if year_value is not None and normaliser(year_value) not in _MISSING | {"adulte", "enfant"}:
        try:
            numeric = float(str(year_value).strip())
            if not numeric.is_integer():
                raise ValueError
            year = int(numeric)
            if not 1850 <= year <= date.today().year:
                raise ValueError
        except (ValueError, OverflowError):
            invalid.append("invalid_birth_year")
            year = None
    if parsed and (parsed > date.today() or parsed.year < 1850):
        invalid.append("invalid_birth_date")
        parsed = None
    if parsed and year and parsed.year != year:
        invalid.append("contradictory_birth_year")
    return (year or (parsed.year if parsed else None)), (parsed.isoformat() if parsed else None), invalid


def normalize_person(raw):
    territory = normalize_territory(raw.get("ville"), raw.get("quartier"))
    year, birth_date, anomalies = _birth(raw)
    nom, prenom = normaliser(raw.get("nom")), normaliser(raw.get("prenom"))
    if not nom or not prenom:
        anomalies.append("incomplete_identity")
    phone_raw, email_raw = _first(raw, "telephone", "phone", "tel"), _first(raw, "email", "mail")
    phone, email = normalize_phone(phone_raw), normalize_email(email_raw)
    if phone_raw and not phone:
        anomalies.append("invalid_telephone")
    if email_raw and not email:
        anomalies.append("invalid_email")
    return {
        "nom": nom, "prenom": prenom, "birth_year": year, "birth_date": birth_date,
        "telephone": phone, "email": email, "ville": normaliser(territory["ville"]) or None,
        "quartier": normaliser(territory["quartier"]) or None,
        "genre": normaliser_sexe(_first(raw, "genre", "sexe")),
        "adresse": normaliser(raw.get("adresse")) or None,
    }, territory, anomalies


def _one_edit(a, b):
    """At most one insertion/deletion/substitution; avoid short-name guesses."""
    if a == b:
        return True
    if min(len(a), len(b)) < 4 or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) == 1
    if len(a) > len(b):
        a, b = b, a
    return any(a == b[:i] + b[i + 1:] for i in range(len(b)))


def _pair(a, b):
    exact_name = a["nom"] == b["nom"] and a["prenom"] == b["prenom"] and bool(a["nom"] and a["prenom"])
    near_name = bool(a["nom"] and a["prenom"] and b["nom"] and b["prenom"]) and (
        a["nom"] == b["nom"] and _one_edit(a["prenom"], b["prenom"])
        or a["prenom"] == b["prenom"] and _one_edit(a["nom"], b["nom"])
    )
    contacts = [f for f in ("telephone", "email") if a[f] and a[f] == b[f]]
    if not (near_name or contacts):
        return None
    conflicts = [f for f in ("birth_year", "birth_date", "telephone", "email", "genre") if a[f] and b[f] and a[f] != b[f]]
    if conflicts:
        return "REVIEW", ["conflict:" + f for f in conflicts]
    same_year = a["birth_year"] is not None and a["birth_year"] == b["birth_year"]
    if exact_name and contacts and a["birth_date"] and a["birth_date"] == b["birth_date"]:
        return "EXACT", ["same_normalized_names", "same_full_birth_date"] + ["same_" + c for c in contacts]
    if near_name and contacts and same_year or exact_name and len(contacts) == 2:
        return "HIGH_CONFIDENCE", ["same_normalized_names" if exact_name else "minor_name_typo"] + (["same_birth_year"] if same_year else []) + ["same_" + c for c in contacts]
    return "REVIEW", ["names_without_sufficient_identity_evidence" if near_name else "shared_contact_without_matching_names"]


def _values(nodes):
    """Assemble missing values only, preserving raw sources in the rows."""
    ordered = sorted(nodes, key=lambda n: (-sum(v is not None and v != "" for v in n["normalized"].values()), n["token"]))
    out = {}
    for field in ("nom", "prenom"):
        value = next((n["raw"].get(field) for n in ordered if n["raw"].get(field)), None)
        out[field] = nettoyer_affichage(value) or None
    for field in ("adresse", "telephone", "email"):
        values = {n["normalized"][field] for n in ordered if n["normalized"][field]}
        out[field] = next((nettoyer_affichage(n["raw"].get(field)) for n in ordered if n["raw"].get(field)), None) if len(values) == 1 else None
    for field, normalized in (("date_naissance", "birth_date"), ("annee_naissance", "birth_year"), ("genre", "genre")):
        values = {n["normalized"][normalized] for n in ordered if n["normalized"][normalized]}
        out[field] = next(iter(values)) if len(values) == 1 else None
    if len({n["normalized"]["birth_year"] for n in ordered if n["normalized"]["birth_year"]}) > 1:
        out["date_naissance"] = None
    # Explicit conflicting territories in a manually grouped set are not
    # resolved arbitrarily. The originals remain in the report for review.
    for field in ("ville", "quartier"):
        values = {n["territory"][field] for n in ordered if n["territory"][field]}
        out[field] = next(iter(values)) if len(values) == 1 else None
    return out


def resolve_participants(source_rows, existing_participants=(), decisions=None):
    """Return a JSON-like preview, globally and independently of row order.

    Source records have ``id`` or ``key`` and ``raw``. Existing snapshots
    have an integer ``id`` and raw person fields, optionally under ``raw``.
    ``decisions`` maps a source key to ``participant`` (participant_id),
    ``new`` (optional explicit group), ``source`` (source_id), or ``ignore``.
    Missing decisions leave REVIEW rows unresolved. See the documented
    contract for aliases retained for the workbook parser.
    """
    nodes = {}
    source_keys = set()
    existing_ids = set()
    for kind, records in (("source", source_rows), ("participant", existing_participants)):
        for record in records:
            identifier = str(record.get("key", record.get("id", ""))) if kind == "source" else record.get("id")
            if not identifier or kind == "participant" and (not isinstance(identifier, int) or isinstance(identifier, bool)):
                raise ValueError("Chaque source a une clé non vide et chaque participant un identifiant entier.")
            token = f"{kind}:{identifier}"
            if token in nodes:
                raise ValueError("Clé source ou identifiant participant dupliqué : " + token)
            raw = deepcopy(record.get("raw", {k: v for k, v in record.items() if k != "id"}))
            normalized, territory, anomalies = normalize_person(raw)
            nodes[token] = {"token": token, "kind": kind, "id": identifier, "raw": raw,
                            "normalized": normalized, "territory": territory, "anomalies": anomalies}
            (source_keys if kind == "source" else existing_ids).add(identifier)

    # In-memory candidate blocking: exact first/last names plus contacts.
    # No SQL scan per source line and no order-dependent incremental lookup.
    indexes = {f: defaultdict(set) for f in ("nom", "prenom", "telephone", "email")}
    for token, node in nodes.items():
        for field, index in indexes.items():
            if node["normalized"][field]:
                index[node["normalized"][field]].add(token)
    edges, neighbours = {}, defaultdict(set)
    for token in sorted(nodes):
        node = nodes[token]
        candidates = set()
        for field, index in indexes.items():
            candidates.update(index.get(node["normalized"][field], ()))
        for other in sorted(c for c in candidates if c > token):
            relation = _pair(node["normalized"], nodes[other]["normalized"])
            if relation:
                edges[token, other] = relation
                neighbours[token].add(other)
                neighbours[other].add(token)

    def edge(a, b):
        return edges.get(tuple(sorted((a, b))))

    # All plausible connected records are assessed together. A chain of
    # strong links cannot bridge incompatible or weakly linked identities.
    components = []
    remaining = set(nodes)
    while remaining:
        pending = [min(remaining)]
        component = set()
        while pending:
            token = pending.pop()
            if token in component:
                continue
            component.add(token)
            pending.extend(neighbours[token] - component)
        remaining -= component
        components.append(sorted(component))

    rows = []
    for component in components:
        source = [nodes[t] for t in component if nodes[t]["kind"] == "source"]
        if not source:
            continue
        db_nodes = [nodes[t] for t in component if nodes[t]["kind"] == "participant"]
        reasons = set()
        if len(db_nodes) > 1:
            reasons.add("multiple_existing_participants")
        for i, left in enumerate(component):
            reasons.update(nodes[left]["anomalies"])
            for right in component[i + 1:]:
                relation = edge(left, right)
                if relation is None:
                    reasons.add("non_transitive_identity_chain")
                elif relation[0] == "REVIEW":
                    reasons.update(relation[1])
        # Different known territories are not overwritten or guessed,
        # even with otherwise strong identity evidence.
        for field in ("ville", "quartier"):
            distinct = {nodes[t]["normalized"][field] for t in component if nodes[t]["normalized"][field]}
            if len(distinct) > 1:
                reasons.add("conflict:" + field)
        entity = None if reasons else (db_nodes[0]["token"] if db_nodes else min(n["token"] for n in source))
        for node in source:
            token = node["token"]
            candidates = []
            for other in sorted(set(component) - {token}):
                candidate = nodes[other]
                relation = edge(token, other)
                entry = {"kind": candidate["kind"], "classification": relation[0] if relation else "REVIEW",
                         "reasons": relation[1] if relation else ["non_transitive_identity_chain"],
                         "raw": deepcopy(candidate["raw"])}
                entry["id" if candidate["kind"] == "participant" else "key"] = candidate["id"]
                candidates.append(entry)
            if reasons:
                classification, row_reasons = "REVIEW", sorted(reasons)
            elif db_nodes:
                classification, row_reasons = edge(token, db_nodes[0]["token"])
            elif token == entity:
                classification, row_reasons = "NEW", ["new_identity_group" if len(source) > 1 else "no_credible_candidate"]
            else:
                classification, row_reasons = "HIGH_CONFIDENCE", edge(token, entity)[1]
            rows.append({
                "source_id": node["id"], "key": node["id"], "classification": classification,
                "entity_key": entity, "group_key": entity,
                "participant_id": db_nodes[0]["id"] if entity and db_nodes else None,
                "candidate_ids": [c["id"] for c in candidates if c["kind"] == "participant"],
                "candidate_source_ids": [c["key"] for c in candidates if c["kind"] == "source"],
                "candidates": candidates, "reasons": list(row_reasons), "resolved": bool(entity),
                "raw": deepcopy(node["raw"]), "normalized": node["normalized"], "territory": node["territory"],
            })
    rows.sort(key=lambda row: row["key"])
    by_key = {row["key"]: row for row in rows}
    decisions = decisions or {}
    unknown = set(decisions) - source_keys
    if unknown:
        raise ValueError("Décision sur une ligne absente : " + ", ".join(sorted(unknown)))
    for key, decision in decisions.items():
        if not isinstance(decision, dict):
            raise ValueError("Décision invalide : " + key)
        action = decision.get("action")
        if action not in {"participant", "new", "source", "ignore"}:
            raise ValueError("Action de résolution inconnue : " + str(action))
        if action == "participant" and decision.get("participant_id") not in existing_ids:
            raise ValueError("Participant cible absent : " + str(decision.get("participant_id")))
        if action == "participant" and (not isinstance(decision.get("participant_id"), int) or isinstance(decision.get("participant_id"), bool)):
            raise ValueError("Identifiant participant invalide.")
        if action == "new" and "group" in decision and (not isinstance(decision["group"], str) or not decision["group"].strip()):
            raise ValueError("Le groupe explicite doit être une chaîne non vide.")
        if "values" in decision:
            values = decision["values"]
            if (not isinstance(values, dict) or set(values) - {"nom", "prenom"}
                    or any(not isinstance(v, str) or not normaliser(v) or len(nettoyer_affichage(v)) > 120 for v in values.values())):
                raise ValueError("Les corrections doivent contenir seulement nom/prenom non vides (120 caractères maximum).")
        if action == "source" and decision.get("source_id", decision.get("source_key")) not in source_keys:
            raise ValueError("Ligne source cible absente.")
        if action == "source" and "source_id" in decision and "source_key" in decision and decision["source_id"] != decision["source_key"]:
            raise ValueError("Deux cibles sources contradictoires.")
        # Prevent fields from silently suggesting a different target.
        allowed = {"participant": {"action", "participant_id"}, "new": {"action", "group", "values"},
                   "source": {"action", "source_id", "source_key"}, "ignore": {"action"}}[action]
        if set(decision) - allowed:
            raise ValueError("Champs incompatibles avec l'action de résolution : " + key)

    settled = set()
    def apply_decision(key, visiting):
        if key in settled:
            return
        if key in visiting:
            raise ValueError("Cycle dans les décisions de rapprochement.")
        if key not in decisions:
            return
        row, decision = by_key[key], decisions[key]
        action = decision["action"]
        participant_id = None
        if action == "participant":
            participant_id = decision["participant_id"]
            entity = f"participant:{participant_id}"
        elif action == "new":
            entity = "manual-group:" + decision["group"] if decision.get("group") else "manual-source:" + key
        elif action == "source":
            target = decision.get("source_id", decision.get("source_key"))
            apply_decision(target, visiting | {key})
            target_row = by_key[target]
            if not target_row["entity_key"]:
                raise ValueError("La source cible doit d'abord être résolue et ne pas être ignorée.")
            entity, participant_id = target_row["entity_key"], target_row["participant_id"]
        else:
            entity = None
        row.update(entity_key=entity, group_key=entity, participant_id=participant_id,
                   resolved=True, decision=deepcopy(decision), ignored=action == "ignore")
        settled.add(key)

    for key in sorted(decisions):
        apply_decision(key, set())
    # A manual split must not change the interpretation of undecided peers
    # in an automatic group: require a decision for each such source row.
    initial_entities = defaultdict(set)
    for component in components:
        for token in component:
            node = nodes[token]
            if node["kind"] == "source":
                initial_entities[component[0]].add(node["id"])
    for keys in initial_entities.values():
        changed = {k for k in keys if k in decisions}
        if changed and changed != keys:
            destinations = {by_key[k]["entity_key"] for k in keys if by_key[k]["entity_key"]}
            # REVIEW peers were never assigned, so independent explicit
            # decisions are safe. Automatic peers must all be explicit
            # if the decision splits the original assignment.
            automatic = {k for k in keys - changed if by_key[k]["classification"] != "REVIEW"}
            if automatic and len(destinations) > 1:
                raise ValueError("Décision partielle divisant un groupe automatique : préciser toutes ses lignes.")

    grouped = defaultdict(list)
    for row in rows:
        if row["entity_key"]:
            grouped[row["entity_key"]].append(row)
    groups = []
    for key, members in sorted(grouped.items()):
        ids = {r["participant_id"] for r in members if r["participant_id"] is not None}
        if len(ids) > 1:
            raise ValueError("Un groupe ne peut pas cibler plusieurs participants existants.")
        group_nodes = []
        for row in members:
            node = nodes["source:" + row["key"]]
            if row.get("decision", {}).get("values"):
                node = deepcopy(node)
                node["raw"].update(row["decision"]["values"])
                node["normalized"], node["territory"], node["anomalies"] = normalize_person(node["raw"])
            group_nodes.append(node)
        values = _values(group_nodes)
        if not ids and any(not values.get(field) for field in ("nom", "prenom")):
            raise ValueError("Une nouvelle personne nécessite un nom et un prénom : corriger explicitement values, choisir une fiche existante ou ignorer.")
        conflicts = [field for field in ("birth_year", "birth_date", "telephone", "email", "genre", "ville", "quartier", "adresse")
                     if len({n["normalized"][field] for n in group_nodes if n["normalized"][field]}) > 1]
        source_ids = [r["key"] for r in members]
        groups.append({"key": key, "entity_key": key, "group_key": key,
                       "participant_id": next(iter(ids)) if ids else None,
                       "source_ids": source_ids, "source_keys": source_ids, "values": values, "value_conflicts": conflicts})
    counts = {key: 0 for key in RULES}
    counts.update(Counter(r["classification"] for r in rows))
    counts.update(source_rows=len(rows), groups=len(groups),
                  new_participants=sum(g["participant_id"] is None for g in groups),
                  internal_duplicates=sum(max(0, len(g["source_ids"]) - 1) for g in groups),
                  unresolved_review=sum(r["classification"] == "REVIEW" and not r["resolved"] for r in rows),
                  ignored=sum(bool(r.get("ignored")) for r in rows))
    return {"rows": rows, "entities": groups, "groups": groups, "counts": counts,
            "rules_version": RULES_VERSION, "rules": RULES,
            "territory_anomalies": [{"key": r["key"], "source_id": r["key"], **a} for r in rows for a in r["territory"]["anomalies"]]}


def resolve_people(people, existing=(), decisions=None):
    """Parser-oriented alias using source ``key`` and ``raw`` records."""
    return resolve_participants(people, existing, decisions)
