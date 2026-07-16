import os

from app.utils.dates import utcnow

from flask import (
    request,
    redirect,
    url_for,
    flash,
    current_app,
    send_file,
    abort,
)
from werkzeug.utils import secure_filename
from flask_login import login_required

from app.extensions import db
from app.models import (
    AtelierActivite,
    SessionActivite,
    AtelierCapaciteMois,
    ArchiveEmargement,
)

from ..rbac import require_perm
from . import bp
from .services.docx_utils import (
    generate_collectif_docx_pdf,
    generate_individuel_mensuel_docx,
    finalize_individuel_mensuel_pdf,
)
from .services.mail_utils import send_email_with_attachment
from app.activite.helpers import (
    _best_archive_path,
    _can_access_activity_secteur,
    _consumption_period_request_args,
    _deny_activity_access,
    _redirect_emargement_with_period,
)



# ------------------ Archives collectives ------------------


@bp.route("/session/<int:session_id>/generate_collectif")
@login_required
def generate_collectif(session_id: int):
    require_perm("ateliers:edit")(lambda: None)()
    s = db.get_or_404(SessionActivite, session_id)
    atelier = db.get_or_404(AtelierActivite, s.atelier_id)
    if s.session_type != "COLLECTIF":
        flash("Uniquement pour les séances collectives.", "warning")
        return _redirect_emargement_with_period(session_id)
    if not _can_access_activity_secteur(s.secteur):
        return _deny_activity_access()

    conso_period_args = _consumption_period_request_args()
    out_docx, out_pdf = generate_collectif_docx_pdf(
        app=current_app,
        atelier=atelier,
        session=s,
        period_mode=conso_period_args["period_mode"],
        period_start=conso_period_args["period_start"],
        period_end=conso_period_args["period_end"],
    )

    annee = (s.date_session.year if s.date_session else utcnow().year)
    mois = (s.date_session.month if s.date_session else utcnow().month)
    arch = ArchiveEmargement.query.filter_by(atelier_id=atelier.id, session_id=s.id, annee=annee, mois=mois).first()
    if not arch:
        arch = ArchiveEmargement(secteur=atelier.secteur, atelier_id=atelier.id, session_id=s.id, annee=annee, mois=mois)
        db.session.add(arch)
    arch.docx_path = out_docx
    arch.pdf_path = out_pdf
    arch.status = "locked"
    db.session.commit()

    if out_pdf and os.path.exists(out_pdf):
        return send_file(out_pdf, as_attachment=True)
    if out_docx and os.path.exists(out_docx):
        return send_file(out_docx, as_attachment=True)
    flash("Génération échouée.", "danger")
    return _redirect_emargement_with_period(session_id)


