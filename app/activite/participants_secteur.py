from datetime import datetime, date


from flask import (
    render_template,
    request,
    redirect,
    url_for,
    flash,
)
from flask_login import current_user, login_required
from sqlalchemy import or_, false

from app.extensions import db
from app.models import (
    SessionActivite,
    Participant,
    PresenceActivite,
    Quartier,
)

from ..rbac import require_perm
from . import bp
from app.services.quartiers import normalize_quartier_for_ville
from app.activite.helpers import (
    _attestation_document_fields,
    _attestation_options_from_request,
    _attestation_participant_options,
    _attestation_selected_participant_ids,
    _available_secteur_labels,
    _build_attestation_summaries,
    _can_access_activity_secteur,
    _deny_activity_access,
    _has_all_secteurs_scope,
    _is_admin_global,
    _parse_iso_date_arg,
    _require_any_perm,
    _resolve_attestation_secteur,
    _user_secteur,
)



# ------------------ Gestion Participants (par secteur) ------------------


@bp.route("/participants")
@login_required
def participants():
    _require_any_perm("participants:view", "participants:view_all")
    """Liste des participants ayant au moins une présence dans le secteur."""
    secteur = _user_secteur()
    q = (request.args.get("q") or "").strip()

    base = (
        db.session.query(Participant)
        .join(PresenceActivite, PresenceActivite.participant_id == Participant.id)
        .join(SessionActivite, SessionActivite.id == PresenceActivite.session_id)
    )
    if secteur:
        base = base.filter(SessionActivite.secteur == secteur)
    elif not (_is_admin_global() or _has_all_secteurs_scope()):
        flash("Aucun secteur n'est associé à votre compte pour l'activité.", "warning")
        base = base.filter(false())
    base = base.distinct()
    if q:
        like = f"%{q.lower()}%"
        base = base.filter(
            or_(
                db.func.lower(Participant.nom).like(like),
                db.func.lower(Participant.prenom).like(like),
                db.func.lower(db.func.coalesce(Participant.email, "")).like(like),
                db.func.lower(db.func.coalesce(Participant.telephone, "")).like(like),
            )
        )

    participants_list = base.order_by(Participant.nom.asc(), Participant.prenom.asc()).limit(500).all()

    stats_map = {}
    participant_ids = [p.id for p in participants_list]
    if participant_ids:
        stats_query = (
            db.session.query(
                PresenceActivite.participant_id,
                db.func.count(PresenceActivite.id).label("visites"),
                db.func.max(PresenceActivite.created_at).label("last_seen"),
            )
            .join(SessionActivite, SessionActivite.id == PresenceActivite.session_id)
            .filter(PresenceActivite.participant_id.in_(participant_ids))
        )
        if secteur:
            stats_query = stats_query.filter(SessionActivite.secteur == secteur)
        elif not (_is_admin_global() or _has_all_secteurs_scope()):
            stats_query = stats_query.filter(false())
        rows = stats_query.group_by(PresenceActivite.participant_id).all()
    else:
        rows = []
    for r in rows:
        stats_map[r.participant_id] = {"visites": int(r.visites or 0), "last_seen": r.last_seen}

    return render_template(
        "activite/participants.html",
        secteur=secteur,
        q=q,
        participants=participants_list,
        stats_map=stats_map,
        is_admin_global=_is_admin_global(),
    )


@bp.route("/attestations", methods=["GET", "POST"])
@login_required
def attestations():
    _require_any_perm("participants:view", "participants:view_all")
    secteur = _resolve_attestation_secteur()
    if secteur and not _can_access_activity_secteur(secteur):
        return _deny_activity_access()
    if not secteur and not (_is_admin_global() or _has_all_secteurs_scope()):
        flash("Aucun secteur n'est associé à votre compte pour générer des attestations.", "warning")
        return redirect(url_for("activite.participants"))

    today = date.today()
    date_from = _parse_iso_date_arg(request.values.get("date_from"))
    date_to = _parse_iso_date_arg(request.values.get("date_to"))
    if request.method == "GET":
        if "date_from" not in request.args:
            date_from = date(today.year, 1, 1)
        if "date_to" not in request.args:
            date_to = today

    q = (request.values.get("q") or "").strip()
    selected_ids = _attestation_selected_participant_ids()
    options = _attestation_options_from_request()
    document = _attestation_document_fields()
    participant_options = _attestation_participant_options(secteur, q, selected_ids)
    secteur_options = _available_secteur_labels()
    can_choose_secteur = _is_admin_global() or _has_all_secteurs_scope()

    if request.method == "POST":
        if not selected_ids:
            flash("Sélectionnez au moins un participant.", "warning")
        else:
            attestations_payload = _build_attestation_summaries(
                selected_ids,
                secteur=secteur,
                date_from=date_from,
                date_to=date_to,
            )
            if not attestations_payload:
                flash("Aucun participant accessible avec ces filtres.", "warning")
            else:
                return render_template(
                    "activite/attestations_print.html",
                    attestations=attestations_payload,
                    secteur=secteur,
                    date_from=date_from,
                    date_to=date_to,
                    options=options,
                    document=document,
                    generated_on=today,
                )

    return render_template(
        "activite/attestations.html",
        secteur=secteur,
        secteur_options=secteur_options,
        can_choose_secteur=can_choose_secteur,
        q=q,
        selected_ids=selected_ids,
        participants=participant_options,
        date_from=date_from,
        date_to=date_to,
        options=options,
        document=document,
    )


