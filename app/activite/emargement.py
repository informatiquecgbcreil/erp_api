import os
import base64
import secrets
from datetime import datetime, date

from app.utils.dates import utcnow

from flask import (
    render_template,
    request,
    redirect,
    url_for,
    flash,
    current_app,
)
from flask_login import login_required, current_user

from app.extensions import db
from app.models import (
    AtelierActivite,
    SessionActivite,
    Participant,
    PresenceActivite,
    Quartier,
    Competence,
    Evaluation,
    Objectif,
    PedagogieModule,
    PlanProjetAtelierModule,
    PasseportNote,
    SessionScheduleEditLog,
)

from ..rbac import require_perm
from . import bp
from .services.docx_utils import (
    generate_individuel_mensuel_docx,
)
from app.services.quartiers import normalize_quartier_for_ville
from app.services.public_urls import kiosk_public_base_url
from app.services.consumption import (
    list_materiels_actifs,
    save_session_materiels_from_form,
    assign_session_config,
    calculate_session_consumption,
    default_individual_materiel_id,
    replace_presence_consumptions,
    ensure_presence_consumption_from_session_default,
    regenerate_presence_consumptions_for_session,
    clear_presence_consumptions_for_session,
    build_presence_consumption_maps,
    resolve_consumption_period,
)
from app.activite.helpers import (
    _can_access_activity_secteur,
    _session_est_accessible,
    _collect_session_competences,
    _consumption_period_request_args,
    _deny_activity_access,
    _ensure_month_capacity,
    _materiel_ids_from_request_form,
    _normalize_note_category,
    _redirect_emargement_with_period,
    _sort_competences,
    _user_secteur,
)



# ------------------ Émargement ------------------


