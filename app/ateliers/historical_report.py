"""Human-readable local report; contains personal data and must remain private."""
import json


def _cell(value):
    return str(value if value is not None else "—").replace("|", "\\|").replace("\n", " ")


def markdown_report(plan):
    source = plan["source"]
    lines = ["# Migration historique — rapport du dry-run", "",
             f"Source : **{source['filename']}**. Empreinte SHA-256 : `{source['sha256']}`.", "",
             "Ce rapport contient des données personnelles. Conserver en accès privé.", "",
             "L'analyse ne crée aucune donnée et termine sa transaction par rollback. Les correspondances avec la base décrivent uniquement la base effectivement analysée.", "",
             "## Résultat", "", "Import prêt : **" + ("oui" if plan["ready"] else "non, validations nécessaires") + "**.", "",
             "| Contrôle | Résultat |", "|---|---:|"]
    labels = {
        "sheets": "Feuilles analysées", "activities_recognized": "Feuilles d'activité reconnues",
        "activities_unassigned": "Activités restant à affecter à un secteur",
        "activities_existing": "Activités existantes retrouvées", "activities_new": "Nouvelles activités proposées",
        "sessions_detected": "Séances détectées", "sessions_review": "Séances à valider",
        "participants_source": "Lignes source de participants", "participants_exact": "Lignes EXACT",
        "participants_high_confidence": "Lignes HIGH_CONFIDENCE", "participants_review": "Lignes REVIEW (classification initiale)",
        "participants_new": "Nouveaux participants proposés après décisions", "presences_detected": "Cellules de présence conservées",
        "presences_existing": "Présences déjà en base", "presences_new": "Présences proposées avec identité et date résolues",
        "presences_pending": "Présences en attente d'identité ou de date", "presences_ignored": "Présences explicitement exclues",
        "internal_duplicate_presences": "Cellules de présence redondantes après résolution",
        "internal_duplicate_people": "Lignes réunies automatiquement ou après validation",
        "territory_anomalies": "Anomalies territoriales", "date_anomalies": "Anomalies de dates bloquantes",
        "ignored_rows": "Lignes ignorées (totaux, vides, en-têtes)", "errors": "Erreurs de lecture",
        "blockers": "Points restant à valider",
    }
    lines.extend(f"| {label} | {plan['summary'].get(key, 0)} |" for key, label in labels.items())
    lines += ["", "Feuilles exclues : " + ", ".join(plan["summary"]["ignored_sheets"]) + ".", "",
              "REVIEW compte des lignes, pas des personnes uniques. Un doublon d'identité possible reste en attente tant que la preuve ou la décision humaine manque. Les présences proposées ne seront écrites qu'une fois tous les blocages résolus, y compris l'affectation des secteurs.", "",
              "## Feuilles et totaux Excel", "", "| Feuille source | Nom métier | Classement | Séances | Lignes | Présences lues | Total Excel | Contrôle |", "|---|---|---|---:|---:|---:|---:|---|"]
    activities = {a["key"]: a for a in plan["activities"]}
    status = {"coherent": "✅ cohérent", "difference": "⚠️ écart", "uninterpretable": "❌ impossible à interpréter", "unavailable": "total non disponible"}
    for sheet in plan["parser"]["sheets"]:
        a = activities.get(sheet.get("activity_key"), {})
        total = sheet.get("totals") or {}
        values = [sheet["name"], a.get("name", "—"), sheet["classification"], sheet["sessions"], sheet["rows"], sheet["presences"], total.get("excel_total"), status.get(total.get("status"), "sans objet")]
        lines.append("| " + " | ".join(_cell(v) for v in values) + " |")
    lines += ["", "Les totaux sont les valeurs enregistrées par Excel. Aucune formule du classeur n'est recalculée. Les contrôles par ligne et par colonne figurent dans le JSON complet.", "",
              "## Affectations des activités", "", "| Activité | Secteur choisi | Correspondance |", "|---|---|---|"]
    for a in plan["activities"]:
        lines.append(f"| {_cell(a['name'])} | {_cell(a.get('secteur'))} | {_cell(a['status'])} |")
    lines += ["", "## Séances à vérifier", "", "Chaque colonne reste une séance distincte. M, AM et ME sont conservés tels quels ; aucune heure n'est inventée.", "",
              "| Source | Créneau | Valeur du jour | Date initiale candidate | Autres possibilités à valider | Motifs |", "|---|---|---|---|---|---|"]
    for s in plan["sessions"]:
        if s["status"] != "REVIEW":
            continue
        values = [s["key"], s.get("source_slot"), s.get("raw_headers", {}).get(s.get("source_cell")), s.get("candidate_date"), ", ".join(s.get("candidate_dates", [])), "; ".join(a["message"] for a in s.get("anomalies", []) if a.get("blocking", True))]
        lines.append("| " + " | ".join(_cell(v) for v in values) + " |")
    lines += ["", "## Autres anomalies", ""]
    for a in plan.get("anomalies", []):
        if a.get("blocking", True):
            lines.append(f"- `{a['id']}` : " + _cell(json.dumps({k: v for k, v in a.items() if k != 'id'}, ensure_ascii=False, default=str)))
    lines += ["", "## Identités à valider", "", "Les informations sources sont conservées. Les candidats et les motifs complets sont dans le JSON du rapport.", "",
              "| Source | Nom source | Prénom source | Naissance source | Territoire source | Candidats | Motifs |", "|---|---|---|---|---|---|---|"]
    for r in plan["matching"]["rows"]:
        if r["classification"] != "REVIEW":
            continue
        raw = r["raw"]
        candidates = ["participant #" + str(c["id"]) if c["kind"] == "participant" else c["key"] for c in r["candidates"]]
        values = [r["key"], raw.get("nom"), raw.get("prenom"), raw.get("annee_naissance", raw.get("date_naissance")), raw.get("ville") or raw.get("quartier"), ", ".join(candidates), ", ".join(r["reasons"])]
        lines.append("| " + " | ".join(_cell(v) for v in values) + " |")
    lines += ["", "Empreinte de prévisualisation : `" + plan["digest"] + "`.", "",
              "Sources techniques : app/ateliers/historical_parser.py, historical_matching.py et historical_import.py ; règles et procédure dans docs/historical-import.md.", ""]
    return "\n".join(lines)
