import csv
from io import StringIO, BytesIO
from datetime import date, timedelta


from flask import (
    render_template, request, redirect, url_for, flash, abort,
    current_app, Response, jsonify
)
from flask_login import login_required, current_user
from app.rbac import require_perm, can

from app.extensions import db
from app.models import (
    Subvention,
    SUBVENTION_STATUTS,
    SUBVENTION_STATUTS_DICT,
    SubventionAtelier,
    AtelierActivite,
    LigneBudget,
    Depense,
    Projet,
    SubventionProjet,
    AuditLog,
)
from app.previsionnel.referentiel import BudgetCategorieReferentiel, BudgetCompteReferentiel
from app.services.audit import journaliser


from app.main.common import bp, can_see_secteur
from app.utils.delete_guard import commit_delete

# Horizon (en jours) au-delà duquel une échéance n'est plus signalée comme « proche ».
ECHEANCE_HORIZON_JOURS = 30


def _parse_money(value, default=0.0) -> float:
    raw = str(value or "").replace(" ", "").replace(",", ".")
    try:
        return float(raw) if raw else float(default)
    except Exception:
        return float(default)


def _parse_date(value):
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except Exception:
        return None


def alertes_echeances(subs, today=None, horizon_jours: int = ECHEANCE_HORIZON_JOURS):
    """Construit les alertes d'échéance pour une liste de subventions.

    Retourne une liste de dicts ``{sub, niveau, message}`` triée par gravité.
    ``niveau`` ∈ {"danger", "warning"}. Ne touche jamais la base.
    """
    if today is None:
        today = date.today()
    alertes = []
    for s in subs:
        statut = s.statut_cycle or "sollicitee"
        if statut in ("soldee", "refusee"):
            continue

        # Versement attendu dépassé alors que tout n'est pas reçu.
        dvp = s.date_versement_prevu
        recu = float(s.montant_recu or 0)
        attribue = float(s.montant_attribue or 0)
        if dvp and recu < attribue:
            if dvp < today:
                alertes.append({"sub": s, "niveau": "danger",
                                "message": f"Versement attendu le {dvp.strftime('%d/%m/%Y')} non soldé ({recu:.0f}€ reçus sur {attribue:.0f}€ attribués)."})
            elif dvp <= today + timedelta(days=horizon_jours):
                alertes.append({"sub": s, "niveau": "warning",
                                "message": f"Versement attendu le {dvp.strftime('%d/%m/%Y')} (dans {(dvp - today).days} j)."})

        # Bilan / compte-rendu à rendre au financeur.
        dbp = s.date_bilan_prevu
        if dbp:
            if dbp < today:
                alertes.append({"sub": s, "niveau": "danger",
                                "message": f"Bilan à rendre depuis le {dbp.strftime('%d/%m/%Y')} (dépassé)."})
            elif dbp <= today + timedelta(days=horizon_jours):
                alertes.append({"sub": s, "niveau": "warning",
                                "message": f"Bilan à rendre le {dbp.strftime('%d/%m/%Y')} (dans {(dbp - today).days} j)."})

        # Dossier déposé mais sans décision depuis longtemps.
        if statut == "sollicitee" and s.date_depot and not s.date_decision:
            if s.date_depot < today - timedelta(days=120):
                alertes.append({"sub": s, "niveau": "warning",
                                "message": f"Demande déposée le {s.date_depot.strftime('%d/%m/%Y')} toujours sans décision (plus de 4 mois)."})

    alertes.sort(key=lambda a: 0 if a["niveau"] == "danger" else 1)
    return alertes


def _ref_scope_query(query, model, secteur: str | None):
    if secteur:
        return query.filter((model.secteur.is_(None)) | (model.secteur == secteur))
    return query


def _budget_categories_ref_for_secteur(secteur: str | None = None, nature: str | None = None, active_only: bool = True):
    q = BudgetCategorieReferentiel.query.join(BudgetCompteReferentiel)
    q = _ref_scope_query(q, BudgetCategorieReferentiel, secteur)
    if secteur:
        q = q.filter((BudgetCompteReferentiel.secteur.is_(None)) | (BudgetCompteReferentiel.secteur == secteur))
    if nature in ("charge", "produit"):
        q = q.filter(BudgetCategorieReferentiel.nature == nature)
    if active_only:
        q = q.filter(BudgetCategorieReferentiel.actif.is_(True), BudgetCompteReferentiel.actif.is_(True))
    return q.order_by(
        BudgetCompteReferentiel.nature.asc(),
        BudgetCompteReferentiel.ordre.asc(),
        BudgetCompteReferentiel.code.asc(),
        BudgetCategorieReferentiel.ordre.asc(),
        BudgetCategorieReferentiel.libelle.asc(),
    ).all()


def _budget_category_allowed(cat: BudgetCategorieReferentiel, secteur: str | None, nature: str | None = None) -> bool:
    if not cat or not getattr(cat, "compte", None):
        return False
    if nature in ("charge", "produit") and cat.nature != nature:
        return False
    if cat.secteur and secteur and cat.secteur != secteur:
        return False
    if cat.compte.secteur and secteur and cat.compte.secteur != secteur:
        return False
    return bool(cat.actif and cat.compte.actif)


def _budget_category_compte_label(cat: BudgetCategorieReferentiel) -> str:
    if not cat or not getattr(cat, "compte", None):
        return ""
    return f"{cat.compte.code} - {cat.compte.libelle}".strip(" -")


def _compute_prorata(lignes, montant_cible: float):
    """
    Calcule une répartition pro-rata sur montant_base.
    Ne modifie pas la DB : retourne un dict {ligne_id: montant_theorique}
    Ajuste la dernière ligne pour tomber pile au centime.
    """
    lignes = list(lignes)
    out = {}
    if not lignes:
        return out

    total_base = sum(float(l.montant_base or 0) for l in lignes)
    if total_base <= 0:
        for l in lignes:
            out[l.id] = 0.0
        return out

    ratio = float(montant_cible or 0) / total_base

    cumul = 0.0
    for i, l in enumerate(lignes):
        base = float(l.montant_base or 0)
        part = round(base * ratio, 2)
        if i == len(lignes) - 1:
            part = round(float(montant_cible or 0) - cumul, 2)
        out[l.id] = float(part)
        cumul += float(part)

    return out