@bp.route("/session/<int:session_id>/archive/<string:kind>")
@login_required
def download_collectif_archive(session_id: int, kind: str):
    require_perm("emargement:view")(lambda: None)()
    if kind not in {"docx", "pdf"}:
        abort(404)

    s = db.get_or_404(SessionActivite, session_id)
    atelier = db.get_or_404(AtelierActivite, s.atelier_id)

    if not _can_access_activity_secteur(s.secteur):
        return _deny_activity_access()

    conso_period_args = _consumption_period_request_args()
    period_requested = any([
        request.args.get("conso_period_mode"),
        request.args.get("conso_period_start"),
        request.args.get("conso_period_end"),
    ])
    if period_requested:
        out_docx, out_pdf = generate_collectif_docx_pdf(
            app=current_app,
            atelier=atelier,
            session=s,
            period_mode=conso_period_args["period_mode"],
            period_start=conso_period_args["period_start"],
            period_end=conso_period_args["period_end"],
        )
        direct_path = out_pdf if kind == "pdf" else out_docx
        if direct_path and os.path.exists(direct_path):
            return send_file(direct_path, as_attachment=True)
        flash("Document introuvable : génération impossible (LibreOffice ?).", "warning")
        return _redirect_emargement_with_period(session_id)

    annee = (s.date_session.year if s.date_session else utcnow().year)
    mois = (s.date_session.month if s.date_session else utcnow().month)

    arch = ArchiveEmargement.query.filter_by(atelier_id=atelier.id, session_id=s.id, annee=annee, mois=mois).first()
    if not arch:
        arch = ArchiveEmargement(secteur=atelier.secteur, atelier_id=atelier.id, session_id=s.id, annee=annee, mois=mois)
        db.session.add(arch)

    need_pdf = (kind == "pdf")

    best_docx = arch.corrected_docx_path or arch.docx_path
    if not best_docx or not os.path.exists(best_docx):
        out_docx, _ = generate_collectif_docx_pdf(app=current_app, atelier=atelier, session=s)
        arch.docx_path = out_docx

    best_pdf = arch.corrected_pdf_path or arch.pdf_path
    if need_pdf and (not best_pdf or not os.path.exists(best_pdf)):
        out_docx, out_pdf = generate_collectif_docx_pdf(app=current_app, atelier=atelier, session=s)
        arch.docx_path = out_docx
        arch.pdf_path = out_pdf

    db.session.commit()

    path = _best_archive_path(arch, kind)
    if path and os.path.exists(path):
        return send_file(path, as_attachment=True)

    flash("Document introuvable : génération impossible (LibreOffice ?).", "warning")
    return _redirect_emargement_with_period(session_id)


@bp.route("/session/<int:session_id>/archive/upload", methods=["POST"])
@login_required
def upload_collectif_corrected(session_id: int):
    require_perm("ateliers:edit")(lambda: None)()
    s = db.get_or_404(SessionActivite, session_id)
    atelier = db.get_or_404(AtelierActivite, s.atelier_id)
    if not _can_access_activity_secteur(s.secteur):
        return _deny_activity_access()

    f = request.files.get("file")
    if not f or not f.filename:
        flash("Aucun fichier.", "warning")
        return _redirect_emargement_with_period(session_id)

    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in {".docx", ".pdf"}:
        flash("Uniquement .docx ou .pdf", "warning")
        return _redirect_emargement_with_period(session_id)

    annee = (s.date_session.year if s.date_session else utcnow().year)
    mois = (s.date_session.month if s.date_session else utcnow().month)
    arch = ArchiveEmargement.query.filter_by(atelier_id=atelier.id, session_id=s.id, annee=annee, mois=mois).first()
    if not arch:
        arch = ArchiveEmargement(secteur=atelier.secteur, atelier_id=atelier.id, session_id=s.id, annee=annee, mois=mois)
        db.session.add(arch)
        db.session.commit()

    base_doc = arch.docx_path or ""
    if base_doc and os.path.exists(base_doc):
        base_dir = os.path.dirname(base_doc)
    else:
        base_dir = os.path.join(current_app.instance_path, "archives_emargements")
        os.makedirs(base_dir, exist_ok=True)

    safe = secure_filename(os.path.splitext(f.filename)[0])
    out_name = f"CORRIGE__{safe}{ext}"
    out_path = os.path.join(base_dir, out_name)
    f.save(out_path)

    if ext == ".docx":
        arch.corrected_docx_path = out_path
    else:
        arch.corrected_pdf_path = out_path
    db.session.commit()

    flash("Version corrigée enregistrée.", "success")
    return _redirect_emargement_with_period(session_id)


