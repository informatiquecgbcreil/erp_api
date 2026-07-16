from datetime import date

from flask import render_template, request, redirect, url_for, flash, abort
from flask_login import login_required
from app.rbac import require_perm, can, can_access_secteur

from app.extensions import db
from app.utils.delete_guard import commit_delete

from app.models import (
    Projet,
    ChargeProjet,
    ProduitProjet,
    VentilationProjet,
    Depense,
    DepenseAffectation,
    Subvention,
    SubventionProjet,
    LigneBudget,
)


from app.projets.helpers_projet import (
    _budget_stats,
    _parse_date_or_none,
    _parse_float_fr,
    _projet_selected_year,
)
from app.projets.projets_crud import (
    _projet_finance_context,
)
from app.projets.common import (
    bp,
)

# ---------------------------------------------------------------------
# Budget AAP par projet : Charges / Produits / Ventilation / Synthèse
# ---------------------------------------------------------------------

@bp.route("/projets/<int:projet_id>/finance")
@login_required
@require_perm("projets:view")
def projet_finance(projet_id):
    projet = db.get_or_404(Projet, projet_id)
    if not can_access_secteur(projet.secteur):
        abort(403)

    year = _projet_selected_year(projet)
    tab = (request.args.get("tab") or "vue").strip().lower()
    if tab not in {"vue", "budget", "financements", "depenses", "exports"}:
        tab = "vue"

    finance = _projet_finance_context(projet, year)
    return render_template(
        "projet_finance.html",
        projet=projet,
        finance=finance,
        year=year,
        tab=tab,
        can_edit_budget=can("aap:charges_edit") or can("aap:produits_edit") or can("subventions:create") or can("depenses:create"),
    )



