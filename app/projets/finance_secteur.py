from datetime import date

from flask import render_template, request, redirect, url_for, flash, abort
from flask_login import login_required
from app.rbac import require_perm, can, can_access_secteur

from app.extensions import db

from app.models import (
    Projet,
    Depense,
    DepenseAffectation,
    Subvention,
    LigneBudget,
    BudgetPrevisionnel,
    AppelProjetBudget,
)


from app.projets.helpers_projet import (
    _money,
    _parse_date_or_none,
    _parse_float_fr,
    _pct,
)
from app.projets.projets_crud import (
    _projet_finance_context,
)
from app.projets.common import (
    bp,
)

# ---------------------------------------------------------------------
# P0.2D — Pilotage financier annuel du secteur
# ---------------------------------------------------------------------

def _finance_accessible_sectors() -> list[str]:
    sectors = set()

    for (s,) in db.session.query(Projet.secteur).distinct().all():
        if s and can_access_secteur(s):
            sectors.add(s)

    for (s,) in db.session.query(BudgetPrevisionnel.secteur).distinct().all():
        if s and can_access_secteur(s):
            sectors.add(s)

    for (s,) in db.session.query(Subvention.secteur).distinct().all():
        if s and can_access_secteur(s):
            sectors.add(s)

    return sorted(sectors)


def _finance_sector_years(secteur: str) -> list[int]:
    years = set()

    for (annee,) in db.session.query(BudgetPrevisionnel.annee).filter(BudgetPrevisionnel.secteur == secteur).distinct().all():
        if annee:
            years.add(int(annee))

    for (annee,) in db.session.query(Subvention.annee_exercice).filter(Subvention.secteur == secteur).distinct().all():
        if annee:
            years.add(int(annee))

    if not years:
        years.add(date.today().year)

    return sorted(years, reverse=True)


def _selected_sector_year():
    sectors = _finance_accessible_sectors()
    selected_sector = (request.args.get("secteur") or "").strip()
    if not selected_sector or selected_sector not in sectors:
        selected_sector = sectors[0] if sectors else None

    years = _finance_sector_years(selected_sector) if selected_sector else [date.today().year]
    try:
        year = int(request.args.get("year") or years[0])
    except Exception:
        year = years[0]

    if year not in years:
        year = years[0]

    return sectors, selected_sector, years, year


def _is_compte_fonds_propres(compte) -> bool:
    raw = str(compte or "").strip().lower().replace(" ", "")
    return raw.startswith("99")


def _label_is_fonds_propres(label) -> bool:
    raw = str(label or "").strip().lower()
    markers = (
        "fonds propre",
        "fonds propres",
        "autofinancement",
        "auto-financement",
        "reste à charge",
        "reste a charge",
        "interne",
    )
    return any(m in raw for m in markers)


def _is_fonds_propres_enveloppe(subvention=None, ligne=None) -> bool:
    """Convention métier P0.2G.1.

    On ne crée pas de table dédiée pour les fonds propres.
    Une enveloppe reste une Subvention côté code, mais elle est classée à part si :
    - une de ses lignes utilise un compte commençant par 99 ;
    - ou son nom contient fonds propres/autofinancement/reste à charge.
    """
    if ligne is not None and _is_compte_fonds_propres(getattr(ligne, "compte", None)):
        return True

    if subvention is not None:
        if _label_is_fonds_propres(getattr(subvention, "nom", "")):
            return True
        for l in getattr(subvention, "lignes", []) or []:
            if _is_compte_fonds_propres(getattr(l, "compte", None)):
                return True

    return False



def _collect_sector_depenses(secteur: str, year: int, subventions: list[Subvention]) -> list[Depense]:
    """Récupère les dépenses de l'exercice via les enveloppes réelles du secteur.

    On part des subventions réelles, parce que c'est le vrai point d'imputation.
    Les fonds propres remontent via les affectations de ces dépenses.
    """
    depenses = {}
    for sub in subventions:
        for ligne in getattr(sub, "lignes", []) or []:
            for dep in getattr(ligne, "depenses", []) or []:
                if dep and not dep.est_supprimee:
                    depenses[dep.id] = dep
        for aff in getattr(sub, "depense_affectations", []) or []:
            dep = getattr(aff, "depense", None)
            if dep and not dep.est_supprimee:
                depenses[dep.id] = dep

    return sorted(
        depenses.values(),
        key=lambda d: (d.date_paiement or (d.created_at.date() if getattr(d, "created_at", None) else date.min), d.id),
        reverse=True,
    )


