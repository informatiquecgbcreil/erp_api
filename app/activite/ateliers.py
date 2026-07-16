from datetime import date, datetime, time as dt_time
from io import BytesIO

from app.utils.dates import utcnow

from flask import (
    render_template,
    request,
    redirect,
    url_for,
    flash,
    send_file,
)
from flask_login import login_required
from sqlalchemy import false

from app.extensions import db
from app.models import (
    AtelierActivite,
    SessionActivite,
    PresenceActivite,
    Competence,
    ArchiveEmargement,
)

from ..rbac import require_perm
from . import bp
from .services.emargement_models import (
    encode_storage_value,
)
from app.utils.delete_guard import commit_delete
from app.activite.helpers import (
    _atelier_model_context,
    _can_access_activity_secteur,
    _deny_activity_access,
    _ensure_activity_secteur_context,
    _ensure_seed_ateliers,
    _has_all_secteurs_scope,
    _is_admin_global,
    _load_referentiels,
    _require_any_perm,
    _safe_unlink,
    _user_secteur,
)



# ------------------ Home (ateliers) ------------------


@bp.route("/")
@login_required
def index():
    _require_any_perm("ateliers:view", "emargement:view")
    secteur = _user_secteur()
    _ensure_seed_ateliers(secteur)
    corbeille = (request.args.get("corbeille") == "1")
    show_inactive = (request.args.get("inactifs") == "1")
    if secteur:
        # Son secteur + les ateliers intersecteur (visibles par tous).
        q = AtelierActivite.query.filter(db.or_(
            AtelierActivite.secteur == secteur,
            AtelierActivite.est_intersecteur.is_(True),
        ))
    elif _is_admin_global() or _has_all_secteurs_scope():
        q = AtelierActivite.query
    else:
        flash("Aucun secteur n'est associé à votre compte pour l'activité.", "warning")
        q = AtelierActivite.query.filter(false())
    if corbeille:
        q = q.filter(AtelierActivite.is_deleted.is_(True))
    else:
        q = q.filter(AtelierActivite.is_deleted.is_(False))
        if not show_inactive:
            q = q.filter(AtelierActivite.is_active.is_(True))
    ateliers = q.order_by(AtelierActivite.nom.asc()).all()
    from app.activite.helpers import _available_secteur_labels
    peut_choisir_secteur = _is_admin_global() or _has_all_secteurs_scope()
    return render_template(
        "activite/index.html",
        secteur=secteur,
        ateliers=ateliers,
        is_admin_global=_is_admin_global(),
        corbeille=corbeille,
        show_inactive=show_inactive,
        peut_choisir_secteur=peut_choisir_secteur,
        secteurs_disponibles=_available_secteur_labels() if peut_choisir_secteur else [],
    )


# ------------------ Gestion Ateliers ------------------


