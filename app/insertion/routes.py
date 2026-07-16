from __future__ import annotations

from datetime import date

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func, or_
from sqlalchemy.orm import joinedload

from app.extensions import db
from app.models import (
    InsertionDiplomeRef,
    InsertionDispositifRef,
    InsertionNiveauRef,
    InsertionPrescripteurRef,
    InsertionTitreSejourTypeRef,
    Participant,
    ParticipantInsertionCertification,
    ParticipantInsertionParcours,
    ParticipantInsertionPositionnement,
    ParticipantInsertionProfile,
)
from app.services.insertion import (
    can_edit_insertion,
    can_manage_insertion_refs,
    can_view_insertion,
    can_view_insertion_sensitive,
)
from app.utils.delete_guard import commit_delete

bp = Blueprint("insertion", __name__, url_prefix="/insertion")

REFERENCE_MODELS = {
    "dispositifs": {
        "model": InsertionDispositifRef,
        "title": "Dispositifs",
        "singular": "dispositif",
        "description": "Gérez les dispositifs utilisés pour le suivi insertion et les feuilles d’émargement.",
    },
    "prescripteurs": {
        "model": InsertionPrescripteurRef,
        "title": "Prescripteurs",
        "singular": "prescripteur",
        "description": "Gérez les organismes prescripteurs utilisés dans le secteur insertion.",
    },
    "titres-sejour": {
        "model": InsertionTitreSejourTypeRef,
        "title": "Types de titre de séjour",
        "singular": "type de titre de séjour",
        "description": "Gérez les types de titre de séjour disponibles dans les parcours insertion.",
    },
    "diplomes": {
        "model": InsertionDiplomeRef,
        "title": "Diplômes linguistiques",
        "singular": "diplôme linguistique",
        "description": "Gérez les diplômes linguistiques proposés dans le module insertion.",
    },
    "niveaux": {
        "model": InsertionNiveauRef,
        "title": "Niveaux linguistiques",
        "singular": "niveau linguistique",
        "description": "Gérez les niveaux utilisés pour les positionnements et les certifications (A1, A2, B1, etc.).",
    },
}

POSITIONNEMENT_TYPES = [
    ("entree", "Entrée"),
    ("intermediaire", "Intermédiaire"),
    ("sortie", "Sortie"),
    ("externe", "Externe"),
]