def _sector_finance_context(secteur: str, year: int) -> dict:
    budgets = (
        BudgetPrevisionnel.query
        .filter_by(secteur=secteur, annee=year)
        .order_by(BudgetPrevisionnel.created_at.desc(), BudgetPrevisionnel.id.desc())
        .all()
    )

    prev_lines = []
    for budget in budgets:
        prev_lines.extend(list(getattr(budget, "lignes", []) or []))

    subventions = (
        Subvention.query
        .filter(Subvention.secteur == secteur, Subvention.annee_exercice == year, Subvention.est_archive.is_(False))
        .order_by(Subvention.nom.asc())
        .all()
    )

    dossiers = (
        AppelProjetBudget.query
        .join(BudgetPrevisionnel, AppelProjetBudget.budget_id == BudgetPrevisionnel.id)
        .filter(BudgetPrevisionnel.secteur == secteur, BudgetPrevisionnel.annee == year)
        .order_by(AppelProjetBudget.created_at.desc(), AppelProjetBudget.id.desc())
        .all()
    )

    projets = Projet.query.filter_by(secteur=secteur).order_by(Projet.nom.asc()).all()
    depenses = _collect_sector_depenses(secteur, year, subventions)

    # Prévisionnel global
    charges_prevues = _money(sum(float(l.montant or 0) for l in prev_lines if l.nature == "charge"))
    produits_prevus = _money(sum(float(l.montant or 0) for l in prev_lines if l.nature == "produit"))

    # Enveloppes réelles : externes + fonds propres/autofinancement interne.
    # Techniquement, les fonds propres peuvent rester dans la table Subvention,
    # mais ils sont isolés par convention via compte 99 ou libellé.
    fonds_propres_enveloppes = [s for s in subventions if _is_fonds_propres_enveloppe(s)]
    subventions_externes = [s for s in subventions if not _is_fonds_propres_enveloppe(s)]

    demande_externe = _money(sum(float(s.montant_demande or 0) for s in subventions_externes))
    attribue_externe = _money(sum(float(s.montant_attribue or 0) for s in subventions_externes))
    recu_externe = _money(sum(float(s.montant_recu or 0) for s in subventions_externes))

    fonds_propres_demande = _money(sum(float(s.montant_demande or 0) for s in fonds_propres_enveloppes))
    fonds_propres_attribue = _money(sum(float(s.montant_attribue or 0) for s in fonds_propres_enveloppes))
    fonds_propres_recu = _money(sum(float(s.montant_recu or 0) for s in fonds_propres_enveloppes))

    demande = _money(demande_externe + fonds_propres_demande)
    attribue = _money(attribue_externe + fonds_propres_attribue)
    recu = _money(recu_externe + fonds_propres_recu)

    # Imputations réelles : subventions externes + fonds propres/autofinancement + autres
    impute_subventions = 0.0
    impute_fonds_propres = 0.0
    impute_autre = 0.0
    source_rows = {}

    for dep in depenses:
        for aff in getattr(dep, "affectations", []) or []:
            amount = float(aff.montant or 0)
            if aff.source_type == "subvention" and aff.subvention and aff.subvention.secteur == secteur and int(aff.subvention.annee_exercice or 0) == int(year):
                is_fp = _is_fonds_propres_enveloppe(aff.subvention, getattr(aff, "ligne_budget", None))
                if is_fp:
                    impute_fonds_propres += amount
                    key = f"fonds-sub-{aff.subvention_id}"
                    row = source_rows.setdefault(key, {
                        "type": "Fonds propres",
                        "label": aff.subvention.nom,
                        "demande": float(aff.subvention.montant_demande or 0),
                        "attribue": float(aff.subvention.montant_attribue or 0),
                        "recu": float(aff.subvention.montant_recu or 0),
                        "impute": 0.0,
                        "reste": 0.0,
                        "subvention": aff.subvention,
                        "is_enveloppe": True,
                    })
                else:
                    impute_subventions += amount
                    key = f"sub-{aff.subvention_id}"
                    row = source_rows.setdefault(key, {
                        "type": "Subvention",
                        "label": aff.subvention.nom,
                        "demande": float(aff.subvention.montant_demande or 0),
                        "attribue": float(aff.subvention.montant_attribue or 0),
                        "recu": float(aff.subvention.montant_recu or 0),
                        "impute": 0.0,
                        "reste": 0.0,
                        "subvention": aff.subvention,
                        "is_enveloppe": True,
                    })
                row["impute"] += amount
            elif aff.source_type == "fonds_propres":
                impute_fonds_propres += amount
                key = "fonds-propres"
                row = source_rows.setdefault(key, {
                    "type": "Fonds propres",
                    "label": aff.libelle_source or "Fonds propres",
                    "demande": 0.0,
                    "attribue": 0.0,
                    "recu": 0.0,
                    "impute": 0.0,
                    "reste": None,
                    "subvention": None,
                    "is_enveloppe": False,
                })
                row["impute"] += amount
            else:
                impute_autre += amount
                key = f"autre-{aff.libelle_source or 'autre'}"
                row = source_rows.setdefault(key, {
                    "type": "Autre",
                    "label": aff.libelle_source or "Autre source",
                    "demande": 0.0,
                    "attribue": 0.0,
                    "recu": 0.0,
                    "impute": 0.0,
                    "reste": None,
                    "subvention": None,
                    "is_enveloppe": False,
                })
                row["impute"] += amount

    for row in source_rows.values():
        if row["type"] in {"Subvention", "Fonds propres"} and row.get("is_enveloppe"):
            base = row["attribue"] if row["attribue"] > 0 else (row["recu"] if row["recu"] > 0 else row["demande"])
            row["reste"] = _money(base - row["impute"])
        row["impute"] = _money(row["impute"])

    impute_subventions = _money(impute_subventions)
    impute_fonds_propres = _money(impute_fonds_propres)
    impute_autre = _money(impute_autre)
    total_impute = _money(impute_subventions + impute_fonds_propres + impute_autre)
    depenses_uniques = _money(sum(float(d.montant or 0) for d in depenses))

    # Groupes budget par compte
    compte_rows = {}
    for line in prev_lines:
        key = (line.nature, line.compte or "")
        row = compte_rows.setdefault(key, {"nature": line.nature, "compte": line.compte or "", "prevu": 0.0, "nb": 0})
        row["prevu"] += float(line.montant or 0)
        row["nb"] += 1
    compte_rows = sorted(
        [
            {**r, "prevu": _money(r["prevu"]), "nature_label": "Produit" if r["nature"] == "produit" else "Charge"}
            for r in compte_rows.values()
        ],
        key=lambda r: (r["nature"], r["compte"])
    )

    # Projets : vue filtrée de l'année
    project_rows = []
    for projet in projets:
        pdata = _projet_finance_context(projet, year)
        pk = pdata["kpis"]
        has_data = any([
            pk.get("charges_prevues", 0),
            pk.get("produits_prevus", 0),
            pk.get("sub_attribue", 0),
            pk.get("sub_recu", 0),
            pk.get("depenses_total", 0),
            pk.get("nb_subventions", 0),
            pk.get("nb_dossiers", 0),
        ])
        project_rows.append({
            "projet": projet,
            "charges_prevues": pk.get("charges_prevues", 0),
            "produits_prevus": pk.get("produits_prevus", 0),
            "sub_attribue": pk.get("sub_attribue", 0),
            "sub_recu": pk.get("sub_recu", 0),
            "depenses_total": pk.get("depenses_total", 0),
            "reste_a_financer": pk.get("reste_a_financer", 0),
            "nb_subventions": pk.get("nb_subventions", 0),
            "nb_dossiers": pk.get("nb_dossiers", 0),
            "has_data": has_data,
        })

    project_rows = sorted(project_rows, key=lambda r: (not r["has_data"], r["projet"].nom.lower()))

    depense_rows = []
    for dep in depenses:
        depense_rows.append({
            "depense": dep,
            "date": dep.date_paiement or (dep.created_at.date() if dep.created_at else None),
            "montant": _money(dep.montant or 0),
            "affecte": _money(dep.total_affecte or 0),
            "reste": _money(dep.reste_a_affecter or 0),
            "statut": dep.statut_affectation,
        })

    sub_rows = []
    for sub in subventions:
        is_fp = _is_fonds_propres_enveloppe(sub)
        sub_rows.append({
            "subvention": sub,
            "type_enveloppe": "Fonds propres" if is_fp else "Subvention",
            "is_fonds_propres": is_fp,
            "demande": _money(sub.montant_demande or 0),
            "attribue": _money(sub.montant_attribue or 0),
            "recu": _money(sub.montant_recu or 0),
            "impute": _money(sub.total_impute_affectations or 0),
            "reste": _money(sub.reste_imputable or 0),
            "pct_impute": _pct(sub.total_impute_affectations or 0, sub.montant_attribue or sub.montant_recu or sub.montant_demande or 0),
        })

    controls = []
    partials = [r for r in depense_rows if r["statut"] != "ok"]

    if not budgets:
        _finance_add_control(controls, "warn", "Prévisionnel", f"Aucun budget prévisionnel global détecté pour {secteur} en {year}.", "Créer ou sélectionner un budget prévisionnel.")
    if charges_prevues > 0 and produits_prevus < charges_prevues:
        _finance_add_control(controls, "warn", "Prévisionnel", f"Déficit prévisionnel : {_money(charges_prevues - produits_prevus):.2f} € à couvrir.", "Vérifier les produits attendus ou les fonds propres.")
    if attribue > 0 and recu > attribue + 0.01:
        _finance_add_control(controls, "danger", "Enveloppes", f"Montant reçu supérieur au montant attribué de {_money(recu - attribue):.2f} €.", "Corriger les montants reçus/attribués.")
    if attribue > 0 and recu < attribue - 0.01:
        _finance_add_control(controls, "warn", "Encaissements", f"Subventions accordées non totalement reçues : {_money(attribue - recu):.2f} € restent à encaisser.", "Suivre les versements restants.")
    if partials:
        _finance_add_control(controls, "danger", "Dépenses", f"{len(partials)} dépense(s) ne sont pas totalement équilibrées en affectations.", "Ouvrir l'onglet Dépenses et corriger les répartitions.")
    if not subventions:
        _finance_add_control(controls, "info", "Enveloppes", "Aucune enveloppe/subvention réelle détectée pour cet exercice.", "Créer une subvention réelle ou une enveloppe fonds propres 99.")
    if not depenses:
        _finance_add_control(controls, "info", "Dépenses", "Aucune dépense imputée détectée pour cet exercice.", "Créer une dépense financée depuis la vue simple.")

    for r in sub_rows:
        sub = r["subvention"]
        base = float(r.get("attribue") or r.get("recu") or r.get("demande") or 0)
        if float(r.get("recu") or 0) > float(r.get("attribue") or 0) + 0.01 and float(r.get("attribue") or 0) > 0:
            _finance_add_control(controls, "danger", "Enveloppes", f"{sub.nom} : reçu supérieur à l'attribué.", "Corriger la fiche de l'enveloppe.")
        if float(r.get("impute") or 0) > base + 0.01 and base > 0:
            _finance_add_control(controls, "danger", "Surconsommation", f"{sub.nom} : dépenses imputées supérieures à l'enveloppe de {_money(float(r.get('impute') or 0) - base):.2f} €.", "Réduire les imputations ou augmenter l'enveloppe réelle.")
        charge_lines = [l for l in getattr(sub, "lignes", []) or [] if getattr(l, "nature", "charge") == "charge"]
        if not charge_lines:
            _finance_add_control(controls, "warn", "Enveloppes", f"{sub.nom} n'a aucune ligne de charge imputable.", "Créer au moins une ligne de charge.")
        for ligne in charge_lines:
            if float(getattr(ligne, "reste", 0) or 0) < -0.01:
                _finance_add_control(controls, "danger", "Lignes", f"{sub.nom} — {ligne.compte} {ligne.libelle} est surconsommée de {_money(abs(float(ligne.reste or 0))):.2f} €.", "Corriger les affectations de dépenses sur cette ligne.")

    for dossier in dossiers:
        if not getattr(dossier, "subvention_id", None) and str(getattr(dossier, "statut", "") or "").lower() in {"accorde", "partiel", "solde"}:
            _finance_add_control(controls, "warn", "Dossiers financeurs", f"{dossier.nom} est marqué {dossier.statut} mais n'est pas lié à une subvention réelle.", "Ouvrir le dossier et créer/mettre à jour l'enveloppe réelle.")

    for s in source_rows.values():
        if not s.get("is_enveloppe") and s.get("type") != "Subvention":
            _finance_add_control(controls, "info", "Sources libres", f"{s.get('label')} est utilisé hors enveloppe formelle.", "Si c'est récurrent, créer une enveloppe Fonds propres 99.")

    alerts = []
    for c in controls:
        if c["level"] in {"danger", "warn", "info"}:
            alerts.append({"level": c["level"], "text": c["message"]})
    if not alerts:
        alerts.append({"level": "ok", "text": "Aucune alerte bloquante détectée sur le pilotage annuel."})
        _finance_add_control(controls, "ok", "Global", "Aucun contrôle bloquant détecté sur l'exercice.", "")

    kpis = {
        "charges_prevues": charges_prevues,
        "produits_prevus": produits_prevus,
        "solde_prevu": _money(produits_prevus - charges_prevues),
        "demande": demande,
        "attribue": attribue,
        "recu": recu,
        "demande_externe": demande_externe,
        "attribue_externe": attribue_externe,
        "recu_externe": recu_externe,
        "fonds_propres_demande": fonds_propres_demande,
        "fonds_propres_attribue": fonds_propres_attribue,
        "fonds_propres_recu": fonds_propres_recu,
        "impute_subventions": impute_subventions,
        "impute_fonds_propres": impute_fonds_propres,
        "impute_autre": impute_autre,
        "total_impute": total_impute,
        "depenses_uniques": depenses_uniques,
        "reste_a_financer": _money(charges_prevues - (attribue if attribue > 0 else produits_prevus)),
        "reste_a_encaisser": _money(attribue - recu),
        "reste_subventions": _money(sum(float(r["reste"] or 0) for r in sub_rows)),
        "pct_finance": _pct(attribue if attribue > 0 else produits_prevus, charges_prevues),
        "pct_recu": _pct(recu, attribue if attribue > 0 else demande),
        "pct_impute": _pct(total_impute, charges_prevues),
        "nb_budgets": len(budgets),
        "nb_subventions": len(subventions_externes),
        "nb_fonds_propres": len(fonds_propres_enveloppes),
        "nb_dossiers": len(dossiers),
        "nb_projets": len([r for r in project_rows if r["has_data"]]),
        "nb_depenses": len(depenses),
        "nb_depenses_a_corriger": len(partials),
        "nb_controles": len([c for c in controls if c.get("level") != "ok"]),
        "nb_controles_danger": len([c for c in controls if c.get("level") == "danger"]),
        "nb_controles_warn": len([c for c in controls if c.get("level") == "warn"]),
    }

    return {
        "kpis": kpis,
        "alerts": alerts,
        "controls": controls,
        "budgets": budgets,
        "prev_lines": prev_lines,
        "subventions": sub_rows,
        "dossiers": dossiers,
        "projets": project_rows,
        "depenses": depense_rows,
        "sources": sorted(source_rows.values(), key=lambda r: (r["type"], r["label"].lower())),
        "comptes": compte_rows,
    }