@bp.route("/atelier/new", methods=["GET", "POST"])
@login_required
def atelier_new():
    require_perm("ateliers:edit")(lambda: None)()
    from app.activite.helpers import _available_secteur_labels

    peut_choisir_secteur = _is_admin_global() or _has_all_secteurs_scope()
    secteurs_disponibles = _available_secteur_labels() if peut_choisir_secteur else []
    secteur = _user_secteur()

    if request.method == "POST" and peut_choisir_secteur:
        # Un profil à portée globale (ex. direction sans secteur assigné)
        # choisit le secteur dans le formulaire, à chaque création.
        form_secteur = (request.form.get("secteur") or "").strip()
        if form_secteur and (not secteurs_disponibles or form_secteur in secteurs_disponibles):
            secteur = form_secteur

    # Atelier intersecteur : ouvert à tous les secteurs, et le créateur
    # (même borné à son secteur) choisit le secteur d'IMPUTATION des stats.
    est_intersecteur = request.method == "POST" and request.form.get("est_intersecteur") == "1"
    if est_intersecteur:
        tous_secteurs = _available_secteur_labels()
        secteur_stats = (request.form.get("secteur_stats") or "").strip()
        if secteur_stats and (not tous_secteurs or secteur_stats in tous_secteurs):
            secteur = secteur_stats

    if not secteur and not peut_choisir_secteur:
        flash("Aucun secteur n'est associé à votre compte.", "warning")
        return redirect(url_for("activite.index"))

    if request.method == "POST":
        nom = (request.form.get("nom") or "").strip()
        if not nom or not secteur:
            flash("Le nom de l'activité est obligatoire." if secteur else "Choisis le secteur de l'activité.", "danger")
            referentiels = _load_referentiels()
            return render_template(
                "activite/atelier_form.html",
                tous_secteurs=_available_secteur_labels(),
                secteur=secteur,
                atelier=None,
                referentiels=referentiels,
                selected_competences=set(),
                peut_choisir_secteur=peut_choisir_secteur,
                secteurs_disponibles=secteurs_disponibles,
                **_atelier_model_context(secteur),
            )

        type_atelier = request.form.get("type_atelier") or "COLLECTIF"
        description = (request.form.get("description") or "").strip() or None
        duree_defaut_minutes = request.form.get("duree_defaut_minutes") or None

        capacite_defaut = request.form.get("capacite_defaut") or None
        heures_dispo_defaut_mois = request.form.get("heures_dispo_defaut_mois") or None

        motifs = [m.strip() for m in (request.form.get("motifs") or "").split(";") if m.strip()]
        is_active = request.form.get("is_active") in {"1", "true", "on", "yes", "YES"}
        continuity_parent_id = request.form.get("continuity_parent_id", type=int)
        modele_collectif_key = (request.form.get("modele_emargement_collectif_key") or "").strip()
        modele_individuel_key = (request.form.get("modele_emargement_individuel_key") or "").strip()
        modele_collectif_key = (request.form.get("modele_emargement_collectif_key") or "").strip()
        modele_individuel_key = (request.form.get("modele_emargement_individuel_key") or "").strip()
        motifs_json = None
        if motifs:
            import json as _json
            motifs_json = _json.dumps(motifs, ensure_ascii=False)

        a = AtelierActivite(
            secteur=secteur,
            est_intersecteur=est_intersecteur,
            nom=nom,
            description=description,
            type_atelier=type_atelier,
            capacite_defaut=int(capacite_defaut) if capacite_defaut else None,
            heures_dispo_defaut_mois=float(heures_dispo_defaut_mois) if heures_dispo_defaut_mois else None,
            duree_defaut_minutes=int(duree_defaut_minutes) if duree_defaut_minutes else None,
            motifs_json=motifs_json,
            is_active=is_active,
            continuity_parent_id=continuity_parent_id,
            modele_docx_collectif=encode_storage_value(modele_collectif_key),
            modele_docx_individuel=encode_storage_value(modele_individuel_key),
        )
        competence_ids = [int(cid) for cid in request.form.getlist("competence_ids") if cid.isdigit()]
        if competence_ids:
            a.competences = Competence.query.filter(Competence.id.in_(competence_ids)).all()
        db.session.add(a)
        db.session.commit()
        flash("L'activité a bien été créée.", "success")
        return redirect(url_for("activite.index"))

    referentiels = _load_referentiels()
    ateliers_continuite = []
    if secteur:
        ateliers_continuite = AtelierActivite.query.filter(AtelierActivite.secteur == secteur, AtelierActivite.is_deleted.is_(False)).order_by(AtelierActivite.nom.asc()).all()
    return render_template(
        "activite/atelier_form.html",
                tous_secteurs=_available_secteur_labels(),
        secteur=secteur,
        atelier=None,
        referentiels=referentiels,
        selected_competences=set(),
        ateliers_continuite=ateliers_continuite,
        peut_choisir_secteur=peut_choisir_secteur,
        secteurs_disponibles=secteurs_disponibles,
        **_atelier_model_context(secteur),
    )


