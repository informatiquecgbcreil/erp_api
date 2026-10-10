"""Heures supplémentaires et récupérations.

Circuit :
salarié — déclare ses heures sup (crédit), que l'assistant·e et la direction
voient aussitôt et peuvent corriger dans un sens ou dans l'autre
(justification obligatoire) ; demande une récupération (brouillon), la signe
et l'envoie → « soumise » ;
assistant·e de direction — transmet en signant → « transmise », ou refuse en
signant avec une justification → « refusee » (``refusee_par_relais``) ;
direction — accepte, ou refuse avec une justification, en signant, y compris
directement depuis « soumise » quand l'assistant·e est absent·e ;
assistant·e — prend connaissance de la décision (``notifiee_le``, sans
signature : le salarié a déjà reçu l'e-mail).

Le salarié est prévenu par e-mail à chaque étape (transmise, refusée,
décidée, heures corrigées) ; la justification n'est demandée qu'en cas de
refus ou de correction.

Le solde peut devenir négatif (récupération prise par avance). Seules les
récupérations ACCEPTÉES décomptent.
"""
from __future__ import annotations

from datetime import date, timedelta

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.models import DemandeRecuperation, HeureSupplementaire, Salarie
from app.rbac import can, require_perm
from app.services import courriels_rh
from app.services import espace_salarie as es
from app.services.audit import journaliser
from app.utils.dates import utcnow

from . import bp
from .commun import erreur, ma_fiche_ou_explication, retour


def _url_mes():
    return url_for("salaries.mes_recuperations")


def _url_equipe():
    return url_for("salaries.equipe_recuperations")


def _equipe() -> bool:
    return can("recup:transmettre") or can("recup:decider")


# ---------------------------------------------------------------------------
# Côté salarié
# ---------------------------------------------------------------------------

@bp.route("/recuperations")
@login_required
@require_perm("salarie:espace")
@ma_fiche_ou_explication
def mes_recuperations(salarie):
    heures = (HeureSupplementaire.query.filter_by(salarie_id=salarie.id)
              .order_by(HeureSupplementaire.date_travail.desc(), HeureSupplementaire.id.desc()).all())
    demandes = (DemandeRecuperation.query.filter_by(salarie_id=salarie.id)
                .order_by(DemandeRecuperation.created_at.desc(), DemandeRecuperation.id.desc()).all())
    jour = es.parse_date(request.args.get("jour")) or date.today()
    return render_template(
        "salaries/mes_recuperations.html",
        salarie=salarie,
        resume=es.resume_recup(salarie.id),
        heures=heures,
        en_cours=[d for d in demandes if d.statut in es.EN_COURS],
        terminees=[d for d in demandes if d.statut not in es.EN_COURS],
        jour=jour,
        suggestions=es.evenements_du_jour(current_user, jour),
        es=es,
    )


@bp.route("/recuperations/heures", methods=["POST"])
@login_required
@require_perm("salarie:espace")
@ma_fiche_ou_explication
def declarer_heures(salarie):
    jour = es.parse_date(request.form.get("date_travail"))
    if jour is None:
        return erreur("Indiquez la date à laquelle vous avez fait ces heures.", _url_mes())
    if jour > date.today() + timedelta(days=1):
        return erreur("On ne déclare pas d'heures supplémentaires à l'avance.", _url_mes())
    try:
        minutes = es.parse_heures(request.form.get("duree"))
        if minutes <= 0:
            raise ValueError("La durée doit être positive.")
        session_id, creneau_id = es.lien_agenda_valide(current_user, jour, request.form.get("lien"))
    except ValueError as exc:
        return erreur(str(exc), _url_mes())
    motif = (request.form.get("motif") or "").strip()
    if not motif and not (session_id or creneau_id):
        return erreur("Précisez pour quoi (un mot suffit), ou choisissez l'activité dans votre agenda.", _url_mes())
    ligne = HeureSupplementaire(
        salarie_id=salarie.id, saisi_par_user_id=current_user.id, date_travail=jour,
        minutes=minutes, motif=motif, session_id=session_id, creneau_id=creneau_id, secteur=salarie.secteur,
    )
    db.session.add(ligne)
    db.session.commit()
    journaliser("rh.heures_declarees", cible=f"salarie#{salarie.id}",
                details={"minutes": minutes, "date": jour.isoformat(), "heure_sup_id": ligne.id})
    flash(f"{es.format_minutes(minutes)} d'heures supplémentaires enregistrées.", "success")
    return redirect(_url_mes())


