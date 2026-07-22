"""Routes du module Veille financements.

- ``/veille-financements/`` : le tableau de veille (filtres, statuts).
- ``/veille-financements/rafraichir`` : collecte immédiate (synchrone, pour
  voir le résultat tout de suite ; la collecte automatique tourne, elle,
  tous les 3 jours en arrière-plan).
- ``/veille-financements/sources`` : gestion des sources surveillées.
"""

from __future__ import annotations

from datetime import date, timedelta

from flask import flash, redirect, render_template, request, url_for
from flask_login import login_required

from app.extensions import db
from app.models import VeilleOpportunite, VeilleSource
from app.rbac import require_perm
from app.services.veille_financements import (
    INTERVALLE_JOURS,
    TYPES_SOURCE_LABELS,
    rafraichir_toutes_sources,
    seed_sources_par_defaut,
)

from . import bp

TRIS = {
    "pertinence": "Pertinence (score)",
    "cloture": "Date de clôture",
    "recent": "Dernières trouvailles",
}


@bp.route("/")
@login_required
@require_perm("veille:view")
def index():
    seed_sources_par_defaut()

    q = (request.args.get("q") or "").strip()
    statut = (request.args.get("statut") or "").strip()
    source_id = request.args.get("source", type=int)
    tri = request.args.get("tri") or "pertinence"
    if tri not in TRIS:
        tri = "pertinence"
    # Par défaut : masquer les écartés et les appels déjà clôturés.
    afficher_tout = request.args.get("tout") == "1"

    requete = VeilleOpportunite.query
    if q:
        motif = f"%{q}%"
        requete = requete.filter(
            db.or_(
                VeilleOpportunite.titre.ilike(motif),
                VeilleOpportunite.financeur.ilike(motif),
                VeilleOpportunite.description.ilike(motif),
                VeilleOpportunite.mots_cles.ilike(motif),
            )
        )
    if statut:
        requete = requete.filter(VeilleOpportunite.statut == statut)
    elif not afficher_tout:
        requete = requete.filter(VeilleOpportunite.statut != "ecarte")
    if not afficher_tout:
        requete = requete.filter(
            db.or_(
                VeilleOpportunite.date_cloture.is_(None),
                VeilleOpportunite.date_cloture >= date.today(),
            )
        )
    if source_id:
        requete = requete.filter(VeilleOpportunite.source_id == source_id)

    if tri == "cloture":
        requete = requete.order_by(
            VeilleOpportunite.date_cloture.is_(None),
            VeilleOpportunite.date_cloture.asc(),
            VeilleOpportunite.score.desc(),
        )
    elif tri == "recent":
        requete = requete.order_by(VeilleOpportunite.cree_le.desc())
    else:
        requete = requete.order_by(
            VeilleOpportunite.score.desc(), VeilleOpportunite.date_cloture.asc()
        )

    opportunites = requete.limit(300).all()

    bientot = date.today() + timedelta(days=15)
    stats = {
        "nouveaux": VeilleOpportunite.query.filter_by(statut="nouveau").count(),
        "a_etudier": VeilleOpportunite.query.filter_by(statut="a_etudier").count(),
        "cloture_proche": VeilleOpportunite.query.filter(
            VeilleOpportunite.date_cloture.isnot(None),
            VeilleOpportunite.date_cloture >= date.today(),
            VeilleOpportunite.date_cloture <= bientot,
            VeilleOpportunite.statut.notin_(["ecarte", "retenu"]),
        ).count(),
    }

    sources = VeilleSource.query.order_by(VeilleSource.nom).all()
    derniere_verif = max(
        (s.derniere_verification for s in sources if s.derniere_verification), default=None
    )

    return render_template(
        "veille/index.html",
        opportunites=opportunites,
        stats=stats,
        sources=sources,
        derniere_verif=derniere_verif,
        intervalle_jours=INTERVALLE_JOURS,
        statuts=VeilleOpportunite.STATUTS,
        types=VeilleOpportunite.TYPES,
        tris=TRIS,
        q=q,
        statut=statut,
        source_id=source_id,
        tri=tri,
        afficher_tout=afficher_tout,
    )