@bp.route("/atelier/<int:atelier_id>/edit", methods=["GET", "POST"])
@login_required
def atelier_edit(atelier_id: int):
    require_perm("ateliers:edit")(lambda: None)()
    secteur = _user_secteur()
    atelier = db.get_or_404(AtelierActivite, atelier_id)
    if atelier.is_deleted:
        flash("Cet atelier est dans la corbeille. Restaure-le pour le modifier.", "warning")
        return redirect(url_for("activite.index", corbeille=1))
    from app.activite.helpers import _atelier_est_accessible, _available_secteur_labels
    if not _atelier_est_accessible(atelier):
        return _deny_activity_access()

    if request.method == "POST":
        # Statut intersecteur + secteur d'imputation des statistiques.
        atelier.est_intersecteur = request.form.get("est_intersecteur") == "1"
        if atelier.est_intersecteur:
            tous_secteurs = _available_secteur_labels()
            secteur_stats = (request.form.get("secteur_stats") or "").strip()
            if secteur_stats and (not tous_secteurs or secteur_stats in tous_secteurs):
                atelier.secteur = secteur_stats

        atelier.nom = (request.form.get("nom") or atelier.nom).strip()
        atelier.description = (request.form.get("description") or "").strip() or None
        atelier.type_atelier = request.form.get("type_atelier") or atelier.type_atelier

        duree_defaut_minutes = request.form.get("duree_defaut_minutes") or None
        atelier.duree_defaut_minutes = int(duree_defaut_minutes) if duree_defaut_minutes else None

        capacite_defaut = request.form.get("capacite_defaut") or None
        atelier.capacite_defaut = int(capacite_defaut) if capacite_defaut else None

        heures_dispo_defaut_mois = request.form.get("heures_dispo_defaut_mois") or None
        atelier.heures_dispo_defaut_mois = float(heures_dispo_defaut_mois) if heures_dispo_defaut_mois else None

        motifs = [m.strip() for m in (request.form.get("motifs") or "").split(";") if m.strip()]
        is_active = request.form.get("is_active") in {"1", "true", "on", "yes", "YES"}
        continuity_parent_id = request.form.get("continuity_parent_id", type=int)
        modele_collectif_key = (request.form.get("modele_emargement_collectif_key") or "").strip()
        modele_individuel_key = (request.form.get("modele_emargement_individuel_key") or "").strip()
        if continuity_parent_id == atelier.id:
            continuity_parent_id = None
        atelier.is_active = is_active
        atelier.continuity_parent_id = continuity_parent_id
        if motifs:
            import json as _json
            atelier.motifs_json = _json.dumps(motifs, ensure_ascii=False)
        else:
            atelier.motifs_json = None

        atelier.modele_docx_collectif = encode_storage_value(modele_collectif_key, atelier.modele_docx_collectif)
        atelier.modele_docx_individuel = encode_storage_value(modele_individuel_key, atelier.modele_docx_individuel)

        competence_ids = [int(cid) for cid in request.form.getlist("competence_ids") if cid.isdigit()]
        if competence_ids:
            atelier.competences = Competence.query.filter(Competence.id.in_(competence_ids)).all()
        else:
            atelier.competences = []

        db.session.commit()
        flash("L'activité a bien été mise à jour.", "success")
        return redirect(url_for("activite.index"))

    motifs_str = "; ".join(atelier.motifs() or [])
    referentiels = _load_referentiels()
    selected_competences = {c.id for c in atelier.competences}
    ateliers_continuite = AtelierActivite.query.filter(AtelierActivite.secteur == atelier.secteur, AtelierActivite.is_deleted.is_(False), AtelierActivite.id != atelier.id).order_by(AtelierActivite.nom.asc()).all()
    return render_template(
        "activite/atelier_form.html",
                tous_secteurs=_available_secteur_labels(),
        secteur=secteur,
        atelier=atelier,
        motifs_str=motifs_str,
        referentiels=referentiels,
        selected_competences=selected_competences,
        ateliers_continuite=ateliers_continuite,
        **_atelier_model_context(atelier.secteur, atelier),
    )


@bp.route("/atelier/<int:atelier_id>/sessions")
@login_required
def sessions(atelier_id: int):
    _require_any_perm("ateliers:view", "emargement:view")
    secteur = _user_secteur()
    atelier = db.get_or_404(AtelierActivite, atelier_id)
    corbeille = (request.args.get("corbeille") == "1")
    if atelier.is_deleted and not corbeille:
        flash("Cet atelier est dans la corbeille.", "warning")
        return redirect(url_for("activite.index", corbeille=1))
    from app.activite.helpers import _atelier_est_accessible
    if not _atelier_est_accessible(atelier):
        return _deny_activity_access()

    q = SessionActivite.query.filter_by(atelier_id=atelier.id)
    if corbeille:
        q = q.filter(SessionActivite.is_deleted.is_(True))
    else:
        q = q.filter(SessionActivite.is_deleted.is_(False))
    q = q.order_by(SessionActivite.created_at.desc())
    sessions_list = q.limit(200).all()

    session_stats = []
    for s in sessions_list:
        nb = len(s.presences)
        cap = s.capacite or 0
        taux = None
        if s.session_type == "COLLECTIF" and cap > 0:
            taux = round((nb / cap) * 100, 1)
        session_stats.append((s, nb, cap, taux))

    nb_a_exporter_csat = (
        SessionActivite.query
        .filter_by(atelier_id=atelier.id, is_deleted=False)
        .filter(SessionActivite.exported_csat_at.is_(None))
        .count()
    )

    return render_template(
        "activite/sessions.html",
        secteur=secteur,
        atelier=atelier,
        sessions=session_stats,
        corbeille=corbeille,
        current_year=date.today().year,
        current_month=date.today().month,
        nb_a_exporter_csat=nb_a_exporter_csat,
    )