@bp.route("/projets/<int:projet_id>/finance/action", methods=["POST"])
@login_required
@require_perm("projets:view")
def projet_finance_action(projet_id):
    projet = db.get_or_404(Projet, projet_id)
    if not can_access_secteur(projet.secteur):
        abort(403)

    action = request.form.get("action") or ""
    year = request.form.get("year") or request.args.get("year") or date.today().year
    try:
        year = int(year)
    except Exception:
        year = date.today().year

    if action == "quick_charge":
        if not can("aap:charges_edit"):
            abort(403)
        libelle = (request.form.get("libelle") or "").strip()
        if not libelle:
            flash("Libellé de charge obligatoire.", "danger")
            return redirect(url_for("projets.projet_finance", projet_id=projet.id, year=year, tab="budget"))

        charge = ChargeProjet(
            projet_id=projet.id,
            bloc=(request.form.get("bloc") or "directe").strip(),
            code_plan=(request.form.get("code_plan") or "60").strip(),
            libelle=libelle,
            montant_previsionnel=_parse_float_fr(request.form.get("montant_previsionnel"), 0.0),
            montant_reel=_parse_float_fr(request.form.get("montant_reel"), 0.0),
            commentaire=(request.form.get("commentaire") or "").strip() or None,
        )
        db.session.add(charge)
        db.session.commit()
        flash("Charge projet ajoutée.", "success")
        return redirect(url_for("projets.projet_finance", projet_id=projet.id, year=year, tab="budget"))

    if action == "quick_produit":
        if not can("aap:produits_edit"):
            abort(403)
        financeur = (request.form.get("financeur") or "").strip()
        if not financeur:
            flash("Financeur obligatoire.", "danger")
            return redirect(url_for("projets.projet_finance", projet_id=projet.id, year=year, tab="budget"))

        produit = ProduitProjet(
            projet_id=projet.id,
            financeur=financeur,
            categorie=(request.form.get("categorie") or "autre").strip(),
            statut=(request.form.get("statut") or "prevu").strip(),
            montant_demande=_parse_float_fr(request.form.get("montant_demande"), 0.0),
            montant_accorde=_parse_float_fr(request.form.get("montant_accorde"), 0.0),
            montant_recu=_parse_float_fr(request.form.get("montant_recu"), 0.0),
            reference_dossier=(request.form.get("reference_dossier") or "").strip() or None,
            commentaire=(request.form.get("commentaire") or "").strip() or None,
        )
        db.session.add(produit)
        db.session.commit()
        flash("Produit / financement projet ajouté.", "success")
        return redirect(url_for("projets.projet_finance", projet_id=projet.id, year=year, tab="budget"))

    if action == "create_subvention_linked":
        if not can("subventions:edit"):
            abort(403)
        nom = (request.form.get("nom") or "").strip()
        if not nom:
            flash("Nom de la subvention obligatoire.", "danger")
            return redirect(url_for("projets.projet_finance", projet_id=projet.id, year=year, tab="financements"))

        sub = Subvention(
            nom=nom,
            secteur=projet.secteur,
            annee_exercice=year,
            montant_demande=_parse_float_fr(request.form.get("montant_demande"), 0.0),
            montant_attribue=_parse_float_fr(request.form.get("montant_attribue"), 0.0),
            montant_recu=_parse_float_fr(request.form.get("montant_recu"), 0.0),
        )
        db.session.add(sub)
        db.session.flush()
        db.session.add(SubventionProjet(projet_id=projet.id, subvention_id=sub.id))

        if (request.form.get("create_product_line") or "").strip() == "1":
            db.session.add(LigneBudget(
                subvention_id=sub.id,
                nature="produit",
                compte="74",
                libelle=nom,
                montant_base=float(sub.montant_demande or 0.0),
                montant_reel=float(sub.montant_recu or 0.0),
            ))

        db.session.commit()
        flash("Subvention créée et rattachée au projet.", "success")
        return redirect(url_for("projets.projet_finance", projet_id=projet.id, year=year, tab="financements"))

    if action == "link_subvention":
        if not can("subventions:link"):
            abort(403)
        sub_id = int(request.form.get("subvention_id") or 0)
        sub = db.get_or_404(Subvention, sub_id)
        if sub.secteur != projet.secteur:
            abort(400)

        exists = SubventionProjet.query.filter_by(projet_id=projet.id, subvention_id=sub.id).first()
        if exists:
            flash("Cette subvention est déjà rattachée au projet.", "info")
        else:
            db.session.add(SubventionProjet(projet_id=projet.id, subvention_id=sub.id))
            db.session.commit()
            flash("Subvention existante rattachée au projet.", "success")
        return redirect(url_for("projets.projet_finance", projet_id=projet.id, year=year, tab="financements"))

    if action == "quick_depense":
        if not can("depenses:create"):
            abort(403)

        ligne_id = int(request.form.get("ligne_budget_id") or 0)
        ligne = db.get_or_404(LigneBudget, ligne_id)
        sub = ligne.source_sub
        if not sub or sub.secteur != projet.secteur:
            abort(400)
        linked = SubventionProjet.query.filter_by(projet_id=projet.id, subvention_id=sub.id).first()
        if not linked:
            abort(400)
        if getattr(ligne, "nature", "charge") != "charge":
            flash("Une dépense doit être rattachée à une ligne de charge.", "danger")
            return redirect(url_for("projets.projet_finance", projet_id=projet.id, year=year, tab="depenses"))

        charge_id = int(request.form.get("charge_projet_id") or 0)
        charge = None
        if charge_id:
            charge = db.get_or_404(ChargeProjet, charge_id)
            if charge.projet_id != projet.id:
                abort(400)

        libelle = (request.form.get("libelle") or "").strip()
        if not libelle:
            flash("Libellé de dépense obligatoire.", "danger")
            return redirect(url_for("projets.projet_finance", projet_id=projet.id, year=year, tab="depenses"))

        dep = Depense(
            ligne_budget_id=ligne.id,
            charge_projet_id=charge.id if charge else None,
            libelle=libelle,
            montant=_parse_float_fr(request.form.get("montant"), 0.0),
            date_paiement=_parse_date_or_none(request.form.get("date_paiement")),
            type_depense=(request.form.get("type_depense") or "Fonctionnement").strip(),
            fournisseur=(request.form.get("fournisseur") or "").strip() or None,
            reference_piece=(request.form.get("reference_piece") or "").strip() or None,
            mode_paiement=(request.form.get("mode_paiement") or "").strip() or None,
        )
        db.session.add(dep)
        db.session.flush()
        db.session.add(DepenseAffectation(
            depense_id=dep.id,
            source_type="subvention",
            subvention_id=sub.id,
            ligne_budget_id=ligne.id,
            montant=float(dep.montant or 0.0),
        ))
        db.session.commit()
        flash("Dépense rapide créée et imputée à 100% sur la ligne choisie. Tu peux affiner la répartition depuis la fiche dépense.", "success")
        return redirect(url_for("projets.projet_finance", projet_id=projet.id, year=year, tab="depenses"))

    abort(400)


@bp.route("/projets/<int:projet_id>/budget")
@login_required
@require_perm("aap:view")
def projet_budget_home(projet_id):
    projet = db.get_or_404(Projet, projet_id)
    if not can_access_secteur(projet.secteur):
        abort(403)
    return redirect(url_for("projets.projet_budget_charges", projet_id=projet_id))


