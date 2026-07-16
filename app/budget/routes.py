import os
from datetime import datetime

from app.utils.dates import utcnow
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from sqlalchemy import or_, func

from app.extensions import db
from app.models import Subvention, LigneBudget, Depense, DepenseDocument, DepenseAffectation
from app.rbac import require_perm, can_access_secteur
from app.services.storage import ensure_upload_subdir, media_relpath, send_media_file
from app.utils.delete_guard import commit_delete

bp = Blueprint("budget", __name__)

ALLOWED_EXT = {"pdf", "png", "jpg", "jpeg", "webp", "doc", "docx", "xls", "xlsx"}

def can_see_secteur(secteur: str) -> bool:
    return can_access_secteur(secteur)

def depense_visible(dep: Depense) -> bool:
    # Compat ancien modèle : dépense liée à une ligne budget unique.
    if dep.budget_source and dep.budget_source.source_sub:
        return can_see_secteur(dep.budget_source.source_sub.secteur)

    # Nouveau modèle : dépense visible via au moins une affectation visible.
    for aff in getattr(dep, "affectations", []) or []:
        if aff.subvention and can_see_secteur(aff.subvention.secteur):
            return True
    return False


def _visible_charge_lines_for_depense(dep: Depense | None = None, secteur: str | None = None, annee: int | None = None):
    q = LigneBudget.query.join(Subvention).filter(LigneBudget.nature == "charge", Subvention.est_archive.is_(False))
    if secteur:
        q = q.filter(Subvention.secteur == secteur)
    elif not current_user.has_perm("scope:all_secteurs"):
        q = q.filter(Subvention.secteur == current_user.secteur_assigne)
    if annee:
        q = q.filter(Subvention.annee_exercice == annee)
    return q.order_by(Subvention.annee_exercice.desc(), Subvention.nom.asc(), LigneBudget.compte.asc(), LigneBudget.libelle.asc()).all()

def allowed_file(filename: str) -> bool:
    if not filename or "." not in filename:
        return False
    ext = filename.rsplit(".", 1)[1].lower()
    return ext in ALLOWED_EXT

def ensure_justifs_folder():
    return ensure_upload_subdir("justifs")


def parse_money(raw_value, default: float = 0.0) -> float:
    """Parse un montant saisi par un humain sans fabriquer de centimes fantômes.

    Accepte :
    - 155
    - 155.00
    - 155,00
    - 1 255,50
    - 1 255.50 €
    """
    raw = str(raw_value or "").strip()
    if not raw:
        return float(default)

    cleaned = (
        raw.replace("\u00a0", "")
        .replace(" ", "")
        .replace("€", "")
        .replace(",", ".")
        .strip()
    )

    try:
        value = Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return float(default)

    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def parse_optional_money(raw_value):
    raw = str(raw_value or "").strip()
    if not raw:
        return None
    return parse_money(raw)


def _reduce_existing_affectations_for_split(dep: Depense, surplus: float) -> bool:
    """Libère de la place dans la répartition existante.

    Cas visé :
    - dépense = 400 €
    - affectation initiale = CAF 400 €
    - l'utilisateur ajoute QPV 100 €
    => on réduit automatiquement CAF à 300 € puis on ajoute QPV 100 €.

    Retourne True si la réduction a été possible, False sinon.
    """
    remaining = round(float(surplus or 0), 2)
    if remaining <= 0.01:
        return True

    # On réduit d'abord l'affectation de départ historique si elle existe,
    # puis les autres, de la plus ancienne à la plus récente.
    def sort_key(aff):
        is_initial = 0 if (dep.ligne_budget_id and aff.ligne_budget_id == dep.ligne_budget_id) else 1
        created = aff.created_at or datetime.min
        return (is_initial, created, aff.id or 0)

    candidates = sorted(list(getattr(dep, "affectations", []) or []), key=sort_key)

    for aff in candidates:
        if remaining <= 0.01:
            break
        amount = round(float(aff.montant or 0), 2)
        if amount <= 0:
            continue

        take = min(amount, remaining)
        new_amount = round(amount - take, 2)
        remaining = round(remaining - take, 2)

        if new_amount <= 0.01:
            db.session.delete(aff)
        else:
            aff.montant = new_amount

    return remaining <= 0.01