def _parse_hhmm(value: str | None) -> dt_time | None:
    if not value:
        return None
    try:
        h, m = str(value).split(":")[:2]
        return dt_time(int(h), int(m))
    except Exception:
        return None


@bp.route("/atelier/<int:atelier_id>/export-csat-sessions.xlsx")
@login_required
def export_csat_sessions(atelier_id: int):
    """Export des séances d'un atelier au format d'import « Sessions » du
    portail CSAT (Centres Sociaux Acteurs des Transitions) : évite la
    ressaisie manuelle des créneaux entre les deux outils.

    Colonnes attendues par CSAT : session_date, session_debut, session_fin
    (heure de début en texte, heure de fin en valeur horaire, comme dans le
    modèle d'import fourni par CSAT).

    Comme CSAT n'a pas d'API, on ne peut pas savoir ce qui y a déjà été saisi :
    on trace donc côté ERP (``exported_csat_at``) les séances déjà transmises,
    pour ne proposer par défaut que les NOUVELLES séances à chaque export et
    éviter de régénérer tout l'historique (et donc des doublons dans CSAT).
    Le paramètre ``tout=1`` permet de forcer un export complet si besoin.
    """
    from openpyxl import Workbook

    _require_any_perm("ateliers:view", "emargement:view")
    atelier = db.get_or_404(AtelierActivite, atelier_id)
    if not _can_access_activity_secteur(atelier.secteur):
        return _deny_activity_access()

    tout = request.args.get("tout") == "1"

    sessions_q = SessionActivite.query.filter_by(atelier_id=atelier.id, is_deleted=False)
    if not tout:
        sessions_q = sessions_q.filter(SessionActivite.exported_csat_at.is_(None))
    sessions_list = sessions_q.all()

    rows = []
    for s in sessions_list:
        if s.session_type == "COLLECTIF":
            eff_date, eff_debut, eff_fin = s.date_session, s.heure_debut, s.heure_fin
        else:
            eff_date, eff_debut, eff_fin = s.rdv_date, s.rdv_debut, s.rdv_fin
        if not eff_date:
            continue
        rows.append((s, eff_date, eff_debut or "", _parse_hhmm(eff_fin)))
    rows.sort(key=lambda r: r[1])

    if not rows:
        flash("Aucune nouvelle séance à exporter pour CSAT : tout est déjà à jour.", "info")
        return redirect(url_for("activite.sessions", atelier_id=atelier.id))

    wb = Workbook()
    ws = wb.active
    ws.title = "Sessions"
    ws.append(["session_date", "session_debut", "session_fin"])
    for _s, eff_date, eff_debut, eff_fin in rows:
        ws.append([datetime.combine(eff_date, dt_time()), eff_debut, eff_fin])

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)

    now = utcnow()
    for s, _eff_date, _eff_debut, _eff_fin in rows:
        s.exported_csat_at = now
    db.session.commit()

    filename = f"sessions_csat_{atelier.id}_{date.today().isoformat()}.xlsx"
    return send_file(
        buf,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# ------------------ Suppression / Restauration (soft-delete) ------------------


@bp.route("/atelier/<int:atelier_id>/delete", methods=["POST"])
@login_required
@require_perm("activite:delete")
def atelier_delete(atelier_id: int):
    require_perm("activite:delete")(lambda: None)()
    """Met un atelier (et ses sessions) en corbeille."""
    atelier = db.get_or_404(AtelierActivite, atelier_id)
    if not _can_access_activity_secteur(atelier.secteur):
        return _deny_activity_access()

    if atelier.is_deleted:
        flash("Atelier déjà dans la corbeille.", "info")
        return redirect(url_for("activite.index", corbeille=1))

    atelier.is_deleted = True
    atelier.deleted_at = utcnow()

    for s in SessionActivite.query.filter_by(atelier_id=atelier.id).all():
        s.is_deleted = True
        s.deleted_at = utcnow()
        s.kiosk_open = False
        s.kiosk_pin = None
        s.kiosk_token = None

    db.session.commit()
    flash("L'activité a été placée dans la corbeille. Elle peut être restaurée.", "success")
    return redirect(url_for("activite.index"))


@bp.route("/atelier/<int:atelier_id>/restore", methods=["POST"])
@login_required
def atelier_restore(atelier_id: int):
    require_perm("activite:restore")(lambda: None)()
    """Restaure un atelier (et ses sessions)."""
    atelier = db.get_or_404(AtelierActivite, atelier_id)
    if not _can_access_activity_secteur(atelier.secteur):
        return _deny_activity_access()

    if not atelier.is_deleted:
        flash("Atelier déjà actif.", "info")
        return redirect(url_for("activite.index"))

    atelier.is_deleted = False
    atelier.deleted_at = None
    for s in SessionActivite.query.filter_by(atelier_id=atelier.id).all():
        s.is_deleted = False
        s.deleted_at = None
    db.session.commit()
    flash("L'activité a bien été restaurée.", "success")
    return redirect(url_for("activite.index"))


@bp.route("/session/<int:session_id>/delete", methods=["POST"])
@login_required
@require_perm("activite:delete")
def session_delete(session_id: int):
    require_perm("activite:delete")(lambda: None)()
    """Met une session/RDV en corbeille."""
    s = db.get_or_404(SessionActivite, session_id)
    atelier = db.get_or_404(AtelierActivite, s.atelier_id)
    if not _can_access_activity_secteur(s.secteur):
        return _deny_activity_access()

    if s.is_deleted:
        flash("Session déjà dans la corbeille.", "info")
        return redirect(url_for("activite.sessions", atelier_id=atelier.id, corbeille=1))

    s.is_deleted = True
    s.deleted_at = utcnow()
    s.kiosk_open = False
    s.kiosk_pin = None
    s.kiosk_token = None
    db.session.commit()

    flash("Session placée dans la corbeille (restaurable).", "success")
    return redirect(url_for("activite.sessions", atelier_id=atelier.id))


@bp.route("/session/<int:session_id>/restore", methods=["POST"])
@login_required
def session_restore(session_id: int):
    require_perm("activite:restore")(lambda: None)()
    """Restaure une session/RDV."""
    s = db.get_or_404(SessionActivite, session_id)
    atelier = db.get_or_404(AtelierActivite, s.atelier_id)
    if not _can_access_activity_secteur(s.secteur):
        return _deny_activity_access()
    if atelier.is_deleted:
        flash("Restaure d'abord l'atelier.", "warning")
        return redirect(url_for("activite.index", corbeille=1))

    if not s.is_deleted:
        flash("Session déjà active.", "info")
        return redirect(url_for("activite.sessions", atelier_id=atelier.id))

    s.is_deleted = False
    s.deleted_at = None
    db.session.commit()
    flash("Session restaurée.", "success")
    return redirect(url_for("activite.sessions", atelier_id=atelier.id))


@bp.route("/session/<int:session_id>/purge", methods=["POST"])
@login_required
@require_perm("activite:purge")
def session_purge(session_id: int):
    require_perm("activite:purge")(lambda: None)()
    """Suppression définitive d'une session (nécessite qu'elle soit déjà en corbeille)."""
    s = db.get_or_404(SessionActivite, session_id)
    atelier = db.get_or_404(AtelierActivite, s.atelier_id)

    if not _can_access_activity_secteur(s.secteur):
        return _deny_activity_access()

    if not _is_admin_global() and not s.is_deleted:
        flash("Place d'abord la séance dans la corbeille avant suppression définitive.", "warning")
        return redirect(url_for("activite.sessions", atelier_id=atelier.id))

    presences = PresenceActivite.query.filter_by(session_id=s.id).all()
    signature_paths = [pr.signature_path for pr in presences if pr.signature_path]
    for pr in presences:
        db.session.delete(pr)

    archives = ArchiveEmargement.query.filter_by(session_id=s.id).all()
    archive_paths = []
    for a in archives:
        archive_paths.extend([p for p in [a.docx_path, a.pdf_path, a.corrected_docx_path, a.corrected_pdf_path] if p])
        db.session.delete(a)

    db.session.delete(s)
    if commit_delete(
        f"la session du {s.date_session}",
        "Session supprimée définitivement.",
        blocked_message="Impossible de supprimer définitivement cette session : elle est encore référencée ailleurs dans l'application.",
    ):
        for rel in signature_paths + archive_paths:
            _safe_unlink(rel)
    return redirect(url_for("activite.sessions", atelier_id=atelier.id, corbeille=1))


