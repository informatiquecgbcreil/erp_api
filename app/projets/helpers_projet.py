from datetime import date

from flask import request

from app.extensions import db

from app.models import (
    Projet,
    ChargeProjet,
    ProduitProjet,
    Depense,
    Subvention,
    BudgetPrevisionnel,
    BudgetPrevisionnelLigne,
    AppelProjetBudget,
    AtelierActivite,
    ProjetAtelier,
    ProjetJournalEntry,
    ProjetAction,
)
from app.statsimpact.engine import (
    StatsFilters,
    compute_demography_stats,
    compute_participation_frequency_stats,
    compute_volume_activity_stats,
)


from app.projets.common import (
    PROJET_ACTION_CATEGORIES,
    PROJET_ACTION_STATUTS,
)

# ---------------------------------------------------------------------
# Helpers Budget AAP (UX)
# ---------------------------------------------------------------------

def _budget_stats(projet_id: int) -> dict:
    """Stats simples pour l'UX du budget AAP.

    Objectif : afficher en haut des pages un résumé (totaux + "reste à...")
    et permettre d'afficher des alertes rapides.
    """
    charges = ChargeProjet.query.filter_by(projet_id=projet_id).all()
    produits = ProduitProjet.query.filter_by(projet_id=projet_id).all()

    total_charges = float(sum((c.montant_previsionnel or 0) for c in charges))
    total_charges_ventile = float(sum((c.ventile or 0) for c in charges))
    total_charges_reste = max(0.0, total_charges - total_charges_ventile)

    total_demande = float(sum((p.montant_demande or 0) for p in produits))
    total_accorde = float(sum((p.montant_accorde or 0) for p in produits))
    total_recu = float(sum((p.montant_recu or 0) for p in produits))

    total_produits_ventile = float(sum((p.ventile or 0) for p in produits))
    total_produits_reste = float(sum((p.reste_a_ventiler or 0) for p in produits))

    # Base "produits" utilisée pour donner du contexte (reçu > accordé > demandé)
    base_produits = total_recu if total_recu > 0 else (total_accorde if total_accorde > 0 else total_demande)
    base_label = "reçu" if total_recu > 0 else ("accordé" if total_accorde > 0 else "demandé")

    def pct(a: float, b: float) -> int:
        if b <= 0:
            return 0
        v = int(round((a / b) * 100))
        return max(0, min(100, v))

    return {
        "total_charges": total_charges,
        "total_charges_ventile": total_charges_ventile,
        "total_charges_reste": total_charges_reste,
        "total_demande": total_demande,
        "total_accorde": total_accorde,
        "total_recu": total_recu,
        "base_produits": base_produits,
        "base_label": base_label,
        "total_produits_ventile": total_produits_ventile,
        "total_produits_reste": total_produits_reste,
        "pct_charges_financees": pct(total_charges_ventile, total_charges),
        "pct_produits_ventiles": pct(total_produits_ventile, base_produits if base_produits > 0 else total_charges),
    }


# ---------------------------------------------------------------------
# P0.2A — Dossier financier projet
# ---------------------------------------------------------------------

def _money(value) -> float:
    try:
        return round(float(value or 0), 2)
    except Exception:
        return 0.0


def _pct(part, total) -> int:
    try:
        total = float(total or 0)
        part = float(part or 0)
    except Exception:
        return 0
    if total <= 0:
        return 0
    return max(0, min(100, int(round((part / total) * 100))))


def _parse_float_fr(value, default=0.0) -> float:
    raw = str(value or '').strip()
    if not raw:
        return float(default)
    raw = raw.replace('\u00a0', '').replace(' ', '').replace('€', '').replace(',', '.')
    try:
        return round(float(raw), 2)
    except Exception:
        return float(default)


def _parse_date_or_none(value):
    raw = str(value or '').strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except Exception:
        return None


def _clean_choice(value: str | None, choices: dict, default: str) -> str:
    raw = (value or default).strip()
    return raw if raw in choices else default


def _project_linked_atelier_ids(projet_id: int) -> set[int]:
    return {
        row.atelier_id for row in ProjetAtelier.query.filter_by(projet_id=projet_id).all()
    }


def _action_linked_atelier_ids(action: ProjetAction) -> list[int]:
    return [link.atelier_id for link in getattr(action, "atelier_links", []) or [] if link.atelier_id]


def _action_period(action: ProjetAction, year: int) -> tuple[date, date]:
    dmin = action.date_debut or date(year, 1, 1)
    dmax = action.date_fin or date(year, 12, 31)
    if dmax < dmin:
        dmin, dmax = dmax, dmin
    return dmin, dmax


def _empty_action_stats() -> dict:
    return {
        "kpi": {"sessions": 0, "presences": 0, "uniques": 0, "hours_people": 0},
        "freq": {"returning": 0, "returning_rate": 0, "freq_avg": 0},
        "demo": {"age_avg": None, "qpv": {"qpv": 0, "hors_qpv": 0, "inconnu": 0}},
    }