# --------- List subventions ---------
@bp.route("/subventions")
@login_required
@require_perm("subventions:view")
def subventions_list():
    secteurs = current_app.config.get("SECTEURS", [])
    voir_archives = request.args.get("archives") == "1"

    scope_q = Subvention.query
    if not can("scope:all_secteurs"):
        scope_q = scope_q.filter(Subvention.secteur == current_user.secteur_assigne)

    base_q = scope_q.filter(Subvention.est_archive.is_(voir_archives))

    years = [
        y for (y,) in base_q.with_entities(Subvention.annee_exercice).distinct().order_by(Subvention.annee_exercice.desc()).all()
        if y is not None
    ]
    try:
        selected_year = int(request.args.get("annee") or (years[0] if years else date.today().year))
    except Exception:
        selected_year = years[0] if years else date.today().year

    subs_q = base_q.filter(Subvention.annee_exercice == selected_year)
    subs = subs_q.order_by(Subvention.annee_exercice.desc(), Subvention.nom.asc()).all()

    nb_archives = scope_q.filter(Subvention.est_archive.is_(True)).count()
    default_secteur = current_user.secteur_assigne if not can("scope:all_secteurs") else (secteurs[0] if secteurs else None)
    categories_ref = _budget_categories_ref_for_secteur(default_secteur)
    alertes = alertes_echeances(subs)
    return render_template(
        "subventions_list.html",
        subs=subs,
        secteurs=secteurs,
        selected_year=selected_year,
        available_years=years,
        categories_ref=categories_ref,
        alertes=alertes,
        statuts=SUBVENTION_STATUTS,
        voir_archives=voir_archives,
        nb_archives=nb_archives,
    )


@bp.route("/subvention/nouvelle", methods=["POST"])
@login_required
@require_perm('subventions:edit')
def subvention_create():
    nom = (request.form.get("nom") or "").strip()
    secteur = (request.form.get("secteur") or "").strip()
    annee = int(request.form.get("annee_exercice") or 2025)

    montant_demande = float(request.form.get("montant_demande") or 0)
    montant_attribue = float(request.form.get("montant_attribue") or 0)
    montant_recu = float(request.form.get("montant_recu") or 0)

    financeur = (request.form.get("financeur") or "").strip() or None
    statut = (request.form.get("statut_cycle") or "sollicitee").strip()
    if statut not in SUBVENTION_STATUTS_DICT:
        statut = "sollicitee"

    if not can("scope:all_secteurs"):
        secteur = current_user.secteur_assigne

    if not nom or not secteur:
        flash("Nom + secteur obligatoires.", "danger")
        return redirect(url_for("main.subventions_list"))

    if not can_see_secteur(secteur):
        abort(403)

    s = Subvention(
        nom=nom,
        secteur=secteur,
        annee_exercice=annee,
        financeur=financeur,
        statut_cycle=statut,
        date_depot=_parse_date(request.form.get("date_depot")),
        date_versement_prevu=_parse_date(request.form.get("date_versement_prevu")),
        date_bilan_prevu=_parse_date(request.form.get("date_bilan_prevu")),
        montant_demande=montant_demande,
        montant_attribue=montant_attribue,
        montant_recu=montant_recu,
    )
    db.session.add(s)
    db.session.flush()

    selected_cat_ids = [int(x) for x in request.form.getlist("categorie_ref_id") if str(x).isdigit()]
    if selected_cat_ids:
        cats = BudgetCategorieReferentiel.query.filter(BudgetCategorieReferentiel.id.in_(selected_cat_ids)).all()
        for cat in cats:
            if not _budget_category_allowed(cat, secteur):
                continue
            db.session.add(LigneBudget(
                subvention_id=s.id,
                nature=cat.nature,
                compte=_budget_category_compte_label(cat) or ("74" if cat.nature == "produit" else "60"),
                libelle=cat.libelle,
                montant_base=float(cat.montant_defaut or 0.0),
                montant_reel=0.0,
            ))

    db.session.commit()
    journaliser("subvention.create", cible=f"subvention#{s.id}",
                details={"nom": s.nom, "secteur": s.secteur, "financeur": s.financeur})

    flash("Subvention créée.", "success")
    return redirect(url_for("main.subvention_pilotage", subvention_id=s.id))