@bp.route("/participant/<int:participant_id>/attestation")
@login_required
def participant_attestation(participant_id: int):
    _require_any_perm("participants:view", "participants:view_all")
    return redirect(url_for("activite.attestations", participant_ids=participant_id))


@bp.route("/participant/<int:participant_id>/edit", methods=["GET", "POST"])
@login_required
def participant_edit(participant_id: int):
    require_perm("participants:edit")(lambda: None)()
    secteur = _user_secteur()
    p = db.get_or_404(Participant, participant_id)

    # Ancienne route : même règle que la fiche (audit C3, revue des droits).
    # Une présence posée au kiosque et pas encore validée ouvre la lecture,
    # jamais la modification.
    from app.participants.routes import _can_edit_participant
    if not _can_edit_participant(p):
        flash("Accès refusé.", "danger")
        return redirect(url_for("activite.participants"))

    if request.method == "POST":
        p.nom = (request.form.get("nom") or p.nom).strip()
        p.prenom = (request.form.get("prenom") or p.prenom).strip()
        p.adresse = (request.form.get("adresse") or "").strip() or None
        from app.services.villes import normaliser as normaliser_ville

        p.ville = normaliser_ville(request.form.get("ville"))
        p.email = (request.form.get("email") or "").strip() or None
        p.telephone = (request.form.get("telephone") or "").strip() or None
        from app.services.genre import normaliser as normaliser_genre

        p.genre = normaliser_genre(request.form.get("genre"))
        p.type_public = (request.form.get("type_public") or p.type_public or "H").strip()[:2]

        dn = (request.form.get("date_naissance") or "").strip()
        if dn:
            try:
                p.date_naissance = datetime.strptime(dn, "%Y-%m-%d").date()
            except Exception:
                flash("Date de naissance invalide.", "warning")
        else:
            p.date_naissance = None

        qid_raw = (request.form.get("quartier_id") or "").strip()
        p.quartier_id = normalize_quartier_for_ville(p.ville, qid_raw)

        db.session.commit()
        flash("Le participant a bien été mis à jour.", "success")
        return redirect(url_for("activite.participants"))

    quartiers = Quartier.query.order_by(Quartier.ville.asc(), Quartier.nom.asc()).all()
    return render_template("activite/participant_form.html", secteur=secteur, p=p, quartiers=quartiers)


@bp.route("/participant/<int:participant_id>/anonymize", methods=["POST"])
@login_required
def participant_anonymize(participant_id: int):
    """Anonymise un participant : même traitement complet et journalisé que
    depuis la fiche (identité, contacts, géocodage, notes, fichiers…).

    L'ancienne version ne remplaçait que nom, prénom et coordonnées : la
    personne restait identifiable (date de naissance, adresse géocodée,
    titre de séjour, signature…) et rien n'était tracé.
    """
    require_perm("participants:anonymize")(lambda: None)()
    from app.services.access_scope import participant_destructible
    from app.services.audit import enregistrer
    from app.services.purge_rgpd import anonymiser_participant

    p = db.get_or_404(Participant, participant_id)
    if not participant_destructible(p):
        flash("Accès refusé : seule l'équipe du secteur qui a créé la fiche peut l'anonymiser.", "danger")
        return redirect(url_for("activite.participants"))

    anonymiser_participant(p, actor_id=getattr(current_user, "id", None))
    enregistrer("participant.anonymize", cible=f"participant #{p.id}")
    if request.form.get("strict") == "1" and _is_admin_global():
        p.genre = None
        p.date_naissance = None
        p.annee_naissance = None
        p.type_public = "H"
        p.quartier_id = None

    db.session.commit()
    flash("Le participant a bien été anonymisé.", "success")
    return redirect(url_for("activite.participants"))


@bp.route("/participant/<int:participant_id>/delete", methods=["POST"])
@login_required
def participant_delete(participant_id: int):
    """Ancienne suppression rapide, retirée : elle effaçait sans confirmation ni
    trace (et aurait emporté les paiements). La suppression définitive se fait
    depuis la fiche, avec confirmation par le nom et journal d'audit."""
    require_perm("participants:delete")(lambda: None)()
    db.get_or_404(Participant, participant_id)
    flash("La suppression définitive se fait depuis la fiche du participant "
          "(confirmation par le nom de famille).", "warning")
    return redirect(url_for("participants.edit_participant", participant_id=participant_id))


