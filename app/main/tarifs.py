"""Barème des tarifs : adhésions (individuelle/familiale) et participation.

Réservé à la direction/finance (permission cotisations:tarifs). Le montant
dû d'une cotisation déjà créée est figé à sa création : modifier ou
supprimer une ligne de barème ne change jamais rétroactivement une dette
déjà enregistrée — seules les prochaines cotisations créées s'appuient sur
la ligne en vigueur à ce moment-là.
"""
from datetime import date, datetime

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.main.common import bp
from app.models import TYPES_TARIF, TYPES_TARIF_LABELS, TarifBareme
from app.rbac import require_perm
from app.services.cotisations import annees_scolaires_disponibles, bareme_annee, libelle_annee_scolaire


def _annee_demandee() -> int:
    from app.services.cotisations import annee_scolaire_courante
    try:
        return int(request.args.get("annee") or request.form.get("annee") or annee_scolaire_courante())
    except Exception:
        return annee_scolaire_courante()


@bp.route("/tarifs")
@login_required
@require_perm("cotisations:tarifs")
def tarifs_cotisations():
    annee = _annee_demandee()
    return render_template(
        "tarifs.html",
        annee=annee,
        libelle_annee=libelle_annee_scolaire(annee),
        annees=sorted(set(annees_scolaires_disponibles()) | {annee}, reverse=True),
        bareme=bareme_annee(annee),
        types_tarif=TYPES_TARIF,
        types_tarif_labels=TYPES_TARIF_LABELS,
        today=date.today(),
    )


@bp.post("/tarifs/ligne")
@login_required
@require_perm("cotisations:tarifs")
def tarif_ligne_creer():
    annee = _annee_demandee()
    type_tarif = (request.form.get("type_tarif") or "").strip()
    if type_tarif not in TYPES_TARIF:
        flash("Type de tarif invalide.", "danger")
        return redirect(url_for("main.tarifs_cotisations", annee=annee))

    try:
        montant = round(float(str(request.form.get("montant") or "0").replace(",", ".")), 2)
    except Exception:
        montant = -1
    if montant < 0:
        flash("Montant invalide.", "danger")
        return redirect(url_for("main.tarifs_cotisations", annee=annee))

    try:
        date_debut = datetime.strptime((request.form.get("date_debut") or "").strip(), "%Y-%m-%d").date()
    except Exception:
        date_debut = date(annee, 9, 1)

    db.session.add(TarifBareme(
        annee_scolaire=annee,
        type_tarif=type_tarif,
        montant=montant,
        date_debut=date_debut,
        commentaire=(request.form.get("commentaire") or "").strip() or None,
        created_by_user_id=getattr(current_user, "id", None),
    ))
    db.session.commit()
    flash(f"Tarif ajouté : {TYPES_TARIF_LABELS.get(type_tarif, type_tarif)} à {montant:.2f} € à partir du {date_debut.strftime('%d/%m/%Y')}.", "success")
    return redirect(url_for("main.tarifs_cotisations", annee=annee))


@bp.post("/tarifs/ligne/<int:tarif_id>/supprimer")
@login_required
@require_perm("cotisations:tarifs")
def tarif_ligne_supprimer(tarif_id: int):
    annee = _annee_demandee()
    ligne = db.session.get(TarifBareme, tarif_id)
    if ligne is not None:
        db.session.delete(ligne)
        db.session.commit()
        flash("Ligne de tarif supprimée (sans effet sur les cotisations déjà créées).", "info")
    return redirect(url_for("main.tarifs_cotisations", annee=annee))