# --------- Pilotage subvention ---------
@bp.route("/subvention/<int:subvention_id>/pilotage", methods=["GET", "POST"])
@login_required
@require_perm("subventions:view")
def subvention_pilotage(subvention_id):
    sub = db.get_or_404(Subvention, subvention_id)
    if not can_see_secteur(sub.secteur):
        abort(403)

    if request.method == "POST":
        if not can("subventions:edit"):
            abort(403)
        action = request.form.get("action") or ""

        # --- Montants globaux ---
        if action == "update_montants":
            sub.montant_demande = float(request.form.get("montant_demande") or 0)
            sub.montant_attribue = float(request.form.get("montant_attribue") or 0)
            sub.montant_recu = float(request.form.get("montant_recu") or 0)
            db.session.commit()
            journaliser("subvention.montants", cible=f"subvention#{sub.id}",
                        details={"demande": sub.montant_demande, "attribue": sub.montant_attribue, "recu": sub.montant_recu})
            flash("Montants mis à jour.", "success")
            return redirect(url_for("main.subvention_pilotage", subvention_id=sub.id))

        # --- Dossier : financeur, cycle, échéances ---
        if action == "update_dossier":
            sub.financeur = (request.form.get("financeur") or "").strip() or None
            sub.reference = (request.form.get("reference") or "").strip() or None
            statut = (request.form.get("statut_cycle") or "sollicitee").strip()
            if statut not in SUBVENTION_STATUTS_DICT:
                statut = sub.statut_cycle or "sollicitee"
            ancien_statut = sub.statut_cycle
            sub.statut_cycle = statut
            sub.date_depot = _parse_date(request.form.get("date_depot"))
            sub.date_decision = _parse_date(request.form.get("date_decision"))
            sub.date_versement_prevu = _parse_date(request.form.get("date_versement_prevu"))
            sub.date_bilan_prevu = _parse_date(request.form.get("date_bilan_prevu"))
            db.session.commit()
            journaliser("subvention.dossier", cible=f"subvention#{sub.id}",
                        details={"financeur": sub.financeur, "statut": sub.statut_cycle,
                                 "ancien_statut": ancien_statut})
            flash("Dossier mis à jour.", "success")
            return redirect(url_for("main.subvention_pilotage", subvention_id=sub.id))

        # --- Activités financées (justificatif financeur) ---
        if action == "add_atelier":
            atelier_id = int(request.form.get("atelier_id") or 0)
            atelier = db.get_or_404(AtelierActivite, atelier_id)
            if atelier.secteur != sub.secteur:
                abort(400)
            if SubventionAtelier.query.filter_by(subvention_id=sub.id, atelier_id=atelier.id).first():
                flash("Cet atelier est déjà rattaché à la subvention.", "info")
                return redirect(url_for("main.subvention_pilotage", subvention_id=sub.id))
            try:
                poids = float(str(request.form.get("poids_pct") or "100").replace(",", "."))
            except Exception:
                poids = 100.0
            poids = min(max(poids, 0.0), 100.0)
            db.session.add(SubventionAtelier(
                subvention_id=sub.id, atelier_id=atelier.id, poids_pct=poids,
                justification=(request.form.get("justification") or "").strip() or None,
            ))
            db.session.commit()
            journaliser("subvention.atelier_lien", cible=f"subvention#{sub.id}",
                        details={"atelier": atelier.nom, "poids_pct": poids})
            flash(f"Atelier « {atelier.nom} » rattaché à la subvention.", "success")
            return redirect(url_for("main.subvention_pilotage", subvention_id=sub.id))

        if action == "del_atelier":
            lien = db.get_or_404(SubventionAtelier, int(request.form.get("lien_id") or 0))
            if lien.subvention_id != sub.id:
                abort(400)
            nom_atelier = lien.atelier.nom if lien.atelier else "?"
            db.session.delete(lien)
            db.session.commit()
            journaliser("subvention.atelier_delien", cible=f"subvention#{sub.id}",
                        details={"atelier": nom_atelier})
            flash("Atelier détaché de la subvention.", "warning")
            return redirect(url_for("main.subvention_pilotage", subvention_id=sub.id))

        # --- Ajouter une ligne ---
        if action == "add_ligne":
            nature = (request.form.get("nature") or "charge").strip()
            cat_id = int(request.form.get("categorie_ref_id") or 0)
            cat = None
            if cat_id:
                cat = db.get_or_404(BudgetCategorieReferentiel, cat_id)
                if not _budget_category_allowed(cat, sub.secteur):
                    abort(400)
                nature = cat.nature

            compte_default = _budget_category_compte_label(cat) if cat else ("74" if nature == "produit" else "60")
            libelle_default = cat.libelle if cat else ""
            montant_default = float(cat.montant_defaut or 0.0) if cat else 0.0

            compte = (request.form.get("compte") or compte_default).strip()
            libelle = (request.form.get("libelle") or libelle_default).strip()
            montant_base = _parse_money(request.form.get("montant_base"), montant_default)
            montant_reel = _parse_money(request.form.get("montant_reel"), 0.0)

            if not libelle:
                flash("Libellé obligatoire.", "danger")
                return redirect(url_for("main.subvention_pilotage", subvention_id=sub.id))

            l = LigneBudget(
                subvention_id=sub.id,
                nature=nature,
                compte=compte,
                libelle=libelle,
                montant_base=montant_base,
                montant_reel=montant_reel,
            )
            db.session.add(l)
            db.session.commit()
            flash("Ligne ajoutée.", "success")
            return redirect(url_for("main.subvention_pilotage", subvention_id=sub.id))

        # --- Ventilation automatique (écrit dans montant_reel) ---
        if action == "auto_ventilation":
            mode = (request.form.get("mode") or "copy_base").strip()
            target = (request.form.get("target") or "recu").strip()

            if target == "attribue":
                montant_cible = float(sub.montant_attribue or 0)
            else:
                montant_cible = float(sub.montant_recu or 0)

            lignes = list(sub.lignes)
            if not lignes:
                flash("Aucune ligne à ventiler.", "warning")
                return redirect(url_for("main.subvention_pilotage", subvention_id=sub.id))

            if mode == "reset":
                for l in lignes:
                    l.montant_reel = 0.0
                db.session.commit()
                flash("Ventilation réinitialisée (réel = 0).", "warning")
                return redirect(url_for("main.subvention_pilotage", subvention_id=sub.id))

            if mode == "copy_base":
                for l in lignes:
                    l.montant_reel = float(l.montant_base or 0)
                db.session.commit()
                flash("Ventilation : base copiée vers réel.", "success")
                return redirect(url_for("main.subvention_pilotage", subvention_id=sub.id))

            if mode == "prorata_base":
                total_base = sum(float(l.montant_base or 0) for l in lignes)
                if total_base <= 0:
                    flash("Impossible : total des bases = 0.", "danger")
                    return redirect(url_for("main.subvention_pilotage", subvention_id=sub.id))

                theor = _compute_prorata(lignes, montant_cible)
                for l in lignes:
                    l.montant_reel = float(theor.get(l.id, 0.0))
                db.session.commit()
                flash(f"Ventilation pro-rata effectuée sur {montant_cible:.2f}€.", "success")
                return redirect(url_for("main.subvention_pilotage", subvention_id=sub.id))

            abort(400)

        abort(400)

    # --- GET : données pour page ---
    projets_q = Projet.query.filter(Projet.secteur == sub.secteur)
    if not can("scope:all_secteurs"):
        projets_q = projets_q.filter(Projet.secteur == current_user.secteur_assigne)

    projets = projets_q.order_by(Projet.nom.asc()).all()
    linked_ids = set(sp.projet_id for sp in sub.projets)

    lignes = list(sub.lignes)
    total_base = round(sum(float(l.montant_base or 0) for l in lignes), 2)

    theor_recu = _compute_prorata(lignes, float(sub.montant_recu or 0))
    theor_attribue = _compute_prorata(lignes, float(sub.montant_attribue or 0))

    recu = float(sub.montant_recu or 0)
    reel_lignes = float(sub.total_reel_lignes or 0)
    engage = float(sub.total_engage or 0)

    warnings = []
    if recu > 0 and reel_lignes == 0:
        warnings.append("Tu as un montant reçu, mais aucune ventilation en lignes réel : utilise la ventilation auto ou renseigne le réel par ligne.")
    if recu > 0 and reel_lignes > 0 and reel_lignes < recu:
        warnings.append("Ventilation partielle : total lignes réel < montant reçu. Il manque une répartition.")
    if reel_lignes > 0 and engage > reel_lignes:
        warnings.append("Attention : engagé > total lignes réel (dépenses au-dessus de l'enveloppe ventilée).")

    # Échéances propres à ce dossier (versement / bilan / décision en attente).
    for a in alertes_echeances([sub]):
        warnings.append(a["message"])

    liens_ateliers = (SubventionAtelier.query.filter_by(subvention_id=sub.id)
                      .order_by(SubventionAtelier.id.asc()).all())
    dispo_q = AtelierActivite.query.filter_by(secteur=sub.secteur, is_deleted=False)
    if liens_ateliers:
        dispo_q = dispo_q.filter(~AtelierActivite.id.in_([l.atelier_id for l in liens_ateliers]))
    ateliers_disponibles = dispo_q.order_by(AtelierActivite.nom.asc()).all()

    return render_template(
        "budget_pilotage.html",
        sub=sub,
        projets=projets,
        linked_ids=linked_ids,
        total_base=total_base,
        theor_recu=theor_recu,
        theor_attribue=theor_attribue,
        warnings=warnings,
        statuts=SUBVENTION_STATUTS,
        categories_ref=_budget_categories_ref_for_secteur(sub.secteur),
        liens_ateliers=liens_ateliers,
        ateliers_disponibles=ateliers_disponibles,
    )


