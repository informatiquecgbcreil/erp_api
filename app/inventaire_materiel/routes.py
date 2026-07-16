from datetime import datetime

from app.utils.dates import utcnow
import re
import unicodedata

from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, current_app
from flask_login import login_required, current_user

from app.extensions import db
from app.models import InventaireItem, FactureLigne, Depense
from app.rbac import require_perm
from app.utils.delete_guard import commit_delete


bp = Blueprint("inventaire_materiel", __name__, url_prefix="/inventaire")


def can_see_secteur(secteur: str) -> bool:
    if current_user.has_perm("admin:all") or current_user.has_perm("scope:all_secteurs"):
        return True
    return (current_user.secteur_assigne or "") == (secteur or "")


def _require_can_see_item(item: InventaireItem):
    if not can_see_secteur(item.secteur):
        abort(403)


def _default_secteur() -> str:
    if not current_user.has_perm("scope:all_secteurs"):
        return current_user.secteur_assigne or ""
    # finance/directrice can choose; default empty
    return ""


def _secteur_code(secteur: str) -> str:
    """Retourne un code court (3 lettres) à partir du nom de secteur.

    Exemple: "Numérique" -> "NUM". On normalise (sans accents), on garde les lettres.
    """
    s = (secteur or "").strip()
    if not s:
        return "GEN"
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.upper()
    s = re.sub(r"[^A-Z]", "", s)
    if len(s) >= 3:
        return s[:3]
    return (s + "XXX")[:3]


def _next_id_interne(secteur: str, date_ref) -> str:
    """Génère un ID interne au format CODE-MM-YYYY-0001.

    Le compteur repart pour chaque (CODE, MOIS, ANNEE).
    """
    code = _secteur_code(secteur)
    mm = int(getattr(date_ref, "month", 0) or 1)
    yyyy = int(getattr(date_ref, "year", 0) or utcnow().year)
    prefix = f"{code}-{mm:02d}-{yyyy}-"

    last = (
        InventaireItem.query
        .filter(InventaireItem.id_interne.like(prefix + "%"))
        .order_by(InventaireItem.id_interne.desc())
        .first()
    )

    n = 0
    if last and last.id_interne:
        m = re.search(r"(\d{4})$", last.id_interne)
        if m:
            try:
                n = int(m.group(1))
            except Exception:
                n = 0
    n += 1
    return f"{prefix}{n:04d}"


@bp.route("/")
@login_required
@require_perm("inventaire:view")
def list_items():

    q = InventaireItem.query
    if not current_user.has_perm("scope:all_secteurs"):
        q = q.filter(InventaireItem.secteur == current_user.secteur_assigne)

    # filtres
    secteur = (request.args.get("secteur") or "").strip()
    etat = (request.args.get("etat") or "").strip()
    categorie = (request.args.get("categorie") or "").strip()
    localisation = (request.args.get("localisation") or "").strip()
    search = (request.args.get("q") or "").strip()
    sort = (request.args.get("sort") or "recent").strip()  # recent / id / designation / categorie

    if secteur:
        q = q.filter(InventaireItem.secteur == secteur)
    if etat:
        q = q.filter(InventaireItem.etat == etat)
    if categorie:
        q = q.filter(InventaireItem.categorie == categorie)
    if localisation:
        q = q.filter(InventaireItem.localisation == localisation)
    if search:
        like = f"%{search.lower()}%"
        q = q.filter(
            db.or_(
                db.func.lower(InventaireItem.designation).like(like),
                db.func.lower(db.coalesce(InventaireItem.id_interne, "")).like(like),
                db.func.lower(db.coalesce(InventaireItem.numero_serie, "")).like(like),
                db.func.lower(db.coalesce(InventaireItem.marque, "")).like(like),
                db.func.lower(db.coalesce(InventaireItem.modele, "")).like(like),
            )
        )

    if sort == "id":
        q = q.order_by(InventaireItem.id.asc())
    elif sort == "designation":
        q = q.order_by(InventaireItem.designation.asc())
    elif sort == "categorie":
        q = q.order_by(db.coalesce(InventaireItem.categorie, "").asc(), InventaireItem.designation.asc())
    else:
        q = q.order_by(InventaireItem.created_at.desc())

    items = q.all()
    return render_template(
        "inventaire_list.html",
        items=items,
        filtre_secteur=secteur,
        filtre_etat=etat,
        filtre_categorie=categorie,
        filtre_localisation=localisation,
        filtre_q=search,
        filtre_sort=sort,
    )