@bp.route("/rafraichir", methods=["POST"])
@login_required
@require_perm("veille:view")
def rafraichir():
    resume = rafraichir_toutes_sources()
    message = (
        f"Veille mise à jour : {resume['nouveaux']} nouvelle(s) opportunité(s), "
        f"{resume['sources_ok']} source(s) lue(s)"
    )
    if resume["sources_erreur"]:
        message += f", {resume['sources_erreur']} source(s) en erreur (voir la page Sources)"
    flash(message + ".", "success" if not resume["sources_erreur"] else "warning")
    return redirect(url_for("veille.index"))


@bp.route("/opportunite/<int:opp_id>/statut", methods=["POST"])
@login_required
@require_perm("veille:view")
def changer_statut(opp_id: int):
    opp = db.session.get(VeilleOpportunite, opp_id)
    if opp is None:
        flash("Opportunité introuvable.", "danger")
        return redirect(url_for("veille.index"))
    statut = (request.form.get("statut") or "").strip()
    if statut in VeilleOpportunite.STATUTS:
        opp.statut = statut
        commentaire = (request.form.get("commentaire") or "").strip()
        if commentaire:
            opp.commentaire = commentaire[:500]
        db.session.commit()
    retour = request.form.get("retour") or url_for("veille.index")
    return redirect(retour)


# ---------------------------------------------------------------------------
# Gestion des sources
# ---------------------------------------------------------------------------

@bp.route("/sources")
@login_required
@require_perm("veille:view")
def sources():
    seed_sources_par_defaut()
    liste = VeilleSource.query.order_by(VeilleSource.nom).all()
    return render_template(
        "veille/sources.html",
        sources=liste,
        types_source=TYPES_SOURCE_LABELS,
    )


@bp.route("/sources/ajouter", methods=["POST"])
@login_required
@require_perm("veille:edit")
def source_ajouter():
    nom = (request.form.get("nom") or "").strip()
    url_source = (request.form.get("url") or "").strip()
    type_source = (request.form.get("type_source") or "rss").strip()
    if not nom or not url_source or type_source not in TYPES_SOURCE_LABELS:
        flash("Nom, adresse et type de source sont obligatoires.", "danger")
        return redirect(url_for("veille.sources"))
    db.session.add(VeilleSource(nom=nom[:160], url=url_source[:500], type_source=type_source))
    db.session.commit()
    flash("Source ajoutée. Elle sera lue au prochain rafraîchissement.", "success")
    return redirect(url_for("veille.sources"))


@bp.route("/sources/<int:source_id>/modifier", methods=["POST"])
@login_required
@require_perm("veille:edit")
def source_modifier(source_id: int):
    source = db.session.get(VeilleSource, source_id)
    if source is None:
        flash("Source introuvable.", "danger")
        return redirect(url_for("veille.sources"))

    action = request.form.get("action") or "enregistrer"
    if action == "basculer":
        source.actif = not source.actif
        db.session.commit()
        flash(("Source activée." if source.actif else "Source mise en pause."), "success")
    elif action == "supprimer":
        VeilleOpportunite.query.filter_by(source_id=source.id).update({"source_id": None})
        db.session.delete(source)
        db.session.commit()
        flash("Source supprimée (les opportunités déjà trouvées sont conservées).", "success")
    else:
        nom = (request.form.get("nom") or "").strip()
        url_source = (request.form.get("url") or "").strip()
        if nom:
            source.nom = nom[:160]
        if url_source:
            source.url = url_source[:500]
        api_cle = (request.form.get("api_cle") or "").strip()
        if api_cle:
            source.api_cle = api_cle[:255]
        db.session.commit()
        flash("Source enregistrée.", "success")
    return redirect(url_for("veille.sources"))
