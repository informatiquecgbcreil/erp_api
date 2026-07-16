import os
from datetime import datetime

from app.utils.dates import utcnow

from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from app.extensions import db
from app.models import Subvention, LigneBudget, Depense, FactureAchat, FactureLigne
from app.rbac import require_perm
from app.services.storage import ensure_upload_subdir, media_relpath, send_media_file


bp = Blueprint("inventaire", __name__, url_prefix="/factures")


ALLOWED_EXT = {"pdf", "png", "jpg", "jpeg", "webp"}


def can_see_secteur(secteur: str) -> bool:
    if current_user.has_perm("scope:all_secteurs"):
        return True
    if not current_user.has_perm("scope:all_secteurs"):
        return (current_user.secteur_assigne or "") == (secteur or "")
    return False


def facture_visible(f: FactureAchat) -> bool:
    # Facture visible si (a) secteur principal visible, ou (b) au moins une ligne visible.
    # Pour rester simple et sûr : on exige le secteur_principal.
    return can_see_secteur(f.secteur_principal)


def allowed_file(filename: str) -> bool:
    if not filename or "." not in filename:
        return False
    ext = filename.rsplit(".", 1)[1].lower()
    return ext in ALLOWED_EXT


def ensure_factures_folder():
    return ensure_upload_subdir("factures")


def visible_subventions():
    q = Subvention.query.filter_by(est_archive=False)
    if not current_user.has_perm("scope:all_secteurs"):
        q = q.filter(Subvention.secteur == current_user.secteur_assigne)
    return q.order_by(Subvention.annee_exercice.desc(), Subvention.nom.asc()).all()


def visible_lignes_budget(sub_id: int):
    q = LigneBudget.query.filter_by(subvention_id=sub_id)
    # uniquement CHARGES pour générer des dépenses
    q = q.filter(LigneBudget.nature == "charge")
    return q.order_by(LigneBudget.compte.asc(), LigneBudget.libelle.asc()).all()


def _financement_label(ft: str) -> str:
    ft = (ft or "").strip().lower()
    return {
        "subvention": "Subvention",
        "fonds_propres": "Fonds propres",
        "don": "Don / mécénat",
        "autre": "Autre",
    }.get(ft, "Autre")


def get_or_create_hors_subvention(secteur: str, financement_type: str = "fonds_propres") -> Subvention:
    """Crée/retourne une "subvention" technique pour porter les lignes hors subvention.

    On ne change pas ton modèle LigneBudget (qui dépend d'une Subvention), donc on crée une
    Subvention par secteur et par type de financement.
    """
    secteur = (secteur or "").strip()
    if not secteur:
        raise ValueError("secteur manquant")

    ft = (financement_type or "fonds_propres").strip().lower()
    if ft == "subvention":
        ft = "fonds_propres"

    annee = utcnow().year
    nom = f"Hors subvention — {_financement_label(ft)} — {secteur}"

    s = Subvention.query.filter_by(secteur=secteur, annee_exercice=annee, nom=nom, est_archive=False).first()
    if not s:
        s = Subvention(nom=nom, secteur=secteur, annee_exercice=annee, est_archive=False)
        db.session.add(s)
        db.session.commit()

    # ligne tampon "à ventiler" (charge)
    l = LigneBudget.query.filter_by(subvention_id=s.id, nature="charge", compte="60", libelle="À ventiler").first()
    if not l:
        l = LigneBudget(subvention_id=s.id, nature="charge", compte="60", libelle="À ventiler", montant_base=0.0, montant_reel=0.0)
        db.session.add(l)
        db.session.commit()

    return s


def get_ligne_a_ventiler(sub: Subvention) -> LigneBudget:
    l = LigneBudget.query.filter_by(subvention_id=sub.id, nature="charge", libelle="À ventiler").first()
    if not l:
        l = LigneBudget(subvention_id=sub.id, nature="charge", compte="60", libelle="À ventiler", montant_base=0.0, montant_reel=0.0)
        db.session.add(l)
        db.session.commit()
    return l


@bp.route("/")
@login_required
@require_perm("inventaire:view")
def factures_list():

    q = FactureAchat.query
    if not current_user.has_perm("scope:all_secteurs"):
        q = q.filter(FactureAchat.secteur_principal == current_user.secteur_assigne)

    factures = q.order_by(FactureAchat.created_at.desc()).all()
    return render_template("factures_list.html", factures=factures)