@bp.route("/subvention/<int:subvention_id>/delete", methods=["POST"])
@login_required
@require_perm('subventions:delete')
def subvention_delete(subvention_id):
    sub = db.get_or_404(Subvention, subvention_id)
    if not can_see_secteur(sub.secteur):
        abort(403)

    db.session.delete(sub)
    commit_delete(
        f"la subvention « {sub.nom} »",
        "Subvention supprimée.",
        success_category="warning",
        blocked_message=f"Impossible de supprimer la subvention « {sub.nom} » : elle est encore liée à d'autres données.",
    )
    return redirect(url_for("main.subventions_list"))


@bp.route("/subvention/<int:subvention_id>/historique")
@login_required
@require_perm("subventions:view")
def subvention_historique(subvention_id):
    """Historique d'audit d'un dossier (créations, changements de statut/montants)."""
    sub = db.get_or_404(Subvention, subvention_id)
    if not can_see_secteur(sub.secteur):
        abort(403)
    import json as _json
    entrees = (AuditLog.query
               .filter(AuditLog.cible == f"subvention#{sub.id}")
               .order_by(AuditLog.created_at.desc()).all())
    lignes = []
    for e in entrees:
        try:
            details = _json.loads(e.details) if e.details else {}
        except Exception:
            details = {}
        lignes.append({"entry": e, "details": details})
    return render_template("subvention_historique.html", sub=sub, lignes=lignes)


@bp.route("/subvention/<int:subvention_id>/justificatif")
@login_required
@require_perm("subventions:view")
def subvention_justificatif(subvention_id):
    """Justificatif financeur : activité réelle des ateliers financés par la
    subvention sur son exercice (séances, heures, participants, coûts unitaires)."""
    from app.main.couts import mesures_activite, couts_unitaires

    sub = db.get_or_404(Subvention, subvention_id)
    if not can_see_secteur(sub.secteur):
        abort(403)

    annee = int(sub.annee_exercice or date.today().year)
    d1, d2 = date(annee, 1, 1), date(annee, 12, 31)
    montant_base = float(sub.montant_attribue or 0) or float(sub.montant_demande or 0)
    base_label = "attribué" if sub.montant_attribue else "demandé"

    liens = (SubventionAtelier.query.filter_by(subvention_id=sub.id)
             .order_by(SubventionAtelier.id.asc()).all())

    lignes = []
    for lien in liens:
        mesures = mesures_activite([lien.atelier_id], d1, d2)
        montant_affecte = round(montant_base * float(lien.poids_pct or 0) / 100.0, 2)
        lignes.append({
            "lien": lien,
            "atelier": lien.atelier,
            "mesures": mesures,
            "montant": montant_affecte,
            "couts": couts_unitaires(montant_affecte, mesures),
        })

    mesures_global = mesures_activite([l.atelier_id for l in liens], d1, d2) if liens else \
        {"sessions": 0, "heures": 0.0, "presences": 0, "uniques": 0}
    total_pct = round(sum(float(l.poids_pct or 0) for l in liens), 1)
    montant_global = round(montant_base * min(total_pct, 100.0) / 100.0, 2)
    couts_global = couts_unitaires(montant_global, mesures_global) if montant_global else None

    # --- Dossier de justification : démographie, dépenses, bénévolat ---
    from app.models import (
        BenevoleHeures, DepenseAffectation, Participant,
        PresenceActivite, SessionActivite,
    )

    atelier_ids = [l.atelier_id for l in liens]
    demographie = None
    if atelier_ids:
        eff = db.func.coalesce(SessionActivite.rdv_date, SessionActivite.date_session)
        participants = (
            Participant.query
            .join(PresenceActivite, PresenceActivite.participant_id == Participant.id)
            .join(SessionActivite, SessionActivite.id == PresenceActivite.session_id)
            .filter(
                SessionActivite.atelier_id.in_(atelier_ids),
                SessionActivite.is_deleted.is_(False),
                eff >= d1, eff <= d2,
            )
            .distinct()
            .all()
        )
        if participants:
            genres: dict[str, int] = {}
            tranches = {"0-11 ans": 0, "12-17 ans": 0, "18-25 ans": 0, "26-59 ans": 0, "60 ans et +": 0, "Âge inconnu": 0}
            qpv = 0
            for pers in participants:
                g = (pers.genre or "Non renseigné").strip() or "Non renseigné"
                genres[g] = genres.get(g, 0) + 1
                age = pers.age
                if age is None:
                    tranches["Âge inconnu"] += 1
                elif age <= 11:
                    tranches["0-11 ans"] += 1
                elif age <= 17:
                    tranches["12-17 ans"] += 1
                elif age <= 25:
                    tranches["18-25 ans"] += 1
                elif age <= 59:
                    tranches["26-59 ans"] += 1
                else:
                    tranches["60 ans et +"] += 1
                if pers.is_qpv:
                    qpv += 1
            total = len(participants)
            demographie = {
                "total": total,
                "genres": sorted(genres.items(), key=lambda kv: -kv[1]),
                "tranches": [(k, v) for k, v in tranches.items() if v],
                "qpv": qpv,
                "qpv_pct": round(qpv * 100.0 / total) if total else 0,
            }

    # Dépenses imputées à la subvention sur l'exercice.
    affectations = (
        db.session.query(DepenseAffectation, Depense)
        .join(Depense, Depense.id == DepenseAffectation.depense_id)
        .filter(
            DepenseAffectation.subvention_id == sub.id,
            Depense.est_supprimee.is_(False),
        )
        .order_by(Depense.date_paiement.asc())
        .all()
    )
    depenses = [{"libelle": dep.libelle, "date": dep.date_paiement,
                 "fournisseur": dep.fournisseur, "montant": float(aff.montant or 0)}
                for aff, dep in affectations]
    total_depenses = round(sum(x["montant"] for x in depenses), 2)

    # Bénévolat du secteur de la subvention sur l'exercice (valorisé).
    from app.services.benevolat import taux_horaire
    heures_benevolat = float(
        db.session.query(db.func.coalesce(db.func.sum(BenevoleHeures.heures), 0.0))
        .filter(
            BenevoleHeures.secteur == sub.secteur,
            BenevoleHeures.date_action >= d1,
            BenevoleHeures.date_action <= d2,
        ).scalar() or 0.0
    )
    benevolat = {
        "heures": round(heures_benevolat, 1),
        "taux": taux_horaire(),
        "valorisation": round(heures_benevolat * taux_horaire(), 2),
    } if heures_benevolat else None

    return render_template(
        "subvention_justificatif.html",
        sub=sub, annee=annee, lignes=lignes,
        montant_base=montant_base, base_label=base_label,
        total_pct=total_pct, montant_global=montant_global,
        mesures_global=mesures_global, couts_global=couts_global,
        demographie=demographie,
        depenses=depenses, total_depenses=total_depenses,
        benevolat=benevolat,
    )


