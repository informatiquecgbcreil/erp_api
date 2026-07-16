from datetime import datetime, date, timedelta


from flask import (
    render_template,
    request,
    redirect,
    url_for,
    flash,
)
from flask_login import login_required, current_user

from app.extensions import db
from app.models import (
    AtelierActivite,
    SessionActivite,
    PresenceActivite,
    Referentiel,
    Competence,
    Evaluation,
    PedagogieModule,
    PlanProjetAtelierModule,
    Projet,
    SessionScheduleEditLog,
)

from ..rbac import require_perm
from . import bp
from app.services.consumption import (
    list_materiels_actifs,
    save_session_materiels_from_form,
    assign_session_config,
)
from app.activite.helpers import (
    _can_access_activity_secteur,
    _atelier_est_accessible,
    _session_est_accessible,
    _collect_session_competences,
    _deny_activity_access,
    _require_any_perm,
    _sort_competences,
    _user_secteur,
)



# ------------------ Création Session ------------------


@bp.route("/atelier/<int:atelier_id>/session/new", methods=["GET", "POST"])
@login_required
def session_new(atelier_id: int):
    require_perm("ateliers:edit")(lambda: None)()
    secteur = _user_secteur()
    atelier = db.get_or_404(AtelierActivite, atelier_id)
    if atelier.is_deleted:
        flash("Cet atelier est dans la corbeille. Restaure-le pour créer une séance.", "warning")
        return redirect(url_for("activite.index", corbeille=1))
    if not _atelier_est_accessible(atelier):
        return _deny_activity_access()

    if request.method == "POST":
        session_type = atelier.type_atelier
        if session_type == "INDIVIDUEL_MENSUEL":
            rdv_date = request.form.get("rdv_date")
            rdv_debut = (request.form.get("rdv_debut") or "").strip() or None
            rdv_fin = (request.form.get("rdv_fin") or "").strip() or None
            if not rdv_date:
                flash("Date RDV obligatoire.", "danger")
                modules = PedagogieModule.query.filter(PedagogieModule.actif.is_(True)).order_by(PedagogieModule.nom.asc()).all()
                return render_template(
                    "activite/session_form.html",
                    secteur=secteur,
                    atelier=atelier,
                    session=None,
                    modules=modules,
                    projets_atelier=[],
                    projet_id=None,
                    materiels=list_materiels_actifs(),
                )
            rdv_date_obj = datetime.strptime(rdv_date, "%Y-%m-%d").date()
            s = SessionActivite(
                atelier_id=atelier.id,
                secteur=atelier.secteur,
                session_type="INDIVIDUEL_MENSUEL",
                rdv_date=rdv_date_obj,
                rdv_debut=rdv_debut,
                rdv_fin=rdv_fin,
            )
        else:
            date_session = request.form.get("date_session")
            heure_debut = (request.form.get("heure_debut") or "").strip() or None
            heure_fin = (request.form.get("heure_fin") or "").strip() or None
            capacite = request.form.get("capacite") or atelier.capacite_defaut
            if not date_session:
                flash("Date de séance obligatoire.", "danger")
                modules = PedagogieModule.query.filter(PedagogieModule.actif.is_(True)).order_by(PedagogieModule.nom.asc()).all()
                return render_template(
                    "activite/session_form.html",
                    secteur=secteur,
                    atelier=atelier,
                    session=None,
                    modules=modules,
                    projets_atelier=[],
                    projet_id=None,
                    materiels=list_materiels_actifs(),
                )
            date_obj = datetime.strptime(date_session, "%Y-%m-%d").date()
            s = SessionActivite(
                atelier_id=atelier.id,
                secteur=atelier.secteur,
                session_type="COLLECTIF",
                date_session=date_obj,
                heure_debut=heure_debut,
                heure_fin=heure_fin,
                capacite=int(capacite) if capacite else None,
            )

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

        s.competences = Competence.query.filter(Competence.id.in_(list(competence_ids))).all() if competence_ids else []
        s.modules = modules
        s.est_evenement = request.form.get("est_evenement") in {"1", "on", "true"}

        db.session.add(s)
        db.session.flush()
        assign_session_config(s)
        save_session_materiels_from_form(s, request.form)
        db.session.commit()
        flash("La séance a bien été créée.", "success")
        return redirect(url_for("activite.emargement", session_id=s.id))

    projet_id = request.args.get("projet_id", type=int)
    projets_atelier = (
        Projet.query.join(PlanProjetAtelierModule, PlanProjetAtelierModule.projet_id == Projet.id)
        .filter(PlanProjetAtelierModule.atelier_id == atelier.id)
        .distinct()
        .order_by(Projet.nom.asc())
        .all()
    )

    if projet_id:
        modules = (
            PedagogieModule.query.join(PlanProjetAtelierModule, PlanProjetAtelierModule.module_id == PedagogieModule.id)
            .filter(
                PlanProjetAtelierModule.atelier_id == atelier.id,
                PlanProjetAtelierModule.projet_id == projet_id,
                PedagogieModule.actif.is_(True),
            )
            .order_by(PedagogieModule.nom.asc())
            .all()
        )
    else:
        modules = (
            PedagogieModule.query.join(PlanProjetAtelierModule, PlanProjetAtelierModule.module_id == PedagogieModule.id)
            .filter(
                PlanProjetAtelierModule.atelier_id == atelier.id,
                PedagogieModule.actif.is_(True),
            )
            .distinct()
            .order_by(PedagogieModule.nom.asc())
            .all()
        )

    return render_template(
        "activite/session_form.html",
        secteur=secteur,
        atelier=atelier,
        session=None,
        modules=modules,
        projets_atelier=projets_atelier,
        projet_id=projet_id,
        materiels=list_materiels_actifs(),
    )