@bp.route("/nouvelle", methods=["GET", "POST"])
@login_required
@require_perm("inventaire:edit")
def facture_new():

    # choix secteur
    secteur_default = current_user.secteur_assigne if not current_user.has_perm("scope:all_secteurs") else ""

    if request.method == "POST":
        secteur = (request.form.get("secteur_principal") or secteur_default or "").strip()
        if not secteur:
            flash("Secteur obligatoire.", "danger")
            return redirect(url_for("inventaire.facture_new"))
        if not can_see_secteur(secteur):
            abort(403)

        fournisseur = (request.form.get("fournisseur") or "").strip()
        ref = (request.form.get("reference_facture") or "").strip()

        date_str = (request.form.get("date_facture") or "").strip()
        if date_str:
            try:
                date_f = datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                flash("Date de facture invalide.", "danger")
                return redirect(url_for("inventaire.facture_new"))
        else:
            date_f = None

        f = FactureAchat(
            secteur_principal=secteur,
            fournisseur=fournisseur or None,
            reference_facture=ref or None,
            date_facture=date_f,
            statut="brouillon",
            created_by=current_user.id,
        )

        # upload fichier
        file = request.files.get("facture_file")
        if file and file.filename:
            if not allowed_file(file.filename):
                flash("Type de fichier non autorisé (pdf / images).", "danger")
                return redirect(url_for("inventaire.facture_new"))
            folder = ensure_factures_folder()
            safe_original = secure_filename(file.filename)
            ts = utcnow().strftime("%Y%m%d_%H%M%S")
            stored = secure_filename(f"F_{ts}_{safe_original}")
            file.save(os.path.join(folder, stored))
            f.filename = stored
            f.original_name = safe_original

        db.session.add(f)
        db.session.commit()

        flash("Facture créée. Ajoute maintenant les lignes 👇", "success")
        return redirect(url_for("inventaire.facture_detail", facture_id=f.id))

    # pour la vue : si responsable, on cache la saisie secteur
    return render_template(
        "facture_new.html",
        secteur_default=secteur_default,
        is_resp=(not current_user.has_perm("scope:all_secteurs")),
    )


# Alias UX : beaucoup de gens tentent /factures/new
@bp.route("/new", methods=["GET", "POST"])
@login_required
@require_perm("inventaire:edit")
def facture_new_alias():
    return facture_new()


@bp.route("/<int:facture_id>", methods=["GET", "POST"])
@login_required
@require_perm("inventaire:view")
def facture_detail(facture_id):

    f = db.get_or_404(FactureAchat, facture_id)
    if not facture_visible(f):
        abort(403)

    subs = visible_subventions()

    # Prépare toutes les lignes charge visibles (pour dropdown filtrable côté JS)
    all_lignes = []
    for s in subs:
        for l in visible_lignes_budget(s.id):
            all_lignes.append(l)

    if request.method == "POST":
        action = (request.form.get("action") or "").strip()

        if action == "add_line":
            financement_type = (request.form.get("financement_type") or "subvention").strip().lower()
            a_ventiler = bool(request.form.get("a_ventiler"))

            sub_id = int(request.form.get("subvention_id") or 0)
            ligne_id = int(request.form.get("ligne_budget_id") or 0)

            # cohérence avec secteur principal (blindage)
            if not current_user.has_perm("scope:all_secteurs") and f.secteur_principal != current_user.secteur_assigne:
                abort(403)

            # Détermine la "subvention" support :
            # - si financement_type=subvention : subvention choisie obligatoire
            # - sinon : subvention technique "Hors subvention" (par secteur)
            if financement_type == "subvention":
                if not sub_id:
                    flash("Choisis une subvention OU passe en Fonds propres/Autre (aide terrain).", "danger")
                    return redirect(url_for("inventaire.facture_detail", facture_id=f.id))

                sub = db.get_or_404(Subvention, sub_id)
                if not can_see_secteur(sub.secteur):
                    abort(403)
                if sub.secteur != f.secteur_principal:
                    # on reste simple : une facture est sectorisée
                    flash("La subvention choisie n'est pas dans le secteur de la facture.", "danger")
                    return redirect(url_for("inventaire.facture_detail", facture_id=f.id))
            else:
                sub = get_or_create_hors_subvention(f.secteur_principal, financement_type)
                # si l'utilisateur n'a pas choisi de ligne, on bascule en 'à ventiler'
                if not ligne_id:
                    a_ventiler = True

            # Choix de la ligne budget (compte) :
            # - si 'à ventiler' => ligne tampon
            # - sinon => ligne choisie sur la subvention support
            if a_ventiler:
                ligne = get_ligne_a_ventiler(sub)
            else:
                if not ligne_id:
                    flash("Choisis une ligne budgétaire (ou coche 'À ventiler').", "danger")
                    return redirect(url_for("inventaire.facture_detail", facture_id=f.id))
                ligne = db.get_or_404(LigneBudget, ligne_id)
                if ligne.subvention_id != sub.id:
                    abort(400)
                if getattr(ligne, "nature", "charge") != "charge":
                    flash("Impossible : une facture d'achat doit pointer des lignes de CHARGE (compte 6*).", "danger")
                    return redirect(url_for("inventaire.facture_detail", facture_id=f.id))

            libelle = (request.form.get("libelle") or "").strip()
            if not libelle:
                flash("Libellé obligatoire.", "danger")
                return redirect(url_for("inventaire.facture_detail", facture_id=f.id))

            quantite = int(request.form.get("quantite") or 1)
            prix_unitaire = float(request.form.get("prix_unitaire") or 0)
            montant_ligne = float(request.form.get("montant_ligne") or 0)

            # si montant non fourni, calcule
            if montant_ligne <= 0:
                montant_ligne = round(float(quantite or 0) * float(prix_unitaire or 0), 2)

            fl = FactureLigne(
                facture_id=f.id,
                secteur=f.secteur_principal,
                financement_type=financement_type,
                a_ventiler=a_ventiler,
                libelle=libelle,
                quantite=quantite,
                prix_unitaire=prix_unitaire,
                montant_ligne=montant_ligne,
                ligne_budget_id=ligne.id,
                subvention_id=sub.id,
            )
            db.session.add(fl)
            db.session.commit()
            flash("Ligne ajoutée.", "success")
            return redirect(url_for("inventaire.facture_detail", facture_id=f.id))

        if action == "delete_line":
            line_id = int(request.form.get("line_id") or 0)
            fl = db.get_or_404(FactureLigne, line_id)
            if fl.facture_id != f.id:
                abort(400)
            if not can_see_secteur(fl.secteur):
                abort(403)
            if f.statut != "brouillon":
                flash("Facture déjà validée : suppression impossible.", "danger")
                return redirect(url_for("inventaire.facture_detail", facture_id=f.id))
            db.session.delete(fl)
            db.session.commit()
            flash("Ligne supprimée.", "warning")
            return redirect(url_for("inventaire.facture_detail", facture_id=f.id))

        abort(400)

    return render_template(
        "facture_detail.html",
        f=f,
        subs=subs,
        all_lignes=all_lignes,
    )


