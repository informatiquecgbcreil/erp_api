"""Coffre-fort de documents RH.

Un document n'est lisible que par la personne qui l'a déposé et par la liste
d'accès qu'elle a choisie — pas même par la direction si elle n'y figure pas.
Désigner le salarié CONCERNÉ (fiche de paie, contrat…) ajoute
automatiquement son compte à la liste d'accès.
"""
from __future__ import annotations

import os
from datetime import datetime, time, timedelta

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from werkzeug.utils import secure_filename

from app.extensions import db
from app.models import DocumentRh, DocumentRhAcces, Salarie, TypeDocumentRh, User
from app.rbac import require_perm
from app.services import espace_salarie as es
from app.services.audit import journaliser
from app.services.storage import send_media_file, get_upload_root
from app.utils.dates import utcnow

from . import bp
from .commun import erreur

TAILLE_MAX = 10 * 1024 * 1024


def _url_documents():
    return url_for("salaries.documents")


def _lisible(document: DocumentRh, user) -> bool:
    if document.depose_par_user_id == user.id:
        return True
    return any(a.user_id == user.id for a in document.acces)


@bp.route("/documents")
@login_required
@require_perm("salarie:espace")
def documents():
    acces = db.session.query(DocumentRhAcces.document_id).filter(DocumentRhAcces.user_id == current_user.id)
    q = DocumentRh.query.filter(db.or_(DocumentRh.depose_par_user_id == current_user.id, DocumentRh.id.in_(acces)))
    f_type = request.args.get("type_id", type=int)
    f_depose_par = request.args.get("depose_par", type=int)
    du, au = es.parse_date(request.args.get("du")), es.parse_date(request.args.get("au"))
    if f_type:
        q = q.filter(DocumentRh.type_id == f_type)
    if f_depose_par:
        q = q.filter(DocumentRh.depose_par_user_id == f_depose_par)
    if du:
        q = q.filter(DocumentRh.created_at >= datetime.combine(du, time.min))
    if au:
        q = q.filter(DocumentRh.created_at < datetime.combine(au + timedelta(days=1), time.min))
    items = q.order_by(DocumentRh.created_at.desc(), DocumentRh.id.desc()).limit(500).all()
    deposants = sorted({d.depose_par for d in items if d.depose_par}, key=lambda u: u.nom or "")
    return render_template(
        "salaries/documents.html", items=items, types=TypeDocumentRh.query.order_by(TypeDocumentRh.libelle).all(),
        deposants=deposants, f_type=f_type, f_depose_par=f_depose_par, du=du, au=au, es=es,
    )


@bp.route("/documents/deposer", methods=["GET", "POST"])
@login_required
@require_perm("salarie:espace")
def deposer_document():
    types = TypeDocumentRh.query.order_by(TypeDocumentRh.libelle).all()
    comptes = User.query.filter(User.actif.is_(True)).order_by(User.nom.asc()).all()
    salaries = Salarie.query.order_by(Salarie.nom.asc(), Salarie.prenom.asc()).all()
    if request.method == "GET":
        return render_template("salaries/deposer_document.html", types=types, comptes=comptes, salaries=salaries)

    url = url_for("salaries.deposer_document")
    type_doc = db.session.get(TypeDocumentRh, request.form.get("type_id", type=int) or 0)
    if type_doc is None:
        return erreur("Choisis le type de document.", url)
    fichier = request.files.get("fichier")
    if fichier is None or not fichier.filename:
        return erreur("Choisis un fichier.", url)
    extension = fichier.filename.rsplit(".", 1)[-1].lower() if "." in fichier.filename else ""
    if extension not in type_doc.liste_extensions:
        return erreur(f"« {type_doc.libelle} » accepte : {', '.join(type_doc.liste_extensions)}.", url)
    fichier.stream.seek(0, os.SEEK_END)
    taille = fichier.stream.tell()
    fichier.stream.seek(0)
    if taille > TAILLE_MAX:
        return erreur("Fichier trop lourd (10 Mo au plus).", url)

    salarie = db.session.get(Salarie, request.form.get("salarie_id", type=int) or 0)
    lecteurs = {int(v) for v in request.form.getlist("acces") if str(v).isdigit()}
    if salarie is not None and salarie.user_id:
        lecteurs.add(salarie.user_id)
    lecteurs.discard(current_user.id)
    valides = {u.id for u in User.query.filter(User.id.in_(lecteurs)).all()} if lecteurs else set()

    dossier = es.dossier_rh("coffre", utcnow().strftime("%Y"))
    nom_stocke = f"{current_user.id}_{utcnow().strftime('%Y%m%d%H%M%S%f')}_{secure_filename(fichier.filename) or 'document'}"
    fichier.save(str(dossier / nom_stocke))
    document = DocumentRh(
        depose_par_user_id=current_user.id, type_id=type_doc.id, salarie_id=salarie.id if salarie else None,
        nom_original=fichier.filename[:300], chemin=es.chemin_relatif(dossier / nom_stocke), taille=taille,
        mime=(fichier.mimetype or "")[:120] or None, note=(request.form.get("note") or "").strip()[:500] or None,
    )
    document.acces = [DocumentRhAcces(user_id=uid) for uid in sorted(valides)]
    db.session.add(document)
    db.session.flush()
    from app.services import courriels_rh
    courriels_rh.document_depose(document, current_user)
    db.session.commit()
    courriels_rh.expedier_en_attente()
    journaliser("rh.document_depose", cible=f"document_rh#{document.id}",
                details={"type": type_doc.code, "lecteurs": len(valides)})
    flash("Document déposé dans le coffre-fort.", "success")
    return redirect(_url_documents())