def _form_list(name: str):
    return request.form.getlist(name) or request.form.getlist(name.rstrip("[]"))


def _merge_affectation_rows(rows: list[dict]) -> list[dict]:
    merged = {}
    for row in rows:
        if row["source_type"] == "subvention":
            key = ("subvention", row.get("ligne_budget_id"))
        else:
            key = (row["source_type"], (row.get("libelle_source") or "").strip().lower())
        if key not in merged:
            merged[key] = dict(row)
        else:
            merged[key]["montant"] = round(float(merged[key]["montant"] or 0) + float(row["montant"] or 0), 2)
            if row.get("commentaire"):
                old = merged[key].get("commentaire") or ""
                merged[key]["commentaire"] = (old + " / " + row["commentaire"]).strip(" /")
    return list(merged.values())


def _line_available_for_affectation(ligne: LigneBudget) -> float:
    try:
        return round(float(getattr(ligne, "reste", 0) or 0), 2)
    except Exception:
        return 0.0


def _validate_rows_capacity(rows: list[dict]) -> tuple[bool, str]:
    amounts_by_line = {}
    for row in rows:
        line_id = row.get("ligne_budget_id")
        if not line_id:
            continue
        amounts_by_line[line_id] = round(float(amounts_by_line.get(line_id, 0.0)) + float(row.get("montant") or 0), 2)

    for line_id, amount in amounts_by_line.items():
        ligne = db.session.get(LigneBudget, line_id)
        if not ligne:
            return False, "Une ligne de financement est introuvable."
        available = _line_available_for_affectation(ligne)
        if amount > available + 0.01:
            sub_name = ligne.source_sub.nom if getattr(ligne, "source_sub", None) else "Enveloppe inconnue"
            return False, (
                f"Budget insuffisant sur {sub_name} — {ligne.compte} {ligne.libelle} : "
                f"tu essaies d'imputer {amount:.2f} €, il reste {available:.2f} €."
            )
    return True, ""


def _parse_creation_affectations(initial_line: LigneBudget, dep_amount: float):
    """Parse les lignes de répartition saisies directement à la création.

    Si aucune répartition n'est saisie, on garde l'ancien comportement :
    100% de la dépense sur la ligne de départ.

    Si une répartition partielle est saisie et que l'option auto-completion est cochée,
    le reste est ajouté sur la ligne de départ.
    """
    source_types = _form_list("aff_source_type[]")
    line_ids = _form_list("aff_ligne_budget_id[]")
    labels = _form_list("aff_libelle_source[]")
    amounts = _form_list("aff_montant[]")
    comments = _form_list("aff_commentaire[]")

    rows = []
    max_len = max(len(source_types), len(line_ids), len(labels), len(amounts), len(comments), 0)

    for idx in range(max_len):
        raw_amount = amounts[idx] if idx < len(amounts) else ""
        if not str(raw_amount or "").strip():
            continue

        amount = parse_money(raw_amount)
        if amount <= 0:
            continue

        source_type = (source_types[idx] if idx < len(source_types) else "subvention").strip() or "subvention"
        comment = (comments[idx] if idx < len(comments) else "").strip() or None

        if source_type == "fonds_propres":
            rows.append({
                "source_type": "fonds_propres",
                "subvention_id": None,
                "ligne_budget_id": None,
                "libelle_source": (labels[idx] if idx < len(labels) else "").strip() or "Fonds propres",
                "montant": amount,
                "commentaire": comment,
            })
            continue

        if source_type == "autre":
            rows.append({
                "source_type": "autre",
                "subvention_id": None,
                "ligne_budget_id": None,
                "libelle_source": (labels[idx] if idx < len(labels) else "").strip() or "Autre source",
                "montant": amount,
                "commentaire": comment,
            })
            continue

        try:
            line_id = int(line_ids[idx] if idx < len(line_ids) else 0)
        except Exception:
            line_id = 0
        target_line = db.session.get(LigneBudget, line_id)
        if not target_line:
            return None, "Une ligne de répartition pointe vers une ligne budgétaire introuvable."
        if getattr(target_line, "nature", "charge") != "charge":
            return None, "Une affectation de dépense doit pointer vers une ligne de charge."
        target_sub = target_line.source_sub
        if not target_sub or not can_see_secteur(target_sub.secteur):
            return None, "Tu n'as pas accès à l'une des subventions utilisées dans la répartition."

        rows.append({
            "source_type": "subvention",
            "subvention_id": target_sub.id,
            "ligne_budget_id": target_line.id,
            "libelle_source": None,
            "montant": amount,
            "commentaire": comment,
        })

    amount_total = round(float(dep_amount or 0), 2)
    rows = _merge_affectation_rows(rows)
    rows_total = round(sum(float(r["montant"] or 0) for r in rows), 2)

    # Aucun split saisi : ancien comportement, 100% sur la ligne de départ.
    if rows_total <= 0.01:
        return [{
            "source_type": "subvention",
            "subvention_id": initial_line.source_sub.id,
            "ligne_budget_id": initial_line.id,
            "libelle_source": None,
            "montant": amount_total,
            "commentaire": None,
        }], None

    if rows_total > amount_total + 0.01:
        return None, f"La répartition dépasse le montant de la dépense : {rows_total:.2f} € affectés pour {amount_total:.2f} €."

    auto_complete = (request.form.get("auto_complete_initial") or "").strip() == "1"
    if rows_total < amount_total - 0.01 and auto_complete:
        remaining = round(amount_total - rows_total, 2)
        rows.append({
            "source_type": "subvention",
            "subvention_id": initial_line.source_sub.id,
            "ligne_budget_id": initial_line.id,
            "libelle_source": None,
            "montant": remaining,
            "commentaire": "Complément automatique à la création",
        })
        rows = _merge_affectation_rows(rows)
        rows_total = round(sum(float(r["montant"] or 0) for r in rows), 2)

    ok_capacity, capacity_msg = _validate_rows_capacity(rows)
    if not ok_capacity:
        return None, capacity_msg

    # On autorise une dépense partiellement affectée si l'utilisateur a volontairement décoché
    # l'auto-complétion : elle remontera en alerte dans le pilotage annuel.
    return rows, None


