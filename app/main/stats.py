from datetime import date


from app.extensions import db
from app.services.indicators import compute_project_indicators
from flask import (
    render_template, request, current_app
)
from flask_login import login_required, current_user
from app.rbac import require_perm, can

from app.models import (
    Subvention,
    Projet,
    SubventionProjet,
)
from app.services.consumption import aggregate_individual_consumption


from app.main.common import bp, can_see_secteur

def _eco_date_range_from_year(selected_annee: int | None):
    if selected_annee:
        return date(selected_annee, 1, 1), date(selected_annee, 12, 31)
    return None, None


def _eco_conso_stats_for_context(selected_annee=None, selected_secteur=None, selected_projet_id=None):
    date_from, date_to = _eco_date_range_from_year(selected_annee)
    secteur = selected_secteur
    if not (can("stats:view_all") or can("scope:all_secteurs")):
        secteur = current_user.secteur_assigne
    try:
        return aggregate_individual_consumption(
            date_from=date_from,
            date_to=date_to,
            secteur=secteur,
            projet_id=selected_projet_id,
        )
    except Exception:
        current_app.logger.exception("Impossible de calculer l'éco-conso individuelle pour les stats")
        return {
            "total_kwh": 0.0,
            "total_co2": 0.0,
            "sessions_count": 0,
            "presences_count": 0,
            "participants_count": 0,
            "materiel_lines_count": 0,
            "avg_kwh_per_presence": 0.0,
            "top_materiels": [],
            "by_atelier": [],
            "by_secteur": [],
        }