@bp.route("/documents/<int:document_id>/telecharger")
@login_required
@require_perm("salarie:espace")
def telecharger_document(document_id: int):
    document = db.get_or_404(DocumentRh, document_id)
    if not _lisible(document, current_user):
        abort(403)
    journaliser("rh.document_lu", cible=f"document_rh#{document.id}")
    reponse = send_media_file(document.chemin, as_attachment=True, download_name=document.nom_original)
    reponse.headers["Cache-Control"] = "private, no-store"
    return reponse


@bp.route("/documents/<int:document_id>/supprimer", methods=["POST"])
@login_required
@require_perm("salarie:espace")
def supprimer_document(document_id: int):
    """Seule la personne qui a déposé le document peut le retirer."""
    from app.services import file_cleanup

    document = db.get_or_404(DocumentRh, document_id)
    if document.depose_par_user_id != current_user.id:
        abort(403)
    file_cleanup.schedule(os.path.join(get_upload_root(), document.chemin))
    db.session.delete(document)
    db.session.commit()
    journaliser("rh.document_supprime", cible=f"document_rh#{document_id}")
    flash("Document retiré du coffre-fort.", "success")
    return redirect(_url_documents())


@bp.route("/documents/types", methods=["GET", "POST"])
@login_required
@require_perm("coffre:types")
def types_documents():
    url = url_for("salaries.types_documents")
    if request.method == "POST":
        code = secure_filename((request.form.get("code") or "").strip().lower())[:50]
        libelle = (request.form.get("libelle") or "").strip()[:120]
        extensions = ",".join(e.strip().lower().lstrip(".") for e in (request.form.get("extensions") or "").split(",")
                              if e.strip())
        if not (code and libelle and extensions):
            return erreur("Code, libellé et extensions sont obligatoires.", url)
        type_doc = TypeDocumentRh.query.filter_by(code=code).first()
        if type_doc is None:
            db.session.add(TypeDocumentRh(code=code, libelle=libelle, extensions=extensions[:300]))
        else:
            type_doc.libelle, type_doc.extensions = libelle, extensions[:300]
        db.session.commit()
        journaliser("rh.type_document", cible=code)
        flash("Type de document enregistré.", "success")
        return redirect(url)
    items = TypeDocumentRh.query.order_by(TypeDocumentRh.libelle).all()
    utilises = {tid for (tid,) in db.session.query(DocumentRh.type_id).distinct().all()}
    return render_template("salaries/types_documents.html", items=items, utilises=utilises)


@bp.route("/documents/types/<int:type_id>/supprimer", methods=["POST"])
@login_required
@require_perm("coffre:types")
def supprimer_type_document(type_id: int):
    type_doc = db.get_or_404(TypeDocumentRh, type_id)
    if DocumentRh.query.filter_by(type_id=type_doc.id).first() is not None:
        return erreur("Ce type est utilisé par des documents : il ne peut pas être supprimé.",
                      url_for("salaries.types_documents"))
    db.session.delete(type_doc)
    db.session.commit()
    flash("Type de document supprimé.", "success")
    return redirect(url_for("salaries.types_documents"))