@bp.route("/projets/<int:projet_id>/budget/charges", methods=["GET", "POST"])
@login_required
@require_perm("aap:charges_view")
def projet_budget_charges(projet_id):
    projet = db.get_or_404(Projet, projet_id)
    if not can_access_secteur(projet.secteur):
        abort(403)

    if request.method == "POST":
        if not can("aap:charges_edit"):
            abort(403)
        libelle = (request.form.get("libelle") or "").strip()
        bloc = (request.form.get("bloc") or "directe").strip()
        code_plan = (request.form.get("code_plan") or "60").strip()
        montant = float(request.form.get("montant_previsionnel") or 0)

        if not libelle:
            flash("Le libellé est obligatoire.", "warning")
        else:
            c = ChargeProjet(
                projet_id=projet.id,
                libelle=libelle,
                bloc=bloc,
                code_plan=code_plan,
                montant_previsionnel=montant,
                montant_reel=float(request.form.get("montant_reel") or 0),
                commentaire=(request.form.get("commentaire") or "").strip() or None,
            )
            db.session.add(c)
            db.session.commit()
            flash("La charge a bien été ajoutée.", "success")
            return redirect(url_for("projets.projet_budget_charges", projet_id=projet.id))

    charges = ChargeProjet.query.filter_by(projet_id=projet.id).order_by(ChargeProjet.bloc.asc(), ChargeProjet.code_plan.asc(), ChargeProjet.id.asc()).all()
    return render_template(
        "projets_budget_charges.html",
        projet=projet,
        charges=charges,
        budget_stats=_budget_stats(projet.id),
        active_tab="charges",
    )


@bp.route("/projets/<int:projet_id>/budget/charges/<int:charge_id>/edit", methods=["GET", "POST"])
@login_required
@require_perm("aap:charges_edit")
def projet_budget_charge_edit(projet_id, charge_id):
    projet = db.get_or_404(Projet, projet_id)
    if not can_access_secteur(projet.secteur):
        abort(403)
    charge = ChargeProjet.query.filter_by(id=charge_id, projet_id=projet.id).first_or_404()

    if request.method == "POST":
        charge.libelle = (request.form.get("libelle") or "").strip()
        charge.bloc = (request.form.get("bloc") or "directe").strip()
        charge.code_plan = (request.form.get("code_plan") or "60").strip()
        charge.montant_previsionnel = float(request.form.get("montant_previsionnel") or 0)
        charge.montant_reel = float(request.form.get("montant_reel") or 0)
        charge.commentaire = (request.form.get("commentaire") or "").strip() or None
        db.session.commit()
        flash("La charge a bien été mise à jour.", "success")
        return redirect(url_for("projets.projet_budget_charges", projet_id=projet.id))

    return render_template(
        "projets_budget_charge_edit.html",
        projet=projet,
        charge=charge,
        budget_stats=_budget_stats(projet.id),
        active_tab="charges",
    )


@bp.route("/projets/<int:projet_id>/budget/charges/<int:charge_id>/delete", methods=["POST"])
@login_required
@require_perm("aap:charges_edit")
def projet_budget_charge_delete(projet_id, charge_id):
    projet = db.get_or_404(Projet, projet_id)
    if not can_access_secteur(projet.secteur):
        abort(403)
    charge = ChargeProjet.query.filter_by(id=charge_id, projet_id=projet.id).first_or_404()
    db.session.delete(charge)
    commit_delete(
        f"la charge « {charge.libelle} »",
        "Charge supprimée.",
        blocked_message=f"Impossible de supprimer la charge « {charge.libelle} » : elle est encore utilisée ailleurs.",
    )
    return redirect(url_for("projets.projet_budget_charges", projet_id=projet.id))


