"""Page « Mon espace salarié », suggestions d'agenda et images de signature."""
from __future__ import annotations

from flask import abort, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.models import DemandeRecuperation, FraisKilometrique, SignatureRh
from app.rbac import can, require_perm
from app.services import espace_salarie as es
from app.services.storage import send_media_file

from . import bp


@bp.route("/")
@login_required
@require_perm("salarie:espace")
def espace():
    """L'espace salarié vit sur l'accueil (onglet « Salarié »)."""
    return redirect(url_for("main.dashboard", espace="salarie"))


@bp.route("/preferences/courriels", methods=["POST"])
@login_required
@require_perm("salarie:espace")
def preference_courriels():
    """Couper ou rétablir les e-mails de l'espace salarié (badges de l'accueil inchangés)."""
    from flask import flash
    from app.services.audit import journaliser
    from app.services.courriels_rh import changer_preference
    actif = request.form.get("actif") == "1"
    changer_preference(current_user, actif)
    journaliser("rh.preference_courriels", details={"actif": actif})
    flash("Tu recevras les e-mails de l'espace salarié." if actif
          else "Plus d'e-mails de l'espace salarié : les badges de l'accueil restent là.", "success")
    return redirect(url_for("main.dashboard", espace="salarie"))


@bp.route("/agenda-du-jour")
@login_required
@require_perm("salarie:espace")
def agenda_du_jour():
    """Activités de mon agenda pour un jour (formulaires heures sup / trajets)."""
    jour = es.parse_date(request.args.get("jour"))
    return jsonify([
        {"cle": e["cle"], "libelle": f"{e['titre']}{' · ' + e['horaire'] if e['horaire'] else ''}"}
        for e in es.evenements_du_jour(current_user, jour)
    ])


@bp.route("/signatures/<int:signature_id>.png")
@login_required
@require_perm("salarie:espace")
def signature_image(signature_id: int):
    """Image d'une signature : la personne concernée ou l'équipe qui instruit."""
    sig = db.get_or_404(SignatureRh, signature_id)
    if not sig.chemin:
        abort(404)
    autorise = False
    if sig.objet_type == "demande_recuperation":
        demande = db.session.get(DemandeRecuperation, sig.objet_id)
        autorise = demande is not None and (
            demande.salarie.user_id == current_user.id
            or can("recup:transmettre") or can("recup:decider"))
    elif sig.objet_type == "frais_kilometrique":
        frais = db.session.get(FraisKilometrique, sig.objet_id)
        autorise = frais is not None and (frais.salarie.user_id == current_user.id or can("frais_km:suivi"))
    if not autorise:
        abort(403)
    reponse = send_media_file(sig.chemin)
    reponse.headers["Cache-Control"] = "private, no-store"
    return reponse
