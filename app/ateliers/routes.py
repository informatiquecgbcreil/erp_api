from flask import Blueprint, render_template, redirect, url_for, flash, abort
from flask_login import login_required, current_user

from app.models import Atelier, ProjetAtelier
from app.rbac import require_perm
from app.ateliers.services import sync_ateliers_from_presence_db


bp = Blueprint("ateliers", __name__)


def _can_manage() -> bool:
    return current_user.is_authenticated and current_user.has_perm("ateliers:sync")


@bp.route("/ateliers")
@login_required
@require_perm("ateliers:view")
def list_ateliers():
    # Les responsables secteur ne voient que les ateliers déjà rattachés à leurs projets
    q = Atelier.query
    if current_user.has_role("admin_tech"):
        abort(403)
    # Note: on affiche tous les ateliers synchronisés pour permettre le repérage.
    # Le filtrage par secteur se fait au niveau des projets (can_see_secteur).

    ateliers = q.order_by(Atelier.date.desc(), Atelier.id.desc()).limit(400).all()
    linked_map = {}
    if ateliers:
        ids = [a.id for a in ateliers]
        links = ProjetAtelier.query.filter(ProjetAtelier.atelier_id.in_(ids)).all()
        for l in links:
            linked_map.setdefault(l.atelier_id, []).append(l.projet.nom)

    return render_template("ateliers_list.html", ateliers=ateliers, linked_map=linked_map, can_manage=_can_manage())


@bp.route("/ateliers/sync", methods=["POST"])
@login_required
@require_perm("ateliers:sync")
def sync_ateliers():
    try:
        n = sync_ateliers_from_presence_db(limit=800)
        flash(f"Synchronisation OK : {n} atelier(s) mis à jour.", "success")
    except Exception as e:
        flash(f"Erreur sync présence : {e}", "danger")
    return redirect(url_for("activite.index"))