def _create_affectations_for_depense(dep: Depense, rows: list[dict]):
    for row in rows:
        db.session.add(DepenseAffectation(
            depense_id=dep.id,
            source_type=row.get("source_type") or "subvention",
            subvention_id=row.get("subvention_id"),
            ligne_budget_id=row.get("ligne_budget_id"),
            libelle_source=row.get("libelle_source"),
            montant=float(row.get("montant") or 0),
            commentaire=row.get("commentaire"),
        ))


@bp.route("/depense/nouvelle", methods=["GET", "POST"])
@login_required
@require_perm("depenses:create")
def depense_new():
    subs_q = Subvention.query.filter_by(est_archive=False)
    if not current_user.has_perm("scope:all_secteurs"):
        subs_q = subs_q.filter(Subvention.secteur == current_user.secteur_assigne)
    subs = subs_q.order_by(Subvention.annee_exercice.desc(), Subvention.nom.asc()).all()

    if request.method == "POST":
        sub_id = int(request.form.get("subvention_id") or 0)
        ligne_id = int(request.form.get("ligne_budget_id") or 0)

        sub = db.get_or_404(Subvention, sub_id)
        if not can_see_secteur(sub.secteur):
            abort(403)

        ligne = db.get_or_404(LigneBudget, ligne_id)
        if ligne.subvention_id != sub.id:
            abort(400)

        if getattr(ligne, "nature", "charge") != "charge":
            flash("Une dépense doit être rattachée à une ligne de charge, et non à un produit.", "danger")
            return redirect(url_for("budget.depense_new"))


        libelle = (request.form.get("libelle") or "").strip()
        montant = parse_money(request.form.get("montant"))
        type_depense = (request.form.get("type_depense") or "Fonctionnement").strip()

        date_str = (request.form.get("date_paiement") or "").strip()
        date_p = datetime.strptime(date_str, "%Y-%m-%d").date() if date_str else None

        if not libelle:
            flash("Le libellé de la dépense est obligatoire.", "danger")
            return redirect(url_for("budget.depense_new"))

        dep = Depense(
            ligne_budget_id=ligne.id,
            libelle=libelle,
            montant=montant,
            date_paiement=date_p,
            type_depense=type_depense,
            fournisseur=(request.form.get("fournisseur") or "").strip() or None,
            # Depense model uses reference_piece (not 'reference')
            reference_piece=(request.form.get("reference") or "").strip() or None,
            mode_paiement=(request.form.get("mode_paiement") or "").strip() or None,
        )
        affectation_rows, aff_error = _parse_creation_affectations(ligne, montant)
        if aff_error:
            flash(aff_error, "danger")
            return redirect(url_for("budget.depense_new"))

        db.session.add(dep)
        db.session.flush()
        _create_affectations_for_depense(dep, affectation_rows)
        db.session.commit()

        # Option ergonomique : créer en même temps une entrée inventaire (si demandé)
        if (request.form.get("create_inventory") or "").strip() == "1":
            try:
                from app.models import InventaireItem
                from app.inventaire_materiel.routes import _next_id_interne

                date_ref = date_p or utcnow().date()
                id_interne = _next_id_interne(sub.secteur, date_ref)

                inv = InventaireItem(
                    secteur=sub.secteur,
                    id_interne=id_interne,
                    categorie=(request.form.get("inv_categorie") or "").strip() or None,
                    designation=(request.form.get("inv_designation") or libelle).strip() or libelle,
                    quantite=int(request.form.get("inv_quantite") or 1),
                    valeur_unitaire=parse_optional_money(request.form.get("inv_valeur_unitaire")),
                    date_entree=date_ref,
                    localisation=(request.form.get("inv_localisation") or "").strip() or None,
                    etat=(request.form.get("inv_etat") or "OK").strip() or "OK",
                    numero_serie=(request.form.get("inv_numero_serie") or "").strip() or None,
                    notes=(request.form.get("inv_notes") or "").strip() or None,
                    depense_id=dep.id,
                    created_by=getattr(current_user, "id", None),
                )
                db.session.add(inv)
                db.session.commit()
                flash("L'entrée d'inventaire a bien été créée et liée à la dépense.", "ok")
            except Exception as e:
                db.session.rollback()
                flash(f"La dépense a bien été enregistrée, mais l'entrée d'inventaire n'a pas pu être créée ({e}).", "warning")

        flash("La dépense a bien été enregistrée. Un justificatif peut être ajouté si nécessaire.", "warning")
        return redirect(url_for("budget.depense_edit", depense_id=dep.id))

    available_lines = _visible_charge_lines_for_depense()
    return render_template("depense_new.html", subs=subs, available_lines=available_lines)