@bp.route("/recuperations/demandes", methods=["POST"])
@login_required
@require_perm("salarie:espace")
@ma_fiche_ou_explication
def demander_recuperation(salarie):
    jour = es.parse_date(request.form.get("date_recuperation"))
    if jour is None:
        return erreur("Indiquez le jour où vous voulez récupérer.", _url_mes())
    try:
        minutes = es.parse_heures(request.form.get("duree"))
        if minutes <= 0:
            raise ValueError("La durée doit être positive.")
    except ValueError as exc:
        return erreur(str(exc), _url_mes())
    demande = DemandeRecuperation(
        salarie_id=salarie.id, demandeur_user_id=current_user.id, date_recuperation=jour,
        minutes=minutes, motif=(request.form.get("motif") or "").strip(), statut="brouillon",
        secteur=salarie.secteur,
    )
    db.session.add(demande)
    db.session.flush()
    message = "Demande créée en brouillon : signez-la pour l'envoyer."
    if request.form.get("signature_data"):
        try:
            sig = es.enregistrer_signature(request.form.get("signature_data"), contexte="recup_soumission",
                                           objet_type="demande_recuperation", objet_id=demande.id)
        except ValueError as exc:
            db.session.commit()
            flash(f"Demande gardée en brouillon : {exc}", "warning")
            return redirect(_url_mes())
        demande.signature_salarie_id = sig.id
        demande.statut = "soumise"
        message = "Demande signée et envoyée à l'assistant·e de direction."
        courriels_rh.demande_soumise(demande, current_user)
    db.session.commit()
    courriels_rh.expedier_en_attente()
    journaliser("rh.recup_demande", cible=f"demande_recuperation#{demande.id}",
                details={"minutes": minutes, "date": jour.isoformat(), "statut": demande.statut})
    flash(message, "success")
    return redirect(_url_mes())


def _ma_demande(salarie, demande_id: int) -> DemandeRecuperation:
    demande = db.get_or_404(DemandeRecuperation, demande_id)
    if demande.salarie_id != salarie.id:
        abort(404)
    return demande


@bp.route("/recuperations/demandes/<int:demande_id>/envoyer", methods=["POST"])
@login_required
@require_perm("salarie:espace")
@ma_fiche_ou_explication
def envoyer_demande(salarie, demande_id: int):
    demande = _ma_demande(salarie, demande_id)
    if demande.statut != "brouillon":
        flash("Cette demande est déjà envoyée.", "warning")
        return redirect(_url_mes())
    try:
        sig = es.enregistrer_signature(request.form.get("signature_data"), contexte="recup_soumission",
                                       objet_type="demande_recuperation", objet_id=demande.id)
    except ValueError as exc:
        db.session.rollback()
        return erreur(str(exc), _url_mes())
    demande.signature_salarie_id = sig.id
    demande.statut = "soumise"
    courriels_rh.demande_soumise(demande, current_user)
    db.session.commit()
    courriels_rh.expedier_en_attente()
    journaliser("rh.recup_soumise", cible=f"demande_recuperation#{demande.id}")
    flash("Demande signée et envoyée.", "success")
    return redirect(_url_mes())


@bp.route("/recuperations/demandes/<int:demande_id>/annuler", methods=["POST"])
@login_required
@require_perm("salarie:espace")
@ma_fiche_ou_explication
def annuler_demande(salarie, demande_id: int):
    demande = _ma_demande(salarie, demande_id)
    if demande.statut not in es.EN_COURS:
        flash("Une demande déjà décidée ne s'annule plus ici : voyez avec la direction.", "warning")
        return redirect(_url_mes())
    demande.statut = "annulee"
    db.session.commit()
    journaliser("rh.recup_annulee", cible=f"demande_recuperation#{demande.id}")
    flash("Demande annulée.", "success")
    return redirect(_url_mes())