@bp.route("/new", methods=["GET", "POST"])
@login_required
@require_perm("inventaire:edit")
def new_item():

    if request.method == "POST":
        secteur = (request.form.get("secteur") or "").strip()
        if not current_user.has_perm("scope:all_secteurs"):
            secteur = current_user.secteur_assigne or ""

        if not secteur:
            flash("Secteur manquant.", "err")
            return redirect(url_for("inventaire_materiel.new_item"))

        if not can_see_secteur(secteur):
            abort(403)

        designation = (request.form.get("designation") or "").strip()
        if not designation:
            flash("Désignation obligatoire.", "err")
            return redirect(url_for("inventaire_materiel.new_item"))

        categorie = (request.form.get("categorie") or "").strip() or None
        marque = (request.form.get("marque") or "").strip() or None
        modele = (request.form.get("modele") or "").strip() or None
        numero_serie = (request.form.get("numero_serie") or "").strip() or None
        localisation = (request.form.get("localisation") or "").strip() or None
        etat = (request.form.get("etat") or "OK").strip() or "OK"
        notes = (request.form.get("notes") or "").strip() or None

        try:
            quantite = int(request.form.get("quantite") or 1)
        except Exception:
            quantite = 1
        if quantite < 1:
            quantite = 1

        valeur_unitaire = request.form.get("valeur_unitaire")
        try:
            valeur_unitaire = float(valeur_unitaire) if valeur_unitaire not in (None, "") else None
        except Exception:
            valeur_unitaire = None

        date_entree_raw = (request.form.get("date_entree") or "").strip()
        date_entree = None
        if date_entree_raw:
            try:
                date_entree = datetime.strptime(date_entree_raw, "%Y-%m-%d").date()
            except Exception:
                date_entree = None

        # ID interne auto (aide terrain) : basé sur la date d'entrée si fournie, sinon aujourd'hui
        date_ref = date_entree or utcnow().date()
        id_interne = _next_id_interne(secteur, date_ref)

        item = InventaireItem(
            secteur=secteur,
            id_interne=id_interne,
            categorie=categorie,
            designation=designation,
            marque=marque,
            modele=modele,
            quantite=quantite,
            numero_serie=numero_serie,
            etat=etat,
            localisation=localisation,
            valeur_unitaire=valeur_unitaire,
            date_entree=date_entree,
            notes=notes,
            created_by=getattr(current_user, "id", None),
        )
        db.session.add(item)
        db.session.commit()

        flash("Entrée inventaire créée.", "ok")
        return redirect(url_for("inventaire_materiel.list_items"))

    return render_template("inventaire_new.html", default_secteur=_default_secteur(), secteur_choices=(current_app.config.get("SECTEURS") or []))


