"""Adhésions, participation & foyers — actions depuis la fiche participant.

Chaque action reste bornée par les droits déjà en vigueur sur la fiche
(_can_read_participant / _can_edit_participant) EN PLUS de la permission
cotisations:view / cotisations:edit — on n'ouvre jamais une porte que le
RBAC existant fermait déjà sur ce participant.
"""
from __future__ import annotations

from datetime import date, datetime

from flask import abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.models import Cotisation, Participant, SuiviRappel, TYPES_TARIF, MODES_PAIEMENT
from app.rbac import require_perm
from app.services.cotisations import (
    annee_scolaire_courante,
    cotisation_existante,
    cotisations_du_participant,
    detacher_du_foyer,
    foyer_membres_autres,
    libelle_annee_scolaire,
    rapprocher_foyer,
    tarif_en_vigueur,
)

from app.participants.routes import bp, _can_edit_participant, _can_read_participant, _can_see_participant


def cotisations_contexte(participant: Participant) -> dict:
    """Le contexte à injecter dans la fiche participant (synthese.html)."""
    annee = annee_scolaire_courante()
    q_famille = (request.args.get("q_famille") or "").strip()
    resultats_famille = []
    if q_famille:
        deja = {m.id for m in foyer_membres_autres(participant)} | {participant.id}
        motif = f"%{q_famille}%"
        trouves = (
            Participant.query
            .filter(db.or_(Participant.nom.ilike(motif), Participant.prenom.ilike(motif)))
            .order_by(Participant.nom.asc(), Participant.prenom.asc())
            .limit(20)
            .all()
        )
        resultats_famille = [p for p in trouves if p.id not in deja]

    toutes = cotisations_du_participant(participant)
    courante = [c for c in toutes if c.annee_scolaire == annee]
    historique_par_annee: dict[int, list] = {}
    for c in toutes:
        if c.annee_scolaire != annee:
            historique_par_annee.setdefault(c.annee_scolaire, []).append(c)

    return {
        "annee_scolaire_courante": annee,
        "libelle_annee_scolaire_courante": libelle_annee_scolaire(annee),
        "cotisations_courante": courante,
        "cotisations_historique": sorted(historique_par_annee.items(), key=lambda kv: -kv[0]),
        "adhesion_courante": next((c for c in courante if c.type_cotisation in ("adhesion_individuelle", "adhesion_familiale")), None),
        "participation_courante": next((c for c in courante if c.type_cotisation == "participation"), None),
        "tarifs_en_vigueur": {
            t: tarif_en_vigueur(annee, t) for t in TYPES_TARIF
        },
        "foyer_membres": foyer_membres_autres(participant),
        "q_famille": q_famille,
        "resultats_famille": resultats_famille,
        "modes_paiement": MODES_PAIEMENT,
    }


def _get_participant_autorise(participant_id: int, *, edition: bool) -> Participant:
    p = db.get_or_404(Participant, participant_id)
    if not _can_read_participant(p) or not _can_see_participant(p):
        abort(403)
    if edition and not _can_edit_participant(p):
        abort(403)
    return p


def _parse_date(raw: str | None, defaut: date) -> date:
    try:
        return datetime.strptime((raw or "").strip(), "%Y-%m-%d").date()
    except Exception:
        return defaut


@bp.post("/<int:participant_id>/cotisation/creer")
@login_required
@require_perm("cotisations:edit")
def cotisation_creer(participant_id: int):
    participant = _get_participant_autorise(participant_id, edition=True)
    type_cotisation = (request.form.get("type_cotisation") or "").strip()
    if type_cotisation not in TYPES_TARIF:
        flash("Type de cotisation invalide.", "danger")
        return redirect(url_for("participants.synthese_participant", participant_id=participant.id))

    annee = annee_scolaire_courante()
    date_ref = date.today()

    if type_cotisation == "adhesion_familiale":
        if not participant.foyer_id:
            flash("Rapproche d'abord cette personne d'un foyer pour une adhésion familiale (ou choisis l'adhésion individuelle).", "warning")
            return redirect(url_for("participants.synthese_participant", participant_id=participant.id))
        if cotisation_existante(annee_scolaire=annee, type_cotisation=type_cotisation, foyer_id=participant.foyer_id):
            flash(f"Une adhésion familiale {libelle_annee_scolaire(annee)} existe déjà pour ce foyer.", "info")
            return redirect(url_for("participants.synthese_participant", participant_id=participant.id))
        tarif = tarif_en_vigueur(annee, type_cotisation, date_ref)
        c = Cotisation(
            annee_scolaire=annee, type_cotisation=type_cotisation,
            foyer_id=participant.foyer_id,
            montant_du=tarif.montant if tarif else 0.0,
            date_reference=date_ref,
            created_by_user_id=getattr(current_user, "id", None),
        )
    else:
        if cotisation_existante(annee_scolaire=annee, type_cotisation=type_cotisation, participant_id=participant.id):
            flash(f"Une cotisation {libelle_annee_scolaire(annee)} de ce type existe déjà pour cette personne.", "info")
            return redirect(url_for("participants.synthese_participant", participant_id=participant.id))
        tarif = tarif_en_vigueur(annee, type_cotisation, date_ref)
        c = Cotisation(
            annee_scolaire=annee, type_cotisation=type_cotisation,
            participant_id=participant.id,
            montant_du=tarif.montant if tarif else 0.0,
            date_reference=date_ref,
            created_by_user_id=getattr(current_user, "id", None),
        )
    if tarif is None:
        flash("Aucun tarif défini pour cette année scolaire : le montant dû est à 0 €, à corriger ci-dessous (ou définis d'abord le barème).", "warning")
    db.session.add(c)
    db.session.commit()
    flash("Cotisation créée.", "success")
    return redirect(url_for("participants.synthese_participant", participant_id=participant.id))