@bp.route("/recuperations/demandes/<int:demande_id>")
@login_required
@require_perm("salarie:espace")
def demande_detail(demande_id: int):
    from app.models import SignatureRh

    demande = db.get_or_404(DemandeRecuperation, demande_id)
    mienne = demande.salarie.user_id == current_user.id
    if not (mienne or _equipe()):
        abort(403)
    sig_ids = [i for i in (demande.signature_salarie_id, demande.signature_transmission_id,
                           demande.signature_decision_id, demande.signature_notification_id) if i]
    signatures = {s.id: s for s in SignatureRh.query.filter(SignatureRh.id.in_(sig_ids)).all()} if sig_ids else {}
    heures = (HeureSupplementaire.query.filter_by(salarie_id=demande.salarie_id)
              .order_by(HeureSupplementaire.date_travail.desc()).limit(30).all())
    historique = (DemandeRecuperation.query.filter(DemandeRecuperation.salarie_id == demande.salarie_id,
                                                   DemandeRecuperation.id != demande.id)
                  .order_by(DemandeRecuperation.created_at.desc()).limit(20).all())
    return render_template(
        "salaries/demande_detail.html", d=demande, signatures=signatures, mienne=mienne,
        solde=es.solde_minutes(demande.salarie_id), heures=heures, historique=historique, es=es,
    )


# ---------------------------------------------------------------------------
# Côté équipe de direction
# ---------------------------------------------------------------------------

@bp.route("/equipe/recuperations")
@login_required
@require_perm("salarie:espace")
def equipe_recuperations():
    if not _equipe():
        abort(403)
    f_salarie = (request.args.get("salarie_id") or "").strip()
    f_statut = (request.args.get("statut") or "").strip()
    f_periode = (request.args.get("periode") or "tout").strip()

    q = DemandeRecuperation.query.filter(DemandeRecuperation.statut != "brouillon")
    if f_salarie.isdigit():
        q = q.filter(DemandeRecuperation.salarie_id == int(f_salarie))
    if f_statut in es.STATUTS_RECUP:
        q = q.filter(DemandeRecuperation.statut == f_statut)
    elif f_statut == "par_interesse":
        q = q.filter(DemandeRecuperation.decision_par_interesse.is_(True))
    elif f_statut == "a_traiter":
        q = q.filter(db.or_(DemandeRecuperation.statut.in_(es.EN_ATTENTE),
                            db.and_(DemandeRecuperation.statut.in_(es.DECIDEES),
                                    DemandeRecuperation.notifiee_le.is_(None))))
    if f_periode == "30j":
        q = q.filter(DemandeRecuperation.created_at >= utcnow() - timedelta(days=30))
    elif f_periode == "annee":
        q = q.filter(DemandeRecuperation.date_recuperation >= date(date.today().year, 1, 1))
    demandes = q.order_by(DemandeRecuperation.created_at.desc()).limit(500).all()

    # Déclarations des 30 derniers jours, visibles « en direct » par
    # l'assistant·e comme par la direction, avec leur durée en vigueur.
    heures_recentes = (HeureSupplementaire.query
                       .filter(HeureSupplementaire.created_at >= utcnow() - timedelta(days=30),
                               HeureSupplementaire.est_ajustement.is_(False))
                       .order_by(HeureSupplementaire.created_at.desc()).limit(100).all())
    salaries = Salarie.query.order_by(Salarie.nom.asc(), Salarie.prenom.asc()).all()
    return render_template(
        "salaries/equipe_recuperations.html",
        demandes=demandes, soldes=es.soldes_par_salarie(), salaries=salaries,
        heures_recentes=heures_recentes, effectives=es.corrections_de(heures_recentes),
        corrections=_corrections_par_origine(heures_recentes),
        compteurs=es.a_traiter_equipe(current_user),
        f_salarie=f_salarie, f_statut=f_statut or "", f_periode=f_periode, es=es,
        peut_transmettre=can("recup:transmettre"), peut_decider=can("recup:decider"),
    )


def _corrections_par_origine(origines) -> dict[int, list[HeureSupplementaire]]:
    ids = [h.id for h in origines]
    if not ids:
        return {}
    resultat: dict[int, list[HeureSupplementaire]] = {}
    for c in (HeureSupplementaire.query.filter(HeureSupplementaire.origine_id.in_(ids))
              .order_by(HeureSupplementaire.created_at.asc(), HeureSupplementaire.id.asc()).all()):
        resultat.setdefault(c.origine_id, []).append(c)
    return resultat


def _signer_etape(demande, contexte: str):
    return es.enregistrer_signature(request.form.get("signature_data"), contexte=contexte,
                                    objet_type="demande_recuperation", objet_id=demande.id)