# --------- Stats ---------
@bp.route("/stats")
@login_required
@require_perm("stats:view")
def stats():
    """
    Vue synthèse des budgets avec représentation graphique.

    On peut filtrer par année et/ou secteur via des paramètres GET.
    Responsable de secteur : le filtre secteur est forcé sur son secteur.
    Option : filtre projet (projet_id) pour croiser finance + indicateurs participants.
    """
    has_global_scope = can("stats:view_all") or can("scope:all_secteurs")

    # --- Lecture filtres (année, secteur, projet) ---
    annee_raw = (request.args.get("annee") or "").strip()
    secteur_raw = (request.args.get("secteur") or "").strip()
    projet_id_raw = (request.args.get("projet_id") or "").strip()

    selected_annee: int | None = None
    if annee_raw:
        try:
            selected_annee = int(annee_raw)
        except ValueError:
            selected_annee = None

    selected_secteur: str | None = secteur_raw or None

    selected_projet_id: int | None = None
    if projet_id_raw:
        try:
            selected_projet_id = int(projet_id_raw)
        except ValueError:
            selected_projet_id = None

    # --- Base query ---
    sub_q = Subvention.query.filter_by(est_archive=False)
    proj_q = Projet.query

    # Filtre année
    if selected_annee:
        sub_q = sub_q.filter(Subvention.annee_exercice == selected_annee)

    # Filtre secteur
    if selected_secteur:
        sub_q = sub_q.filter(Subvention.secteur == selected_secteur)
        proj_q = proj_q.filter(Projet.secteur == selected_secteur)

    # Filtre projet (finance)
    if selected_projet_id:
        proj_q = proj_q.filter(Projet.id == selected_projet_id)
        sub_q = sub_q.join(SubventionProjet, SubventionProjet.subvention_id == Subvention.id)                   .filter(SubventionProjet.projet_id == selected_projet_id)

    # Restriction responsable secteur
    if not has_global_scope:
        sub_q = sub_q.filter(Subvention.secteur == current_user.secteur_assigne)
        proj_q = proj_q.filter(Projet.secteur == current_user.secteur_assigne)
        selected_secteur = current_user.secteur_assigne

        # On blind le projet : seulement ceux de son secteur
        if selected_projet_id:
            p_tmp = db.session.get(Projet, selected_projet_id)
            if not p_tmp or p_tmp.secteur != current_user.secteur_assigne:
                selected_projet_id = None

    subs = sub_q.order_by(Subvention.annee_exercice.desc(), Subvention.nom.asc()).all()
    projets = proj_q.order_by(Projet.nom.asc()).all()

    # Pré-calcul des années disponibles (pour sélecteur)
    all_annees = sorted({s.annee_exercice for s in Subvention.query.filter_by(est_archive=False).all()}, reverse=True)
    all_secteurs = current_app.config.get("SECTEURS", [])

    # --- Totaux globaux ---
    total_recu = round(sum(float(s.montant_recu or 0) for s in subs), 2)
    total_engage = round(sum(float(s.total_engage or 0) for s in subs), 2)
    total_reste = round(sum(float(s.total_reste or 0) for s in subs), 2)

    # --- Agrégation par secteur ---
    by_secteur: dict[str, dict[str, float]] = {}
    for s in subs:
        d = by_secteur.setdefault(s.secteur, {"recu": 0.0, "engage": 0.0, "reste": 0.0})
        d["recu"] += float(s.montant_recu or 0)
        d["engage"] += float(s.total_engage or 0)
        d["reste"] += float(s.total_reste or 0)
    for sec, vals in by_secteur.items():
        vals["recu"] = round(vals.get("recu", 0.0), 2)
        vals["engage"] = round(vals.get("engage", 0.0), 2)
        vals["reste"] = round(vals.get("reste", 0.0), 2)

    # --- Agrégation par compte ---
    by_compte: dict[str, dict[str, float]] = {}
    for s in subs:
        for l in s.lignes:
            d = by_compte.setdefault(l.compte, {"reel": 0.0, "engage": 0.0, "reste": 0.0})
            d["reel"] += float(l.montant_reel or 0)
            d["engage"] += float(l.engage or 0)
            d["reste"] += float(l.reste or 0)
    for comp, vals in by_compte.items():
        vals["reel"] = round(vals.get("reel", 0.0), 2)
        vals["engage"] = round(vals.get("engage", 0.0), 2)
        vals["reste"] = round(vals.get("reste", 0.0), 2)

    # --- Détails par projet ---
    by_projet: list[dict[str, float | str]] = []
    for p in projets:
        by_projet.append({
            "id": p.id,
            "nom": p.nom,
            "secteur": p.secteur,
            "demande": p.total_demande,
            "attribue": p.total_attribue,
            "recu": p.total_recu,
            "reel_lignes": p.total_reel_lignes,
            "engage": p.total_engage,
            "reste": p.total_reste,
        })

    # Valeurs max pour barres proportionnelles
    max_secteur_total = max([v["recu"] + v["engage"] + v["reste"] for v in by_secteur.values()] + [0.0])
    max_compte_total = max([v["reel"] + v["engage"] + v["reste"] for v in by_compte.values()] + [0.0])
    max_projet_total = max([p["recu"] + p["engage"] + p["reste"] for p in by_projet] + [0.0])

    # --- Indicateurs projet (si projet sélectionné) ---
    project_indicators = []
    selected_projet = None

    if selected_projet_id:
        selected_projet = db.session.get(Projet, selected_projet_id)

    if selected_projet and can_see_secteur(selected_projet.secteur):
        project_indicators = compute_project_indicators(
            selected_projet,
            selected_annee=selected_annee,
            subventions=subs,
        )

    eco_conso_stats = _eco_conso_stats_for_context(selected_annee, selected_secteur, selected_projet_id)

    return render_template(
        "stats.html",
        total_recu=total_recu,
        total_engage=total_engage,
        total_reste=total_reste,
        by_secteur=by_secteur,
        by_compte=by_compte,
        by_projet=by_projet,
        max_secteur_total=max_secteur_total,
        max_compte_total=max_compte_total,
        max_projet_total=max_projet_total,
        all_annees=all_annees,
        all_secteurs=all_secteurs,
        selected_annee=selected_annee,
        selected_secteur=selected_secteur,
        selected_projet_id=selected_projet_id,
        projets_for_filter=projets,
        selected_projet=selected_projet,
        project_indicators=project_indicators,
        eco_conso_stats=eco_conso_stats,
    )


