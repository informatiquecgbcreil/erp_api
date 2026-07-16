"""Module Transitions — tableau de bord, étiquetage, défis, mesures, export.

Portée secteur : un rôle sans ``scope:all_secteurs`` est borné à son secteur
assigné (mêmes règles que le reste de l'application). L'étiquetage d'un
atelier suit la règle du module activité (_atelier_est_accessible).
"""
from __future__ import annotations

from datetime import date, datetime
from io import BytesIO

from flask import flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.models import (
    STATUTS_DEFI,
    AtelierActivite,
    DefiTransition,
    ObjectifSectoriel,
    Participant,
    TransitionMesure,
    TransitionThematique,
)
from app.rbac import can, require_perm
from app.services.transitions import (
    annees_disponibles,
    construire_export,
    etiqueter_atelier,
    seed_thematiques,
    tableau_de_bord,
    thematiques_actives,
)

from . import bp


def _annee_demandee() -> int:
    try:
        annee = int((request.args.get("annee") or "").strip())
    except (TypeError, ValueError):
        annee = date.today().year
    return max(2000, min(2100, annee))


def _secteur_effectif() -> str | None:
    """Filtre secteur : libre pour la portée globale, forcé sinon."""
    if can("scope:all_secteurs"):
        return (request.args.get("secteur") or "").strip() or None
    return (getattr(current_user, "secteur_assigne", None) or "").strip() or None