@bp.route("/subvention/<int:subvention_id>/justificatif.xlsx")
@login_required
@require_perm("subventions:view")
def subvention_justificatif_xlsx(subvention_id):
    from openpyxl import Workbook
    from app.main.couts import mesures_activite, couts_unitaires

    sub = db.get_or_404(Subvention, subvention_id)
    if not can_see_secteur(sub.secteur):
        abort(403)

    annee = int(sub.annee_exercice or date.today().year)
    d1, d2 = date(annee, 1, 1), date(annee, 12, 31)
    montant_base = float(sub.montant_attribue or 0) or float(sub.montant_demande or 0)
    liens = SubventionAtelier.query.filter_by(subvention_id=sub.id).all()

    wb = Workbook()
    ws = wb.active
    ws.title = "Justificatif"
    ws.append(["Subvention", sub.nom])
    ws.append(["Financeur", sub.financeur or ""])
    ws.append(["Exercice", annee])
    ws.append(["Montant de référence (€)", round(montant_base, 2)])
    ws.append([])
    ws.append(["Atelier financé", "Part (%)", "Montant affecté (€)", "Séances", "Heures",
               "Participants uniques", "Présences", "Coût / participant (€)", "Coût / heure (€)",
               "Justification"])
    for lien in liens:
        mesures = mesures_activite([lien.atelier_id], d1, d2)
        montant = round(montant_base * float(lien.poids_pct or 0) / 100.0, 2)
        couts = couts_unitaires(montant, mesures)
        ws.append([
            lien.atelier.nom if lien.atelier else "?",
            lien.poids_pct, montant,
            mesures["sessions"], mesures["heures"], mesures["uniques"], mesures["presences"],
            couts["par_participant"] if couts["par_participant"] is not None else "",
            couts["par_heure"] if couts["par_heure"] is not None else "",
            lien.justification or "",
        ])
    for col, width in zip("ABCDEFGHIJ", (30, 9, 16, 9, 9, 18, 10, 18, 14, 40)):
        ws.column_dimensions[col].width = width

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return Response(
        buf.getvalue(),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=justificatif_subvention_{sub.id}_{annee}.xlsx"},
    )


@bp.route("/subvention/<int:subvention_id>/archiver", methods=["POST"])
@login_required
@require_perm("subventions:edit")
def subvention_archive(subvention_id):
    sub = db.get_or_404(Subvention, subvention_id)
    if not can_see_secteur(sub.secteur):
        abort(403)
    sub.est_archive = True
    db.session.commit()
    journaliser("subvention.archive", cible=f"subvention#{sub.id}", details={"nom": sub.nom})
    flash(f"Subvention « {sub.nom} » archivée.", "warning")
    return redirect(url_for("main.subventions_list", annee=sub.annee_exercice))


@bp.route("/subvention/<int:subvention_id>/restaurer", methods=["POST"])
@login_required
@require_perm("subventions:edit")
def subvention_restore(subvention_id):
    sub = db.get_or_404(Subvention, subvention_id)
    if not can_see_secteur(sub.secteur):
        abort(403)
    sub.est_archive = False
    db.session.commit()
    journaliser("subvention.restaure", cible=f"subvention#{sub.id}", details={"nom": sub.nom})
    flash(f"Subvention « {sub.nom} » restaurée.", "success")
    return redirect(url_for("main.subventions_list", annee=sub.annee_exercice, archives=1))


@bp.route("/subventions/reconduire", methods=["POST"])
@login_required
@require_perm("subventions:edit")
def subventions_reconduire():
    """Reconduit en masse les subventions d'une année vers l'année suivante.

    Copie nom / secteur / financeur / référence / lignes budgétaires. Remet les
    montants attribué / reçu à 0, le statut à « sollicitée » et efface les dates.
    Ignore un dossier déjà présent dans l'année cible (même nom + financeur).
    """
    try:
        annee_source = int(request.form.get("annee_source") or 0)
        annee_cible = int(request.form.get("annee_cible") or 0)
    except Exception:
        flash("Années invalides.", "danger")
        return redirect(url_for("main.subventions_list"))
    if not annee_source or not annee_cible or annee_source == annee_cible:
        flash("Choisis une année source et une année cible différentes.", "danger")
        return redirect(url_for("main.subventions_list", annee=annee_source))

    q = Subvention.query.filter_by(est_archive=False, annee_exercice=annee_source)
    if not can("scope:all_secteurs"):
        q = q.filter(Subvention.secteur == current_user.secteur_assigne)
    sources = q.all()

    # Dédoublonnage : dossiers déjà présents dans l'année cible.
    existants = set()
    for s in Subvention.query.filter_by(annee_exercice=annee_cible).all():
        existants.add(((s.nom or "").strip().lower(), (s.financeur or "").strip().lower()))

    cree = 0
    for src in sources:
        cle = ((src.nom or "").strip().lower(), (src.financeur or "").strip().lower())
        if cle in existants:
            continue
        copie = Subvention(
            nom=src.nom, secteur=src.secteur, annee_exercice=annee_cible,
            financeur=src.financeur, reference=src.reference,
            statut_cycle="sollicitee",
            montant_demande=src.montant_demande, montant_attribue=0.0, montant_recu=0.0,
        )
        db.session.add(copie)
        db.session.flush()
        for l in src.lignes:
            db.session.add(LigneBudget(
                subvention_id=copie.id, nature=getattr(l, "nature", "charge"),
                compte=l.compte, libelle=l.libelle,
                montant_base=float(l.montant_base or 0), montant_reel=0.0,
            ))
        existants.add(cle)
        cree += 1

    db.session.commit()
    journaliser("subvention.reconduire", cible=f"exercice#{annee_cible}",
                details={"source": annee_source, "cible": annee_cible, "creees": cree})
    flash(f"{cree} subvention(s) reconduite(s) de {annee_source} vers {annee_cible}.", "success")
    return redirect(url_for("main.subventions_list", annee=annee_cible))