@bp.route("/session/<int:session_id>/archive/email", methods=["POST"])
@login_required
def email_collectif_archive(session_id: int):
    require_perm("ateliers:edit")(lambda: None)()
    s = db.get_or_404(SessionActivite, session_id)
    atelier = db.get_or_404(AtelierActivite, s.atelier_id)
    if not _can_access_activity_secteur(s.secteur):
        return _deny_activity_access()

    to = (request.form.get("to") or "").strip()
    if not to:
        flash("Email destinataire manquant.", "warning")
        return _redirect_emargement_with_period(session_id)

    conso_period_args = _consumption_period_request_args()
    period_requested = any([
        request.form.get("conso_period_mode"),
        request.form.get("conso_period_start"),
        request.form.get("conso_period_end"),
    ])

    annee = (s.date_session.year if s.date_session else utcnow().year)
    mois = (s.date_session.month if s.date_session else utcnow().month)
    arch = ArchiveEmargement.query.filter_by(atelier_id=atelier.id, session_id=s.id, annee=annee, mois=mois).first()

    if period_requested:
        out_docx, out_pdf = generate_collectif_docx_pdf(
            app=current_app,
            atelier=atelier,
            session=s,
            period_mode=conso_period_args["period_mode"],
            period_start=conso_period_args["period_start"],
            period_end=conso_period_args["period_end"],
        )
        attachment = out_pdf or out_docx
    else:
        if not arch or not arch.docx_path:
            out_docx, out_pdf = generate_collectif_docx_pdf(app=current_app, atelier=atelier, session=s)
            if not arch:
                arch = ArchiveEmargement(secteur=atelier.secteur, atelier_id=atelier.id, session_id=s.id, annee=annee, mois=mois)
                db.session.add(arch)
            arch.docx_path = out_docx
            arch.pdf_path = out_pdf
            db.session.commit()

        attachment = _best_archive_path(arch, "pdf") or _best_archive_path(arch, "docx")
    if not attachment or not os.path.exists(attachment):
        flash("Aucun document à envoyer.", "warning")
        return _redirect_emargement_with_period(session_id)

    cfg = current_app.config
    if not cfg.get("MAIL_HOST") or not cfg.get("MAIL_SENDER"):
        flash("SMTP non configuré (MAIL_HOST/MAIL_SENDER).", "warning")
        return _redirect_emargement_with_period(session_id)

    subject = request.form.get("subject") or f"Émargement - {atelier.secteur} - {atelier.nom} - {annee}-{mois:02d}"
    body = request.form.get("body") or "Ci-joint le document d'émargement."

    try:
        send_email_with_attachment(
            host=cfg.get("MAIL_HOST"),
            port=int(cfg.get("MAIL_PORT", 587)),
            username=cfg.get("MAIL_USERNAME") or None,
            password=cfg.get("MAIL_PASSWORD") or None,
            use_tls=bool(cfg.get("MAIL_USE_TLS", True)),
            sender=cfg.get("MAIL_SENDER"),
            to=to,
            subject=subject,
            body=body,
            attachment_path=attachment,
        )
        arch.last_emailed_to = to
        arch.last_emailed_at = utcnow()
        db.session.commit()
        flash("Email envoyé.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Échec envoi mail : {e}", "danger")
    return _redirect_emargement_with_period(session_id)


# ------------------ Archives individuels (mensuel) ------------------


@bp.route("/atelier/<int:atelier_id>/individuel/<int:annee>/<int:mois>/docx")
@login_required
def download_individuel_docx(atelier_id: int, annee: int, mois: int):
    require_perm("emargement:view")(lambda: None)()
    return download_individuel_archive(atelier_id=atelier_id, annee=annee, mois=mois, kind="docx")


@bp.route("/atelier/<int:atelier_id>/individuel/<int:annee>/<int:mois>/archive/<string:kind>")
@login_required
def download_individuel_archive(atelier_id: int, annee: int, mois: int, kind: str):
    require_perm("emargement:view")(lambda: None)()
    if kind not in {"docx", "pdf"}:
        abort(404)

    atelier = db.get_or_404(AtelierActivite, atelier_id)

    if atelier.type_atelier != "INDIVIDUEL_MENSUEL":
        flash("Atelier non individuel mensuel.", "warning")
        return redirect(url_for("activite.index"))

    if not _can_access_activity_secteur(atelier.secteur):
        return _deny_activity_access()

    arch = ArchiveEmargement.query.filter_by(atelier_id=atelier.id, session_id=None, annee=annee, mois=mois).first()
    if not arch:
        arch = ArchiveEmargement(secteur=atelier.secteur, atelier_id=atelier.id, session_id=None, annee=annee, mois=mois)
        db.session.add(arch)

    need_pdf = (kind == "pdf")

    best_docx = arch.corrected_docx_path or arch.docx_path
    if not best_docx or not os.path.exists(best_docx):
        arch.docx_path = generate_individuel_mensuel_docx(app=current_app, atelier=atelier, annee=annee, mois=mois)

    best_pdf = arch.corrected_pdf_path or arch.pdf_path
    if need_pdf and (not best_pdf or not os.path.exists(best_pdf)):
        arch.pdf_path = finalize_individuel_mensuel_pdf(app=current_app, atelier=atelier, annee=annee, mois=mois)

    db.session.commit()

    path = _best_archive_path(arch, kind)
    if path and os.path.exists(path):
        return send_file(path, as_attachment=True)

    flash("Document introuvable : génération échouée.", "warning")
    return redirect(url_for("activite.sessions", atelier_id=atelier_id))