# ---------------------------------------------------------------------
# P0.2I — Vue simple + dépense financée
# ---------------------------------------------------------------------

def _line_available_amount(ligne) -> float:
    try:
        return _money(getattr(ligne, "reste", 0))
    except Exception:
        return 0.0


def _validate_line_capacity_for_new_affectations(line_amounts: dict[int, float]) -> tuple[bool, str]:
    """Empêche de créer/imputer au-delà du reste disponible d'une ligne.

    C'est un garde-fou métier : une dépense peut être répartie entre plusieurs
    enveloppes, mais chaque enveloppe/ligne ne doit pas être surconsommée.
    """
    for line_id, amount in line_amounts.items():
        ligne = db.session.get(LigneBudget, line_id)
        if not ligne:
            return False, "Une ligne de financement est introuvable."
        available = _line_available_amount(ligne)
        if amount > available + 0.01:
            sub_name = ligne.source_sub.nom if getattr(ligne, "source_sub", None) else "Enveloppe inconnue"
            return False, (
                f"Budget insuffisant sur {sub_name} — {ligne.compte} {ligne.libelle} : "
                f"tu essaies d'imputer {amount:.2f} €, il reste {available:.2f} €."
            )
    return True, ""


def _finance_add_control(controls: list[dict], level: str, zone: str, message: str, action: str = ""):
    controls.append({
        "level": level,
        "zone": zone,
        "message": message,
        "action": action,
    })