@bp.route("/depense/<int:depense_id>/edit", methods=["GET", "POST"])
@login_required
@require_perm("depenses:create")
def depense_edit(depense_id):
    dep = db.get_or_404(Depense, depense_id)
    if not depense_visible(dep):
        abort(403)

    ligne = dep.budget_source
    sub = ligne.source_sub if ligne else None
    if not sub:
        for aff in getattr(dep, "affectations", []) or []:
            if aff.subvention:
                sub = aff.subvention
                break

    if request.method == "POST":
        action = request.form.get("action") or ""

        if action == "update":
            dep.libelle = (request.form.get("libelle") or "").strip()
            dep.montant = parse_money(request.form.get("montant"))
            dep.type_depense = (request.form.get("type_depense") or "Fonctionnement").strip()

            date_str = (request.form.get("date_paiement") or "").strip()
            dep.date_paiement = datetime.strptime(date_str, "%Y-%m-%d").date() if date_str else None

            db.session.commit()
            flash("La dépense a bien été modifiée.", "success")
            return redirect(url_for("budget.depense_edit", depense_id=dep.id))

        if action == "add_affectation":
            source_type = (request.form.get("source_type") or "subvention").strip()
            montant_aff = parse_money(request.form.get("montant"))
            if montant_aff <= 0:
                flash("Le montant affecté doit être supérieur à 0 €.", "danger")
                return redirect(url_for("budget.depense_edit", depense_id=dep.id))

            if source_type == "fonds_propres":
                aff = DepenseAffectation(
                    depense_id=dep.id,
                    source_type="fonds_propres",
                    libelle_source=(request.form.get("libelle_source") or "Fonds propres").strip() or "Fonds propres",
                    montant=montant_aff,
                    commentaire=(request.form.get("commentaire") or "").strip() or None,
                )
            else:
                ligne_id = int(request.form.get("ligne_budget_id") or 0)
                target_line = db.get_or_404(LigneBudget, ligne_id)
                if getattr(target_line, "nature", "charge") != "charge":
                    flash("Une affectation de dépense doit pointer vers une ligne de charge.", "danger")
                    return redirect(url_for("budget.depense_edit", depense_id=dep.id))
                target_sub = target_line.source_sub
                if not target_sub or not can_see_secteur(target_sub.secteur):
                    abort(403)
                aff = DepenseAffectation(
                    depense_id=dep.id,
                    source_type="subvention",
                    subvention_id=target_sub.id,
                    ligne_budget_id=target_line.id,
                    montant=montant_aff,
                    commentaire=(request.form.get("commentaire") or "").strip() or None,
                )
                if montant_aff > _line_available_for_affectation(target_line) + 0.01:
                    flash(f"Budget insuffisant sur cette ligne : tu essaies d'imputer {montant_aff:.2f} €, il reste {_line_available_for_affectation(target_line):.2f} €.", "danger")
                    return redirect(url_for("budget.depense_edit", depense_id=dep.id))
                if dep.ligne_budget_id is None:
                    dep.ligne_budget_id = target_line.id

            current_total = float(dep.total_affecte or 0)
            dep_amount = float(dep.montant or 0)
            surplus = round(current_total + float(montant_aff or 0) - dep_amount, 2)

            auto_adjusted = False
            if surplus > 0.01:
                if not _reduce_existing_affectations_for_split(dep, surplus):
                    db.session.rollback()
                    flash("Le total affecté dépasserait le montant de la dépense et aucune affectation existante ne peut être réduite automatiquement.", "danger")
                    return redirect(url_for("budget.depense_edit", depense_id=dep.id))
                auto_adjusted = True

            db.session.add(aff)
            db.session.commit()
            if auto_adjusted:
                flash("Affectation ajoutée : la répartition existante a été ajustée automatiquement pour ne pas dépasser le montant de la dépense.", "success")
            else:
                flash("Affectation ajoutée à la dépense.", "success")
            return redirect(url_for("budget.depense_edit", depense_id=dep.id))

        if action == "update_affectation":
            aff_id = int(request.form.get("affectation_id") or 0)
            aff = db.get_or_404(DepenseAffectation, aff_id)
            if aff.depense_id != dep.id:
                abort(400)

            new_amount = parse_money(request.form.get("montant"))
            if new_amount <= 0:
                flash("Le montant affecté doit être supérieur à 0 €.", "danger")
                return redirect(url_for("budget.depense_edit", depense_id=dep.id))

            other_total = round(float(dep.total_affecte or 0) - float(aff.montant or 0), 2)
            if other_total + new_amount > float(dep.montant or 0) + 0.01:
                flash("Cette modification ferait dépasser le montant total de la dépense.", "danger")
                return redirect(url_for("budget.depense_edit", depense_id=dep.id))

            if aff.source_type == "subvention" and aff.ligne_budget_id:
                increase = round(float(new_amount or 0) - float(aff.montant or 0), 2)
                if increase > 0:
                    line_available = _line_available_for_affectation(aff.ligne_budget)
                    if increase > line_available + 0.01:
                        flash(f"Budget insuffisant sur cette ligne : augmentation demandée {increase:.2f} €, reste disponible {line_available:.2f} €.", "danger")
                        return redirect(url_for("budget.depense_edit", depense_id=dep.id))

            aff.montant = new_amount
            aff.commentaire = (request.form.get("commentaire") or "").strip() or None
            db.session.commit()
            flash("Affectation modifiée.", "success")
            return redirect(url_for("budget.depense_edit", depense_id=dep.id))

        if action == "delete_affectation":
            aff_id = int(request.form.get("affectation_id") or 0)
            aff = db.get_or_404(DepenseAffectation, aff_id)
            if aff.depense_id != dep.id:
                abort(400)
            db.session.delete(aff)
            db.session.commit()
            flash("Affectation retirée de la dépense.", "warning")
            return redirect(url_for("budget.depense_edit", depense_id=dep.id))

        if action == "upload_doc":
            file = request.files.get("document")
            if not file or not file.filename:
                flash("Aucun fichier n'a été sélectionné.", "danger")
                return redirect(url_for("budget.depense_edit", depense_id=dep.id))

            if not allowed_file(file.filename):
                flash("Le type de fichier n'est pas autorisé. Utilisez un PDF, une image ou un document bureautique.", "danger")
                return redirect(url_for("budget.depense_edit", depense_id=dep.id))

            folder = ensure_justifs_folder()
            safe_original = secure_filename(file.filename)

            ts = utcnow().strftime("%Y%m%d_%H%M%S")
            stored = secure_filename(f"D{dep.id}_{ts}_{safe_original}")
            file.save(os.path.join(folder, stored))

            doc = DepenseDocument(depense_id=dep.id, filename=stored, original_name=safe_original)
            db.session.add(doc)
            db.session.commit()

            flash("Le justificatif a bien été ajouté.", "success")
            return redirect(url_for("budget.depense_edit", depense_id=dep.id))

        abort(400)

    alloue = float(ligne.montant_reel or 0) if ligne else 0.0
    engage = float(ligne.engage or 0) if ligne else 0.0
    reste = float(ligne.reste or 0) if ligne else 0.0
    existing_inv = list(getattr(dep, "inventaire_items", []) or [])

    secteur_ref = sub.secteur if sub else None
    annee_ref = sub.annee_exercice if sub else None
    available_lines = _visible_charge_lines_for_depense(dep, secteur=secteur_ref, annee=annee_ref)

    return render_template(
        "depense_edit.html",
        dep=dep,
        ligne=ligne,
        sub=sub,
        alloue=alloue,
        engage=engage,
        reste=reste,
        affectations=list(getattr(dep, "affectations", []) or []),
        total_affecte=float(dep.total_affecte or 0),
        reste_a_affecter=float(dep.reste_a_affecter or 0),
        available_lines=available_lines,
        inventaire_items=existing_inv,
        inventaire_secteur=secteur_ref or getattr(current_user, "secteur_assigne", ""),
    )