# --------- Edit / Delete lignes ---------
@bp.route("/ligne/<int:ligne_id>/edit", methods=["POST"])
@login_required
@require_perm("subventions:edit")
def ligne_edit(ligne_id):
    l = db.get_or_404(LigneBudget, ligne_id)
    sub = l.source_sub
    if not can_see_secteur(sub.secteur):
        abort(403)

    cat_id = int(request.form.get("categorie_ref_id") or 0)
    cat = None
    if cat_id:
        cat = db.get_or_404(BudgetCategorieReferentiel, cat_id)
        if not _budget_category_allowed(cat, sub.secteur):
            abort(400)

    l.nature = (request.form.get("nature") or (cat.nature if cat else getattr(l, "nature", "charge"))).strip()
    l.compte = (request.form.get("compte") or (_budget_category_compte_label(cat) if cat else l.compte)).strip()
    l.libelle = (request.form.get("libelle") or (cat.libelle if cat else l.libelle)).strip()
    l.montant_base = _parse_money(request.form.get("montant_base"), l.montant_base or 0)
    l.montant_reel = _parse_money(request.form.get("montant_reel"), l.montant_reel or 0)
    db.session.commit()

    flash("Ligne modifiée.", "success")
    return redirect(url_for("main.subvention_pilotage", subvention_id=sub.id))


@bp.route("/ligne/<int:ligne_id>/delete", methods=["POST"])
@login_required
@require_perm('budget:delete')
def ligne_delete(ligne_id):
    l = db.get_or_404(LigneBudget, ligne_id)
    sub = l.source_sub
    if not can_see_secteur(sub.secteur):
        abort(403)

    db.session.delete(l)
    commit_delete(
        f"la ligne « {l.libelle} »",
        "Ligne supprimée.",
        success_category="warning",
        blocked_message=f"Impossible de supprimer la ligne « {l.libelle} » : elle est encore utilisée ailleurs.",
    )
    return redirect(url_for("main.subvention_pilotage", subvention_id=sub.id))


# --------- Lier / délier subvention à projet ---------
@bp.route("/subvention/<int:subvention_id>/toggle_projet", methods=["POST"])
@login_required
@require_perm("subventions:link")
def subvention_toggle_projet(subvention_id):
    sub = db.get_or_404(Subvention, subvention_id)
    if not can_see_secteur(sub.secteur):
        abort(403)

    projet_id = int(request.form.get("projet_id") or 0)
    projet = db.get_or_404(Projet, projet_id)

    if projet.secteur != sub.secteur:
        abort(400)

    link = SubventionProjet.query.filter_by(projet_id=projet.id, subvention_id=sub.id).first()
    if link:
        db.session.delete(link)
        db.session.commit()
        flash("Subvention retirée du projet.", "warning")
    else:
        link = SubventionProjet(projet_id=projet.id, subvention_id=sub.id)
        db.session.add(link)
        db.session.commit()
        flash("Subvention ajoutée au projet.", "success")

    return redirect(url_for("main.subvention_pilotage", subvention_id=sub.id))


# --------- APIs pour dropdowns dépenses ---------
@bp.route("/api/subvention/<int:subvention_id>/comptes")
@login_required
@require_perm("subventions:view")
def api_comptes(subvention_id):
    sub = db.get_or_404(Subvention, subvention_id)
    if not can_see_secteur(sub.secteur):
        abort(403)

    nature = request.args.get("nature")

    q = LigneBudget.query.filter_by(subvention_id=sub.id)
    if nature:
        q = q.filter(LigneBudget.nature == nature)

    comptes = sorted({l.compte for l in q if l.compte})
    return jsonify({"comptes": comptes})


@bp.route("/api/subvention/<int:subvention_id>/lignes")
@login_required
@require_perm("subventions:view")
def api_lignes(subvention_id):
    sub = db.get_or_404(Subvention, subvention_id)
    if not can_see_secteur(sub.secteur):
        abort(403)

    compte = (request.args.get("compte") or "").strip()
    nature = request.args.get("nature")

    q = LigneBudget.query.filter_by(subvention_id=sub.id)
    if nature:
        q = q.filter(LigneBudget.nature == nature)
    if compte:
        q = q.filter(LigneBudget.compte == compte)

    lignes = q.order_by(LigneBudget.compte.asc(), LigneBudget.libelle.asc()).all()

    out = []
    for l in lignes:
        out.append({
            "id": l.id,
            "compte": l.compte,
            "libelle": l.libelle,
            "montant_reel": float(l.montant_reel or 0),
            "engage": float(l.engage or 0),
            "reste": float(l.reste or 0),
        })

    return jsonify({"lignes": out})



# --------- Exports simples ---------
@bp.route("/export/depenses.csv")
@login_required
@require_perm("depenses:view")
def export_depenses_csv():
    dep_q = Depense.query.join(LigneBudget).join(Subvention)
    if not can("scope:all_secteurs"):
        dep_q = dep_q.filter(Subvention.secteur == current_user.secteur_assigne)

    deps = dep_q.all()

    out = StringIO()
    writer = csv.writer(out, delimiter=";")
    writer.writerow(["secteur", "subvention", "annee", "compte", "ligne", "depense", "montant", "date_paiement", "type"])

    for d in deps:
        l = d.budget_source
        s = l.source_sub
        writer.writerow([
            s.secteur,
            s.nom,
            s.annee_exercice,
            l.compte,
            l.libelle,
            d.libelle,
            f"{float(d.montant or 0):.2f}".replace(".", ","),
            d.date_paiement.isoformat() if d.date_paiement else "",
            d.type_depense or ""
        ])

    content = out.getvalue().encode("utf-8-sig")  # Excel friendly
    filename = f"depenses_{date.today().isoformat()}.csv"
    return Response(content, mimetype="text/csv", headers={
        "Content-Disposition": f"attachment; filename={filename}"
    })