CERTIFICATION_RESULTS = [
    ("obtenu", "Obtenu"),
    ("non_obtenu", "Non obtenu"),
    ("absent", "Absent"),
    ("en_attente", "En attente"),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_reference_meta(ref_type: str):
    meta = REFERENCE_MODELS.get(ref_type)
    if not meta:
        abort(404)
    return meta


def _ensure_refs_admin():
    if not can_manage_insertion_refs():
        abort(403)


def _ensure_sensitive_view():
    if not can_view_insertion_sensitive():
        abort(403)


def _ensure_sensitive_edit():
    if not (can_view_insertion_sensitive() and can_edit_insertion()):
        abort(403)


def _normalize_label(raw: str | None) -> str:
    return " ".join((raw or "").strip().split())


def _parse_date(raw: str | None) -> date | None:
    value = (raw or "").strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _bool_or_none(raw: str | None):
    if raw in (None, ""):
        return None
    return str(raw) == "1"


def _load_participant_for_insertion(participant_id: int) -> Participant:
    return (
        Participant.query.options(
            joinedload(Participant.insertion_profile),
            joinedload(Participant.insertion_parcours).joinedload(ParticipantInsertionParcours.dispositif),
            joinedload(Participant.insertion_parcours).joinedload(ParticipantInsertionParcours.prescripteur),
            joinedload(Participant.insertion_parcours).joinedload(ParticipantInsertionParcours.titre_sejour_type),
            joinedload(Participant.insertion_positionnements).joinedload(ParticipantInsertionPositionnement.niveau),
            joinedload(Participant.insertion_positionnements).joinedload(ParticipantInsertionPositionnement.parcours),
            joinedload(Participant.insertion_certifications).joinedload(ParticipantInsertionCertification.diplome),
            joinedload(Participant.insertion_certifications).joinedload(ParticipantInsertionCertification.niveau),
            joinedload(Participant.insertion_certifications).joinedload(ParticipantInsertionCertification.parcours),
            joinedload(Participant.quartier),
        ).get_or_404(participant_id)
    )


def _ref_options(model):
    return model.query.filter_by(is_active=True).order_by(model.sort_order.asc(), model.label.asc()).all()


def _parcours_options(participant: Participant):
    rows = sorted(
        list(participant.insertion_parcours or []),
        key=lambda row: ((row.date_entree or date.min), row.id or 0),
        reverse=True,
    )
    out = []
    for row in rows:
        dispositif = row.dispositif.label if row.dispositif else "Sans dispositif"
        dates = []
        if row.date_entree:
            dates.append(f"du {row.date_entree.strftime('%d/%m/%Y')}")
        if row.date_sortie:
            dates.append(f"au {row.date_sortie.strftime('%d/%m/%Y')}")
        suffix = f" ({' '.join(dates)})" if dates else ""
        out.append({"id": row.id, "label": f"{dispositif}{suffix}"})
    return out


def _positionnement_type_label(code: str | None) -> str:
    mapping = {k: v for k, v in POSITIONNEMENT_TYPES}
    return mapping.get(code or "", code or "—")


def _certification_result_label(code: str | None) -> str:
    mapping = {k: v for k, v in CERTIFICATION_RESULTS}
    return mapping.get(code or "", code or "—")


def _sorted_parcours(rows):
    return sorted(list(rows or []), key=lambda row: ((row.date_entree or date.min), row.id or 0), reverse=True)


def _sorted_positionnements(rows):
    return sorted(list(rows or []), key=lambda row: ((row.date_positionnement or date.min), row.id or 0), reverse=True)


def _sorted_certifications(rows):
    return sorted(list(rows or []), key=lambda row: ((row.date_passage or row.date_obtention or date.min), row.id or 0), reverse=True)


@bp.before_request
@login_required
def _guard_module():
    if not can_view_insertion():
        abort(403)


# ---------------------------------------------------------------------------
# Index / detail / edit participant
# ---------------------------------------------------------------------------

@bp.route("/")
def index():
    q = (request.args.get("q") or "").strip()

    participants_q = (
        Participant.query
        .outerjoin(ParticipantInsertionProfile, ParticipantInsertionProfile.participant_id == Participant.id)
        .outerjoin(ParticipantInsertionParcours, ParticipantInsertionParcours.participant_id == Participant.id)
        .outerjoin(ParticipantInsertionPositionnement, ParticipantInsertionPositionnement.participant_id == Participant.id)
        .outerjoin(ParticipantInsertionCertification, ParticipantInsertionCertification.participant_id == Participant.id)
        .options(joinedload(Participant.insertion_profile))
        .distinct()
    )

    participants_q = participants_q.filter(
        or_(
            ParticipantInsertionProfile.participant_id.isnot(None),
            ParticipantInsertionParcours.id.isnot(None),
            ParticipantInsertionPositionnement.id.isnot(None),
            ParticipantInsertionCertification.id.isnot(None),
        )
    )

    if q:
        like = f"%{q}%"
        participants_q = participants_q.filter(
            or_(Participant.nom.ilike(like), Participant.prenom.ilike(like), Participant.ville.ilike(like))
        )

    participants = participants_q.order_by(Participant.nom.asc(), Participant.prenom.asc()).limit(200).all()

    counts = {
        "profiles": ParticipantInsertionProfile.query.count(),
        "parcours": ParticipantInsertionParcours.query.count(),
        "positionnements": ParticipantInsertionPositionnement.query.count(),
        "certifications": ParticipantInsertionCertification.query.count(),
        "dispositifs": InsertionDispositifRef.query.count(),
        "prescripteurs": InsertionPrescripteurRef.query.count(),
        "titres": InsertionTitreSejourTypeRef.query.count(),
        "diplomes": InsertionDiplomeRef.query.count(),
        "niveaux": InsertionNiveauRef.query.count(),
    }

    return render_template(
        "insertion/index.html",
        participants=participants,
        q=q,
        counts=counts,
        can_manage_refs=can_manage_insertion_refs(),
        can_edit=can_edit_insertion() and can_view_insertion_sensitive(),
    )


@bp.route("/participants/<int:participant_id>")
def participant_detail(participant_id: int):
    _ensure_sensitive_view()
    participant = _load_participant_for_insertion(participant_id)
    return render_template(
        "insertion/participant_detail.html",
        participant=participant,
        can_manage_refs=can_manage_insertion_refs(),
        can_edit=can_edit_insertion(),
        positionnement_type_label=_positionnement_type_label,
        certification_result_label=_certification_result_label,
        parcours_rows=_sorted_parcours(participant.insertion_parcours),
        positionnement_rows=_sorted_positionnements(participant.insertion_positionnements),
        certification_rows=_sorted_certifications(participant.insertion_certifications),
    )


@bp.route("/participants/<int:participant_id>/edit", methods=["GET", "POST"])
def participant_edit(participant_id: int):
    _ensure_sensitive_edit()
    participant = _load_participant_for_insertion(participant_id)
    profile = participant.insertion_profile or ParticipantInsertionProfile(participant=participant)

    if request.method == "POST":
        pays_origine = _normalize_label(request.form.get("pays_origine")) or None
        cir_obtenu = _bool_or_none(request.form.get("cir_obtenu"))
        profile.pays_origine = pays_origine
        profile.cir_obtenu = cir_obtenu
        if not profile.created_by_user_id:
            profile.created_by_user_id = getattr(current_user, "id", None)
        profile.updated_by_user_id = getattr(current_user, "id", None)
        db.session.add(profile)
        db.session.commit()
        flash("Le profil insertion a bien été enregistré.", "success")
        return redirect(url_for("insertion.participant_detail", participant_id=participant.id))

    return render_template(
        "insertion/participant_edit.html",
        participant=participant,
        profile=profile,
        parcours_rows=_sorted_parcours(participant.insertion_parcours),
        positionnement_rows=_sorted_positionnements(participant.insertion_positionnements),
        certification_rows=_sorted_certifications(participant.insertion_certifications),
    )


# ---------------------------------------------------------------------------
# Parcours
# ---------------------------------------------------------------------------

@bp.route("/participants/<int:participant_id>/parcours/new", methods=["GET", "POST"])
def parcours_new(participant_id: int):
    _ensure_sensitive_edit()
    participant = _load_participant_for_insertion(participant_id)
    row = ParticipantInsertionParcours(participant=participant, is_active=True)
    return _render_parcours_form(participant, row, is_new=True)


@bp.route("/participants/<int:participant_id>/parcours/<int:parcours_id>/edit", methods=["GET", "POST"])
def parcours_edit(participant_id: int, parcours_id: int):
    _ensure_sensitive_edit()
    participant = _load_participant_for_insertion(participant_id)
    row = ParticipantInsertionParcours.query.filter_by(id=parcours_id, participant_id=participant.id).first_or_404()
    return _render_parcours_form(participant, row, is_new=False)


@bp.route("/participants/<int:participant_id>/parcours/<int:parcours_id>/delete", methods=["POST"])
def parcours_delete(participant_id: int, parcours_id: int):
    _ensure_sensitive_edit()
    row = ParticipantInsertionParcours.query.filter_by(id=parcours_id, participant_id=participant_id).first_or_404()
    db.session.delete(row)
    commit_delete(
        "le parcours insertion",
        "Le parcours insertion a bien été supprimé.",
        blocked_message="Impossible de supprimer ce parcours car il est encore utilisé ailleurs.",
    )
    return redirect(url_for("insertion.participant_detail", participant_id=participant_id))


def _render_parcours_form(participant: Participant, row: ParticipantInsertionParcours, is_new: bool):
    dispositifs = _ref_options(InsertionDispositifRef)
    prescripteurs = _ref_options(InsertionPrescripteurRef)
    titres = _ref_options(InsertionTitreSejourTypeRef)

    if request.method == "POST":
        row.dispositif_id = request.form.get("dispositif_id") or None
        row.prescripteur_id = request.form.get("prescripteur_id") or None
        row.titre_sejour_type_id = request.form.get("titre_sejour_type_id") or None
        row.date_debut_titre_sejour = _parse_date(request.form.get("date_debut_titre_sejour"))
        row.date_expiration_titre_sejour = _parse_date(request.form.get("date_expiration_titre_sejour"))
        row.date_entree = _parse_date(request.form.get("date_entree"))
        row.date_sortie = _parse_date(request.form.get("date_sortie"))
        row.is_active = bool(request.form.get("is_active"))
        if row.date_entree and row.date_sortie and row.date_sortie < row.date_entree:
            flash("La date de sortie ne peut pas être antérieure à la date d’entrée.", "danger")
        elif row.date_debut_titre_sejour and row.date_expiration_titre_sejour and row.date_expiration_titre_sejour < row.date_debut_titre_sejour:
            flash("La date d’expiration du titre ne peut pas être antérieure à sa date de début.", "danger")
        else:
            if is_new and not row.created_by_user_id:
                row.created_by_user_id = getattr(current_user, "id", None)
            row.updated_by_user_id = getattr(current_user, "id", None)
            row.legacy_source = False
            db.session.add(row)
            db.session.commit()
            if is_new:
                # Inscription : on s'assure que le stagiaire a un code apprenant
                # sur le portail (idempotent, non bloquant en cas d'erreur réseau).
                from app.services.portail_apprenants import assurer_code_portail
                assurer_code_portail(participant)
            flash("Le parcours insertion a bien été enregistré.", "success")
            return redirect(url_for("insertion.participant_detail", participant_id=participant.id))

    return render_template(
        "insertion/participant_parcours_form.html",
        participant=participant,
        row=row,
        is_new=is_new,
        dispositifs=dispositifs,
        prescripteurs=prescripteurs,
        titres=titres,
    )


# ---------------------------------------------------------------------------
# Positionnements
# ---------------------------------------------------------------------------

@bp.route("/participants/<int:participant_id>/positionnements/new", methods=["GET", "POST"])
def positionnement_new(participant_id: int):
    _ensure_sensitive_edit()
    participant = _load_participant_for_insertion(participant_id)
    row = ParticipantInsertionPositionnement(participant=participant, type_positionnement="entree")
    return _render_positionnement_form(participant, row, is_new=True)


@bp.route("/participants/<int:participant_id>/positionnements/<int:positionnement_id>/edit", methods=["GET", "POST"])
def positionnement_edit(participant_id: int, positionnement_id: int):
    _ensure_sensitive_edit()
    participant = _load_participant_for_insertion(participant_id)
    row = ParticipantInsertionPositionnement.query.filter_by(id=positionnement_id, participant_id=participant.id).first_or_404()
    return _render_positionnement_form(participant, row, is_new=False)


@bp.route("/participants/<int:participant_id>/positionnements/<int:positionnement_id>/delete", methods=["POST"])
def positionnement_delete(participant_id: int, positionnement_id: int):
    _ensure_sensitive_edit()
    row = ParticipantInsertionPositionnement.query.filter_by(id=positionnement_id, participant_id=participant_id).first_or_404()
    db.session.delete(row)
    commit_delete(
        "le positionnement linguistique",
        "Le positionnement linguistique a bien été supprimé.",
        blocked_message="Impossible de supprimer ce positionnement car il est encore utilisé ailleurs.",
    )
    return redirect(url_for("insertion.participant_detail", participant_id=participant_id))


def _render_positionnement_form(participant: Participant, row: ParticipantInsertionPositionnement, is_new: bool):
    niveaux = _ref_options(InsertionNiveauRef)
    parcours_options = _parcours_options(participant)

    if request.method == "POST":
        row.parcours_id = request.form.get("parcours_id") or None
        row.niveau_id = request.form.get("niveau_id") or None
        row.date_positionnement = _parse_date(request.form.get("date_positionnement"))
        row.type_positionnement = (request.form.get("type_positionnement") or "entree").strip() or "entree"
        row.commentaire = (request.form.get("commentaire") or "").strip() or None
        if is_new and not row.created_by_user_id:
            row.created_by_user_id = getattr(current_user, "id", None)
        row.updated_by_user_id = getattr(current_user, "id", None)
        row.legacy_source = False
        db.session.add(row)
        db.session.commit()
        flash("Le positionnement linguistique a bien été enregistré.", "success")
        return redirect(url_for("insertion.participant_detail", participant_id=participant.id))

    return render_template(
        "insertion/participant_positionnement_form.html",
        participant=participant,
        row=row,
        is_new=is_new,
        niveaux=niveaux,
        parcours_options=parcours_options,
        positionnement_types=POSITIONNEMENT_TYPES,
    )


# ---------------------------------------------------------------------------
# Certifications
# ---------------------------------------------------------------------------

@bp.route("/participants/<int:participant_id>/certifications/new", methods=["GET", "POST"])
def certification_new(participant_id: int):
    _ensure_sensitive_edit()
    participant = _load_participant_for_insertion(participant_id)
    row = ParticipantInsertionCertification(participant=participant)
    return _render_certification_form(participant, row, is_new=True)


@bp.route("/participants/<int:participant_id>/certifications/<int:certification_id>/edit", methods=["GET", "POST"])
def certification_edit(participant_id: int, certification_id: int):
    _ensure_sensitive_edit()
    participant = _load_participant_for_insertion(participant_id)
    row = ParticipantInsertionCertification.query.filter_by(id=certification_id, participant_id=participant.id).first_or_404()
    return _render_certification_form(participant, row, is_new=False)


@bp.route("/participants/<int:participant_id>/certifications/<int:certification_id>/delete", methods=["POST"])
def certification_delete(participant_id: int, certification_id: int):
    _ensure_sensitive_edit()
    row = ParticipantInsertionCertification.query.filter_by(id=certification_id, participant_id=participant_id).first_or_404()
    db.session.delete(row)
    commit_delete(
        "la certification linguistique",
        "La certification linguistique a bien été supprimée.",
        blocked_message="Impossible de supprimer cette certification car elle est encore utilisée ailleurs.",
    )
    return redirect(url_for("insertion.participant_detail", participant_id=participant_id))


def _render_certification_form(participant: Participant, row: ParticipantInsertionCertification, is_new: bool):
    diplomes = _ref_options(InsertionDiplomeRef)
    niveaux = _ref_options(InsertionNiveauRef)
    parcours_options = _parcours_options(participant)

    if request.method == "POST":
        row.parcours_id = request.form.get("parcours_id") or None
        row.diplome_id = request.form.get("diplome_id") or None
        row.niveau_id = request.form.get("niveau_id") or None
        row.date_passage = _parse_date(request.form.get("date_passage"))
        row.resultat = (request.form.get("resultat") or "").strip() or None
        row.commentaire = (request.form.get("commentaire") or "").strip() or None
        # compat legacy: si obtenu, on aligne date_obtention sur date_passage, sinon on la vide
        row.date_obtention = row.date_passage if row.resultat == "obtenu" else None
        if is_new and not row.created_by_user_id:
            row.created_by_user_id = getattr(current_user, "id", None)
        row.updated_by_user_id = getattr(current_user, "id", None)
        row.legacy_source = False
        db.session.add(row)
        db.session.commit()
        flash("La certification linguistique a bien été enregistrée.", "success")
        return redirect(url_for("insertion.participant_detail", participant_id=participant.id))

    return render_template(
        "insertion/participant_certification_form.html",
        participant=participant,
        row=row,
        is_new=is_new,
        diplomes=diplomes,
        niveaux=niveaux,
        parcours_options=parcours_options,
        certification_results=CERTIFICATION_RESULTS,
    )


# ---------------------------------------------------------------------------
# Référentiels
# ---------------------------------------------------------------------------

@bp.route("/referentiels")
def referentiels_overview():
    data = []
    for ref_type, meta in REFERENCE_MODELS.items():
        model = meta["model"]
        rows = model.query.order_by(model.sort_order.asc(), model.label.asc()).all()
        data.append({
            "type": ref_type,
            "title": meta["title"],
            "description": meta["description"],
            "rows": rows,
            "count": len(rows),
            "active_count": sum(1 for row in rows if row.is_active),
        })
    return render_template("insertion/referentiels.html", data=data, can_manage_refs=can_manage_insertion_refs())


@bp.route("/referentiels/<ref_type>")
def referentiel_list(ref_type: str):
    meta = _get_reference_meta(ref_type)
    model = meta["model"]
    q = (request.args.get("q") or "").strip()
    only_active = (request.args.get("only_active") or "1") == "1"

    rows_q = model.query
    if q:
        like = f"%{q}%"
        rows_q = rows_q.filter(or_(model.label.ilike(like), model.code.ilike(like)))
    if only_active:
        rows_q = rows_q.filter(model.is_active.is_(True))

    rows = rows_q.order_by(model.sort_order.asc(), model.label.asc()).all()
    return render_template(
        "insertion/referentiel_list.html",
        ref_type=ref_type,
        meta=meta,
        rows=rows,
        q=q,
        only_active=only_active,
        can_manage_refs=can_manage_insertion_refs(),
    )


@bp.route("/referentiels/<ref_type>/new", methods=["GET", "POST"])
def referentiel_new(ref_type: str):
    _ensure_refs_admin()
    meta = _get_reference_meta(ref_type)
    model = meta["model"]

    if request.method == "POST":
        label = _normalize_label(request.form.get("label"))
        code = (request.form.get("code") or "").strip() or None
        sort_order_raw = (request.form.get("sort_order") or "0").strip()
        is_active = bool(request.form.get("is_active"))

        if not label:
            flash("Le libellé est obligatoire.", "danger")
            return render_template("insertion/referentiel_form.html", ref_type=ref_type, meta=meta, row=None, values=request.form)

        try:
            sort_order = int(sort_order_raw or 0)
        except ValueError:
            flash("L’ordre d’affichage doit être un nombre entier.", "danger")
            return render_template("insertion/referentiel_form.html", ref_type=ref_type, meta=meta, row=None, values=request.form)

        exists = model.query.filter(func.lower(model.label) == label.lower()).first()
        if exists:
            flash("Une valeur avec ce libellé existe déjà.", "warning")
            return render_template("insertion/referentiel_form.html", ref_type=ref_type, meta=meta, row=None, values=request.form)

        row = model(label=label, code=code, sort_order=sort_order, is_active=is_active)
        db.session.add(row)
        db.session.commit()
        flash(f"Le {meta['singular']} a bien été créé.", "success")
        return redirect(url_for("insertion.referentiel_list", ref_type=ref_type))

    return render_template("insertion/referentiel_form.html", ref_type=ref_type, meta=meta, row=None, values={"sort_order": "0", "is_active": "1"})


@bp.route("/referentiels/<ref_type>/<int:ref_id>/edit", methods=["GET", "POST"])
def referentiel_edit(ref_type: str, ref_id: int):
    _ensure_refs_admin()
    meta = _get_reference_meta(ref_type)
    model = meta["model"]
    row = db.get_or_404(model, ref_id)

    if request.method == "POST":
        label = _normalize_label(request.form.get("label"))
        code = (request.form.get("code") or "").strip() or None
        sort_order_raw = (request.form.get("sort_order") or "0").strip()
        is_active = bool(request.form.get("is_active"))

        if not label:
            flash("Le libellé est obligatoire.", "danger")
            return render_template("insertion/referentiel_form.html", ref_type=ref_type, meta=meta, row=row, values=request.form)

        try:
            sort_order = int(sort_order_raw or 0)
        except ValueError:
            flash("L’ordre d’affichage doit être un nombre entier.", "danger")
            return render_template("insertion/referentiel_form.html", ref_type=ref_type, meta=meta, row=row, values=request.form)

        exists = model.query.filter(func.lower(model.label) == label.lower(), model.id != row.id).first()
        if exists:
            flash("Une valeur avec ce libellé existe déjà.", "warning")
            return render_template("insertion/referentiel_form.html", ref_type=ref_type, meta=meta, row=row, values=request.form)

        row.label = label
        row.code = code
        row.sort_order = sort_order
        row.is_active = is_active
        db.session.commit()
        flash(f"Le {meta['singular']} a bien été mis à jour.", "success")
        return redirect(url_for("insertion.referentiel_list", ref_type=ref_type))

    return render_template("insertion/referentiel_form.html", ref_type=ref_type, meta=meta, row=row, values=None)


@bp.route("/referentiels/<ref_type>/<int:ref_id>/delete", methods=["POST"])
def referentiel_delete(ref_type: str, ref_id: int):
    _ensure_refs_admin()
    meta = _get_reference_meta(ref_type)
    model = meta["model"]
    row = db.get_or_404(model, ref_id)
    label = row.label
    db.session.delete(row)
    commit_delete(
        f"le {meta['singular']} « {label} »",
        f"Le {meta['singular']} a bien été supprimé.",
        blocked_message=f"Impossible de supprimer le {meta['singular']} « {label} » : cette valeur est encore utilisée ailleurs dans l’application.",
    )
    return redirect(url_for("insertion.referentiel_list", ref_type=ref_type))