@bp.route("/atelier/<int:atelier_id>/sessions/bulk", methods=["GET", "POST"])
@login_required
def session_bulk_new(atelier_id: int):
    """Création EN SÉRIE de séances collectives (ex. tous les jeudis 14h-16h)."""
    require_perm("ateliers:edit")(lambda: None)()
    secteur = _user_secteur()
    atelier = db.get_or_404(AtelierActivite, atelier_id)
    if atelier.is_deleted:
        flash("Cet atelier est dans la corbeille.", "warning")
        return redirect(url_for("activite.index", corbeille=1))
    if not _atelier_est_accessible(atelier):
        return _deny_activity_access()
    if atelier.type_atelier == "INDIVIDUEL_MENSUEL":
        flash("La création en série concerne les ateliers collectifs (séances à dates régulières).", "warning")
        return redirect(url_for("activite.session_new", atelier_id=atelier.id))

    if request.method == "POST":
        try:
            d_debut = datetime.strptime(request.form.get("date_debut", ""), "%Y-%m-%d").date()
            d_fin = datetime.strptime(request.form.get("date_fin", ""), "%Y-%m-%d").date()
        except (ValueError, TypeError):
            flash("Renseignez des dates de début et de fin valides.", "danger")
            return redirect(url_for("activite.session_bulk_new", atelier_id=atelier.id))
        if d_fin < d_debut:
            d_debut, d_fin = d_fin, d_debut

        jours = {int(j) for j in request.form.getlist("weekday") if str(j).isdigit() and 0 <= int(j) <= 6}
        if not jours:
            flash("Cochez au moins un jour de la semaine.", "danger")
            return redirect(url_for("activite.session_bulk_new", atelier_id=atelier.id))

        heure_debut = (request.form.get("heure_debut") or "").strip() or None
        heure_fin = (request.form.get("heure_fin") or "").strip() or None
        capacite_raw = request.form.get("capacite") or atelier.capacite_defaut
        capacite = int(capacite_raw) if capacite_raw else None
        eviter_doublons = request.form.get("skip_existing") == "1"

        existantes = set()
        if eviter_doublons:
            for (dte,) in db.session.query(SessionActivite.date_session).filter(
                SessionActivite.atelier_id == atelier.id,
                SessionActivite.is_deleted == False,  # noqa: E712
                SessionActivite.date_session.isnot(None),
            ).all():
                existantes.add(dte)

        crees, ignores = 0, 0
        MAX_SEANCES = 366
        d = d_debut
        gardefou = 0
        while d <= d_fin and crees < MAX_SEANCES:
            gardefou += 1
            if gardefou > 1000:
                break
            if d.weekday() in jours:
                if eviter_doublons and d in existantes:
                    ignores += 1
                else:
                    db.session.add(SessionActivite(
                        atelier_id=atelier.id,
                        secteur=atelier.secteur,
                        session_type="COLLECTIF",
                        date_session=d,
                        heure_debut=heure_debut,
                        heure_fin=heure_fin,
                        capacite=capacite,
                    ))
                    crees += 1
            d += timedelta(days=1)
        db.session.commit()

        msg = f"{crees} séance(s) créée(s)."
        if ignores:
            msg += f" {ignores} ignorée(s) (déjà existantes)."
        flash(msg, "success")
        return redirect(url_for("activite.sessions", atelier_id=atelier.id))

    return render_template("activite/session_bulk.html", secteur=secteur, atelier=atelier)


