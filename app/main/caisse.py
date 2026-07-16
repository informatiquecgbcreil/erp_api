"""Caisse : état, fond de caisse, comptage (arrêté), dépôts en banque.

Écrit pour quelqu'un qui découvre la gestion d'une caisse : chaque écran
explique le vocabulaire, et le guide « Je compte la caisse et je fais le
dépôt » accompagne l'opération de bout en bout.
"""
from datetime import date, datetime

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.main.common import bp
from app.models import CaisseMouvement
from app.rbac import can, require_perm
from app.services.audit import journaliser
from app.services.caisse import enregistrer_comptage, etat_caisse, journal


def _montant_form(champ: str) -> float | None:
    try:
        return round(float(str(request.form.get(champ) or "").replace(",", ".").strip()), 2)
    except Exception:
        return None


def _date_form(champ: str) -> date:
    try:
        return datetime.strptime((request.form.get(champ) or "").strip(), "%Y-%m-%d").date()
    except Exception:
        return date.today()


@bp.route("/caisse")
@login_required
@require_perm("caisse:view")
def caisse():
    return render_template(
        "caisse.html",
        etat=etat_caisse(),
        mouvements=journal(),
        peut_editer=can("caisse:edit"),
        today=date.today(),
    )


@bp.post("/caisse/fond")
@login_required
@require_perm("caisse:edit")
def caisse_fond():
    montant = _montant_form("montant")
    if montant is None or montant < 0:
        flash("Montant du fond de caisse invalide.", "danger")
        return redirect(url_for("main.caisse"))
    db.session.add(CaisseMouvement(
        type_mouvement="fond", canal="especes", montant=montant,
        date_mouvement=date.today(),
        commentaire=(request.form.get("commentaire") or "").strip() or None,
        created_by_user_id=getattr(current_user, "id", None),
    ))
    db.session.commit()
    journaliser("caisse.fond", cible=f"{montant:.2f} €")
    flash(f"Fond de caisse réglé à {montant:.2f} €.", "success")
    return redirect(url_for("main.caisse"))


@bp.post("/caisse/comptage")
@login_required
@require_perm("caisse:edit")
def caisse_comptage():
    montant = _montant_form("montant_constate")
    if montant is None or montant < 0:
        flash("Montant compté invalide.", "danger")
        return redirect(url_for("main.caisse"))
    comptage = enregistrer_comptage(
        montant,
        commentaire=(request.form.get("commentaire") or "").strip() or None,
        user_id=getattr(current_user, "id", None),
    )
    journaliser("caisse.comptage", cible=f"constaté {montant:.2f} €, écart {comptage.ecart:+.2f} €")
    if abs(comptage.ecart or 0) < 0.01:
        flash(f"Caisse comptée : {montant:.2f} €, tout est juste ✔", "success")
    else:
        flash(
            f"Caisse comptée : {montant:.2f} € — écart de {comptage.ecart:+.2f} € par rapport au théorique. "
            "L'écart est tracé et le théorique repart du montant réel.",
            "warning",
        )
    return redirect(url_for("main.caisse"))


@bp.post("/caisse/depot")
@login_required
@require_perm("caisse:edit")
def caisse_depot():
    """Enregistre un dépôt en banque (espèces et/ou chèques)."""
    etat = etat_caisse()
    especes = _montant_form("montant_especes") or 0.0
    cheques = _montant_form("montant_cheques") or 0.0
    try:
        nb_cheques = int(request.form.get("nb_cheques") or 0)
    except Exception:
        nb_cheques = 0

    if especes <= 0 and cheques <= 0:
        flash("Indique au moins un montant (espèces ou chèques) à déposer.", "danger")
        return redirect(url_for("main.caisse"))
    if especes > etat["theorique_especes"]:
        flash(
            f"Impossible de déposer {especes:.2f} € en espèces : la caisse n'en contient que "
            f"{etat['theorique_especes']:.2f} € en théorie (fais d'abord un comptage si le réel diffère).",
            "danger",
        )
        return redirect(url_for("main.caisse"))
    if cheques > 0 and nb_cheques <= 0:
        flash("Indique le nombre de chèques déposés.", "danger")
        return redirect(url_for("main.caisse"))

    jour = _date_form("date_depot")
    commentaire = (request.form.get("commentaire") or "").strip() or None
    # Horodatage COMMUN aux lignes du même dépôt : c'est lui qui permet au
    # bordereau de regrouper espèces + chèques déposés ensemble.
    from app.utils.dates import utcnow
    ts = utcnow()
    ids = []
    if especes > 0:
        m = CaisseMouvement(type_mouvement="depot", canal="especes", montant=especes,
                            date_mouvement=jour, commentaire=commentaire, created_at=ts,
                            created_by_user_id=getattr(current_user, "id", None))
        db.session.add(m)
        ids.append(m)
    if cheques > 0:
        m = CaisseMouvement(type_mouvement="depot", canal="cheque", montant=cheques,
                            nb_cheques=nb_cheques, date_mouvement=jour, commentaire=commentaire, created_at=ts,
                            created_by_user_id=getattr(current_user, "id", None))
        db.session.add(m)
        ids.append(m)
    db.session.commit()
    journaliser("caisse.depot", cible=f"espèces {especes:.2f} € + chèques {cheques:.2f} € ({nb_cheques})")
    flash("Dépôt enregistré. Tu peux imprimer le bordereau pour l'apporter à la banque.", "success")
    return redirect(url_for("main.caisse_bordereau", mouvement_id=ids[0].id))


@bp.route("/caisse/depot/<int:mouvement_id>/bordereau")
@login_required
@require_perm("caisse:view")
def caisse_bordereau(mouvement_id: int):
    """Bordereau imprimable d'un dépôt (regroupe espèces + chèques du même jour)."""
    m = db.get_or_404(CaisseMouvement, mouvement_id)
    if m.type_mouvement != "depot":
        flash("Ce mouvement n'est pas un dépôt.", "danger")
        return redirect(url_for("main.caisse"))
    lignes = (
        CaisseMouvement.query
        .filter(
            CaisseMouvement.type_mouvement == "depot",
            CaisseMouvement.date_mouvement == m.date_mouvement,
            CaisseMouvement.created_at == m.created_at,
        )
        .all()
    )
    if m not in lignes:
        lignes = [m]
    total = round(sum(x.montant for x in lignes), 2)
    return render_template("caisse_bordereau.html", lignes=lignes, depot=m, total=total)