# ✅ NOUVEL ENDPOINT : suppression dépense (fiable)
@bp.route("/depense/<int:depense_id>/delete", methods=["POST"])
@login_required
@require_perm("depenses:delete")
def depense_delete(depense_id):
    dep = db.get_or_404(Depense, depense_id)
    if not depense_visible(dep):
        abort(403)

    # pour retour vers le pilotage
    ligne = dep.budget_source
    sub = ligne.source_sub

    # supprimer les fichiers physiques seulement après succès BDD
    folder = ensure_justifs_folder()
    doc_paths = []
    for doc in dep.documents:
        try:
            doc_paths.append(os.path.join(folder, doc.filename))
        except Exception:
            pass

    db.session.delete(dep)
    if commit_delete(
        f"la dépense « {getattr(dep, 'objet', None) or dep.id} »",
        "Dépense supprimée.",
        success_category="warning",
        blocked_message="Impossible de supprimer cette dépense : elle est encore utilisée ailleurs.",
    ):
        for path in doc_paths:
            try:
                if os.path.exists(path):
                    os.remove(path)
            except Exception:
                pass
    return redirect(url_for("main.subvention_pilotage", subvention_id=sub.id))

@bp.route("/depense/doc/<int:doc_id>/download")
@login_required
@require_perm("depenses:view")
def depense_doc_download(doc_id):
    doc = db.get_or_404(DepenseDocument, doc_id)
    dep = doc.depense

    if not depense_visible(dep):
        abort(403)

    ensure_justifs_folder()
    return send_media_file(media_relpath("justifs", doc.filename), as_attachment=True, download_name=doc.original_name)