def _compute_action_stats(projet: Projet, action: ProjetAction, year: int) -> dict:
    atelier_ids = _action_linked_atelier_ids(action)
    if not atelier_ids:
        return _empty_action_stats()

    dmin, dmax = _action_period(action, year)
    flt = StatsFilters(
        secteur=projet.secteur,
        atelier_ids=atelier_ids,
        date_from=dmin,
        date_to=dmax,
    )
    return {
        "kpi": compute_volume_activity_stats(flt).get("kpi", {}),
        "freq": compute_participation_frequency_stats(flt),
        "demo": compute_demography_stats(flt),
    }


def _action_list_rows(projet: Projet, actions: list[ProjetAction], year: int) -> list[dict]:
    rows = []
    for action in actions:
        atelier_ids = _action_linked_atelier_ids(action)
        quality = _action_completion_payload(action, atelier_ids)
        rows.append({
            "action": action,
            "atelier_ids": atelier_ids,
            "stats": _compute_action_stats(projet, action, year),
            "statut_label": PROJET_ACTION_STATUTS.get(action.statut, action.statut),
            "categorie_label": PROJET_ACTION_CATEGORIES.get(action.categorie, action.categorie),
            "quality": quality,
        })
    return rows


def _quality_issue(severity: str, title: str, detail: str) -> dict:
    return {"severity": severity, "title": title, "detail": detail}


def _quality_counts(issues: list[dict]) -> dict:
    return {
        "bad": sum(1 for item in issues if item.get("severity") == "bad"),
        "warn": sum(1 for item in issues if item.get("severity") == "warn"),
        "info": sum(1 for item in issues if item.get("severity") == "info"),
    }


def _quality_score(issues: list[dict]) -> int:
    counts = _quality_counts(issues)
    score = 100 - (counts["bad"] * 18) - (counts["warn"] * 10) - (counts["info"] * 4)
    return max(0, min(100, score))


def _quality_payload(issues: list[dict]) -> dict:
    counts = _quality_counts(issues)
    score = _quality_score(issues)
    if counts["bad"]:
        status = "bad"
        label = "À sécuriser"
    elif counts["warn"]:
        status = "warn"
        label = "À compléter"
    else:
        status = "ok"
        label = "Prêt à partager"
    return {
        "issues": issues,
        "counts": counts,
        "score": score,
        "status": status,
        "label": label,
    }


def _action_completion_payload(action: ProjetAction, atelier_ids: list[int]) -> dict:
    issues = []
    if not atelier_ids:
        issues.append(_quality_issue(
            "bad",
            "Aucun atelier stats-impact",
            "Les chiffres de l'action resteront a zero tant qu'aucun atelier du projet n'est rattache.",
        ))
    if not getattr(action, "objectifs", None):
        issues.append(_quality_issue("warn", "Objectifs manquants", "Les objectifs de l'action ne sont pas encore renseignes."))
    if not getattr(action, "public_vise", None):
        issues.append(_quality_issue("info", "Public vise a preciser", "Le public vise aidera la lecture par l'equipe et les partenaires."))
    if not getattr(action, "referent", None):
        issues.append(_quality_issue("info", "Referent non renseigne", "La fiche ne precise pas encore qui pilote l'action."))
    if not getattr(action, "date_debut", None) and not getattr(action, "date_fin", None):
        issues.append(_quality_issue("warn", "Periode absente", "Sans periode, les stats utilisent l'annee selectionnee."))
    if getattr(action, "statut", None) == "realisee" and not getattr(action, "bilan_qualitatif", None):
        issues.append(_quality_issue("warn", "Bilan a completer", "L'action est realisee mais le bilan qualitatif n'est pas encore renseigne."))
    if not getattr(action, "description", None):
        issues.append(_quality_issue("info", "Description courte absente", "Une description courte facilite la lecture rapide de la fiche projet."))
    return _quality_payload(issues)


