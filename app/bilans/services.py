import datetime
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from sqlalchemy import func

from app.extensions import db
from app.models import Depense, FactureAchat, FactureLigne, LigneBudget, Subvention, InventaireItem

from datetime import date
from sqlalchemy import extract
from app.models import SessionActivite


@dataclass
class BilansScope:
    """Périmètre de lecture (multi-secteurs possible côté finance/direction)."""

    secteurs: Optional[List[str]]  # None = tous


def scope_for_user(user) -> BilansScope:
    """Détermine le périmètre autorisé pour un utilisateur."""
    has_perm = getattr(user, "has_perm", None)
    if callable(has_perm) and has_perm("scope:all_secteurs"):
        return BilansScope(secteurs=None)
    # responsable_secteur : un seul secteur
    sec = getattr(user, "secteur_assigne", None)
    if sec:
        return BilansScope(secteurs=[sec])
    # fallback : rien
    return BilansScope(secteurs=[])


def _apply_secteur_filter(query, scope: BilansScope, secteur_column):
    if scope.secteurs is None:
        return query
    return query.filter(secteur_column.in_(scope.secteurs))


def _year_bounds(year: int) -> Tuple[datetime.date, datetime.date]:
    start = datetime.date(year, 1, 1)
    end = datetime.date(year + 1, 1, 1)
    return start, end


def list_exercice_years(scope) -> list[int]:
    """
    Retourne les années disponibles pour les bilans.

    Correctif P0.3C.2 :
    auparavant la liste dépendait surtout des sessions d'activité.
    Résultat : une année avec des subventions/budgets mais peu ou pas de sessions
    pouvait disparaître du filtre, puis provoquer un abort lors de la sélection.

    On agrège désormais :
    - années de sessions ;
    - années d'exercice des subventions/enveloppes.
    """
    years = set()

    # Années issues des sessions
    q_sessions = (
        db.session.query(extract("year", SessionActivite.date_session).label("y"))
        .filter(SessionActivite.is_deleted == False)
        .filter(SessionActivite.date_session.isnot(None))
    )

    if getattr(scope, "secteurs", None) is not None:
        secteurs = scope.secteurs or []
        if secteurs:
            q_sessions = q_sessions.filter(SessionActivite.secteur.in_(secteurs))
        else:
            cur = date.today().year
            return [cur]

    for row in q_sessions.distinct().all():
        if row.y is not None:
            try:
                years.add(int(row.y))
            except Exception:
                pass

    # Années issues des subventions / enveloppes financières
    q_subs = (
        db.session.query(Subvention.annee_exercice)
        .filter(Subvention.est_archive.is_(False))
        .filter(Subvention.annee_exercice.isnot(None))
    )
    q_subs = _apply_secteur_filter(q_subs, scope, Subvention.secteur)

    for (y,) in q_subs.distinct().all():
        if y is not None:
            try:
                years.add(int(y))
            except Exception:
                pass

    cur = date.today().year
    years.add(cur)

    return sorted(years, reverse=True)


def compute_kpis(year: int, scope: BilansScope) -> Dict[str, float]:
    """KPIs globaux (tuiles)."""
    start, end = _year_bounds(year)

    # Dépenses validées (charges) sur l'année : via Depense + LigneBudget(nature=charge)
    q_dep = (
        db.session.query(func.coalesce(func.sum(Depense.montant), 0.0))
        .join(LigneBudget, Depense.ligne_budget_id == LigneBudget.id)
        .join(Subvention, LigneBudget.subvention_id == Subvention.id)
        .filter(Depense.est_supprimee.is_(False))
        .filter(Depense.statut == "valide")
        .filter(func.coalesce(LigneBudget.nature, "charge") == "charge")
        .filter(Subvention.annee_exercice == year)
    )
    q_dep = _apply_secteur_filter(q_dep, scope, Subvention.secteur)
    depenses = float(q_dep.scalar() or 0.0)

    # Budget disponible = total des lignes "montant_reel" (charges) sur les subventions de l'année
    q_budget = (
        db.session.query(func.coalesce(func.sum(LigneBudget.montant_reel), 0.0))
        .join(Subvention, LigneBudget.subvention_id == Subvention.id)
        .filter(func.coalesce(LigneBudget.nature, "charge") == "charge")
        .filter(Subvention.annee_exercice == year)
    )
    q_budget = _apply_secteur_filter(q_budget, scope, Subvention.secteur)
    budget = float(q_budget.scalar() or 0.0)

    # À ventiler = somme des lignes de facture marquées a_ventiler sur l'année (date facture)
    q_av = (
        db.session.query(func.coalesce(func.sum(FactureLigne.montant_ligne), 0.0))
        .join(FactureAchat, FactureLigne.facture_id == FactureAchat.id)
        .filter(FactureLigne.a_ventiler.is_(True))
        .filter(FactureAchat.date_facture >= start)
        .filter(FactureAchat.date_facture < end)
    )
    q_av = _apply_secteur_filter(q_av, scope, FactureLigne.secteur)
    a_ventiler = float(q_av.scalar() or 0.0)

    # Nombre factures (sur l'année)
    q_fact = (
        db.session.query(func.count(FactureAchat.id))
        .filter(FactureAchat.date_facture >= start)
        .filter(FactureAchat.date_facture < end)
    )
    q_fact = _apply_secteur_filter(q_fact, scope, FactureAchat.secteur_principal)
    nb_factures = int(q_fact.scalar() or 0)

    taux_exec = (depenses / budget * 100.0) if budget > 0 else 0.0
    reste = budget - depenses

    return {
        "depenses": round(depenses, 2),
        "budget": round(budget, 2),
        "taux_exec": round(taux_exec, 1),
        "reste": round(reste, 2),
        "a_ventiler": round(a_ventiler, 2),
        "nb_factures": nb_factures,
    }