@bp.route("/projets/<int:projet_id>/budget/produits", methods=["GET", "POST"])
@login_required
@require_perm("aap:produits_view")
def projet_budget_produits(projet_id):
    projet = db.get_or_404(Projet, projet_id)
    if not can_access_secteur(projet.secteur):
        abort(403)

    if request.method == "POST":
        if not can("aap:produits_edit"):
            abort(403)
        financeur = (request.form.get("financeur") or "").strip()
        categorie = (request.form.get("categorie") or "autre").strip()
        statut = (request.form.get("statut") or "prevu").strip()
        demande = float(request.form.get("montant_demande") or 0)
        accorde = float(request.form.get("montant_accorde") or 0)
        recu = float(request.form.get("montant_recu") or 0)

        if not financeur:
            flash("Le nom du financeur est obligatoire.", "warning")
        else:
            p = ProduitProjet(
                projet_id=projet.id,
                financeur=financeur,
                categorie=categorie,
                statut=statut,
                montant_demande=demande,
                montant_accorde=accorde,
                montant_recu=recu,
                reference_dossier=(request.form.get("reference_dossier") or "").strip() or None,
                commentaire=(request.form.get("commentaire") or "").strip() or None,
            )
            db.session.add(p)
            db.session.commit()
            flash("Le financeur a bien été ajouté.", "success")
            return redirect(url_for("projets.projet_budget_produits", projet_id=projet.id))

    produits = ProduitProjet.query.filter_by(projet_id=projet.id).order_by(ProduitProjet.categorie.asc(), ProduitProjet.financeur.asc()).all()
    return render_template(
        "projets_budget_produits.html",
        projet=projet,
        produits=produits,
        budget_stats=_budget_stats(projet.id),
        active_tab="produits",
    )


@bp.route("/projets/<int:projet_id>/budget/produits/<int:produit_id>/edit", methods=["GET", "POST"])
@login_required
@require_perm("aap:produits_edit")
def projet_budget_produit_edit(projet_id, produit_id):
    projet = db.get_or_404(Projet, projet_id)
    if not can_access_secteur(projet.secteur):
        abort(403)
    produit = ProduitProjet.query.filter_by(id=produit_id, projet_id=projet.id).first_or_404()

    if request.method == "POST":
        produit.financeur = (request.form.get("financeur") or "").strip()
        produit.categorie = (request.form.get("categorie") or "autre").strip()
        produit.statut = (request.form.get("statut") or "prevu").strip()
        produit.montant_demande = float(request.form.get("montant_demande") or 0)
        produit.montant_accorde = float(request.form.get("montant_accorde") or 0)
        produit.montant_recu = float(request.form.get("montant_recu") or 0)
        produit.reference_dossier = (request.form.get("reference_dossier") or "").strip() or None
        produit.commentaire = (request.form.get("commentaire") or "").strip() or None
        db.session.commit()
        flash("Le financeur a bien été mis à jour.", "success")
        return redirect(url_for("projets.projet_budget_produits", projet_id=projet.id))

    return render_template(
        "projets_budget_produit_edit.html",
        projet=projet,
        produit=produit,
        budget_stats=_budget_stats(projet.id),
        active_tab="produits",
    )


@bp.route("/projets/<int:projet_id>/budget/produits/<int:produit_id>/delete", methods=["POST"])
@login_required
@require_perm("aap:produits_edit")
def projet_budget_produit_delete(projet_id, produit_id):
    projet = db.get_or_404(Projet, projet_id)
    if not can_access_secteur(projet.secteur):
        abort(403)
    produit = ProduitProjet.query.filter_by(id=produit_id, projet_id=projet.id).first_or_404()
    db.session.delete(produit)
    commit_delete(
        f"le produit / financeur « {produit.financeur} »",
        "Produit/financeur supprimé.",
        blocked_message=f"Impossible de supprimer le produit / financeur « {produit.financeur} » : il est encore utilisé ailleurs.",
    )
    return redirect(url_for("projets.projet_budget_produits", projet_id=projet.id))