def _project_completion_payload(
    projet: Projet,
    linked_ateliers: list[AtelierActivite],
    action_rows: list[dict],
    indicators: list[dict],
    journal_entries: list[ProjetJournalEntry],
    finance: dict,
) -> dict:
    issues = []
    if not (projet.description or "").strip():
        issues.append(_quality_issue("warn", "Description projet manquante", "La fiche projet n'a pas encore de description lisible."))
    if not linked_ateliers:
        issues.append(_quality_issue("bad", "Aucun atelier stats-impact lie", "Les statistiques projet et indicateurs connectes resteront vides."))
    if not action_rows:
        issues.append(_quality_issue("warn", "Aucune fiche action", "Le projet n'a pas encore d'action detaillee partageable avec l'equipe."))
    else:
        action_without_stats = sum(1 for row in action_rows if not row.get("atelier_ids"))
        action_without_bilan = sum(
            1 for row in action_rows
            if getattr(row.get("action"), "statut", None) == "realisee"
            and not getattr(row.get("action"), "bilan_qualitatif", None)
        )
        if action_without_stats:
            issues.append(_quality_issue("warn", "Actions sans atelier", f"{action_without_stats} action(s) ne piochent pas encore dans stats-impact."))
        if action_without_bilan:
            issues.append(_quality_issue("warn", "Bilans d'action manquants", f"{action_without_bilan} action(s) realisee(s) n'ont pas encore de bilan qualitatif."))
    if not indicators:
        issues.append(_quality_issue("warn", "Aucun indicateur actif", "La page indicateurs ne contient pas encore d'indicateur actif pour ce projet."))
    else:
        pending = sum(1 for row in indicators if not row.get("status"))
        bad = sum(1 for row in indicators if row.get("status") == "bad")
        if pending:
            issues.append(_quality_issue("info", "Indicateurs a completer", f"{pending} indicateur(s) n'ont pas encore de cible ou de valeur exploitable."))
        if bad:
            issues.append(_quality_issue("warn", "Indicateurs non atteints", f"{bad} indicateur(s) sont sous l'objectif defini."))
    if not journal_entries:
        issues.append(_quality_issue("info", "Journal vide", "Aucun fait marquant n'est encore note pour faciliter le bilan."))
    finance_alerts = finance.get("alerts") or []
    finance_bad = sum(1 for alert in finance_alerts if alert.get("level") == "danger")
    finance_warn = sum(1 for alert in finance_alerts if alert.get("level") == "warn")
    if finance_bad:
        issues.append(_quality_issue("bad", "Alerte financiere critique", f"{finance_bad} point(s) financier(s) demandent une verification."))
    elif finance_warn:
        issues.append(_quality_issue("warn", "Points financiers a suivre", f"{finance_warn} alerte(s) financiere(s) sont a surveiller."))

    payload = _quality_payload(issues)
    payload["sections"] = {
        "description": bool((projet.description or "").strip()),
        "ateliers": bool(linked_ateliers),
        "actions": bool(action_rows),
        "indicateurs": bool(indicators),
        "journal": bool(journal_entries),
    }
    return payload


def _projet_finance_years(projet: Projet) -> list[int]:
    years = set()

    # Années des subventions rattachées au projet
    for link in getattr(projet, "subventions", []) or []:
        sub = getattr(link, "subvention", None)
        if sub and getattr(sub, "annee_exercice", None):
            years.add(int(sub.annee_exercice))

    # Années des budgets prévisionnels contenant des lignes pour ce projet
    rows = (
        db.session.query(BudgetPrevisionnel.annee)
        .join(BudgetPrevisionnelLigne, BudgetPrevisionnelLigne.budget_id == BudgetPrevisionnel.id)
        .filter(BudgetPrevisionnelLigne.projet_id == projet.id)
        .distinct()
        .all()
    )
    for (annee,) in rows:
        if annee:
            years.add(int(annee))

    # Années des dossiers/appels à projet liés
    rows = (
        db.session.query(BudgetPrevisionnel.annee)
        .join(AppelProjetBudget, AppelProjetBudget.budget_id == BudgetPrevisionnel.id)
        .filter(AppelProjetBudget.projet_id == projet.id)
        .distinct()
        .all()
    )
    for (annee,) in rows:
        if annee:
            years.add(int(annee))

    if not years:
        years.add(date.today().year)
    return sorted(years, reverse=True)


def _projet_selected_year(projet: Projet) -> int:
    years = _projet_finance_years(projet)
    raw = request.args.get("year")
    try:
        year = int(raw) if raw else years[0]
    except Exception:
        year = years[0]
    if year not in years:
        year = years[0]
    return year


def _depense_key(dep: Depense) -> str:
    return f"dep-{dep.id}"


def _collect_project_depenses(projet: Projet, subventions: list[Subvention]) -> list[Depense]:
    """Récupère les dépenses via deux chemins sans doublonner :
    - dépenses rattachées aux lignes budgétaires des subventions du projet ;
    - dépenses rattachées directement aux charges projet.
    """
    depenses_by_key = {}

    for sub in subventions:
        # Nouveau modèle : les imputations sont la source la plus fiable.
        for aff in getattr(sub, "depense_affectations", []) or []:
            dep = getattr(aff, "depense", None)
            if dep and not getattr(dep, "est_supprimee", False):
                depenses_by_key[_depense_key(dep)] = dep

        # Compatibilité ancien modèle : dépenses directement attachées aux lignes.
        for ligne in getattr(sub, "lignes", []) or []:
            if getattr(ligne, "nature", "charge") != "charge":
                continue
            for dep in getattr(ligne, "depenses", []) or []:
                if getattr(dep, "est_supprimee", False):
                    continue
                depenses_by_key[_depense_key(dep)] = dep

    for charge in getattr(projet, "charges_projet", []) or []:
        for dep in getattr(charge, "depenses", []) or []:
            if getattr(dep, "est_supprimee", False):
                continue
            depenses_by_key[_depense_key(dep)] = dep

    return sorted(
        depenses_by_key.values(),
        key=lambda d: (d.date_paiement or (d.created_at.date() if getattr(d, "created_at", None) else date.min), d.id),
        reverse=True,
    )