def compute_depenses_mensuelles(year: int, scope: BilansScope) -> List[Dict[str, float]]:
    """Séries mensuelles des dépenses (année en cours)."""
    start, end = _year_bounds(year)

    # On essaye d'utiliser la date de facture si la dépense provient d'une ligne de facture,
    # sinon fallback date_paiement, sinon created_at.
    date_expr = func.coalesce(
        FactureAchat.date_facture,
        Depense.date_paiement,
        func.date(Depense.created_at),
    )

    # P0.3C.3 :
    # func.strftime("%m", ...) est spécifique SQLite et plante en PostgreSQL.
    # SQLAlchemy extract("month", ...) compile correctement selon le dialecte :
    # - PostgreSQL : EXTRACT(month FROM ...)
    # - SQLite : STRFTIME sous le capot.
    month_expr = extract("month", date_expr)

    q = (
        db.session.query(month_expr.label("mois"), func.coalesce(func.sum(Depense.montant), 0.0).label("total"))
        .select_from(Depense)
        .join(LigneBudget, Depense.ligne_budget_id == LigneBudget.id)
        .join(Subvention, LigneBudget.subvention_id == Subvention.id)
        .outerjoin(FactureLigne, Depense.facture_ligne_id == FactureLigne.id)
        .outerjoin(FactureAchat, FactureLigne.facture_id == FactureAchat.id)
        .filter(Depense.est_supprimee.is_(False))
        .filter(Depense.statut == "valide")
        .filter(func.coalesce(LigneBudget.nature, "charge") == "charge")
        .filter(date_expr >= start)
        .filter(date_expr < end)
    )
    # filtre secteur : via Subvention.secteur (car Depense rattachée à ligne budget)
    q = _apply_secteur_filter(q, scope, Subvention.secteur)
    q = q.group_by(month_expr).order_by(month_expr)

    by_month = {}
    for m, t in q.all():
        if m is None:
            continue
        try:
            month_num = int(float(m))
        except Exception:
            continue
        by_month[month_num] = float(t or 0.0)
    out = []
    for m in range(1, 13):
        out.append({"mois": m, "total": round(by_month.get(m, 0.0), 2)})
    return out


def compute_depenses_par_secteur(year: int, scope: BilansScope) -> List[Dict[str, float]]:
    """Répartition des dépenses par secteur."""
    q = (
        db.session.query(Subvention.secteur, func.coalesce(func.sum(Depense.montant), 0.0))
        .join(LigneBudget, Depense.ligne_budget_id == LigneBudget.id)
        .join(Subvention, LigneBudget.subvention_id == Subvention.id)
        .filter(Depense.est_supprimee.is_(False))
        .filter(Depense.statut == "valide")
        .filter(func.coalesce(LigneBudget.nature, "charge") == "charge")
        .filter(Subvention.annee_exercice == year)
        .group_by(Subvention.secteur)
        .order_by(func.coalesce(func.sum(Depense.montant), 0.0).desc())
    )
    q = _apply_secteur_filter(q, scope, Subvention.secteur)
    return [{"secteur": s, "total": round(float(t or 0.0), 2)} for s, t in q.all()]


def compute_alertes(year: int, scope: BilansScope, seuil_ventiler: float = 500.0) -> List[Dict[str, str]]:
    """Alertes 'aide' (non bloquantes)."""
    kpis = compute_kpis(year, scope)
    alertes: List[Dict[str, str]] = []

    # À ventiler
    if kpis["a_ventiler"] >= seuil_ventiler:
        alertes.append({
            "niveau": "warn",
            "titre": "Montant important à ventiler",
            "detail": f"{kpis['a_ventiler']:.2f} € en lignes marquées 'À ventiler'.",
        })

    # Consommation (global ou secteur)
    if kpis["taux_exec"] >= 90:
        alertes.append({
            "niveau": "danger",
            "titre": "Budget presque consommé",
            "detail": f"Taux d'exécution {kpis['taux_exec']:.1f}%.",
        })
    # sous-consommation à mi-année
    today = datetime.date.today()
    if today.year == year and today.month >= 7 and kpis["taux_exec"] < 40:
        alertes.append({
            "niveau": "warn",
            "titre": "Sous-consommation (mi-année)",
            "detail": f"Taux d'exécution {kpis['taux_exec']:.1f}% au {today.strftime('%d/%m/%Y')}",
        })

    # Factures sans inventaire associé (aide)
    start, end = _year_bounds(year)
    q_fact = (
        db.session.query(FactureAchat.id)
        .filter(FactureAchat.date_facture >= start)
        .filter(FactureAchat.date_facture < end)
    )
    q_fact = _apply_secteur_filter(q_fact, scope, FactureAchat.secteur_principal)
    facture_ids = [fid for (fid,) in q_fact.all()]
    if facture_ids:
        q_with_inv = (
            db.session.query(FactureAchat.id)
            .join(FactureLigne, FactureLigne.facture_id == FactureAchat.id)
            .join(InventaireItem, InventaireItem.facture_ligne_id == FactureLigne.id)
            .filter(FactureAchat.id.in_(facture_ids))
            .distinct()
        )
        have_inv = set([fid for (fid,) in q_with_inv.all()])
        missing = len([fid for fid in facture_ids if fid not in have_inv])
        if missing > 0:
            alertes.append({
                "niveau": "info",
                "titre": "Factures sans inventaire lié",
                "detail": f"{missing} facture(s) sans entrée inventaire associée.",
            })

    return alertes