def _simple_finance_sources(data: dict) -> list[dict]:
    rows = []
    seen_line_ids = set()

    for r in data.get("subventions", []):
        sub = r.get("subvention")
        if not sub:
            continue

        type_label = "Fonds propres / autofinancement" if r.get("is_fonds_propres") else "Subvention externe"
        for ligne in getattr(sub, "lignes", []) or []:
            if getattr(ligne, "nature", "charge") != "charge":
                continue
            if ligne.id in seen_line_ids:
                continue
            seen_line_ids.add(ligne.id)
            rows.append({
                "ligne": ligne,
                "subvention": sub,
                "type": type_label,
                "label": f"{sub.nom} — {ligne.compte} · {ligne.libelle}",
                "reste": _money(getattr(ligne, "reste", 0)),
                "is_fonds_propres": bool(r.get("is_fonds_propres")),
            })

    return sorted(rows, key=lambda x: (x["is_fonds_propres"], x["subvention"].nom.lower(), x["ligne"].compte or ""))


def _create_depense_from_simple_form(secteur: str, year: int):
    if not can("depenses:create"):
        abort(403)

    libelle = (request.form.get("libelle") or "").strip()
    montant = _parse_float_fr(request.form.get("montant"), 0.0)
    if not libelle:
        flash("Libellé obligatoire pour créer une dépense.", "danger")
        return False, None
    if montant <= 0:
        flash("Le montant de la dépense doit être supérieur à 0 €.", "danger")
        return False, None

    line_ids = request.form.getlist("ligne_budget_id[]") or request.form.getlist("ligne_budget_id")
    amounts = request.form.getlist("montant_affecte[]") or request.form.getlist("montant_affecte")
    comments = request.form.getlist("commentaire_affectation[]") or request.form.getlist("commentaire_affectation")

    affectations = []
    total_affecte = 0.0
    first_line = None

    for idx, raw_line_id in enumerate(line_ids):
        raw_amount = amounts[idx] if idx < len(amounts) else ""
        if not str(raw_amount or "").strip():
            continue
        amount = _parse_float_fr(raw_amount, 0.0)
        if amount <= 0:
            continue

        try:
            line_id = int(raw_line_id or 0)
        except Exception:
            line_id = 0

        ligne = db.session.get(LigneBudget, line_id)
        if not ligne or getattr(ligne, "nature", "charge") != "charge":
            flash("Une ligne de répartition est invalide.", "danger")
            return False, None

        sub = getattr(ligne, "source_sub", None)
        if not sub or sub.secteur != secteur or int(sub.annee_exercice or 0) != int(year):
            flash("Une ligne de répartition ne correspond pas au secteur / exercice sélectionné.", "danger")
            return False, None

        if not can_access_secteur(sub.secteur):
            abort(403)

        if first_line is None:
            first_line = ligne

        comment = (comments[idx] if idx < len(comments) else "").strip() or None
        affectations.append((ligne, amount, comment))
        total_affecte = round(total_affecte + amount, 2)

    if not affectations:
        flash("Ajoute au moins une source de financement pour cette dépense.", "danger")
        return False, None

    if total_affecte > montant + 0.01:
        flash(f"La répartition dépasse la dépense : {total_affecte:.2f} € affectés pour {montant:.2f} €.", "danger")
        return False, None

    auto_complete = (request.form.get("auto_complete_reste") or "").strip() == "1"
    if total_affecte < montant - 0.01 and auto_complete:
        remaining = round(montant - total_affecte, 2)
        affectations.append((first_line, remaining, "Complément automatique sur la première source"))
        total_affecte = round(total_affecte + remaining, 2)

    line_amounts = {}
    for ligne, amount, _comment in affectations:
        line_amounts[ligne.id] = round(float(line_amounts.get(ligne.id, 0.0)) + float(amount or 0), 2)
    ok_capacity, capacity_msg = _validate_line_capacity_for_new_affectations(line_amounts)
    if not ok_capacity:
        flash(capacity_msg, "danger")
        return False, None

    dep = Depense(
        ligne_budget_id=first_line.id,
        libelle=libelle,
        montant=montant,
        date_paiement=_parse_date_or_none(request.form.get("date_paiement")),
        type_depense=(request.form.get("type_depense") or "Fonctionnement").strip(),
        fournisseur=(request.form.get("fournisseur") or "").strip() or None,
        reference_piece=(request.form.get("reference_piece") or "").strip() or None,
        mode_paiement=(request.form.get("mode_paiement") or "").strip() or None,
    )
    db.session.add(dep)
    db.session.flush()

    for ligne, amount, comment in affectations:
        sub = ligne.source_sub
        db.session.add(DepenseAffectation(
            depense_id=dep.id,
            source_type="subvention",
            subvention_id=sub.id,
            ligne_budget_id=ligne.id,
            montant=amount,
            commentaire=comment,
        ))

    db.session.commit()

    if abs(total_affecte - montant) <= 0.01:
        flash("Dépense financée créée et équilibrée.", "success")
    else:
        flash(f"Dépense créée, mais il reste {round(montant - total_affecte, 2):.2f} € à affecter.", "warning")

    return True, dep.id