@bp.route("/<int:facture_id>/validate", methods=["POST"])
@login_required
def facture_validate(facture_id):

    f = db.get_or_404(FactureAchat, facture_id)
    if not facture_visible(f):
        abort(403)
    if f.statut != "brouillon":
        flash("Facture déjà validée.", "warning")
        return redirect(url_for("inventaire.facture_detail", facture_id=f.id))

    if not f.lignes:
        flash("Ajoute au moins une ligne avant de valider.", "danger")
        return redirect(url_for("inventaire.facture_detail", facture_id=f.id))

    # Blindage : un responsable ne peut valider que si toutes les lignes sont dans SON secteur
    if not current_user.has_perm("scope:all_secteurs"):
        if f.secteur_principal != current_user.secteur_assigne:
            abort(403)
        for fl in f.lignes:
            if fl.secteur != current_user.secteur_assigne:
                abort(403)

    # Génère 1 dépense par ligne
    created = 0
    for fl in f.lignes:
        # sécurité : on vérifie aussi la cohérence ligne budget/subvention
        sub = db.session.get(Subvention, fl.subvention_id)
        ligne = db.session.get(LigneBudget, fl.ligne_budget_id)
        if not sub or not ligne or ligne.subvention_id != sub.id:
            flash("Incohérence détectée (ligne budget/subvention). Validation stoppée.", "danger")
            return redirect(url_for("inventaire.facture_detail", facture_id=f.id))
        if getattr(ligne, "nature", "charge") != "charge":
            flash("Une ligne n'est pas une CHARGE : validation stoppée.", "danger")
            return redirect(url_for("inventaire.facture_detail", facture_id=f.id))

        dep = Depense(
            ligne_budget_id=ligne.id,
            facture_ligne_id=fl.id,
            libelle=fl.libelle,
            montant=float(fl.montant_ligne or 0),
            fournisseur=f.fournisseur,
            reference_piece=f.reference_facture,
            date_paiement=f.date_facture,
            type_depense="Fonctionnement",
            statut="valide",
        )
        db.session.add(dep)
        created += 1

    f.statut = "valide"
    db.session.commit()

    flash(f"Facture validée : {created} dépense(s) créée(s).", "success")
    return redirect(url_for("inventaire.facture_detail", facture_id=f.id))


@bp.route("/doc/<int:facture_id>/download")
@login_required
@require_perm("inventaire:view")
def facture_download(facture_id):

    f = db.get_or_404(FactureAchat, facture_id)
    if not facture_visible(f):
        abort(403)
    if not f.filename:
        abort(404)
    ensure_factures_folder()
    return send_media_file(media_relpath("factures", f.filename), as_attachment=True, download_name=f.original_name or f.filename)