@bp.route("/atelier/<int:atelier_id>/individuel/<int:annee>/<int:mois>/archive/upload", methods=["POST"])
@login_required
def upload_individuel_corrected(atelier_id: int, annee: int, mois: int):
    require_perm("ateliers:edit")(lambda: None)()
    atelier = db.get_or_404(AtelierActivite, atelier_id)
    if atelier.type_atelier != "INDIVIDUEL_MENSUEL":
        flash("Atelier non individuel mensuel.", "warning")
        return redirect(url_for("activite.index"))
    if not _can_access_activity_secteur(atelier.secteur):
        return _deny_activity_access()

    f = request.files.get("file")
    if not f or not f.filename:
        flash("Aucun fichier.", "warning")
        return redirect(url_for("activite.sessions", atelier_id=atelier_id))
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in {".docx", ".pdf"}:
        flash("Uniquement .docx ou .pdf", "warning")
        return redirect(url_for("activite.sessions", atelier_id=atelier_id))

    arch = ArchiveEmargement.query.filter_by(atelier_id=atelier.id, session_id=None, annee=annee, mois=mois).first()
    if not arch:
        arch = ArchiveEmargement(secteur=atelier.secteur, atelier_id=atelier.id, session_id=None, annee=annee, mois=mois)
        db.session.add(arch)
        db.session.commit()

    base_doc = arch.docx_path or ""
    if base_doc and os.path.exists(base_doc):
        base_dir = os.path.dirname(base_doc)
    else:
        base_dir = os.path.join(current_app.instance_path, "archives_emargements")
        os.makedirs(base_dir, exist_ok=True)

    safe = secure_filename(os.path.splitext(f.filename)[0])
    out_name = f"CORRIGE__{safe}{ext}"
    out_path = os.path.join(base_dir, out_name)
    f.save(out_path)

    if ext == ".docx":
        arch.corrected_docx_path = out_path
    else:
        arch.corrected_pdf_path = out_path
    db.session.commit()
    flash("Version corrigée enregistrée.", "success")
    return redirect(url_for("activite.sessions", atelier_id=atelier_id))