@bp.route("/depense/doc/<int:doc_id>/delete", methods=["POST"])
@login_required
@require_perm("depenses:delete")
def depense_doc_delete(doc_id):
    doc = db.get_or_404(DepenseDocument, doc_id)
    dep = doc.depense

    if not depense_visible(dep):
        abort(403)

    folder = ensure_justifs_folder()
    path = os.path.join(folder, doc.filename)

    db.session.delete(doc)
    if commit_delete(
        f"le justificatif « {doc.original_name or doc.filename} »",
        "Justificatif supprimé.",
        success_category="warning",
        blocked_message="Impossible de supprimer ce justificatif : il est encore utilisé ailleurs.",
    ):
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass

    return redirect(url_for("budget.depense_edit", depense_id=dep.id))

@bp.route("/depenses")
@login_required
@require_perm("depenses:view")
def depenses_list():
    # Liste des enveloppes visibles
    subs_q = Subvention.query.filter_by(est_archive=False)
    if not current_user.has_perm("scope:all_secteurs"):
        subs_q = subs_q.filter(Subvention.secteur == current_user.secteur_assigne)
    subs = subs_q.order_by(Subvention.annee_exercice.desc(), Subvention.nom.asc()).all()

    # Exercices visibles pour filtre rapide
    years_q = db.session.query(Subvention.annee_exercice).filter(Subvention.est_archive.is_(False))
    if not current_user.has_perm("scope:all_secteurs"):
        years_q = years_q.filter(Subvention.secteur == current_user.secteur_assigne)
    years = [y for (y,) in years_q.distinct().order_by(Subvention.annee_exercice.desc()).all() if y]

    # Filtres
    sub_id = request.args.get("subvention_id", type=int)
    ligne_id = request.args.get("ligne_budget_id", type=int)
    selected_year = request.args.get("annee", type=int)
    q = (request.args.get("q") or "").strip()

    try:
        per_page = int(request.args.get("per_page") or 50)
    except Exception:
        per_page = 50
    per_page = max(20, min(per_page, 200))

    try:
        page = int(request.args.get("page") or 1)
    except Exception:
        page = 1
    page = max(page, 1)

    dep_q = Depense.query.join(LigneBudget).join(Subvention)

    if not current_user.has_perm("scope:all_secteurs"):
        dep_q = dep_q.filter(Subvention.secteur == current_user.secteur_assigne)

    if selected_year:
        dep_q = dep_q.filter(Subvention.annee_exercice == selected_year)

    if sub_id:
        dep_q = dep_q.filter(LigneBudget.subvention_id == sub_id)

    if ligne_id:
        dep_q = dep_q.filter(Depense.ligne_budget_id == ligne_id)

    if q:
        like = f"%{q}%"
        dep_q = dep_q.filter(or_(
            Depense.libelle.ilike(like),
            Depense.fournisseur.ilike(like),
            Depense.reference_piece.ilike(like),
            Depense.type_depense.ilike(like),
            Subvention.nom.ilike(like),
            LigneBudget.libelle.ilike(like),
            LigneBudget.compte.ilike(like),
        ))

    total_count = dep_q.count()
    total_amount = db.session.query(func.coalesce(func.sum(Depense.montant), 0.0)).select_from(Depense).join(LigneBudget).join(Subvention)

    if not current_user.has_perm("scope:all_secteurs"):
        total_amount = total_amount.filter(Subvention.secteur == current_user.secteur_assigne)
    if selected_year:
        total_amount = total_amount.filter(Subvention.annee_exercice == selected_year)
    if sub_id:
        total_amount = total_amount.filter(LigneBudget.subvention_id == sub_id)
    if ligne_id:
        total_amount = total_amount.filter(Depense.ligne_budget_id == ligne_id)
    if q:
        like = f"%{q}%"
        total_amount = total_amount.filter(or_(
            Depense.libelle.ilike(like),
            Depense.fournisseur.ilike(like),
            Depense.reference_piece.ilike(like),
            Depense.type_depense.ilike(like),
            Subvention.nom.ilike(like),
            LigneBudget.libelle.ilike(like),
            LigneBudget.compte.ilike(like),
        ))

    total_amount = float(total_amount.scalar() or 0.0)

    total_pages = max(1, (total_count + per_page - 1) // per_page)
    if page > total_pages:
        page = total_pages

    deps = (
        dep_q
        .order_by(Depense.date_paiement.desc().nullslast(), Depense.id.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    # Lignes possibles si une subvention est sélectionnée
    lignes = []
    if sub_id:
        lignes = LigneBudget.query.filter_by(subvention_id=sub_id).order_by(LigneBudget.compte.asc(), LigneBudget.libelle.asc()).all()

    return render_template(
        "depenses_list.html",
        subs=subs,
        lignes=lignes,
        years=years,
        selected_sub_id=sub_id,
        selected_ligne_id=ligne_id,
        selected_year=selected_year,
        q=q,
        deps=deps,
        page=page,
        per_page=per_page,
        total_pages=total_pages,
        total_count=total_count,
        total_amount=total_amount,
    )