@bp.route("/<int:item_id>", methods=["GET", "POST"])
@login_required
@require_perm("inventaire:edit")
def edit_item(item_id: int):

    item = db.get_or_404(InventaireItem, item_id)
    _require_can_see_item(item)

    if request.method == "POST":
        # secteur: only finance/directrice can change, but still must be allowed
        if current_user.has_perm("scope:all_secteurs"):
            secteur = (request.form.get("secteur") or "").strip()
            if secteur:
                item.secteur = secteur
        # responsable_secteur cannot change secteur

        item.categorie = (request.form.get("categorie") or "").strip() or None
        item.designation = (request.form.get("designation") or "").strip() or item.designation
        item.marque = (request.form.get("marque") or "").strip() or None
        item.modele = (request.form.get("modele") or "").strip() or None
        item.numero_serie = (request.form.get("numero_serie") or "").strip() or None
        item.localisation = (request.form.get("localisation") or "").strip() or None
        item.etat = (request.form.get("etat") or "OK").strip() or "OK"
        item.notes = (request.form.get("notes") or "").strip() or None

        try:
            qte = int(request.form.get("quantite") or item.quantite or 1)
            if qte < 1:
                qte = 1
            item.quantite = qte
        except Exception:
            pass

        valeur_unitaire = request.form.get("valeur_unitaire")
        try:
            item.valeur_unitaire = float(valeur_unitaire) if valeur_unitaire not in (None, "") else None
        except Exception:
            item.valeur_unitaire = None

        date_entree_raw = (request.form.get("date_entree") or "").strip()
        if date_entree_raw:
            try:
                item.date_entree = datetime.strptime(date_entree_raw, "%Y-%m-%d").date()
            except Exception:
                pass

        if not can_see_secteur(item.secteur):
            abort(403)

        db.session.commit()
        flash("Entrée inventaire mise à jour.", "ok")
        return redirect(url_for("inventaire_materiel.edit_item", item_id=item.id))

    return render_template("inventaire_edit.html", item=item)


@bp.route("/<int:item_id>/delete", methods=["POST"])
@login_required
@require_perm("inventaire:edit")
def delete_item(item_id: int):

    item = db.get_or_404(InventaireItem, item_id)
    _require_can_see_item(item)

    # On ne supprime pas la dépense : on détache juste les liens si besoin.
    db.session.delete(item)
    commit_delete(
        f"l'entrée inventaire « {item.designation} »",
        "Entrée inventaire supprimée définitivement.",
        success_category="warning",
        blocked_message=f"Impossible de supprimer l'entrée inventaire « {item.designation} » : elle est encore utilisée ailleurs.",
    )
    return redirect(url_for("inventaire_materiel.list_items"))


@bp.route("/from_facture_ligne/<int:ligne_id>", methods=["POST"])
@login_required
@require_perm("inventaire:edit")
def create_from_facture_ligne(ligne_id: int):
    """Créer une entrée inventaire pré-remplie depuis une ligne de facture."""

    fl = db.get_or_404(FactureLigne, ligne_id)
    if not can_see_secteur(fl.secteur):
        abort(403)

    # Try to find matching depense generated from this facture line
    dep = Depense.query.filter_by(facture_ligne_id=fl.id).first()

    # Date de référence = date de facture (si dispo), sinon aujourd'hui
    date_ref = (fl.facture.date_facture if fl.facture and fl.facture.date_facture else utcnow().date())
    id_interne = _next_id_interne(fl.secteur, date_ref)

    item = InventaireItem(
        secteur=fl.secteur,
        id_interne=id_interne,
        categorie="Informatique",
        designation=fl.libelle,
        quantite=fl.quantite or 1,
        valeur_unitaire=(fl.prix_unitaire if fl.prix_unitaire else None),
        date_entree=date_ref,
        facture_ligne_id=fl.id,
        depense_id=(dep.id if dep else None),
        created_by=getattr(current_user, "id", None),
        notes=f"Créé depuis facture #{fl.facture_id} (ligne #{fl.id}).",
    )
    db.session.add(item)
    db.session.commit()

    flash("Entrée inventaire créée depuis la ligne de facture.", "ok")
    return redirect(url_for("inventaire_materiel.edit_item", item_id=item.id))