@bp.route("/session/<int:session_id>/edit-schedule", methods=["GET", "POST"])
@login_required
def session_edit_schedule(session_id: int):
    require_perm("ateliers:edit")(lambda: None)()
    """Autorise la correction de date/heure/capacité d'une session déjà émargée, avec traçabilité."""
    secteur = _user_secteur()
    s = db.get_or_404(SessionActivite, session_id)
    atelier = db.get_or_404(AtelierActivite, s.atelier_id)

    if s.is_deleted or atelier.is_deleted:
        flash("Cette séance (ou son atelier) est dans la corbeille.", "warning")
        return redirect(url_for("activite.sessions", atelier_id=atelier.id, corbeille=1))
    if not _session_est_accessible(s):
        return _deny_activity_access()

    if request.method == "POST":
        reason = (request.form.get("edit_reason") or "").strip()
        if len(reason) < 8:
            flash("Merci de préciser une raison (au moins 8 caractères) pour la traçabilité.", "danger")
            return redirect(url_for("activite.session_edit_schedule", session_id=s.id))

        old_date = s.rdv_date if s.session_type == "INDIVIDUEL_MENSUEL" else s.date_session
        old_start = s.rdv_debut if s.session_type == "INDIVIDUEL_MENSUEL" else s.heure_debut
        old_end = s.rdv_fin if s.session_type == "INDIVIDUEL_MENSUEL" else s.heure_fin

        if s.session_type == "INDIVIDUEL_MENSUEL":
            rdv_date = request.form.get("rdv_date")
            if not rdv_date:
                flash("Date RDV obligatoire.", "danger")
                return redirect(url_for("activite.session_edit_schedule", session_id=s.id))
            s.rdv_date = datetime.strptime(rdv_date, "%Y-%m-%d").date()
            s.rdv_debut = (request.form.get("rdv_debut") or "").strip() or None
            s.rdv_fin = (request.form.get("rdv_fin") or "").strip() or None
        else:
            date_session = request.form.get("date_session")
            if not date_session:
                flash("Date de séance obligatoire.", "danger")
                return redirect(url_for("activite.session_edit_schedule", session_id=s.id))
            s.date_session = datetime.strptime(date_session, "%Y-%m-%d").date()
            s.heure_debut = (request.form.get("heure_debut") or "").strip() or None
            s.heure_fin = (request.form.get("heure_fin") or "").strip() or None
            capacite = request.form.get("capacite")
            s.capacite = int(capacite) if capacite else None

        new_date = s.rdv_date if s.session_type == "INDIVIDUEL_MENSUEL" else s.date_session
        new_start = s.rdv_debut if s.session_type == "INDIVIDUEL_MENSUEL" else s.heure_debut
        new_end = s.rdv_fin if s.session_type == "INDIVIDUEL_MENSUEL" else s.heure_fin

        if old_date == new_date and old_start == new_start and old_end == new_end:
            flash("Aucun changement détecté sur date/heure.", "info")
            return redirect(url_for("activite.session_edit_schedule", session_id=s.id))

        log = SessionScheduleEditLog(
            session_id=s.id,
            atelier_id=atelier.id,
            secteur=s.secteur,
            old_date=old_date,
            old_start=old_start,
            old_end=old_end,
            new_date=new_date,
            new_start=new_start,
            new_end=new_end,
            reason=reason,
            edited_by=getattr(current_user, "id", None),
        )
        db.session.add(log)
        db.session.commit()
        flash("Date/heure de session mises à jour et tracées.", "success")
        return redirect(url_for("activite.emargement", session_id=s.id))

    edits = (
        SessionScheduleEditLog.query.filter_by(session_id=s.id)
        .order_by(SessionScheduleEditLog.edited_at.desc())
        .limit(30)
        .all()
    )
    return render_template(
        "activite/session_edit_schedule.html",
        secteur=secteur,
        atelier=atelier,
        session=s,
        edits=edits,
    )


# ------------------ Compétences (tag au niveau SESSION) ------------------