def list_secteurs(year: int, scope: BilansScope) -> List[str]:
    """Liste des secteurs visibles (selon périmètre)."""
    q = (
        db.session.query(Subvention.secteur)
        .filter(Subvention.annee_exercice == year)
        .distinct()
        .order_by(Subvention.secteur)
    )
    q = _apply_secteur_filter(q, scope, Subvention.secteur)
    return [s for (s,) in q.all() if s]


def list_subventions(year: int, scope: BilansScope) -> List[Dict[str, object]]:
    """Liste des subventions visibles (id + nom + secteur)."""
    q = (
        db.session.query(Subvention.id, Subvention.nom, Subvention.secteur, Subvention.montant_attribue)
        .filter(Subvention.annee_exercice == year)
        .filter(Subvention.est_archive.is_(False))
        .order_by(Subvention.secteur, Subvention.nom)
    )
    q = _apply_secteur_filter(q, scope, Subvention.secteur)
    return [
        {
            "id": int(i),
            "nom": n,
            "secteur": s,
            "montant_attribue": float(ma or 0.0),
        }
        for (i, n, s, ma) in q.all()
    ]


def compute_bilans_financeurs(year: int, scope: BilansScope) -> Dict[str, object]:
    """Synthèse par financeur (subvention) pour l'exercice : montant accordé,
    dépenses imputées, reste, taux de consommation, reste à ventiler, factures.

    Une ligne par subvention visible dans le périmètre (scope)."""
    q = (
        db.session.query(Subvention)
        .filter(Subvention.annee_exercice == year)
        .filter(Subvention.est_archive.is_(False))
        .order_by(Subvention.secteur, Subvention.nom)
    )
    q = _apply_secteur_filter(q, scope, Subvention.secteur)

    rows = []
    for sub in q.all():
        accorde = float(sub.montant_attribue or 0.0)
        if accorde <= 0:
            accorde = float(sub.total_reel_lignes or 0.0)

        depenses, nb_factures = (
            db.session.query(
                func.coalesce(func.sum(Depense.montant), 0.0),
                func.count(Depense.id),
            )
            .join(LigneBudget, Depense.ligne_budget_id == LigneBudget.id)
            .filter(LigneBudget.subvention_id == sub.id)
            .filter(Depense.est_supprimee.is_(False))
            .filter(Depense.statut == "valide")
            .filter(func.coalesce(LigneBudget.nature, "charge") == "charge")
            .one()
        )
        depenses = float(depenses or 0.0)
        taux = (depenses / accorde * 100.0) if accorde > 0 else 0.0
        # Reste à ventiler : montant reçu non encore réparti sur des lignes réelles.
        a_ventiler = max(0.0, float(sub.montant_recu or 0.0) - float(sub.total_reel_lignes or 0.0))

        rows.append(
            {
                "id": sub.id,
                "secteur": sub.secteur,
                "nom": sub.nom,
                "accorde": round(accorde, 2),
                "depenses": round(depenses, 2),
                "reste": round(accorde - depenses, 2),
                "taux": round(taux, 1),
                "a_ventiler": round(a_ventiler, 2),
                "nb_factures": int(nb_factures or 0),
            }
        )

    totaux = {
        "accorde": round(sum(r["accorde"] for r in rows), 2),
        "depenses": round(sum(r["depenses"] for r in rows), 2),
        "reste": round(sum(r["reste"] for r in rows), 2),
        "a_ventiler": round(sum(r["a_ventiler"] for r in rows), 2),
    }
    return {"rows": rows, "totaux": totaux}