def _get_cotisation_liee(participant: Participant, cotisation_id: int) -> Cotisation:
    c = db.get_or_404(Cotisation, cotisation_id)
    autorise = c.participant_id == participant.id or (
        participant.foyer_id and c.foyer_id == participant.foyer_id
    )
    if not autorise:
        abort(404)
    return c


@bp.post("/<int:participant_id>/cotisation/<int:cotisation_id>/versement")
@login_required
@require_perm("cotisations:edit")
def cotisation_versement(participant_id: int, cotisation_id: int):
    participant = _get_participant_autorise(participant_id, edition=True)
    cotisation = _get_cotisation_liee(participant, cotisation_id)

    try:
        montant = round(float(str(request.form.get("montant") or "0").replace(",", ".")), 2)
    except Exception:
        montant = 0.0
    if montant <= 0:
        flash("Le montant du règlement doit être supérieur à 0 €.", "danger")
        return redirect(url_for("participants.synthese_participant", participant_id=participant.id))

    mode = (request.form.get("mode") or "especes").strip()
    if mode not in MODES_PAIEMENT:
        mode = "especes"

    from app.models import Paiement
    db.session.add(Paiement(
        cotisation_id=cotisation.id,
        montant=montant,
        date_paiement=_parse_date(request.form.get("date_paiement"), date.today()),
        mode=mode,
        commentaire=(request.form.get("commentaire") or "").strip() or None,
        created_by_user_id=getattr(current_user, "id", None),
    ))
    db.session.commit()
    flash(f"Règlement de {montant:.2f} € enregistré.", "success")
    return redirect(url_for("participants.synthese_participant", participant_id=participant.id))


@bp.post("/<int:participant_id>/cotisation/<int:cotisation_id>/montant")
@login_required
@require_perm("cotisations:edit")
def cotisation_montant_update(participant_id: int, cotisation_id: int):
    """Ajuste le montant dû au cas par cas (tarif réduit négocié…)."""
    participant = _get_participant_autorise(participant_id, edition=True)
    cotisation = _get_cotisation_liee(participant, cotisation_id)
    try:
        montant = round(float(str(request.form.get("montant_du") or "0").replace(",", ".")), 2)
    except Exception:
        flash("Montant invalide.", "danger")
        return redirect(url_for("participants.synthese_participant", participant_id=participant.id))
    if montant < 0:
        flash("Le montant dû ne peut pas être négatif.", "danger")
        return redirect(url_for("participants.synthese_participant", participant_id=participant.id))
    cotisation.montant_du = montant
    db.session.commit()
    flash("Montant dû mis à jour.", "success")
    return redirect(url_for("participants.synthese_participant", participant_id=participant.id))


@bp.post("/<int:participant_id>/cotisation/<int:cotisation_id>/supprimer")
@login_required
@require_perm("cotisations:edit")
def cotisation_supprimer(participant_id: int, cotisation_id: int):
    """Supprime une cotisation créée par erreur (aucun règlement enregistré)."""
    participant = _get_participant_autorise(participant_id, edition=True)
    cotisation = _get_cotisation_liee(participant, cotisation_id)
    if cotisation.montant_regle > 0:
        flash("Impossible de supprimer une cotisation qui a déjà des règlements enregistrés.", "danger")
        return redirect(url_for("participants.synthese_participant", participant_id=participant.id))
    db.session.delete(cotisation)
    db.session.commit()
    flash("Cotisation supprimée.", "info")
    return redirect(url_for("participants.synthese_participant", participant_id=participant.id))