@bp.route("/session/<int:session_id>/skills", methods=["GET"])
@login_required
def session_skills(session_id: int):
    _require_any_perm("pedagogie:view", "pedagogie:edit")
    """Associer des compétences legacy (Referentiel/Competence) à une session.

    Important : cette page doit refléter exactement ce que l'émargement lit,
    donc on se base sur SessionActivite.competences + SessionActivite.modules.
    """
    secteur = _user_secteur()
    s = db.get_or_404(SessionActivite, session_id)
    atelier = db.get_or_404(AtelierActivite, s.atelier_id)

    if s.is_deleted or atelier.is_deleted:
        flash("Cette séance (ou son atelier) est dans la corbeille.", "warning")
        return redirect(url_for("activite.sessions", atelier_id=atelier.id, corbeille=1))
    if not _session_est_accessible(s):
        return _deny_activity_access()

    referentiel_id = request.args.get("referentiel_id", type=int)
    q = (request.args.get("q") or "").strip()

    referentiels = Referentiel.query.order_by(Referentiel.nom.asc()).all()
    ref_map = {ref.id: ref for ref in referentiels}
    if referentiel_id is None and referentiels:
        referentiel_id = referentiels[0].id

    current_direct = _sort_competences(list(getattr(s, "competences", []) or []))
    current_direct_ids = {c.id for c in current_direct}

    inherited_from_modules = []
    inherited_ids = set()
    for module in (getattr(s, "modules", []) or []):
        for comp in (getattr(module, "competences", []) or []):
            if comp.id not in inherited_ids:
                inherited_ids.add(comp.id)
                inherited_from_modules.append(comp)
    inherited_from_modules = _sort_competences(inherited_from_modules)

    current_merged = _collect_session_competences(s)
    current_merged_ids = {c.id for c in current_merged}

    module_source_map = {}
    for module in (getattr(s, "modules", []) or []):
        for comp in (getattr(module, "competences", []) or []):
            module_source_map.setdefault(comp.id, []).append(module.nom)
    for comp_id in list(module_source_map.keys()):
        module_source_map[comp_id] = sorted(set(module_source_map[comp_id]), key=lambda x: (x or '').lower())

    results = []
    if q:
        cq = Competence.query
        if referentiel_id:
            cq = cq.filter(Competence.referentiel_id == referentiel_id)
        like = f"%{q}%"
        cq = cq.filter((Competence.code.ilike(like)) | (Competence.nom.ilike(like)) | (Competence.description.ilike(like)))
        results = cq.order_by(Competence.code.asc(), Competence.nom.asc()).limit(80).all()

    return render_template(
        "activite/session_skills.html",
        secteur=secteur,
        atelier=atelier,
        session=s,
        referentiels=referentiels,
        ref_map=ref_map,
        referentiel_id=referentiel_id,
        q=q,
        current_direct=current_direct,
        current_direct_ids=current_direct_ids,
        inherited_from_modules=inherited_from_modules,
        inherited_ids=inherited_ids,
        current_merged=current_merged,
        current_merged_ids=current_merged_ids,
        module_source_map=module_source_map,
        results=results,
    )


@bp.route("/session/<int:session_id>/skills/add", methods=["POST"])
@login_required
def session_skill_add(session_id: int):
    require_perm("pedagogie:edit")(lambda: None)()
    s = db.get_or_404(SessionActivite, session_id)
    atelier = db.get_or_404(AtelierActivite, s.atelier_id)

    if s.is_deleted or atelier.is_deleted:
        flash("Cette séance (ou son atelier) est dans la corbeille.", "warning")
        return redirect(url_for("activite.sessions", atelier_id=atelier.id, corbeille=1))
    if not _session_est_accessible(s):
        return _deny_activity_access()

    competence_id = request.form.get("competence_id", type=int)
    if not competence_id:
        flash("Compétence manquante.", "warning")
        return redirect(url_for("activite.session_skills", session_id=s.id))

    comp = db.get_or_404(Competence, competence_id)
    existing_ids = {c.id for c in (getattr(s, "competences", []) or [])}
    if comp.id not in existing_ids:
        s.competences.append(comp)
        db.session.commit()
        flash("Compétence ajoutée à la session.", "success")
    else:
        flash("Déjà présente en direct sur la session.", "info")

    referentiel_id = request.form.get("referentiel_id", type=int)
    q = (request.form.get("q") or "").strip()
    return redirect(url_for("activite.session_skills", session_id=s.id, referentiel_id=referentiel_id, q=q))