@bp.route("/export/subvention/<int:subvention_id>.csv")
@login_required
@require_perm("subventions:view")
def export_subvention_csv(subvention_id):
    s = db.get_or_404(Subvention, subvention_id)
    if not can_see_secteur(s.secteur):
        abort(403)

    out = StringIO()
    writer = csv.writer(out, delimiter=";")
    writer.writerow(["subvention", "secteur", "annee", "compte", "ligne", "base", "reel", "engage", "reste"])

    for l in s.lignes:
        writer.writerow([
            s.nom,
            s.secteur,
            s.annee_exercice,
            l.compte,
            l.libelle,
            f"{float(l.montant_base or 0):.2f}".replace(".", ","),
            f"{float(l.montant_reel or 0):.2f}".replace(".", ","),
            f"{float(l.engage or 0):.2f}".replace(".", ","),
            f"{float(l.reste or 0):.2f}".replace(".", ","),
        ])

    content = out.getvalue().encode("utf-8-sig")
    return Response(content, mimetype="text/csv", headers={
        "Content-Disposition": f"attachment; filename=subvention_{s.id}.csv"
    })


# --------- Bilan par subvention ---------
@bp.route("/subvention/<int:subvention_id>/bilan")
@login_required
@require_perm("subventions:view")
def subvention_bilan(subvention_id: int):
    """
    Vue détaillée pour un financeur / subvention.

    Affiche un récapitulatif des montants demandés/attribués/reçus et des lignes de budget,
    avec une représentation graphique proportionnelle par ligne (charges et produits).
    """
    sub = db.get_or_404(Subvention, subvention_id)
    # Vérification des droits : un responsable ne peut consulter que son secteur
    if not can_see_secteur(sub.secteur):
        abort(403)

    # Collecte des lignes de budget
    lignes: list[dict[str, float | str]] = []
    # Calcul du montant maximum utilisé pour la largeur des barres
    max_total = 0.0
    for l in sub.lignes:
        base = float(l.montant_base or 0)
        reel = float(l.montant_reel or 0)
        engage = float(l.engage or 0)
        reste = float(l.reste or 0)
        nature = getattr(l, "nature", "charge")
        total_for_max = reel + engage + reste
        if total_for_max > max_total:
            max_total = total_for_max
        lignes.append({
            "id": l.id,
            "compte": l.compte,
            "libelle": l.libelle,
            "base": base,
            "reel": reel,
            "engage": engage,
            "reste": reste,
            "nature": nature,
        })

    # Calcul des pourcentages pour chaque ligne (évite de surcharger le template)
    if max_total > 0:
        for d in lignes:
            d["p_reel"] = (d["reel"] / max_total) * 100.0 if d["reel"] else 0.0
            d["p_engage"] = (d["engage"] / max_total) * 100.0 if d["engage"] else 0.0
            d["p_reste"] = (d["reste"] / max_total) * 100.0 if d["reste"] else 0.0
    else:
        for d in lignes:
            d["p_reel"] = d["p_engage"] = d["p_reste"] = 0.0

    # Totaux synthétiques (charges et produits séparés)
    totals = {
        "demande": float(sub.montant_demande or 0),
        "attribue": float(sub.montant_attribue or 0),
        "recu": float(sub.montant_recu or 0),
        "base_charges": 0.0,
        "base_produits": 0.0,
        "reel_charges": 0.0,
        "reel_produits": 0.0,
        "engage": 0.0,
        "reste": 0.0,
    }
    for d in lignes:
        if d["nature"] == "produit":
            totals["base_produits"] += d["base"]
            totals["reel_produits"] += d["reel"]
        else:
            totals["base_charges"] += d["base"]
            totals["reel_charges"] += d["reel"]
            totals["engage"] += d["engage"]
            totals["reste"] += d["reste"]

    # Arrondis des totaux
    for k in totals:
        totals[k] = round(float(totals[k] or 0), 2)

    return render_template(
        "subvention_bilan.html",
        sub=sub,
        lignes=lignes,
        totals=totals,
        max_total=max_total,
    )



# --------- Vue consolidée par financeur ---------
@bp.route("/subventions/par-financeur")
@login_required
@require_perm("subventions:view")
def subventions_par_financeur():
    base_q = Subvention.query.filter_by(est_archive=False)
    if not can("scope:all_secteurs"):
        base_q = base_q.filter(Subvention.secteur == current_user.secteur_assigne)

    years = [
        y for (y,) in base_q.with_entities(Subvention.annee_exercice).distinct().order_by(Subvention.annee_exercice.desc()).all()
        if y is not None
    ]
    try:
        selected_year = int(request.args.get("annee") or (years[0] if years else date.today().year))
    except Exception:
        selected_year = years[0] if years else date.today().year

    subs = base_q.filter(Subvention.annee_exercice == selected_year).all()

    groupes = {}
    for s in subs:
        cle = (s.financeur or "").strip() or "— Non renseigné —"
        g = groupes.setdefault(cle, {
            "financeur": cle, "nb": 0,
            "demande": 0.0, "attribue": 0.0, "recu": 0.0, "subs": [],
        })
        g["nb"] += 1
        g["demande"] += float(s.montant_demande or 0)
        g["attribue"] += float(s.montant_attribue or 0)
        g["recu"] += float(s.montant_recu or 0)
        g["subs"].append(s)

    groupes = sorted(groupes.values(), key=lambda g: (-g["attribue"], g["financeur"].lower()))
    totaux = {
        "demande": round(sum(g["demande"] for g in groupes), 2),
        "attribue": round(sum(g["attribue"] for g in groupes), 2),
        "recu": round(sum(g["recu"] for g in groupes), 2),
        "nb": sum(g["nb"] for g in groupes),
    }
    return render_template(
        "subventions_par_financeur.html",
        groupes=groupes,
        totaux=totaux,
        selected_year=selected_year,
        available_years=years,
    )