@bp.route("/projets/<int:projet_id>/budget/ventilation", methods=["GET", "POST"])
@login_required
@require_perm("aap:ventilation_view")
def projet_budget_ventilation(projet_id):
    projet = db.get_or_404(Projet, projet_id)
    if not can_access_secteur(projet.secteur):
        abort(403)

    charges = ChargeProjet.query.filter_by(projet_id=projet.id).order_by(ChargeProjet.bloc.asc(), ChargeProjet.code_plan.asc(), ChargeProjet.id.asc()).all()
    produits = ProduitProjet.query.filter_by(projet_id=projet.id).order_by(ProduitProjet.categorie.asc(), ProduitProjet.financeur.asc()).all()

    # index existing ventilations
    existing = VentilationProjet.query.join(ChargeProjet).filter(ChargeProjet.projet_id == projet.id).all()
    vmap = {(v.charge_id, v.produit_id): v for v in existing}

    if request.method == "POST":
        if not can("aap:ventilation_edit"):
            abort(403)
        # matrice : v_<charge>_<produit> = montant
        # UX/sécurité : on refuse les ventilations incohérentes (somme > charge, somme > financeur)

        # 1) Lire les valeurs du formulaire en mémoire
        new_vals: dict[tuple[int, int], float] = {}
        for c in charges:
            for p in produits:
                key = f"v_{c.id}_{p.id}"
                if key not in request.form:
                    continue
                raw = (request.form.get(key) or "").strip().replace(",", ".")
                try:
                    val = float(raw) if raw else 0.0
                except ValueError:
                    val = 0.0
                if val < 0:
                    val = 0.0
                new_vals[(c.id, p.id)] = val

        # 2) Contrôles par charge
        errors = []
        for c in charges:
            s = sum((new_vals.get((c.id, p.id), float(vmap.get((c.id, p.id)).montant_ventile or 0)) if vmap.get((c.id, p.id)) else 0.0) for p in produits)
            max_c = float(c.montant_previsionnel or 0)
            # tolérance 1 centime
            if max_c > 0 and s - max_c > 0.01:
                errors.append(f"Charge '{c.libelle}' : {s:.2f}€ ventilés pour {max_c:.2f}€ prévus")

        # 3) Contrôles par produit/financeur
        for p in produits:
            s = sum((new_vals.get((c.id, p.id), float(vmap.get((c.id, p.id)).montant_ventile or 0)) if vmap.get((c.id, p.id)) else 0.0) for c in charges)
            # base de comparaison : reçu > accordé > demandé
            base = float(p.montant_recu or 0) if float(p.montant_recu or 0) > 0 else (float(p.montant_accorde or 0) if float(p.montant_accorde or 0) > 0 else float(p.montant_demande or 0))
            if base > 0 and s - base > 0.01:
                errors.append(f"Financeur '{p.financeur}' : {s:.2f}€ ventilés pour {base:.2f}€ ({'reçu' if float(p.montant_recu or 0) > 0 else ('accordé' if float(p.montant_accorde or 0) > 0 else 'demandé')})")

        if errors:
            flash("La ventilation ne peut pas être enregistrée : des incohérences ont été détectées.", "danger")
            for e in errors[:8]:
                flash("• " + e, "warning")
            if len(errors) > 8:
                flash(f"(+{len(errors) - 8} autres incohérences)", "warning")
            return redirect(url_for("projets.projet_budget_ventilation", projet_id=projet.id))

        # 4) Appliquer en base (diff minimal)
        changed = 0
        for c in charges:
            for p in produits:
                val = new_vals.get((c.id, p.id), None)
                if val is None:
                    continue
                cur = vmap.get((c.id, p.id))

                if val <= 0:
                    if cur:
                        db.session.delete(cur)
                        changed += 1
                    continue

                if cur:
                    if float(cur.montant_ventile or 0) != val:
                        cur.montant_ventile = val
                        changed += 1
                else:
                    nv = VentilationProjet(charge_id=c.id, produit_id=p.id, montant_ventile=val)
                    db.session.add(nv)
                    vmap[(c.id, p.id)] = nv
                    changed += 1

        db.session.commit()
        flash(f"La ventilation a bien été enregistrée ({changed} modification(s)).", "success")
        return redirect(url_for("projets.projet_budget_ventilation", projet_id=projet.id))

    # rebuild vmap with numbers for template
    vvals = {(cid, pid): float(v.montant_ventile or 0) for (cid, pid), v in vmap.items()}
    return render_template(
        "projets_budget_ventilation.html",
        projet=projet,
        charges=charges,
        produits=produits,
        vvals=vvals,
        budget_stats=_budget_stats(projet.id),
        active_tab="ventilation",
    )


@bp.route("/projets/<int:projet_id>/budget/synthese")
@login_required
@require_perm("aap:synthese_view")
def projet_budget_synthese(projet_id):
    projet = db.get_or_404(Projet, projet_id)
    if not can_access_secteur(projet.secteur):
        abort(403)
    charges = ChargeProjet.query.filter_by(projet_id=projet.id).all()
    produits = ProduitProjet.query.filter_by(projet_id=projet.id).all()

    alertes = []
    # charges non financées
    for c in charges:
        if float(c.reste_a_financer or 0) > 0.01:
            alertes.append(f"Charge non financée : {c.libelle} (reste {c.reste_a_financer:.2f}€)")
    # produits non ventilés
    for p in produits:
        if float(p.reste_a_ventiler or 0) > 0.01:
            alertes.append(f"Produit non ventilé : {p.financeur} (reste {p.reste_a_ventiler:.2f}€)")

    return render_template(
        "projets_budget_synthese.html",
        projet=projet,
        charges=charges,
        produits=produits,
        alertes=alertes,
        budget_stats=_budget_stats(projet.id),
        active_tab="synthese",
    )