@bp.route("/equipe/recuperations/<int:demande_id>/transmettre", methods=["POST"])
@login_required
@require_perm("recup:transmettre")
def transmettre_demande(demande_id: int):
    """Premier niveau (assistant·e) : transmettre à la direction, ou refuser
    avec une justification. Dans les deux cas l'étape est signée et le
    salarié est prévenu."""
    demande = db.get_or_404(DemandeRecuperation, demande_id)
    if demande.statut != "soumise":
        return erreur("Cette demande n'attend pas de transmission.", _url_equipe())
    refus = request.form.get("decision") == "refuser"
    commentaire = (request.form.get("commentaire") or "").strip()
    if refus and not commentaire:
        return erreur("Un refus doit être justifié : écrivez la raison.", _url_equipe())
    try:
        sig = _signer_etape(demande, "recup_refus_relais" if refus else "recup_transmission")
    except ValueError as exc:
        db.session.rollback()
        return erreur(str(exc), _url_equipe())
    demande.signature_transmission_id = sig.id
    if refus:
        maintenant = utcnow()
        demande.statut = "refusee"
        # Un refus au premier niveau par quelqu'un qui a aussi le pouvoir de
        # décider (direction) est affiché comme un refus de la direction.
        demande.refusee_par_relais = not can("recup:decider")
        demande.commentaire_direction = commentaire
        demande.decidee_par_user_id = current_user.id
        demande.decidee_le = maintenant
        # L'assistant·e a pris la décision : il·elle en a forcément connaissance.
        demande.notifiee_par_user_id = current_user.id
        demande.notifiee_le = maintenant
        demande.decision_par_interesse = es.est_l_interesse(demande, current_user)
        courriels_rh.demande_refusee_relais(demande, current_user)
    else:
        demande.statut = "transmise"
        demande.transmise_par_user_id = current_user.id
        demande.transmise_le = utcnow()
        courriels_rh.demande_transmise(demande, current_user)
    db.session.commit()
    courriels_rh.expedier_en_attente()
    if refus:
        journaliser("rh.recup_auto_decision" if demande.decision_par_interesse else "rh.recup_refus_relais",
                    cible=f"demande_recuperation#{demande.id}",
                    details={"decision": "refusee", "niveau": "assistant", "par_l_interesse": demande.decision_par_interesse})
        flash("Demande refusée ; le salarié est prévenu avec votre justification.", "success")
    else:
        journaliser("rh.recup_transmise", cible=f"demande_recuperation#{demande.id}")
        flash("Demande transmise à la direction ; le salarié est prévenu.", "success")
    return retour(_url_equipe())


@bp.route("/equipe/recuperations/<int:demande_id>/decider", methods=["POST"])
@login_required
@require_perm("recup:decider")
def decider_demande(demande_id: int):
    """Direction : accepter (sans justification) ou refuser (justification
    obligatoire). Elle peut décider sans attendre la transmission (absence de
    l'assistant·e). Le salarié et l'assistant·e sont prévenus."""
    demande = db.get_or_404(DemandeRecuperation, demande_id)
    if demande.statut not in es.EN_ATTENTE:
        return erreur("Cette demande n'attend pas de décision.", _url_equipe())
    accord = request.form.get("decision") == "accepter"
    commentaire = (request.form.get("commentaire") or "").strip()
    if not accord and not commentaire:
        return erreur("Un refus doit être justifié : écrivez la raison.", _url_equipe())
    try:
        sig = _signer_etape(demande, "recup_decision")
    except ValueError as exc:
        db.session.rollback()
        return erreur(str(exc), _url_equipe())
    demande.signature_decision_id = sig.id
    demande.statut = "acceptee" if accord else "refusee"
    demande.commentaire_direction = commentaire or None
    demande.decidee_par_user_id = current_user.id
    demande.decidee_le = utcnow()
    # Décider de sa propre demande reste possible (petite structure, pas
    # d'autre décideur), mais n'est jamais discret : la demande est marquée,
    # le journal a une action dédiée, l'accueil de la direction la compte.
    demande.decision_par_interesse = es.est_l_interesse(demande, current_user)
    courriels_rh.demande_decidee(demande, current_user)
    db.session.commit()
    courriels_rh.expedier_en_attente()
    journaliser("rh.recup_auto_decision" if demande.decision_par_interesse else "rh.recup_decision",
                cible=f"demande_recuperation#{demande.id}",
                details={"decision": demande.statut, "sans_transmission": demande.transmise_le is None,
                         "par_l_interesse": demande.decision_par_interesse})
    message = "Récupération acceptée." if accord else "Récupération refusée."
    if demande.decision_par_interesse:
        message += " C'est votre propre demande : elle est marquée « décidée par l'intéressé·e » et tracée au journal."
    flash(message, "warning" if demande.decision_par_interesse else "success")
    return retour(_url_equipe())