def compute_bilan_secteur(year: int, secteur: str, scope: BilansScope) -> Dict[str, object]:
    """Bilan détaillé d'un secteur : tuiles + tableau lignes budgétaires + top dépenses."""
    if not secteur:
        return {}
    # Sécurité : le secteur demandé doit être dans le scope
    if scope.secteurs is not None and secteur not in scope.secteurs:
        return {}

    # KPIs secteur : on réutilise compute_kpis mais en forçant un scope sur ce secteur
    sec_scope = BilansScope(secteurs=[secteur])
    kpis = compute_kpis(year, sec_scope)

    # Tableau lignes budgétaires (charges uniquement)
    q_lines = (
        db.session.query(
            LigneBudget.id,
            LigneBudget.compte,
            LigneBudget.libelle,
            func.coalesce(LigneBudget.montant_reel, 0.0),
            func.coalesce(func.sum(Depense.montant), 0.0).label("engage"),
        )
        .select_from(LigneBudget)
        .join(Subvention, LigneBudget.subvention_id == Subvention.id)
        .outerjoin(Depense, Depense.ligne_budget_id == LigneBudget.id)
        .filter(Subvention.annee_exercice == year)
        .filter(Subvention.secteur == secteur)
        .filter(func.coalesce(LigneBudget.nature, "charge") == "charge")
        .filter(func.coalesce(Depense.est_supprimee, False).is_(False) | (Depense.id.is_(None)))
        .filter((Depense.statut == "valide") | (Depense.id.is_(None)))
        .group_by(LigneBudget.id)
        .order_by(LigneBudget.compte, LigneBudget.libelle)
    )
    lignes = []
    for lid, compte, libelle, montant_reel, engage in q_lines.all():
        montant_reel = float(montant_reel or 0.0)
        engage = float(engage or 0.0)
        reste = montant_reel - engage
        lignes.append(
            {
                "id": int(lid),
                "compte": compte,
                "libelle": libelle,
                "montant_reel": round(montant_reel, 2),
                "engage": round(engage, 2),
                "reste": round(reste, 2),
                "taux": round((engage / montant_reel * 100.0) if montant_reel > 0 else 0.0, 1),
            }
        )

    # Top 10 dépenses du secteur
    date_expr = func.coalesce(FactureAchat.date_facture, Depense.date_paiement, func.date(Depense.created_at))
    q_top = (
        db.session.query(
            Depense.id,
            date_expr.label("date"),
            func.coalesce(Depense.fournisseur, "").label("fournisseur"),
            Depense.libelle,
            Depense.montant,
            FactureAchat.reference_facture,
        )
        .select_from(Depense)
        .join(LigneBudget, Depense.ligne_budget_id == LigneBudget.id)
        .join(Subvention, LigneBudget.subvention_id == Subvention.id)
        .outerjoin(FactureLigne, Depense.facture_ligne_id == FactureLigne.id)
        .outerjoin(FactureAchat, FactureLigne.facture_id == FactureAchat.id)
        .filter(Depense.est_supprimee.is_(False))
        .filter(Depense.statut == "valide")
        .filter(func.coalesce(LigneBudget.nature, "charge") == "charge")
        .filter(Subvention.annee_exercice == year)
        .filter(Subvention.secteur == secteur)
        .order_by(Depense.montant.desc())
        .limit(10)
    )
    top_depenses = []
    for did, dte, four, lib, mnt, ref in q_top.all():
        top_depenses.append(
            {
                "id": int(did),
                "date": dte.strftime("%d/%m/%Y") if hasattr(dte, "strftime") and dte else "",
                "fournisseur": four or "",
                "libelle": lib or "",
                "montant": round(float(mnt or 0.0), 2),
                "reference": ref or "",
            }
        )

    return {
        "secteur": secteur,
        "kpis": kpis,
        "lignes": lignes,
        "top_depenses": top_depenses,
    }


def compute_bilan_subvention(year: int, subvention_id: int, scope: BilansScope) -> Dict[str, object]:
    """Bilan d'une subvention : tuiles + dépenses imputées + matériel financé."""
    sub = db.session.query(Subvention).filter(Subvention.id == subvention_id).first()
    if not sub or sub.annee_exercice != year:
        return {}
    if scope.secteurs is not None and sub.secteur not in scope.secteurs:
        return {}

    # Montant accordé : montant_attribue (sinon fallback sur budget réel des lignes)
    montant_accorde = float(sub.montant_attribue or 0.0)
    if montant_accorde <= 0:
        montant_accorde = float(sub.total_reel_lignes or 0.0)

    # Dépenses imputées (charges)
    q_dep = (
        db.session.query(func.coalesce(func.sum(Depense.montant), 0.0))
        .join(LigneBudget, Depense.ligne_budget_id == LigneBudget.id)
        .filter(LigneBudget.subvention_id == subvention_id)
        .filter(Depense.est_supprimee.is_(False))
        .filter(Depense.statut == "valide")
        .filter(func.coalesce(LigneBudget.nature, "charge") == "charge")
    )
    depenses = float(q_dep.scalar() or 0.0)
    taux = (depenses / montant_accorde * 100.0) if montant_accorde > 0 else 0.0
    ecart = montant_accorde - depenses

    # Liste des dépenses (détails)
    date_expr = func.coalesce(FactureAchat.date_facture, Depense.date_paiement, func.date(Depense.created_at))
    q_list = (
        db.session.query(
            Depense.id,
            date_expr.label("date"),
            func.coalesce(Depense.fournisseur, "").label("fournisseur"),
            Depense.libelle,
            Depense.montant,
            FactureAchat.reference_facture,
            LigneBudget.compte,
            LigneBudget.libelle.label("ligne_libelle"),
        )
        .select_from(Depense)
        .join(LigneBudget, Depense.ligne_budget_id == LigneBudget.id)
        .outerjoin(FactureLigne, Depense.facture_ligne_id == FactureLigne.id)
        .outerjoin(FactureAchat, FactureLigne.facture_id == FactureAchat.id)
        .filter(LigneBudget.subvention_id == subvention_id)
        .filter(Depense.est_supprimee.is_(False))
        .filter(Depense.statut == "valide")
        .filter(func.coalesce(LigneBudget.nature, "charge") == "charge")
        .order_by(date_expr.desc())
        .limit(300)
    )
    depenses_list = []
    for did, dte, four, lib, mnt, ref, compte, ligne_lib in q_list.all():
        depenses_list.append(
            {
                "id": int(did),
                "date": dte.strftime("%d/%m/%Y") if hasattr(dte, "strftime") and dte else "",
                "fournisseur": four or "",
                "libelle": lib or "",
                "montant": round(float(mnt or 0.0), 2),
                "reference": ref or "",
                "compte": compte or "",
                "ligne": ligne_lib or "",
            }
        )

    # Matériel financé (inventaire) lié à des lignes de facture imputées à cette subvention
    q_mat = (
        db.session.query(
            InventaireItem.id_interne,
            InventaireItem.designation,
            InventaireItem.etat,
            InventaireItem.localisation,
            InventaireItem.valeur_unitaire,
        )
        .select_from(InventaireItem)
        .join(FactureLigne, InventaireItem.facture_ligne_id == FactureLigne.id)
        .filter(FactureLigne.subvention_id == subvention_id)
        .order_by(InventaireItem.created_at.desc())
        .limit(500)
    )
    materiel = []
    for iid, des, etat, loc, val in q_mat.all():
        materiel.append(
            {
                "id_interne": iid or "",
                "designation": des or "",
                "etat": etat or "",
                "localisation": loc or "",
                "valeur": round(float(val or 0.0), 2) if val is not None else "",
            }
        )

    return {
        "subvention": {
            "id": sub.id,
            "nom": sub.nom,
            "secteur": sub.secteur,
        },
        "kpis": {
            "montant_accorde": round(float(montant_accorde or 0.0), 2),
            "depenses": round(depenses, 2),
            "taux": round(taux, 1),
            "ecart": round(ecart, 2),
        },
        "depenses": depenses_list,
        "materiel": materiel,
    }


