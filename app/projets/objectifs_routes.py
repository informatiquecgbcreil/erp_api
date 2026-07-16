"""Routes : référentiel des objectifs sectoriels + vue de consolidation."""
from datetime import date

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import login_required

from app.extensions import db
from app.models import ObjectifSectoriel
from app.projets.common import bp
from app.projets.objectifs import (
    objectifs_du_secteur,
    peut_gerer_secteur,
    secteurs_gerables,
    synthese_secteur,
)


def _secteur_demande() -> str:
    secteurs = secteurs_gerables()
    if not secteurs:
        abort(403)
    demande = (request.values.get("secteur") or "").strip()
    return demande if demande in secteurs else secteurs[0]


@bp.route("/projets/objectifs-sectoriels", methods=["GET", "POST"])
@login_required
def objectifs_referentiel():
    if request.method == "POST":
        # En écriture, on honore le secteur DEMANDÉ et on refuse explicitement
        # toute tentative hors périmètre (pas d'assainissement silencieux).
        secteur = (request.form.get("secteur") or "").strip()
        if not peut_gerer_secteur(secteur):
            abort(403)
        action = request.form.get("action") or ""

        if action == "ajouter":
            code = (request.form.get("code") or "").strip()
            libelle = (request.form.get("libelle") or "").strip()
            if not code or not libelle:
                flash("Code et libellé sont obligatoires.", "danger")
            else:
                db.session.add(ObjectifSectoriel(
                    secteur=secteur, code=code, libelle=libelle,
                    axe=(request.form.get("axe") or "").strip() or None,
                    ordre=request.form.get("ordre", type=int) or 0,
                ))
                db.session.commit()
                flash("Objectif ajouté.", "success")

        elif action == "modifier":
            obj = db.get_or_404(ObjectifSectoriel, request.form.get("id", type=int))
            if obj.secteur != secteur:
                abort(403)
            obj.code = (request.form.get("code") or obj.code).strip()
            obj.libelle = (request.form.get("libelle") or obj.libelle).strip()
            obj.axe = (request.form.get("axe") or "").strip() or None
            obj.ordre = request.form.get("ordre", type=int) or 0
            obj.actif = request.form.get("actif") in {"1", "on", "true"}
            db.session.commit()
            flash("Objectif mis à jour.", "success")

        elif action == "supprimer":
            obj = db.get_or_404(ObjectifSectoriel, request.form.get("id", type=int))
            if obj.secteur != secteur:
                abort(403)
            db.session.delete(obj)
            db.session.commit()
            flash("Objectif supprimé.", "success")

        return redirect(url_for("projets.objectifs_referentiel", secteur=secteur))

    secteur = _secteur_demande()
    return render_template(
        "projets/objectifs_referentiel.html",
        secteur=secteur,
        secteurs=secteurs_gerables(),
        objectifs=objectifs_du_secteur(secteur, actif_only=False),
        peut_gerer=peut_gerer_secteur(secteur),
    )


@bp.route("/projets/objectifs-sectoriels/synthese")
@login_required
def objectifs_synthese():
    secteur = _secteur_demande()
    annee = request.args.get("annee", type=int) or (date.today().year - 1)
    return render_template(
        "projets/objectifs_synthese.html",
        secteur=secteur,
        secteurs=secteurs_gerables(),
        annee=annee,
        synthese=synthese_secteur(secteur, annee),
    )