@bp.route("/equipe/recuperations/<int:demande_id>/pris-connaissance", methods=["POST"])
@login_required
@require_perm("recup:transmettre")
def prendre_connaissance(demande_id: int):
    """L'assistant·e atteste avoir pris connaissance de la décision (sans
    signature : le salarié a déjà été prévenu par e-mail)."""
    demande = db.get_or_404(DemandeRecuperation, demande_id)
    if demande.statut not in es.DECIDEES or demande.notifiee_le:
        return erreur("Rien à prendre en compte pour cette demande.", _url_equipe())
    demande.notifiee_par_user_id = current_user.id
    demande.notifiee_le = utcnow()
    db.session.commit()
    journaliser("rh.recup_pris_connaissance", cible=f"demande_recuperation#{demande.id}")
    flash("Décision prise en compte.", "success")
    return retour(_url_equipe())


@bp.route("/equipe/heures/<int:heure_id>/corriger", methods=["POST"])
@login_required
@require_perm("salarie:espace")
def corriger_heures(heure_id: int):
    """L'assistant·e ou la direction corrige une déclaration d'heures sup,
    dans un sens ou dans l'autre (0 = retrait), justification obligatoire."""
    return _corriger(heure_id, request.form.get("duree") or "0")


@bp.route("/equipe/heures/<int:heure_id>/retirer", methods=["POST"])
@login_required
@require_perm("salarie:espace")
def retirer_heures(heure_id: int):
    """Retrait = correction à zéro."""
    return _corriger(heure_id, "0")


def _corriger(heure_id: int, duree_brute: str):
    """La déclaration d'origine n'est jamais modifiée : la correction est une
    ligne d'ajustement (la différence avec la durée en vigueur), visible du
    salarié, qui est prévenu avec la justification."""
    if not _equipe():
        abort(403)
    origine = db.get_or_404(HeureSupplementaire, heure_id)
    if origine.est_ajustement or origine.minutes <= 0:
        abort(400)
    commentaire = (request.form.get("commentaire") or "").strip()
    if not commentaire:
        return erreur("Corriger ou retirer des heures demande une justification.", _url_equipe())
    try:
        apres = es.parse_heures(duree_brute)
    except ValueError as exc:
        return erreur(str(exc), _url_equipe())
    if apres < 0:
        return erreur("La durée corrigée ne peut pas être négative.", _url_equipe())
    avant = es.minutes_effectives(origine)
    if apres == avant:
        return erreur("La durée est inchangée : rien à corriger.", _url_equipe())
    # Corriger ses propres heures reste possible, mais marqué et tracé.
    par_interesse = bool(origine.salarie.user_id and origine.salarie.user_id == current_user.id)
    correction = HeureSupplementaire(
        salarie_id=origine.salarie_id, saisi_par_user_id=current_user.id, date_travail=origine.date_travail,
        minutes=apres - avant, est_ajustement=True, origine_id=origine.id, secteur=origine.secteur,
        motif=("Retrait" if apres == 0 else f"Correction {es.format_minutes(avant)} → {es.format_minutes(apres)}"),
        commentaire_direction=commentaire, par_interesse=par_interesse,
    )
    db.session.add(correction)
    db.session.flush()
    courriels_rh.heures_corrigees(correction, avant, apres, current_user)
    db.session.commit()
    courriels_rh.expedier_en_attente()
    journaliser("rh.heures_corrigees_par_interesse" if par_interesse else "rh.heures_corrigees",
                cible=f"salarie#{origine.salarie_id}",
                details={"heure_sup_id": origine.id, "avant": avant, "apres": apres, "commentaire": commentaire,
                         "par_l_interesse": par_interesse})
    flash(("Heures retirées" if apres == 0 else
           f"Heures corrigées : {es.format_minutes(avant)} → {es.format_minutes(apres)}")
          + " ; le salarié est prévenu avec votre justification.", "success")
    return retour(_url_equipe())