@bp.post("/<int:participant_id>/cotisation/<int:cotisation_id>/relancer")
@login_required
@require_perm("cotisations:edit")
def cotisation_relancer(participant_id: int, cotisation_id: int):
    participant = _get_participant_autorise(participant_id, edition=True)
    cotisation = _get_cotisation_liee(participant, cotisation_id)
    if cotisation.solde:
        flash("Cette cotisation est déjà soldée.", "info")
        return redirect(url_for("participants.synthese_participant", participant_id=participant.id))

    lien = url_for("participants.synthese_participant", participant_id=participant.id)
    deja = SuiviRappel.query.filter(
        SuiviRappel.statut == "ouvert",
        SuiviRappel.categorie == "cotisation",
        SuiviRappel.lien_url == lien,
        SuiviRappel.titre.contains(f"#{cotisation.id}"),
    ).first()
    if deja is not None:
        flash("Une relance est déjà en cours pour cette cotisation.", "info")
        return redirect(lien)

    from datetime import timedelta
    db.session.add(SuiviRappel(
        titre=f"Reste à payer : {cotisation.type_label} {cotisation.libelle_annee} — {participant.nom} {participant.prenom} (#{cotisation.id})",
        description=f"Reste dû : {cotisation.reste_du:.2f} € sur {cotisation.montant_du:.2f} €.",
        categorie="cotisation",
        priorite="warn",
        secteur=participant.created_secteur,
        echeance=date.today() + timedelta(days=14),
        lien_url=lien,
        created_by_user_id=getattr(current_user, "id", None),
    ))
    db.session.commit()
    flash("Relance posée : elle apparaît dans « À traiter ».", "success")
    return redirect(lien)


@bp.route("/<int:participant_id>/cotisation/<int:cotisation_id>/recu")
@login_required
@require_perm("cotisations:view")
def cotisation_recu(participant_id: int, cotisation_id: int):
    """Reçu / attestation de règlement imprimable — preuve pour la famille
    (CAF, comité d'entreprise…). Reflète l'état actuel (dû, réglé, reste)."""
    participant = _get_participant_autorise(participant_id, edition=False)
    cotisation = _get_cotisation_liee(participant, cotisation_id)
    from app.services.dons import montant_en_lettres
    return render_template(
        "participants/cotisation_recu.html",
        participant=participant,
        cotisation=cotisation,
        organisme_nom=current_app.config.get("ORGANIZATION_NAME") or current_app.config.get("APP_NAME") or "",
        montant_regle_lettres=montant_en_lettres(cotisation.montant_regle),
        today=date.today(),
    )


@bp.post("/<int:participant_id>/foyer/rapprocher")
@login_required
@require_perm("cotisations:edit")
def foyer_rapprocher(participant_id: int):
    participant = _get_participant_autorise(participant_id, edition=True)
    try:
        autre_id = int(request.form.get("autre_participant_id") or 0)
    except Exception:
        autre_id = 0
    autre = db.session.get(Participant, autre_id) if autre_id else None
    if autre is None or not _can_read_participant(autre):
        flash("Personne introuvable.", "danger")
        return redirect(url_for("participants.synthese_participant", participant_id=participant.id))
    ok, message = rapprocher_foyer(participant, autre)
    flash(message, "success" if ok else "danger")
    return redirect(url_for("participants.synthese_participant", participant_id=participant.id))


@bp.post("/<int:participant_id>/foyer/detacher")
@login_required
@require_perm("cotisations:edit")
def foyer_detacher(participant_id: int):
    """Détache un membre du foyer. Par défaut la fiche consultée ; on peut
    aussi détacher un autre membre directement depuis cette même page."""
    participant = _get_participant_autorise(participant_id, edition=True)
    try:
        membre_id = int(request.form.get("membre_id") or participant.id)
    except Exception:
        membre_id = participant.id
    cible = _get_participant_autorise(membre_id, edition=True) if membre_id != participant.id else participant
    if cible.foyer_id != participant.foyer_id:
        flash("Cette personne n'appartient pas au même foyer.", "danger")
        return redirect(url_for("participants.synthese_participant", participant_id=participant.id))
    detacher_du_foyer(cible)
    flash(f"{cible.nom} {cible.prenom} n'appartient plus à ce foyer.", "info")
    return redirect(url_for("participants.synthese_participant", participant_id=participant.id))