# --- Hub ergonomique : 1 menu "Stats & bilans" ---
@bp.route("/stats-bilans")
@login_required
@require_perm("stats:view")
def stats_bilans():
    has_global_scope = can("stats:view_all") or can("scope:all_secteurs")

    annee_raw = (request.args.get("annee") or "").strip()
    secteur_raw = (request.args.get("secteur") or "").strip()
    projet_id_raw = (request.args.get("projet_id") or "").strip()

    selected_annee = None
    if annee_raw:
        try:
            selected_annee = int(annee_raw)
        except ValueError:
            selected_annee = None

    selected_secteur = secteur_raw or None

    selected_projet_id = None
    if projet_id_raw:
        try:
            selected_projet_id = int(projet_id_raw)
        except ValueError:
            selected_projet_id = None

    sub_q = Subvention.query.filter_by(est_archive=False)
    proj_q = Projet.query

    if selected_annee is not None:
        sub_q = sub_q.filter(Subvention.annee_exercice == selected_annee)
    if selected_secteur:
        sub_q = sub_q.filter(Subvention.secteur == selected_secteur)
        proj_q = proj_q.filter(Projet.secteur == selected_secteur)
    if selected_projet_id:
        sub_q = sub_q.join(SubventionProjet, SubventionProjet.subvention_id == Subvention.id)\
                     .filter(SubventionProjet.projet_id == selected_projet_id)
        proj_q = proj_q.filter(Projet.id == selected_projet_id)

    if not has_global_scope:
        selected_secteur = current_user.secteur_assigne
        sub_q = sub_q.filter(Subvention.secteur == selected_secteur)
        proj_q = proj_q.filter(Projet.secteur == selected_secteur)
        if selected_projet_id:
            pjt = db.session.get(Projet, selected_projet_id)
            if not pjt or pjt.secteur != selected_secteur:
                selected_projet_id = None
                proj_q = Projet.query.filter(Projet.secteur == selected_secteur)

    subs = sub_q.order_by(Subvention.annee_exercice.desc(), Subvention.nom.asc()).all()
    projets = proj_q.order_by(Projet.nom.asc()).all()
    selected_projet = next((p for p in projets if p.id == selected_projet_id), None)

    annees = sorted({s.annee_exercice for s in Subvention.query.filter_by(est_archive=False).all()}, reverse=True)
    secteurs = current_app.config.get("SECTEURS", []) or sorted({s.secteur for s in Subvention.query.filter_by(est_archive=False).all() if s.secteur})
    if not has_global_scope:
        secteurs = [current_user.secteur_assigne]

    total_recu = round(sum(float(s.montant_recu or 0) for s in subs), 2)
    total_engage = round(sum(float(s.total_engage or 0) for s in subs), 2)
    total_reste = round(sum(float(s.total_reste or 0) for s in subs), 2)
    total_demande = round(sum(float(s.montant_demande or 0) for s in subs), 2)

    kpis = {
        "subventions": len(subs),
        "projets": len(projets) if not selected_projet_id else (1 if selected_projet else 0),
        "recu": total_recu,
        "engage": total_engage,
        "reste": total_reste,
        "taux_conso": round((total_engage / total_recu) * 100, 1) if total_recu else 0.0,
    }

    by_secteur = []
    by_sec_map = {}
    for s in subs:
        d = by_sec_map.setdefault(s.secteur or "—", {"secteur": s.secteur or "—", "recu": 0.0, "engage": 0.0, "reste": 0.0})
        d["recu"] += float(s.montant_recu or 0)
        d["engage"] += float(s.total_engage or 0)
        d["reste"] += float(s.total_reste or 0)
    for row in by_sec_map.values():
        row["recu"] = round(row["recu"], 2)
        row["engage"] = round(row["engage"], 2)
        row["reste"] = round(row["reste"], 2)
        by_secteur.append(row)
    by_secteur.sort(key=lambda x: x["recu"], reverse=True)

    top_subventions = []
    for s in sorted(subs, key=lambda x: float(x.montant_recu or 0), reverse=True)[:5]:
        top_subventions.append({
            "nom": s.nom,
            "annee": s.annee_exercice,
            "secteur": s.secteur,
            "recu": round(float(s.montant_recu or 0), 2),
            "engage": round(float(s.total_engage or 0), 2),
            "reste": round(float(s.total_reste or 0), 2),
        })

    alertes = []
    for s in subs:
        recu = float(s.montant_recu or 0)
        reel_lignes = float(s.total_reel_lignes or 0)
        engage = float(s.total_engage or 0)
        if recu > 0 and reel_lignes == 0:
            alertes.append({"niveau": "danger", "texte": f"{s.nom} : reçu {recu:.2f}€ mais lignes réel = 0€ (ventilation manquante)."})
        elif recu > 0 and reel_lignes > 0 and reel_lignes < recu:
            alertes.append({"niveau": "warning", "texte": f"{s.nom} : reçu {recu:.2f}€ mais lignes réel = {reel_lignes:.2f}€ (ventilation incomplète)."})
        elif reel_lignes > 0 and engage > reel_lignes:
            alertes.append({"niveau": "danger", "texte": f"{s.nom} : engagé {engage:.2f}€ > lignes réel {reel_lignes:.2f}€ (dépassement)."})
    alertes = alertes[:5]

    summary = {
        "filters_label": {
            "annee": selected_annee if selected_annee is not None else "toutes",
            "secteur": selected_secteur if selected_secteur else "tous",
            "projet": selected_projet.nom if selected_projet else "tous",
        },
        "total_demande": total_demande,
    }

    eco_conso_stats = _eco_conso_stats_for_context(selected_annee, selected_secteur, selected_projet_id)

    return render_template(
        "stats_bilans.html",
        annees=annees,
        secteurs=secteurs,
        projets=projets,
        selected_annee=selected_annee,
        selected_secteur=selected_secteur,
        selected_projet_id=selected_projet_id,
        selected_projet=selected_projet,
        kpis=kpis,
        by_secteur=by_secteur,
        top_subventions=top_subventions,
        alertes=alertes,
        summary=summary,
        eco_conso_stats=eco_conso_stats,
    )