@bp.route("/session/<int:session_id>/emargement", methods=["GET", "POST"])
@login_required
def emargement(session_id: int):
    require_perm("emargement:view")(lambda: None)()
    if request.method == "POST":
        require_perm("emargement:edit")(lambda: None)()
    secteur = _user_secteur()
    s = db.get_or_404(SessionActivite, session_id)
    atelier = db.get_or_404(AtelierActivite, s.atelier_id)

    if s.is_deleted or atelier.is_deleted:
        flash("Cette séance (ou son atelier) est dans la corbeille.", "warning")
        return redirect(url_for("activite.sessions", atelier_id=atelier.id, corbeille=1))
    if not _session_est_accessible(s):
        return _deny_activity_access()

    quartiers = Quartier.query.order_by(Quartier.ville.asc(), Quartier.nom.asc()).all()

    if request.method == "POST":
        action = (request.form.get("action") or "").strip()

        if action == "update_session_modules":
            module_ids = [int(mid) for mid in request.form.getlist("module_ids") if str(mid).isdigit()]
            modules = (
                PedagogieModule.query.filter(PedagogieModule.id.in_(module_ids), PedagogieModule.actif.is_(True)).all()
                if module_ids
                else []
            )

            competence_ids = set()
            for mod in modules:
                for comp in mod.competences:
                    competence_ids.add(comp.id)

            s.modules = modules
            s.competences = (
                Competence.query.filter(Competence.id.in_(list(competence_ids))).all()
                if competence_ids
                else []
            )
            db.session.commit()
            flash("Modules (et compétences) de la séance mis à jour.", "success")
            return _redirect_emargement_with_period(session_id)

        if action == "bulk_eval_selected":
            eval_date = s.rdv_date or s.date_session or date.today()

            participant_ids = [int(pid) for pid in request.form.getlist("participant_ids") if str(pid).isdigit()]
            competence_ids = [int(cid) for cid in request.form.getlist("competence_ids") if str(cid).isdigit()]

            if not participant_ids:
                flash("Aucun participant sélectionné.", "danger")
                return _redirect_emargement_with_period(session_id)
            if not competence_ids:
                flash("Aucune compétence sélectionnée.", "danger")
                return _redirect_emargement_with_period(session_id)

            try:
                etat_value = int(request.form.get("etat", 0))
            except Exception:
                etat_value = 0
            if etat_value not in (0, 1, 2, 3):
                etat_value = 0

            commentaire = (request.form.get("commentaire") or "").strip() or None

            present_ids = {p.participant_id for p in PresenceActivite.query.filter_by(session_id=session_id).all()}
            participant_ids = [pid for pid in participant_ids if pid in present_ids]
            if not participant_ids:
                flash("Sélection invalide (aucun participant présent sur la séance).", "danger")
                return _redirect_emargement_with_period(session_id)

            updates = 0
            for pid in participant_ids:
                for cid in competence_ids:
                    evaluation = Evaluation.query.filter_by(
                        participant_id=pid,
                        competence_id=cid,
                        session_id=s.id,
                    ).first()
                    if evaluation:
                        evaluation.etat = etat_value
                        evaluation.commentaire = commentaire
                        evaluation.user_id = current_user.id
                        evaluation.date_evaluation = eval_date
                    else:
                        db.session.add(
                            Evaluation(
                                participant_id=pid,
                                competence_id=cid,
                                session_id=s.id,
                                user_id=current_user.id,
                                etat=etat_value,
                                date_evaluation=eval_date,
                                commentaire=commentaire,
                            )
                        )
                    updates += 1

            db.session.commit()
            flash(f"{updates} évaluation(s) appliquée(s) à la sélection.", "success")
            return _redirect_emargement_with_period(session_id)

        if action == "save_evaluation":
            participant_id = request.form.get("participant_id")
            if not participant_id:
                flash("Participant manquant.", "danger")
                return _redirect_emargement_with_period(session_id)
            participant = db.session.get(Participant, int(participant_id))
            if not participant:
                flash("Participant introuvable.", "danger")
                return _redirect_emargement_with_period(session_id)

            eval_date = s.rdv_date or s.date_session or date.today()
            competence_ids = [int(cid) for cid in request.form.getlist("competence_ids") if str(cid).isdigit()]
            for comp_id in competence_ids:
                etat = request.form.get(f"etat_{comp_id}")
                if etat is None:
                    continue
                try:
                    etat_value = int(etat)
                except ValueError:
                    continue
                commentaire = (request.form.get(f"commentaire_{comp_id}") or "").strip() or None
                evaluation = Evaluation.query.filter_by(
                    participant_id=participant.id,
                    competence_id=comp_id,
                    session_id=s.id,
                ).first()
                if evaluation:
                    evaluation.etat = etat_value
                    evaluation.commentaire = commentaire
                    evaluation.user_id = current_user.id
                    evaluation.date_evaluation = eval_date
                else:
                    db.session.add(
                        Evaluation(
                            participant_id=participant.id,
                            competence_id=comp_id,
                            session_id=s.id,
                            user_id=current_user.id,
                            etat=etat_value,
                            date_evaluation=eval_date,
                            commentaire=commentaire,
                        )
                    )
            db.session.commit()
            flash("Évaluation enregistrée.", "success")
            return _redirect_emargement_with_period(session_id, highlight=participant.id)

        if action == "bulk_validate":
            eval_date = s.rdv_date or s.date_session or date.today()
            session_competences = _collect_session_competences(s)
            presences = PresenceActivite.query.filter_by(session_id=session_id).all()
            for pr in presences:
                for comp in session_competences:
                    evaluation = Evaluation.query.filter_by(
                        participant_id=pr.participant_id,
                        competence_id=comp.id,
                        session_id=s.id,
                    ).first()
                    if evaluation:
                        evaluation.etat = 2
                        evaluation.user_id = current_user.id
                        evaluation.date_evaluation = eval_date
                    else:
                        db.session.add(
                            Evaluation(
                                participant_id=pr.participant_id,
                                competence_id=comp.id,
                                session_id=s.id,
                                user_id=current_user.id,
                                etat=2,
                                date_evaluation=eval_date,
                            )
                        )
            db.session.commit()
            flash("Évaluation rapide appliquée.", "success")
            return _redirect_emargement_with_period(session_id)

        if action == "add_participant":
            nom = (request.form.get("nom") or "").strip()
            prenom = (request.form.get("prenom") or "").strip()
            ville = (request.form.get("ville") or "").strip() or None
            adresse = (request.form.get("adresse") or "").strip() or None
            email = (request.form.get("email") or "").strip() or None
            telephone = (request.form.get("telephone") or "").strip() or None
            genre = (request.form.get("genre") or "").strip() or None
            date_naissance = request.form.get("date_naissance") or None
            type_public = (request.form.get("type_public") or "H").strip().upper() or "H"
            quartier_id = request.form.get("quartier_id") or None

            if not nom or not prenom:
                flash("Nom et prénom obligatoires.", "danger")
                return _redirect_emargement_with_period(session_id)

            dn = None
            if date_naissance:
                try:
                    dn = datetime.strptime(date_naissance, "%Y-%m-%d").date()
                except Exception:
                    dn = None

            qid = normalize_quartier_for_ville(ville, quartier_id)

            p = Participant(
                nom=nom,
                prenom=prenom,
                ville=ville,
                adresse=adresse,
                email=email,
                telephone=telephone,
                genre=genre,
                date_naissance=dn,
                quartier_id=qid,
                type_public=type_public,
            )
            db.session.add(p)
            db.session.commit()
            flash("Le participant a bien été créé.", "success")
            return redirect(url_for("activite.emargement", session_id=session_id, highlight=p.id))

        if action == "delete_presence":
            from app.services.audit import journaliser

            presence_id = request.form.get("presence_id", type=int)
            pr = PresenceActivite.query.filter_by(id=presence_id, session_id=session_id).first() if presence_id else None
            if not pr:
                flash("Présence introuvable pour cette séance.", "danger")
                return _redirect_emargement_with_period(session_id)
            cible = (f"{pr.participant.nom} {pr.participant.prenom}" if pr.participant else str(pr.participant_id))
            db.session.delete(pr)
            db.session.commit()
            journaliser("presence.delete", cible=f"session #{session_id} · {cible}")
            flash("Présence retirée.", "success")
            return _redirect_emargement_with_period(session_id)

        if action == "update_presence_type":
            presence_id = request.form.get("presence_id", type=int)
            pr = PresenceActivite.query.filter_by(id=presence_id, session_id=session_id).first() if presence_id else None
            if not pr:
                flash("Présence introuvable pour cette séance.", "danger")
                return _redirect_emargement_with_period(session_id)
            ptype = (request.form.get("presence_type") or "present").strip()
            pr.presence_type = ptype if ptype in {"present", "retard", "absent_excuse"} else "present"
            db.session.commit()
            flash("Statut de présence mis à jour.", "success")
            return _redirect_emargement_with_period(session_id, highlight=pr.participant_id)

        if action == "emarger":
            participant_id = request.form.get("participant_id")
            motif = request.form.get("motif") or None
            motif_autre = (request.form.get("motif_autre") or "").strip() or None
            presence_type = (request.form.get("presence_type") or "present").strip()
            if presence_type not in {"present", "retard", "absent_excuse"}:
                presence_type = "present"
            signature_data = request.form.get("signature_data")
            materiel_individuel_ids = _materiel_ids_from_request_form()
            try:
                quantite_individuelle = max(0, int(request.form.get("quantite_individuelle") or "1"))
            except Exception:
                quantite_individuelle = 1

            if not participant_id:
                flash("Choisis un participant.", "danger")
                return _redirect_emargement_with_period(session_id)
            participant = db.session.get(Participant, int(participant_id))
            if not participant:
                flash("Participant introuvable.", "danger")
                return _redirect_emargement_with_period(session_id)

            sig_path = None
            if signature_data and signature_data.startswith("data:image"):
                try:
                    _, b64data = signature_data.split(",", 1)
                    binary = base64.b64decode(b64data)
                    sig_dir = os.path.join(current_app.instance_path, "signatures_tmp")
                    os.makedirs(sig_dir, exist_ok=True)
                    sig_filename = f"sig_s{session_id}_p{participant.id}_{int(utcnow().timestamp())}.png"
                    sig_path = os.path.join(sig_dir, sig_filename)
                    with open(sig_path, "wb") as f:
                        f.write(binary)
                except Exception:
                    sig_path = None

            try:
                pr = PresenceActivite.query.filter_by(session_id=session_id, participant_id=participant.id).first()
                if pr:
                    pr.motif = motif
                    pr.motif_autre = motif_autre
                    pr.presence_type = presence_type
                    if sig_path:
                        pr.signature_path = sig_path
                else:
                    pr = PresenceActivite(
                        session_id=session_id,
                        participant_id=participant.id,
                        motif=motif,
                        motif_autre=motif_autre,
                        presence_type=presence_type,
                        signature_path=sig_path,
                    )
                    db.session.add(pr)
                db.session.flush()
                if materiel_individuel_ids:
                    replace_presence_consumptions(
                        pr,
                        materiel_ids=materiel_individuel_ids,
                        quantite=quantite_individuelle or 1,
                        mode_calcul="manuel_emargement",
                    )
                elif default_individual_materiel_id(s):
                    ensure_presence_consumption_from_session_default(pr)
                db.session.commit()
            except Exception:
                db.session.rollback()
                flash("Impossible d'enregistrer l'émargement (conflit ou erreur).", "danger")
                return _redirect_emargement_with_period(session_id)

            if s.session_type == "INDIVIDUEL_MENSUEL" and s.rdv_date:
                _ensure_month_capacity(atelier, s)
                generate_individuel_mensuel_docx(app=current_app, atelier=atelier, annee=s.rdv_date.year, mois=s.rdv_date.month)

            flash("Émargement enregistré.", "success")
            return _redirect_emargement_with_period(session_id)

        if action == "update_session_materiels":
            assign_session_config(s)
            save_session_materiels_from_form(s, request.form)
            db.session.commit()
            flash("Matériel de la séance mis à jour.", "success")
            return _redirect_emargement_with_period(session_id)

        if action == "update_presence_consumption":
            presence_id = request.form.get("presence_id", type=int)
            pr = PresenceActivite.query.filter_by(id=presence_id, session_id=session_id).first() if presence_id else None
            if not pr:
                flash("Présence introuvable pour cette séance.", "danger")
                return _redirect_emargement_with_period(session_id)

            materiel_ids = _materiel_ids_from_request_form()
            try:
                quantite = max(0, int(request.form.get("quantite_individuelle") or "1"))
            except Exception:
                quantite = 1

            replace_presence_consumptions(
                pr,
                materiel_ids=materiel_ids,
                quantite=quantite,
                mode_calcul="manuel_ligne",
            )
            db.session.commit()
            if materiel_ids and quantite > 0:
                flash("Consommation individuelle mise à jour pour ce participant.", "success")
            else:
                flash("Consommation individuelle retirée pour ce participant.", "success")
            return _redirect_emargement_with_period(session_id)

        if action == "apply_presence_consumption":
            materiel_ids = _materiel_ids_from_request_form()
            try:
                quantite = max(0, int(request.form.get("quantite_individuelle") or "1"))
            except Exception:
                quantite = 1
            if not materiel_ids:
                default_id = default_individual_materiel_id(s)
                materiel_ids = [default_id] if default_id else []
            if not materiel_ids:
                flash("Aucun matériel individuel disponible. Déclarez d'abord au moins un matériel sur la séance.", "warning")
                return _redirect_emargement_with_period(session_id)
            count = regenerate_presence_consumptions_for_session(s, materiel_ids=materiel_ids, quantite=quantite or 1)
            db.session.commit()
            flash(f"Consommation individuelle appliquée à {count} présence(s).", "success")
            return _redirect_emargement_with_period(session_id)

        if action == "clear_presence_consumption":
            count = clear_presence_consumptions_for_session(s)
            db.session.commit()
            flash(f"Consommations individuelles effacées pour {count} ligne(s).", "success")
            return _redirect_emargement_with_period(session_id)

        if action == "quick_passport_note":
            participant_id = request.form.get("participant_id", type=int)
            participant = db.session.get(Participant, participant_id) if participant_id else None
            if not participant:
                flash("Participant invalide.", "danger")
                return _redirect_emargement_with_period(session_id)
            contenu = (request.form.get("contenu") or "").strip()
            if not contenu:
                flash("La note rapide ne peut pas être vide.", "danger")
                return _redirect_emargement_with_period(session_id)
            note = PasseportNote(
                participant_id=participant.id,
                session_id=s.id,
                secteur=s.secteur,
                categorie=_normalize_note_category(request.form.get("categorie")),
                contenu=contenu,
                created_by=current_user.id,
            )
            db.session.add(note)
            db.session.commit()
            flash("Note ajoutée au passeport.", "success")
            return _redirect_emargement_with_period(session_id, highlight=participant.id)

    # ---- GET context ----
    participants = Participant.query.order_by(Participant.nom.asc(), Participant.prenom.asc()).limit(500).all()
    motifs = atelier.motifs() or []
    presences = PresenceActivite.query.filter_by(session_id=session_id).order_by(PresenceActivite.created_at.asc()).all()

    session_objectifs = Objectif.query.filter_by(session_id=s.id, type="operationnel").order_by(Objectif.created_at.asc()).all()
    objectifs_payload = []
    for obj in session_objectifs:
        competences = _sort_competences(obj.competences)
        objectifs_payload.append({"objectif": obj, "competences": competences})

    session_competences = _collect_session_competences(s, session_objectifs=session_objectifs)

    evaluations = Evaluation.query.filter_by(session_id=s.id).all()
    evaluation_map = {(e.participant_id, e.competence_id): e for e in evaluations}

    modules_available = (
        PedagogieModule.query.join(PlanProjetAtelierModule, PlanProjetAtelierModule.module_id == PedagogieModule.id)
        .filter(
            PlanProjetAtelierModule.atelier_id == atelier.id,
            PedagogieModule.actif.is_(True),
        )
        .distinct()
        .order_by(PedagogieModule.nom.asc())
        .all()
    )
    current_module_ids = {m.id for m in (getattr(s, "modules", []) or [])}
    schedule_edits = (
        SessionScheduleEditLog.query.filter_by(session_id=s.id)
        .order_by(SessionScheduleEditLog.edited_at.desc())
        .limit(5)
        .all()
    )
    session_conso = calculate_session_consumption(s, atelier=atelier)
    materiels = list_materiels_actifs()
    default_materiel_id = default_individual_materiel_id(s)
    conso_period_args = _consumption_period_request_args()
    conso_period_ctx = resolve_consumption_period(
        s,
        mode=conso_period_args["period_mode"],
        period_start=conso_period_args["period_start"],
        period_end=conso_period_args["period_end"],
    )
    presence_conso_map, participant_conso_cumul_map = build_presence_consumption_maps(
        presences,
        s,
        period_mode=conso_period_args["period_mode"],
        period_start=conso_period_args["period_start"],
        period_end=conso_period_args["period_end"],
    )
    conso_period_start, conso_period_end = conso_period_ctx.get("start"), conso_period_ctx.get("end")

    # Échelle de Hart : évaluations dues pour les présents (première venue,
    # puis toutes les N séances). L'émargement pilote le déclenchement.
    from app.services import hart as hart_service
    hart_dues = hart_service.evaluations_dues_pour_session(s)

    # Inscriptions préalables : les attendus pas encore pointés pré-remplissent
    # l'émargement (bloc « à pointer » en un clic).
    from app.services.inscriptions import a_pointer_session
    inscrits_a_pointer = a_pointer_session(s)

    return render_template(
        "activite/emargement.html",
        secteur=secteur,
        atelier=atelier,
        session=s,
        participants=participants,
        presences=presences,
        motifs=motifs,
        quartiers=quartiers,
        session_competences=session_competences,
        objectifs_payload=objectifs_payload,
        evaluation_map=evaluation_map,
        modules_available=modules_available,
        current_module_ids=current_module_ids,
        schedule_edits=schedule_edits,
        session_conso=session_conso,
        materiels=materiels,
        default_materiel_id=default_materiel_id,
        kiosk_home_url=f"{kiosk_public_base_url()}{url_for('kiosk.kiosk_home')}",
        kiosk_session_url=f"{kiosk_public_base_url()}{url_for('kiosk.kiosk_session', token=s.kiosk_token)}" if s.kiosk_token else "",
        signature_liens=[
            {"presence": pr,
             "url": f"{kiosk_public_base_url()}{url_for('kiosk.signer', token=pr.signature_token)}"}
            for pr in presences if pr.signature_token and not pr.signature_path
        ],
        nb_sans_signature=sum(1 for pr in presences if not pr.signature_path),
        presence_conso_map=presence_conso_map,
        participant_conso_cumul_map=participant_conso_cumul_map,
        conso_period_start=conso_period_start,
        conso_period_end=conso_period_end,
        conso_period_ctx=conso_period_ctx,
        conso_period_mode=conso_period_args["period_mode"],
        conso_period_start_value=conso_period_args["period_start"],
        conso_period_end_value=conso_period_args["period_end"],
        hart_dues=hart_dues,
        hart_niveaux=hart_service.HART_NIVEAUX,
        inscrits_a_pointer=inscrits_a_pointer,
    )