# --------- Export Excel de la liste des subventions ---------
@bp.route("/export/subventions.xlsx")
@login_required
@require_perm("subventions:view")
def export_subventions_xlsx():
    from openpyxl import Workbook

    base_q = Subvention.query.filter_by(est_archive=False)
    if not can("scope:all_secteurs"):
        base_q = base_q.filter(Subvention.secteur == current_user.secteur_assigne)

    annee = request.args.get("annee")
    if annee:
        try:
            base_q = base_q.filter(Subvention.annee_exercice == int(annee))
        except Exception:
            pass

    subs = base_q.order_by(Subvention.annee_exercice.desc(), Subvention.financeur.asc(), Subvention.nom.asc()).all()

    wb = Workbook()
    ws = wb.active
    ws.title = "Subventions"
    entetes = [
        "Financeur", "Dossier", "Référence", "Secteur", "Exercice", "Statut",
        "Demandé (€)", "Attribué (€)", "Reçu (€)", "Reste à recevoir (€)",
        "Dépôt", "Décision", "Versement prévu", "Bilan prévu",
    ]
    ws.append(entetes)

    def _d(v):
        return v.strftime("%d/%m/%Y") if v else ""

    for s in subs:
        attribue = float(s.montant_attribue or 0)
        recu = float(s.montant_recu or 0)
        ws.append([
            s.financeur or "",
            s.nom,
            s.reference or "",
            s.secteur,
            s.annee_exercice,
            SUBVENTION_STATUTS_DICT.get(s.statut_cycle or "sollicitee", s.statut_cycle or ""),
            round(float(s.montant_demande or 0), 2),
            round(attribue, 2),
            round(recu, 2),
            round(attribue - recu, 2),
            _d(s.date_depot),
            _d(s.date_decision),
            _d(s.date_versement_prevu),
            _d(s.date_bilan_prevu),
        ])

    # Ligne de totaux
    ws.append([])
    ws.append([
        "TOTAL", "", "", "", "", "",
        round(sum(float(s.montant_demande or 0) for s in subs), 2),
        round(sum(float(s.montant_attribue or 0) for s in subs), 2),
        round(sum(float(s.montant_recu or 0) for s in subs), 2),
        round(sum(float(s.montant_attribue or 0) - float(s.montant_recu or 0) for s in subs), 2),
    ])

    for col, width in zip("ABCDEFGHIJKLMN", (26, 28, 16, 16, 10, 12, 13, 13, 13, 16, 12, 12, 14, 12)):
        ws.column_dimensions[col].width = width

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    suffixe = f"_{annee}" if annee else ""
    return Response(
        buf.getvalue(),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=subventions{suffixe}.xlsx"},
    )


# ---------------------------------------------------------------------
# RBAC Test (diagnostic)
# ---------------------------------------------------------------------



# ---------------------------------------------------------------------------
# Feuille de temps par financeur : le temps effectif passé sur la subvention
# ---------------------------------------------------------------------------

@bp.route("/subvention/<int:subvention_id>/feuille-de-temps")
@login_required
@require_perm("subventions:view")
def subvention_feuille_temps(subvention_id):
    """État des heures passées sur la subvention, jour par jour, sur une
    période choisie : séances des ateliers financés (face-à-face) + créneaux
    d'agenda rattachés au projet (réunions, préparation dédiée…) + option
    de temps de préparation forfaitaire par séance. Imprimable et signable —
    exactement ce que les plateformes de financeurs font ressaisir à la main.
    """
    from datetime import datetime as _dt

    from app.main.couts import _duree_session_minutes
    from app.models import AgendaCreneau, PresenceActivite, SessionActivite, SubventionAtelier

    sub = db.get_or_404(Subvention, subvention_id)
    if not can_see_secteur(sub.secteur):
        abort(403)

    def _parse(raw):
        try:
            return _dt.strptime((raw or "").strip(), "%Y-%m-%d").date()
        except Exception:
            return None

    # Période : par défaut le mois précédent (celui que la plateforme extrait).
    today = date.today()
    fin_mois_prec = today.replace(day=1) - timedelta(days=1)
    du = _parse(request.args.get("du")) or fin_mois_prec.replace(day=1)
    au = _parse(request.args.get("au")) or fin_mois_prec
    if du > au:
        du, au = au, du
    try:
        prep_minutes = max(0, min(240, int(request.args.get("prep") or 0)))
    except Exception:
        prep_minutes = 0

    atelier_ids = [l.atelier_id for l in
                   SubventionAtelier.query.filter_by(subvention_id=sub.id).all()]

    # --- Séances (face-à-face), jour par jour ---
    eff = db.func.coalesce(SessionActivite.rdv_date, SessionActivite.date_session)
    seances = []
    if atelier_ids:
        seances = (
            SessionActivite.query
            .filter(
                SessionActivite.atelier_id.in_(atelier_ids),
                SessionActivite.is_deleted.is_(False),
                db.func.lower(db.func.coalesce(SessionActivite.statut, "")) != "annulee",
                eff >= du, eff <= au,
            )
            .order_by(eff.asc(), SessionActivite.id.asc())
            .all()
        )
    presences_par_sid = {}
    if seances:
        rows = (
            db.session.query(PresenceActivite.session_id, db.func.count(PresenceActivite.id))
            .filter(PresenceActivite.session_id.in_([s.id for s in seances]))
            .group_by(PresenceActivite.session_id)
            .all()
        )
        presences_par_sid = {sid: n for sid, n in rows}

    lignes_seances = []
    minutes_seances = 0
    minutes_prep = 0
    for s in seances:
        d = s.rdv_date or s.date_session
        duree = _duree_session_minutes(s)
        minutes_seances += duree
        heure_debut = s.rdv_debut or s.heure_debut
        if prep_minutes and heure_debut:
            minutes_prep += prep_minutes
        lignes_seances.append({
            "date": d,
            "atelier": s.atelier.nom if s.atelier else f"Atelier #{s.atelier_id}",
            "horaire": (f"{heure_debut}–{s.rdv_fin or s.heure_fin}"
                        if heure_debut and (s.rdv_fin or s.heure_fin) else (heure_debut or "—")),
            "duree_minutes": duree,
            "presences": presences_par_sid.get(s.id, 0),
        })

    # --- Créneaux rattachés au projet (tous utilisateurs) ---
    creneaux = (
        AgendaCreneau.query
        .filter(
            AgendaCreneau.subvention_id == sub.id,
            AgendaCreneau.date_creneau >= du,
            AgendaCreneau.date_creneau <= au,
        )
        .order_by(AgendaCreneau.date_creneau.asc(), AgendaCreneau.id.asc())
        .all()
    )
    minutes_creneaux = sum(c.duree_minutes for c in creneaux)

    total_minutes = minutes_seances + minutes_prep + minutes_creneaux

    def _h(minutes):
        return round(minutes / 60.0, 2)

    return render_template(
        "subvention_feuille_temps.html",
        sub=sub, du=du, au=au, prep_minutes=prep_minutes,
        lignes_seances=lignes_seances, creneaux=creneaux,
        heures_seances=_h(minutes_seances),
        heures_prep=_h(minutes_prep),
        heures_creneaux=_h(minutes_creneaux),
        heures_total=_h(total_minutes),
        nb_seances=len(lignes_seances),
        today=today,
    )