@bp.route("/from_facture_ligne/<int:ligne_id>/bulk", methods=["POST"])
@login_required
@require_perm("inventaire:edit")
def create_bulk_from_facture_ligne(ligne_id: int):
    """Créer N items unitaires (quantite=1) depuis une ligne de facture (quantite=N)."""

    fl = db.get_or_404(FactureLigne, ligne_id)
    if not can_see_secteur(fl.secteur):
        abort(403)

    try:
        n = int(fl.quantite or 1)
    except Exception:
        n = 1
    if n < 1:
        n = 1

    dep = Depense.query.filter_by(facture_ligne_id=fl.id).first()
    date_ref = (fl.facture.date_facture if fl.facture and fl.facture.date_facture else utcnow().date())

    created_ids = []
    for _ in range(n):
        item = InventaireItem(
            secteur=fl.secteur,
            id_interne=_next_id_interne(fl.secteur, date_ref),
            categorie="Informatique",
            designation=fl.libelle,
            quantite=1,
            valeur_unitaire=(fl.prix_unitaire if fl.prix_unitaire else None),
            date_entree=date_ref,
            facture_ligne_id=fl.id,
            depense_id=(dep.id if dep else None),
            created_by=getattr(current_user, "id", None),
            notes=f"Créé en lot depuis facture #{fl.facture_id} (ligne #{fl.id}).",
        )
        db.session.add(item)
        db.session.flush()  # pour avoir item.id sans commit
        created_ids.append(item.id)

    db.session.commit()

    flash(f"{len(created_ids)} items inventaire créés (unitaires).", "ok")
    # Redirige vers la liste (tri récent) : l'utilisateur peut filtrer ensuite
    return redirect(url_for("inventaire_materiel.list_items"))


@bp.route("/from_depense/<int:depense_id>", methods=["POST"])
@login_required
def create_from_depense(depense_id: int):
    """Créer une entrée inventaire depuis une dépense (non liée à une facture)."""

    dep = db.get_or_404(Depense, depense_id)
    ligne = dep.budget_source
    sub = ligne.source_sub if ligne else None
    secteur_dep = getattr(sub, "secteur", None)

    if secteur_dep and not can_see_secteur(secteur_dep):
        abort(403)

    secteur = (request.form.get("secteur") or secteur_dep or "").strip()
    if not secteur:
        flash("Secteur manquant pour l'inventaire.", "danger")
        return redirect(url_for("budget.depense_edit", depense_id=dep.id))

    if not can_see_secteur(secteur):
        abort(403)

    designation = (request.form.get("designation") or dep.libelle or "").strip()
    if not designation:
        flash("Désignation obligatoire pour l'inventaire.", "danger")
        return redirect(url_for("budget.depense_edit", depense_id=dep.id))

    categorie = (request.form.get("categorie") or "").strip() or None
    etat = (request.form.get("etat") or "OK").strip() or "OK"
    localisation = (request.form.get("localisation") or "").strip() or None
    notes = (request.form.get("notes") or "").strip() or None

    try:
        quantite = int(request.form.get("quantite") or 1)
    except Exception:
        quantite = 1
    if quantite < 1:
        quantite = 1

    valeur_unitaire = request.form.get("valeur_unitaire")
    try:
        valeur_unitaire = float(valeur_unitaire) if valeur_unitaire not in (None, "") else None
    except Exception:
        valeur_unitaire = None

    date_entree_raw = (request.form.get("date_entree") or "").strip()
    date_entree = None
    if date_entree_raw:
        try:
            date_entree = datetime.strptime(date_entree_raw, "%Y-%m-%d").date()
        except Exception:
            date_entree = None
    if not date_entree:
        date_entree = dep.date_paiement or utcnow().date()

    id_interne = _next_id_interne(secteur, date_entree)

    item = InventaireItem(
        secteur=secteur,
        id_interne=id_interne,
        categorie=categorie,
        designation=designation,
        etat=etat,
        localisation=localisation,
        quantite=quantite,
        valeur_unitaire=valeur_unitaire,
        date_entree=date_entree,
        depense_id=dep.id,
        facture_ligne_id=getattr(dep, "facture_ligne_id", None),
        created_by=getattr(current_user, "id", None),
        notes=notes,
    )
    db.session.add(item)
    db.session.commit()

    flash("Entrée inventaire créée depuis la dépense.", "ok")
    return redirect(url_for("budget.depense_edit", depense_id=dep.id))