@bp.route("/finance")
@login_required
@require_perm("projets:view")
def finance_home():
    sectors, selected_sector, years, year = _selected_sector_year()
    if not selected_sector:
        flash("Aucun secteur accessible pour les finances.", "warning")
        return redirect(url_for("projets.projets_list"))
    data = _sector_finance_context(selected_sector, year)
    return render_template(
        "finance_home.html",
        sectors=sectors,
        secteur=selected_sector,
        years=years,
        year=year,
        data=data,
    )


@bp.route("/finance/simple", methods=["GET", "POST"])
@login_required
@require_perm("projets:view")
def finance_simple():
    sectors, selected_sector, years, year = _selected_sector_year()
    if not selected_sector:
        flash("Aucun secteur accessible pour la finance simplifiée.", "warning")
        return redirect(url_for("projets.projets_list"))

    if request.method == "POST":
        ok, dep_id = _create_depense_from_simple_form(selected_sector, year)
        if ok and dep_id:
            return redirect(url_for("budget.depense_edit", depense_id=dep_id))
        return redirect(url_for("projets.finance_simple", secteur=selected_sector, year=year))

    data = _sector_finance_context(selected_sector, year)
    sources = _simple_finance_sources(data)

    return render_template(
        "finance_simple.html",
        sectors=sectors,
        secteur=selected_sector,
        years=years,
        year=year,
        data=data,
        sources=sources,
    )



@bp.route("/finance/secteur")
@login_required
@require_perm("projets:view")
def finance_secteur():
    sectors, selected_sector, years, year = _selected_sector_year()
    if not selected_sector:
        flash("Aucun secteur accessible pour le pilotage financier.", "warning")
        return redirect(url_for("projets.projets_list"))

    tab = (request.args.get("tab") or "vue").strip().lower()
    if tab not in {"vue", "budget", "financeurs", "projets", "depenses", "controles"}:
        tab = "vue"

    data = _sector_finance_context(selected_sector, year)
    return render_template(
        "finance_secteur.html",
        sectors=sectors,
        secteur=selected_sector,
        years=years,
        year=year,
        tab=tab,
        data=data,
    )