# ------------------ Kiosk ------------------


@bp.route("/session/<int:session_id>/kiosk_open", methods=["POST"])
@login_required
def kiosk_open(session_id: int):
    require_perm("ateliers:edit")(lambda: None)()
    s = db.get_or_404(SessionActivite, session_id)
    if not _session_est_accessible(s):
        return _deny_activity_access()

    token = secrets.token_urlsafe(24)

    for _ in range(50):
        pin = f"{secrets.randbelow(10000):04d}"
        exists = SessionActivite.query.filter_by(kiosk_open=True, kiosk_pin=pin).first()
        if not exists:
            break
    else:
        pin = None

    s.kiosk_open = True
    s.kiosk_token = token
    s.kiosk_pin = pin
    s.kiosk_opened_at = utcnow()
    db.session.commit()

    flash(f"Kiosque ouvert (code: {pin}).", "success")
    return _redirect_emargement_with_period(session_id)


@bp.route("/session/<int:session_id>/kiosk_close", methods=["POST"])
@login_required
def kiosk_close(session_id: int):
    require_perm("ateliers:edit")(lambda: None)()
    s = db.get_or_404(SessionActivite, session_id)
    if not _session_est_accessible(s):
        return _deny_activity_access()

    s.kiosk_open = False
    s.kiosk_pin = None
    s.kiosk_token = None
    db.session.commit()

    flash("Kiosque fermé.", "success")
    return _redirect_emargement_with_period(session_id)