def compute_qualite_gestion(year: int, scope: BilansScope) -> Dict[str, object]:
    """Indicateurs 'qualité de gestion' (aide, non bloquant)."""
    start, end = _year_bounds(year)

    # Lignes à ventiler (compteur, montant, ancienneté moyenne en jours)
    q = (
        db.session.query(
            FactureLigne.montant_ligne,
            FactureAchat.date_facture,
        )
        .join(FactureAchat, FactureLigne.facture_id == FactureAchat.id)
        .filter(FactureLigne.a_ventiler.is_(True))
        .filter(FactureAchat.date_facture >= start)
        .filter(FactureAchat.date_facture < end)
    )
    q = _apply_secteur_filter(q, scope, FactureLigne.secteur)
    av_rows = q.all()
    nb_av = len(av_rows)
    mt_av = sum(float(montant or 0.0) for montant, _date_facture in av_rows)
    today = datetime.date.today()
    age_rows = []
    for _montant, date_facture in av_rows:
        if not date_facture:
            continue
        if hasattr(date_facture, "date"):
            date_facture = date_facture.date()
        age_rows.append(max(0, (today - date_facture).days))
    avg_days = (sum(age_rows) / len(age_rows)) if age_rows else None

    # Dépenses sans subvention (hors subvention / fonds propres)
    q_hs = (
        db.session.query(func.count(Depense.id), func.coalesce(func.sum(Depense.montant), 0.0))
        .filter(Depense.est_supprimee.is_(False))
        .filter(Depense.statut == "valide")
        .filter(Depense.ligne_budget_id.is_(None))
        .filter(func.coalesce(Depense.date_paiement, func.date(Depense.created_at)) >= start)
        .filter(func.coalesce(Depense.date_paiement, func.date(Depense.created_at)) < end)
    )
    # Filtre secteur : on passe par Depense.secteur si présent, sinon rien
    if hasattr(Depense, "secteur"):
        q_hs = _apply_secteur_filter(q_hs, scope, Depense.secteur)
    nb_hs, mt_hs = q_hs.one()

    # Factures sans inventaire
    q_fact = (
        db.session.query(FactureAchat.id)
        .filter(FactureAchat.date_facture >= start)
        .filter(FactureAchat.date_facture < end)
    )
    q_fact = _apply_secteur_filter(q_fact, scope, FactureAchat.secteur_principal)
    facture_ids = [fid for (fid,) in q_fact.all()]
    missing_inv = 0
    if facture_ids:
        q_with_inv = (
            db.session.query(FactureAchat.id)
            .join(FactureLigne, FactureLigne.facture_id == FactureAchat.id)
            .join(InventaireItem, InventaireItem.facture_ligne_id == FactureLigne.id)
            .filter(FactureAchat.id.in_(facture_ids))
            .distinct()
        )
        have_inv = set([fid for (fid,) in q_with_inv.all()])
        missing_inv = len([fid for fid in facture_ids if fid not in have_inv])

    return {
        "a_ventiler": {
            "nb": int(nb_av or 0),
            "montant": round(float(mt_av or 0.0), 2),
            "age_moyen_jours": int(round(float(avg_days or 0.0))) if avg_days is not None else 0,
        },
        "hors_subvention": {
            "nb": int(nb_hs or 0),
            "montant": round(float(mt_hs or 0.0), 2),
        },
        "factures": {
            "sans_inventaire": int(missing_inv),
        },
    }


