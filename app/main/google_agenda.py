"""Connexion Google Agenda (synchro push temps réel) — page Mon agenda.

Routes :
- ``/mon-agenda/google/connecter``      : départ vers le consentement Google ;
- ``/mon-agenda/google/retour``         : retour OAuth (code → jetons, calendrier dédié) ;
- ``/mon-agenda/google/resynchroniser`` : resynchronisation complète à la demande ;
- ``/mon-agenda/google/deconnecter``    : révocation + suppression du calendrier dédié.

Le contenu poussé réutilise les réglages du flux iCal (page Mon agenda) :
un seul endroit à régler pour l'abonnement ET la synchro temps réel.
"""
from flask import current_app, flash, redirect, request, url_for
from flask_login import current_user, login_required

from app.main.common import bp
from app.rbac import require_perm
from app.services import google_agenda as ga


@bp.get("/mon-agenda/google/connecter")
@login_required
@require_perm("emargement:view")
def google_agenda_connecter():
    if not ga.est_configure():
        flash("La synchronisation Google n'est pas configurée sur cette installation (voir docs/google-agenda.md).", "warning")
        return redirect(url_for("main.mon_agenda"))
    return redirect(ga.url_autorisation(current_user))


@bp.get("/mon-agenda/google/retour")
@login_required
@require_perm("emargement:view")
def google_agenda_retour():
    if not ga.est_configure():
        flash("La synchronisation Google n'est pas configurée sur cette installation.", "warning")
        return redirect(url_for("main.mon_agenda"))

    if request.args.get("error"):
        flash("Connexion Google annulée ou refusée. Rien n'a été modifié.", "warning")
        return redirect(url_for("main.mon_agenda"))

    uid = ga.verifier_etat(request.args.get("state") or "")
    if uid is None or uid != current_user.id:
        flash("Retour Google invalide ou expiré : recommence la connexion depuis cette page.", "danger")
        return redirect(url_for("main.mon_agenda"))

    code = request.args.get("code") or ""
    if not code:
        flash("Google n'a pas renvoyé de code d'autorisation.", "danger")
        return redirect(url_for("main.mon_agenda"))

    try:
        compte = ga.connecter(current_user, code)
    except ga.GoogleAgendaErreur as exc:
        flash(f"Connexion Google impossible : {exc}", "danger")
        return redirect(url_for("main.mon_agenda"))

    # Premier remplissage : toutes les séances du périmètre, en arrière-plan.
    ga.lancer_synchro_arriere_plan(current_app._get_current_object(), complet=True)
    flash(
        f"Compte Google « {compte.google_email or 'connecté'} » relié : un calendrier dédié "
        "a été créé dans ton Google Agenda et tes séances s'y remplissent (quelques instants). "
        "Ensuite, chaque création ou modification est poussée immédiatement.",
        "success",
    )
    return redirect(url_for("main.mon_agenda"))


@bp.post("/mon-agenda/google/resynchroniser")
@login_required
@require_perm("emargement:view")
def google_agenda_resynchroniser():
    compte = getattr(current_user, "google_agenda", None)
    if compte is None:
        flash("Aucun compte Google connecté.", "warning")
        return redirect(url_for("main.mon_agenda"))
    ga.lancer_synchro_arriere_plan(current_app._get_current_object(), complet=True)
    flash("Resynchronisation lancée en arrière-plan : ton Google Agenda sera à jour dans quelques instants.", "success")
    return redirect(url_for("main.mon_agenda"))


@bp.post("/mon-agenda/google/deconnecter")
@login_required
@require_perm("emargement:view")
def google_agenda_deconnecter():
    compte = getattr(current_user, "google_agenda", None)
    if compte is None:
        flash("Aucun compte Google connecté.", "warning")
        return redirect(url_for("main.mon_agenda"))
    try:
        ga.deconnecter(compte)
    except ga.GoogleAgendaErreur as exc:
        flash(f"Déconnexion partielle : {exc}", "warning")
        return redirect(url_for("main.mon_agenda"))
    flash("Compte Google déconnecté : le calendrier dédié a été retiré et l'accès révoqué.", "success")
    return redirect(url_for("main.mon_agenda"))