@bp.route("/session/<int:session_id>/signature-liens", methods=["POST"])
@login_required
def signature_liens_generer(session_id: int):
    """Génère un lien personnel de signature pour chaque présence non signée
    de la séance : l'animateur saisit les noms en physique (hors connexion du
    centre), puis envoie à chacun son lien (SMS, WhatsApp…) pour signer
    depuis son propre téléphone. Complète le kiosque, ne le remplace pas."""
    import secrets as _secrets

    from app.activite.helpers import _require_any_perm, _session_est_accessible

    _require_any_perm("emargement:edit")
    s = db.get_or_404(SessionActivite, session_id)
    if not _session_est_accessible(s):
        from app.activite.helpers import _deny_activity_access
        return _deny_activity_access()

    presences = PresenceActivite.query.filter_by(session_id=s.id).all()
    crees = 0
    for pr in presences:
        if pr.signature_path or pr.signature_token:
            continue
        pr.signature_token = _secrets.token_urlsafe(24)
        crees += 1
    db.session.commit()
    if crees:
        flash(f"{crees} lien(s) de signature générés : copie-les ci-dessous et envoie à chacun le sien.", "success")
    else:
        flash("Aucun lien à générer : toutes les présences sont déjà signées (ou ont déjà leur lien).", "info")
    return redirect(url_for("activite.emargement", session_id=s.id) + "#signature-distance")