def compute_stats_inventaire(year: int, scope: BilansScope) -> Dict[str, object]:
    """Stats inventaire (patrimoine + qualité)."""
    start, end = _year_bounds(year)

    # Total items / valeur
    q = db.session.query(
        func.count(InventaireItem.id),
        func.coalesce(func.sum(func.coalesce(InventaireItem.valeur_unitaire, 0.0) * func.coalesce(InventaireItem.quantite, 1)), 0.0),
    )
    q = _apply_secteur_filter(q, scope, InventaireItem.secteur)
    nb_items, valeur = q.one()

    # Répartition états
    q_etat = (
        db.session.query(InventaireItem.etat, func.count(InventaireItem.id))
        .group_by(InventaireItem.etat)
        .order_by(func.count(InventaireItem.id).desc())
    )
    q_etat = _apply_secteur_filter(q_etat, scope, InventaireItem.secteur)
    repartition = [{"etat": e or "", "nb": int(n or 0)} for e, n in q_etat.all()]

    # Qualité inventaire
    q_no_loc = db.session.query(func.count(InventaireItem.id)).filter(
        (InventaireItem.localisation.is_(None)) | (func.trim(InventaireItem.localisation) == "")
    )
    q_no_loc = _apply_secteur_filter(q_no_loc, scope, InventaireItem.secteur)
    sans_localisation = int(q_no_loc.scalar() or 0)

    q_no_sn = db.session.query(func.count(InventaireItem.id)).filter(
        (InventaireItem.numero_serie.is_(None)) | (func.trim(InventaireItem.numero_serie) == "")
    )
    q_no_sn = _apply_secteur_filter(q_no_sn, scope, InventaireItem.secteur)
    sans_serie = int(q_no_sn.scalar() or 0)

    return {
        "nb_items": int(nb_items or 0),
        "valeur": round(float(valeur or 0.0), 2),
        "repartition": repartition,
        "qualite": {
            "sans_localisation": sans_localisation,
            "sans_serie": sans_serie,
        },
    }


# ---------------------------------------------------------------------
# Bilans lourds (activité + présences + évaluations)
# ---------------------------------------------------------------------

_ETAT_LABELS = {
    0: "Non acquis",
    1: "En cours",
    2: "Acquis",
    3: "Expert",
}