@bp.route("/session/<int:session_id>/skills/remove", methods=["POST"])
@login_required
def session_skill_remove(session_id: int):
    require_perm("pedagogie:edit")(lambda: None)()
    s = db.get_or_404(SessionActivite, session_id)
    atelier = db.get_or_404(AtelierActivite, s.atelier_id)

    if s.is_deleted or atelier.is_deleted:
        flash("Cette séance (ou son atelier) est dans la corbeille.", "warning")
        return redirect(url_for("activite.sessions", atelier_id=atelier.id, corbeille=1))
    if not _session_est_accessible(s):
        return _deny_activity_access()

    competence_id = request.form.get("competence_id", type=int)
    if not competence_id:
        flash("Compétence manquante.", "warning")
        return redirect(url_for("activite.session_skills", session_id=s.id))

    remaining = [c for c in (getattr(s, "competences", []) or []) if c.id != competence_id]
    if len(remaining) != len(getattr(s, "competences", []) or []):
        s.competences = remaining
        db.session.commit()
        flash("Compétence retirée de la séance.", "success")
    else:
        flash("Cette compétence n'était pas ajoutée en direct sur la session.", "info")

    referentiel_id = request.form.get("referentiel_id", type=int)
    q = (request.form.get("q") or "").strip()
    return redirect(url_for("activite.session_skills", session_id=s.id, referentiel_id=referentiel_id, q=q))


# ------------------ Évaluation (grille batch) ------------------


@bp.route("/session/<int:session_id>/evaluation_batch", methods=["GET", "POST"])
@login_required
def evaluation_batch(session_id: int):
    _require_any_perm("emargement:view", "pedagogie:view")
    if request.method == "POST":
        require_perm("pedagogie:edit")(lambda: None)()
    s = db.get_or_404(SessionActivite, session_id)
    atelier = db.get_or_404(AtelierActivite, s.atelier_id)
    if s.is_deleted or atelier.is_deleted:
        flash("Cette séance (ou son atelier) est dans la corbeille.", "warning")
        return redirect(url_for("activite.sessions", atelier_id=atelier.id, corbeille=1))
    if not _session_est_accessible(s):
        return _deny_activity_access()

    presences = PresenceActivite.query.filter_by(session_id=session_id).all()
    participants = [p.participant for p in presences]
    competences = _collect_session_competences(s)

    if request.method == "POST":
        eval_date = s.rdv_date or s.date_session or date.today()
        updates = 0
        for participant in participants:
            for comp in competences:
                key = f"etat_{participant.id}_{comp.id}"
                raw = request.form.get(key)
                if raw is None or raw == "":
                    continue
                try:
                    etat_value = int(raw)
                except ValueError:
                    continue
                ev = Evaluation.query.filter_by(participant_id=participant.id, competence_id=comp.id, session_id=s.id).first()
                if not ev:
                    ev = Evaluation(
                        participant_id=participant.id,
                        competence_id=comp.id,
                        session_id=s.id,
                        user_id=current_user.id,
                        etat=etat_value,
                        date_evaluation=eval_date,
                    )
                    db.session.add(ev)
                else:
                    ev.etat = etat_value
                    ev.user_id = current_user.id
                    ev.date_evaluation = eval_date
                updates += 1
        db.session.commit()
        flash(f"{updates} évaluations enregistrées.", "success")
        return redirect(url_for("activite.evaluation_batch", session_id=s.id))

    existing = Evaluation.query.filter_by(session_id=s.id).all()
    eval_map = {(e.participant_id, e.competence_id): e.etat for e in existing}
    return render_template("activite/evaluation_batch.html", session=s, atelier=atelier, participants=participants, competences=competences, eval_map=eval_map)




@bp.route("/session/<int:session_id>/toggle-evenement", methods=["POST"])
@login_required
def session_toggle_evenement(session_id: int):
    """Marque / démarque une séance comme événementielle (volet SENACS)."""
    require_perm("ateliers:edit")(lambda: None)()
    s = db.get_or_404(SessionActivite, session_id)
    if not _session_est_accessible(s):
        return _deny_activity_access()
    s.est_evenement = not bool(s.est_evenement)
    db.session.commit()
    flash("Séance marquée comme événement (comptée dans le volet événementiel SENACS)."
          if s.est_evenement else "Séance retirée du volet événementiel.",
          "success" if s.est_evenement else "warning")
    return redirect(url_for("activite.emargement", session_id=s.id))