@bp.route("/atelier/<int:atelier_id>/individuel/<int:annee>/<int:mois>/archive/email", methods=["POST"])
@login_required
def email_individuel_archive(atelier_id: int, annee: int, mois: int):
    require_perm("ateliers:edit")(lambda: None)()
    atelier = db.get_or_404(AtelierActivite, atelier_id)
    if atelier.type_atelier != "INDIVIDUEL_MENSUEL":
        flash("Atelier non individuel mensuel.", "warning")
        return redirect(url_for("activite.index"))
    if not _can_access_activity_secteur(atelier.secteur):
        return _deny_activity_access()

    to = (request.form.get("to") or "").strip()
    if not to:
        flash("Email destinataire manquant.", "warning")
        return redirect(url_for("activite.sessions", atelier_id=atelier_id))

    arch = ArchiveEmargement.query.filter_by(atelier_id=atelier.id, session_id=None, annee=annee, mois=mois).first()
    if not arch or not arch.docx_path:
        out_docx = generate_individuel_mensuel_docx(app=current_app, atelier=atelier, annee=annee, mois=mois)
        out_pdf = finalize_individuel_mensuel_pdf(app=current_app, atelier=atelier, annee=annee, mois=mois)
        if not arch:
            arch = ArchiveEmargement(secteur=atelier.secteur, atelier_id=atelier.id, session_id=None, annee=annee, mois=mois)
            db.session.add(arch)
        arch.docx_path = out_docx
        arch.pdf_path = out_pdf
        db.session.commit()

    attachment = _best_archive_path(arch, "pdf") or _best_archive_path(arch, "docx")
    if not attachment or not os.path.exists(attachment):
        flash("Aucun document à envoyer.", "warning")
        return redirect(url_for("activite.sessions", atelier_id=atelier_id))

    cfg = current_app.config
    if not cfg.get("MAIL_HOST") or not cfg.get("MAIL_SENDER"):
        flash("SMTP non configuré (MAIL_HOST/MAIL_SENDER).", "warning")
        return redirect(url_for("activite.sessions", atelier_id=atelier_id))

    subject = request.form.get("subject") or f"Émargement - {atelier.secteur} - {atelier.nom} - {annee}-{mois:02d}"
    body = request.form.get("body") or "Ci-joint le document d'émargement."

    try:
        send_email_with_attachment(
            host=cfg.get("MAIL_HOST"),
            port=int(cfg.get("MAIL_PORT", 587)),
            username=cfg.get("MAIL_USERNAME") or None,
            password=cfg.get("MAIL_PASSWORD") or None,
            use_tls=bool(cfg.get("MAIL_USE_TLS", True)),
            sender=cfg.get("MAIL_SENDER"),
            to=to,
            subject=subject,
            body=body,
            attachment_path=attachment,
        )
        arch.last_emailed_to = to
        arch.last_emailed_at = utcnow()
        db.session.commit()
        flash("Email envoyé.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Échec envoi mail : {e}", "danger")
    return redirect(url_for("activite.sessions", atelier_id=atelier_id))


@bp.route("/atelier/<int:atelier_id>/individuel/<int:annee>/<int:mois>/finalize")
@login_required
def finalize_individuel(atelier_id: int, annee: int, mois: int):
    require_perm("ateliers:edit")(lambda: None)()
    atelier = db.get_or_404(AtelierActivite, atelier_id)
    if atelier.type_atelier != "INDIVIDUEL_MENSUEL":
        flash("Atelier non individuel mensuel.", "warning")
        return redirect(url_for("activite.index"))
    if not _can_access_activity_secteur(atelier.secteur):
        return _deny_activity_access()

    out_docx = generate_individuel_mensuel_docx(app=current_app, atelier=atelier, annee=annee, mois=mois)
    out_pdf = finalize_individuel_mensuel_pdf(app=current_app, atelier=atelier, annee=annee, mois=mois)

    cap = AtelierCapaciteMois.query.filter_by(atelier_id=atelier.id, annee=annee, mois=mois).first()
    if cap:
        cap.locked = True

    arch = ArchiveEmargement.query.filter_by(atelier_id=atelier.id, session_id=None, annee=annee, mois=mois).first()
    if not arch:
        arch = ArchiveEmargement(secteur=atelier.secteur, atelier_id=atelier.id, session_id=None, annee=annee, mois=mois)
        db.session.add(arch)
    arch.docx_path = out_docx
    arch.pdf_path = out_pdf
    arch.status = "locked" if out_pdf else "open"
    db.session.commit()

    if out_pdf and os.path.exists(out_pdf):
        return send_file(out_pdf, as_attachment=True)
    if out_docx and os.path.exists(out_docx):
        flash("PDF non généré (LibreOffice manquant ?). Téléchargement du DOCX.", "warning")
        return send_file(out_docx, as_attachment=True)
    flash("Finalisation échouée.", "danger")
    return redirect(url_for("activite.sessions", atelier_id=atelier_id))