def _compute_bilans_lourds_core(year: int, scope: BilansScope) -> dict:
    """Noyau d'agrégations bilans lourds pour une année donnée."""
    from app.models import AtelierActivite, SessionActivite, PresenceActivite, Evaluation

    # --------- base filters ----------
    start = datetime.date(year, 1, 1)
    end = datetime.date(year + 1, 1, 1)

    # ateliers (non supprimés)
    q_ateliers = db.session.query(func.count(AtelierActivite.id)).filter(
        AtelierActivite.is_deleted.is_(False)
    )
    q_ateliers = _apply_secteur_filter(q_ateliers, scope, AtelierActivite.secteur)
    nb_ateliers = int(q_ateliers.scalar() or 0)

    # sessions réalisées (non supprimées)
    q_sessions = db.session.query(func.count(SessionActivite.id)).filter(
        SessionActivite.is_deleted.is_(False),
        SessionActivite.statut == "realisee",
        func.coalesce(SessionActivite.date_session, SessionActivite.rdv_date) >= start,
        func.coalesce(SessionActivite.date_session, SessionActivite.rdv_date) < end,
    )
    q_sessions = _apply_secteur_filter(q_sessions, scope, SessionActivite.secteur)
    nb_sessions = int(q_sessions.scalar() or 0)

    # présences (émargements) sur sessions de l'année
    q_pres = db.session.query(func.count(PresenceActivite.id)).join(
        SessionActivite, PresenceActivite.session_id == SessionActivite.id
    ).filter(
        SessionActivite.is_deleted.is_(False),
        SessionActivite.statut == "realisee",
        func.coalesce(SessionActivite.date_session, SessionActivite.rdv_date) >= start,
        func.coalesce(SessionActivite.date_session, SessionActivite.rdv_date) < end,
    )
    q_pres = _apply_secteur_filter(q_pres, scope, SessionActivite.secteur)
    nb_presences = int(q_pres.scalar() or 0)

    # participants uniques (distinct) via présence
    q_pu = db.session.query(func.count(func.distinct(PresenceActivite.participant_id))).join(
        SessionActivite, PresenceActivite.session_id == SessionActivite.id
    ).filter(
        SessionActivite.is_deleted.is_(False),
        SessionActivite.statut == "realisee",
        func.coalesce(SessionActivite.date_session, SessionActivite.rdv_date) >= start,
        func.coalesce(SessionActivite.date_session, SessionActivite.rdv_date) < end,
    )
    q_pu = _apply_secteur_filter(q_pu, scope, SessionActivite.secteur)
    nb_participants_uniques = int(q_pu.scalar() or 0)

    # fidélisation : participants revenus >= 2 fois sur la période
    q_returning = db.session.query(PresenceActivite.participant_id).join(
        SessionActivite, PresenceActivite.session_id == SessionActivite.id
    ).filter(
        SessionActivite.is_deleted.is_(False),
        SessionActivite.statut == "realisee",
        func.coalesce(SessionActivite.date_session, SessionActivite.rdv_date) >= start,
        func.coalesce(SessionActivite.date_session, SessionActivite.rdv_date) < end,
    )
    q_returning = _apply_secteur_filter(q_returning, scope, SessionActivite.secteur)
    q_returning = q_returning.group_by(PresenceActivite.participant_id).having(func.count(PresenceActivite.id) >= 2)
    nb_participants_retour = int(q_returning.count() or 0)
    taux_fidelisation = round((nb_participants_retour / nb_participants_uniques) * 100, 1) if nb_participants_uniques > 0 else 0.0

    intensite_accompagnement = round((nb_presences / nb_participants_uniques), 2) if nb_participants_uniques > 0 else 0.0

    # collectif : remplissage (présences / total places)
    # dus aux sur-bookings ponctuels, erreurs de capacité ou imports historiques.
    # NOTE: on cappe le numérateur par session à la capacité pour éviter des taux absurdes (>100%)
    q_coll_sessions = db.session.query(
    SessionActivite.id,
    func.coalesce(SessionActivite.capacite, AtelierActivite.capacite_defaut, 0).label("cap_effective"),
    )

    q_coll_sessions = q_coll_sessions.join(
        AtelierActivite, AtelierActivite.id == SessionActivite.atelier_id
    )

    q_coll_sessions = q_coll_sessions.filter(
        SessionActivite.is_deleted.is_(False),
        SessionActivite.statut == "realisee",
        SessionActivite.session_type == "COLLECTIF",
        SessionActivite.date_session >= start,
        SessionActivite.date_session < end,
    )

    q_coll_sessions = _apply_secteur_filter(q_coll_sessions, scope, SessionActivite.secteur)
    coll_sessions = q_coll_sessions.all()
    coll_session_ids = [sid for sid, _cap in coll_sessions]
    q_coll_sessions = _apply_secteur_filter(q_coll_sessions, scope, SessionActivite.secteur)
    coll_sessions = q_coll_sessions.all()
    coll_session_ids = [sid for sid, _cap in coll_sessions]

    if coll_session_ids:
        pres_rows = (
            db.session.query(PresenceActivite.session_id, func.count(PresenceActivite.id))
            .filter(PresenceActivite.session_id.in_(coll_session_ids))
            .group_by(PresenceActivite.session_id)
            .all()
        )
    else:
        pres_rows = []
    pres_by_session = {sid: int(cnt or 0) for sid, cnt in pres_rows}

    total_places_collectif = 0
    nb_presences_collectif = 0
    nb_presences_collectif_capped = 0
    for sid, cap in coll_sessions:
        pres_count = int(pres_by_session.get(sid, 0))
        nb_presences_collectif += pres_count
        cap_value = int(cap or 0)
        if cap_value > 0:
            total_places_collectif += cap_value
            nb_presences_collectif_capped += min(pres_count, cap_value)

    taux_remplissage_collectif = 0
    if total_places_collectif > 0:
        taux_remplissage_collectif = int(round((nb_presences_collectif_capped / total_places_collectif) * 100))

    # rdv individuel : nb + minutes
    q_rdv = db.session.query(func.count(SessionActivite.id)).filter(
        SessionActivite.is_deleted.is_(False),
        SessionActivite.statut == "realisee",
        SessionActivite.session_type != "COLLECTIF",
        SessionActivite.rdv_date >= start,
        SessionActivite.rdv_date < end,
    )
    q_rdv = _apply_secteur_filter(q_rdv, scope, SessionActivite.secteur)
    nb_rdv = int(q_rdv.scalar() or 0)

    q_rdv_min = db.session.query(func.sum(SessionActivite.duree_minutes)).filter(
        SessionActivite.is_deleted.is_(False),
        SessionActivite.statut == "realisee",
        SessionActivite.session_type != "COLLECTIF",
        SessionActivite.rdv_date >= start,
        SessionActivite.rdv_date < end,
    )
    q_rdv_min = _apply_secteur_filter(q_rdv_min, scope, SessionActivite.secteur)
    minutes_rdv = int(q_rdv_min.scalar() or 0)

    # évaluations (sur l'année)
    # On compte uniquement les évaluations rattachées à une session réelle du périmètre,
    # pour éviter le bruit des évaluations legacy sans session (session_id NULL) qui gonfle les bilans.
    base_eval = db.session.query(Evaluation.id, Evaluation.etat, Evaluation.competence_id, Evaluation.participant_id, Evaluation.session_id).join(
        SessionActivite, Evaluation.session_id == SessionActivite.id
    ).filter(
        SessionActivite.is_deleted.is_(False),
        SessionActivite.statut == "realisee",
        func.coalesce(SessionActivite.date_session, SessionActivite.rdv_date) >= start,
        func.coalesce(SessionActivite.date_session, SessionActivite.rdv_date) < end,
    )
    base_eval = _apply_secteur_filter(base_eval, scope, SessionActivite.secteur)

    total_eval_items_manuel = int(base_eval.with_entities(func.count(Evaluation.id)).scalar() or 0)

    # participants / compétences évalués manuellement (ensembles, pour l'union avec le portail)
    manual_pids = {r[0] for r in base_eval.with_entities(Evaluation.participant_id).distinct().all()}
    manual_cids = {r[0] for r in base_eval.with_entities(Evaluation.competence_id).distinct().all()}

    total_eval_sessions = int(
        base_eval.with_entities(func.count(func.distinct(Evaluation.session_id))).scalar() or 0
    )

    # évaluations manuelles par état (volume d'items compétences)
    par_etat_manuel = {}
    for etat in (0, 1, 2, 3):
        q = base_eval.filter(Evaluation.etat == etat)
        par_etat_manuel[etat] = int(q.with_entities(func.count(Evaluation.id)).scalar() or 0)

    # --- Contribution portail, fusionnée dans les totaux ---
    # Attribution validée : secteur du participant + année de la tentative (finished_at).
    from datetime import datetime as _dt, timedelta as _td
    from app.models import Participant
    from app.pedagogie.services import niveaux_portail_par_paire

    _s = start.date() if isinstance(start, _dt) else start
    _e = end.date() if isinstance(end, _dt) else end
    portal_pairs = niveaux_portail_par_paire(None, _s, _e - _td(days=1))  # période [start, end)
    portal_part, portal_comp = set(), set()
    portail_items = 0
    portail_par_niveau = {0: 0, 1: 0, 2: 0, 3: 0}
    if portal_pairs:
        _pids = {pid for (pid, _cid) in portal_pairs}
        _secteur_by_pid = {
            p.id: (p.created_secteur or "")
            for p in Participant.query.filter(Participant.id.in_(_pids)).all()
        }
        _scope_secteurs = set(scope.secteurs) if scope.secteurs is not None else None
        for (pid, cid), niveau in portal_pairs.items():
            if _scope_secteurs is not None and _secteur_by_pid.get(pid, "") not in _scope_secteurs:
                continue
            portal_part.add(pid)
            portal_comp.add(cid)
            portail_items += 1
            portail_par_niveau[niveau] = portail_par_niveau.get(niveau, 0) + 1

    total_eval_items = total_eval_items_manuel + portail_items
    total_eval = len(manual_pids | portal_part)
    nb_comp_u = len(manual_cids | portal_comp)
    par_etat = [
        (_ETAT_LABELS.get(etat, str(etat)), par_etat_manuel[etat] + portail_par_niveau.get(etat, 0))
        for etat in (0, 1, 2, 3)
    ]

    # détail par secteur (dans le périmètre)
    secteurs = scope.secteurs if scope.secteurs is not None else list_secteurs(year, scope)
    par_secteur = []
    for sec in secteurs:
        q_a = db.session.query(func.count(AtelierActivite.id)).filter(AtelierActivite.is_deleted.is_(False), AtelierActivite.secteur == sec)
        q_s = db.session.query(func.count(SessionActivite.id)).filter(
            SessionActivite.is_deleted.is_(False),
            SessionActivite.statut == "realisee",
            SessionActivite.secteur == sec,
            func.coalesce(SessionActivite.date_session, SessionActivite.rdv_date) >= start,
            func.coalesce(SessionActivite.date_session, SessionActivite.rdv_date) < end,
        )
        q_p = db.session.query(func.count(PresenceActivite.id)).join(SessionActivite, PresenceActivite.session_id == SessionActivite.id).filter(
            SessionActivite.is_deleted.is_(False),
            SessionActivite.statut == "realisee",
            SessionActivite.secteur == sec,
            func.coalesce(SessionActivite.date_session, SessionActivite.rdv_date) >= start,
            func.coalesce(SessionActivite.date_session, SessionActivite.rdv_date) < end,
        )
        q_pu2 = db.session.query(func.count(func.distinct(PresenceActivite.participant_id))).join(SessionActivite, PresenceActivite.session_id == SessionActivite.id).filter(
            SessionActivite.is_deleted.is_(False),
            SessionActivite.statut == "realisee",
            SessionActivite.secteur == sec,
            func.coalesce(SessionActivite.date_session, SessionActivite.rdv_date) >= start,
            func.coalesce(SessionActivite.date_session, SessionActivite.rdv_date) < end,
        )

        par_secteur.append({
            "secteur": sec,
            "nb_ateliers": int(q_a.scalar() or 0),
            "nb_sessions": int(q_s.scalar() or 0),
            "nb_presences": int(q_p.scalar() or 0),
            "nb_participants_uniques": int(q_pu2.scalar() or 0),
        })

    return {
        "activite": {
            "nb_ateliers": nb_ateliers,
            "nb_sessions": nb_sessions,
            "nb_presences": nb_presences,
            "nb_participants_uniques": nb_participants_uniques,
            "total_places_collectif": total_places_collectif,
            "nb_presences_collectif": nb_presences_collectif,
            "taux_remplissage_collectif": taux_remplissage_collectif,
            "nb_rdv": nb_rdv,
            "minutes_rdv": minutes_rdv,
            "nb_participants_retour": nb_participants_retour,
            "taux_fidelisation": taux_fidelisation,
            "intensite_accompagnement": intensite_accompagnement,
        },
        "evaluations": {
            "total": total_eval,
            "total_items": total_eval_items,
            "total_sessions": total_eval_sessions,
            "par_etat": par_etat,
            "nb_competences_uniques": nb_comp_u,
            "dont_portail": {
                "participants": len(portal_part),
                "competences": len(portal_comp),
                "items": portail_items,
            },
        },
        "par_secteur": par_secteur,
    }


def compute_bilans_lourds(year: int, scope: BilansScope) -> dict:
    """Stats plus 'lourdes' (multi-agrégations) avec filtre par périmètre utilisateur.

    Compatible SQLite & Postgres (pas de fonctions exotiques).
    """
    current = _compute_bilans_lourds_core(year, scope)
    previous = _compute_bilans_lourds_core(year - 1, scope)
    current["comparatif"] = {
        "annee_precedente": year - 1,
        "activite": previous.get("activite", {}),
        "evaluations": previous.get("evaluations", {}),
    }
    return current