def _parse_date(brut: str | None, defaut: date) -> date:
    try:
        return datetime.strptime((brut or "").strip(), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return defaut


@bp.route("/")
@login_required
@require_perm("transitions:view")
def dashboard():
    annee = _annee_demandee()
    secteur = _secteur_effectif()
    data = tableau_de_bord(annee, secteur)

    q_defi = (request.args.get("q_defi") or "").strip()
    resultats_defi = []
    if q_defi and len(q_defi) >= 2 and can("transitions:edit"):
        motif = f"%{q_defi}%"
        resultats_defi = (
            Participant.query
            .filter(db.or_(Participant.nom.ilike(motif), Participant.prenom.ilike(motif)))
            .order_by(Participant.nom.asc(), Participant.prenom.asc())
            .limit(15)
            .all()
        )

    from app.secteurs import get_secteur_labels

    return render_template(
        "transitions/dashboard.html",
        data=data,
        annee=annee,
        annees=annees_disponibles(),
        secteur=secteur,
        secteurs=get_secteur_labels(active_only=True),
        portee_globale=can("scope:all_secteurs"),
        thematiques_referentiel=TransitionThematique.query.order_by(
            TransitionThematique.ordre.asc(), TransitionThematique.libelle.asc()
        ).all(),
        objectifs_actifs=ObjectifSectoriel.query.filter_by(actif=True).order_by(
            ObjectifSectoriel.secteur.asc(), ObjectifSectoriel.ordre.asc()
        ).all(),
        q_defi=q_defi,
        resultats_defi=resultats_defi,
        today=date.today(),
    )


@bp.route("/ateliers", methods=["GET", "POST"])
@login_required
@require_perm("transitions:view")
def ateliers():
    """Étiquetage : quelles thématiques / quels OS pour chaque atelier."""
    from app.activite.helpers import _atelier_est_accessible

    seed_thematiques()

    if request.method == "POST":
        require_perm("transitions:edit")(lambda: None)()
        atelier = db.get_or_404(AtelierActivite, request.form.get("atelier_id", type=int))
        if atelier.is_deleted or not _atelier_est_accessible(atelier):
            flash("Atelier inaccessible.", "danger")
            return redirect(url_for("transitions.ateliers"))
        thematique_ids = [int(x) for x in request.form.getlist("thematique_ids") if str(x).isdigit()]
        objectif_ids = [int(x) for x in request.form.getlist("objectif_ids") if str(x).isdigit()]
        etiqueter_atelier(atelier, thematique_ids, objectif_ids)
        if thematique_ids:
            flash(f"« {atelier.nom} » compte maintenant dans le suivi transitions ✅", "success")
        else:
            flash(f"« {atelier.nom} » ne compte plus dans le suivi transitions.", "info")
        return redirect(url_for("transitions.ateliers", q=request.form.get("q") or None))

    secteur = _secteur_effectif()
    q = (request.args.get("q") or "").strip()
    ateliers_q = AtelierActivite.query.filter(AtelierActivite.is_deleted.is_(False))
    if secteur:
        ateliers_q = ateliers_q.filter(db.or_(
            AtelierActivite.secteur == secteur,
            AtelierActivite.est_intersecteur.is_(True),
        ))
    if q:
        ateliers_q = ateliers_q.filter(AtelierActivite.nom.ilike(f"%{q}%"))
    liste = ateliers_q.order_by(AtelierActivite.secteur.asc(), AtelierActivite.nom.asc()).limit(200).all()

    objectifs = ObjectifSectoriel.query.filter_by(actif=True).order_by(
        ObjectifSectoriel.secteur.asc(), ObjectifSectoriel.ordre.asc()
    ).all()

    return render_template(
        "transitions/ateliers.html",
        ateliers=liste,
        thematiques=thematiques_actives(),
        objectifs=objectifs,
        q=q,
        secteur=secteur,
    )


# ---------------------------------------------------------------------------
# Référentiel des thématiques
# ---------------------------------------------------------------------------

@bp.route("/thematiques", methods=["POST"])
@login_required
@require_perm("transitions:edit")
def thematiques_gerer():
    action = (request.form.get("action") or "").strip()

    if action == "ajouter":
        libelle = (request.form.get("libelle") or "").strip()
        if not libelle:
            flash("Libellé obligatoire pour une nouvelle thématique.", "danger")
            return redirect(url_for("transitions.dashboard"))
        code = "".join(c if c.isalnum() else "_" for c in libelle.lower())[:40] or "thematique"
        base_code, i = code, 2
        while TransitionThematique.query.filter_by(code=code).first() is not None:
            code = f"{base_code}_{i}"[:40]
            i += 1
        rang_max = db.session.query(db.func.coalesce(db.func.max(TransitionThematique.ordre), 0)).scalar()
        db.session.add(TransitionThematique(code=code, libelle=libelle, ordre=int(rang_max) + 1))
        db.session.commit()
        flash(f"Thématique « {libelle} » ajoutée ✅", "success")

    elif action in ("renommer", "basculer"):
        them = db.get_or_404(TransitionThematique, request.form.get("thematique_id", type=int))
        if action == "renommer":
            libelle = (request.form.get("libelle") or "").strip()
            if libelle:
                them.libelle = libelle
                db.session.commit()
                flash("Thématique renommée ✅", "success")
        else:
            them.actif = not them.actif
            db.session.commit()
            flash(
                f"Thématique « {them.libelle} » {'réactivée' if them.actif else 'désactivée'} "
                "(l'historique est conservé).",
                "info",
            )
    return redirect(url_for("transitions.dashboard"))


# ---------------------------------------------------------------------------
# Défis des habitants
# ---------------------------------------------------------------------------

@bp.route("/defis/creer", methods=["POST"])
@login_required
@require_perm("transitions:edit")
def defi_creer():
    titre = (request.form.get("titre") or "").strip()
    thematique = db.session.get(TransitionThematique, request.form.get("thematique_id", type=int) or 0)
    if not titre or thematique is None:
        flash("Un défi a besoin d'un titre et d'une thématique.", "danger")
        return redirect(url_for("transitions.dashboard"))

    participant_id = request.form.get("participant_id", type=int)
    participant = db.session.get(Participant, participant_id) if participant_id else None
    objectif_id = request.form.get("objectif_id", type=int) or None

    defi = DefiTransition(
        titre=titre,
        thematique_id=thematique.id,
        objectif_id=objectif_id,
        participant_id=participant.id if participant else None,
        foyer_id=participant.foyer_id if (participant and request.form.get("porte_par_foyer")) else None,
        date_engagement=_parse_date(request.form.get("date_engagement"), date.today()),
        commentaire=(request.form.get("commentaire") or "").strip() or None,
        created_by_user_id=getattr(current_user, "id", None),
    )
    # Un défi porté par le foyer n'est pas en plus porté par la personne.
    if defi.foyer_id:
        defi.participant_id = None
    db.session.add(defi)
    db.session.commit()
    flash(f"Défi « {titre} » engagé ✅", "success")
    return redirect(url_for("transitions.dashboard", annee=defi.date_engagement.year))


@bp.route("/defis/<int:defi_id>/statut", methods=["POST"])
@login_required
@require_perm("transitions:edit")
def defi_statut(defi_id: int):
    defi = db.get_or_404(DefiTransition, defi_id)
    statut = (request.form.get("statut") or "").strip()
    if statut not in STATUTS_DEFI:
        flash("Statut de défi invalide.", "danger")
        return redirect(url_for("transitions.dashboard"))
    defi.statut = statut
    defi.date_realisation = date.today() if statut == "realise" else None
    db.session.commit()
    if statut == "realise":
        flash(f"Défi « {defi.titre} » réalisé 🎉", "success")
    else:
        flash("Statut du défi mis à jour.", "info")
    return redirect(url_for("transitions.dashboard", annee=defi.date_engagement.year))


# ---------------------------------------------------------------------------
# Mesures chiffrées
# ---------------------------------------------------------------------------

@bp.route("/mesures/creer", methods=["POST"])
@login_required
@require_perm("transitions:edit")
def mesure_creer():
    from app.activite.helpers import _atelier_est_accessible

    atelier = db.session.get(AtelierActivite, request.form.get("atelier_id", type=int) or 0)
    libelle = (request.form.get("libelle") or "").strip()
    try:
        valeur = round(float(str(request.form.get("valeur") or "").replace(",", ".")), 2)
    except (TypeError, ValueError):
        valeur = None

    if atelier is None or atelier.is_deleted or not _atelier_est_accessible(atelier):
        flash("Choisissez un atelier transitions valide pour la mesure.", "danger")
        return redirect(url_for("transitions.dashboard"))
    if not libelle or valeur is None:
        flash("Une mesure a besoin d'un libellé et d'une valeur chiffrée.", "danger")
        return redirect(url_for("transitions.dashboard"))

    # Thématique déduite de l'atelier (la première), sauf choix explicite.
    thematique_id = request.form.get("thematique_id", type=int) or None
    if not thematique_id and atelier.transition_thematiques:
        thematique_id = atelier.transition_thematiques[0].id

    mesure = TransitionMesure(
        atelier_id=atelier.id,
        thematique_id=thematique_id,
        libelle=libelle,
        valeur=valeur,
        unite=(request.form.get("unite") or "").strip() or None,
        date_mesure=_parse_date(request.form.get("date_mesure"), date.today()),
        commentaire=(request.form.get("commentaire") or "").strip() or None,
        created_by_user_id=getattr(current_user, "id", None),
    )
    db.session.add(mesure)
    db.session.commit()
    flash(f"Mesure « {libelle} : {valeur:g} {mesure.unite or ''} » enregistrée ✅", "success")
    return redirect(url_for("transitions.dashboard", annee=mesure.date_mesure.year))


@bp.route("/mesures/<int:mesure_id>/supprimer", methods=["POST"])
@login_required
@require_perm("transitions:edit")
def mesure_supprimer(mesure_id: int):
    mesure = db.get_or_404(TransitionMesure, mesure_id)
    annee = mesure.date_mesure.year if mesure.date_mesure else date.today().year
    db.session.delete(mesure)
    db.session.commit()
    flash("Mesure supprimée.", "info")
    return redirect(url_for("transitions.dashboard", annee=annee))


# ---------------------------------------------------------------------------
# Export bilan annuel
# ---------------------------------------------------------------------------

@bp.route("/export.xlsx")
@login_required
@require_perm("transitions:view")
def export_xlsx():
    annee = _annee_demandee()
    secteur = _secteur_effectif()
    wb = construire_export(annee, secteur)
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    suffixe = f"_{secteur}" if secteur else ""
    return send_file(
        buf,
        as_attachment=True,
        download_name=f"bilan_transitions_{annee}{suffixe}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
