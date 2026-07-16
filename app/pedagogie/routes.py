import csv
import datetime

from app.utils.dates import utcnow
import mimetypes
import os
from collections import defaultdict
from io import StringIO

from flask import Response, abort, current_app, flash, redirect, render_template, request, send_file, url_for
from sqlalchemy import delete
from flask_login import current_user, login_required
from werkzeug.utils import secure_filename

from app.extensions import db
from app.models import (
    AtelierActivite,
    Competence,
    Evaluation,
    Objectif,
    ObjectifCompetenceMap,
    atelier_competence,
    module_competence,
    objectif_competence,
    projet_competence,
    session_competence,
    ObjectifSuivi,
    PasseportNote,
    PasseportPieceJointe,
    PedagogieModule,
    PlanProjetAtelierModule,
    PresenceActivite,
    Projet,
    ProjetAtelier,
    Referentiel,
    SessionActivite,
)
from app.rbac import require_perm, can

from . import bp
from app.utils.delete_guard import commit_delete
from .services import compute_objectif_scores, participant_timeline, progression_portail


PASSPORT_NOTE_CATEGORIES = {
    "journal": "Journal",
    "participation": "Participation",
    "progression": "Progression",
    "temoignage": "Témoignage",
    "session": "Session",
}


def _normalize_note_category(raw: str | None) -> str:
    val = (raw or "journal").strip().lower()
    return val if val in PASSPORT_NOTE_CATEGORIES else "journal"



# ============================================================
# P0.4G — Suivi des apprentissages simplifié
# ============================================================

def _apprentissage_status_from_counts(counts: dict) -> dict:
    total = sum(counts.values())
    if total <= 0:
        return {"label": "Pas encore observé", "class": "muted", "score": 0, "total": 0}

    weighted = (
        counts.get(0, 0) * 0
        + counts.get(1, 0) * 50
        + counts.get(2, 0) * 100
        + counts.get(3, 0) * 115
    ) / total

    if weighted >= 85:
        return {"label": "Plutôt réussi", "class": "ok", "score": round(weighted, 1), "total": total}
    if weighted >= 50:
        return {"label": "En progression", "class": "warn", "score": round(weighted, 1), "total": total}
    return {"label": "À retravailler", "class": "danger", "score": round(weighted, 1), "total": total}


def _session_apprentissages_directs(session):
    """Apprentissages actifs d'une séance.

    Règle P0.4G : on ne devine plus depuis les projets/modules.
    Un apprentissage est actif seulement s'il est explicitement rattaché à la session.
    """
    if not session:
        return []
    return (
        Objectif.query
        .filter(Objectif.session_id == session.id)
        .order_by(Objectif.created_at.asc(), Objectif.id.asc())
        .all()
    )


def _session_context_suggestions(session, limit: int = 30):
    """Suggestions non actives issues des anciens liens.

    Elles servent à aider l'utilisateur, mais ne sont jamais évaluées tant qu'il
    ne les ajoute pas explicitement à la séance.
    """
    if not session:
        return [], [], []

    modules = {}
    projets = {}
    suggestions = {}

    # Projets liés à l'atelier.
    projet_ids = [
        pid for (pid,) in db.session.query(ProjetAtelier.projet_id)
        .filter(ProjetAtelier.atelier_id == session.atelier_id)
        .all()
        if pid
    ]
    for projet in Projet.query.filter(Projet.id.in_(projet_ids)).all() if projet_ids else []:
        projets[projet.id] = projet

    # Modules liés à la session.
    for module in getattr(session, 'modules', []) or []:
        modules[module.id] = module

    # Modules liés à l'atelier.
    if getattr(session, 'atelier', None):
        for module in getattr(session.atelier, 'modules', []) or []:
            modules[module.id] = module

    # Plan projet/atelier/module.
    for row in (
        PlanProjetAtelierModule.query
        .filter(
            PlanProjetAtelierModule.atelier_id == session.atelier_id,
            PlanProjetAtelierModule.actif.is_(True),
        )
        .all()
    ):
        if row.projet:
            projets[row.projet.id] = row.projet
        if row.module:
            modules[row.module.id] = row.module

    module_ids = list(modules.keys())
    projet_ids = list(projets.keys())

    # Objectifs liés aux modules / projets / atelier, mais sans session directe.
    queries = []
    if module_ids:
        queries.append(Objectif.query.filter(Objectif.module_id.in_(module_ids), Objectif.session_id.is_(None)))
    if projet_ids:
        queries.append(Objectif.query.filter(Objectif.projet_id.in_(projet_ids), Objectif.session_id.is_(None)))
    queries.append(Objectif.query.filter(Objectif.atelier_id == session.atelier_id, Objectif.session_id.is_(None)))

    direct_titles = {o.titre.strip().lower() for o in _session_apprentissages_directs(session)}
    for q in queries:
        for obj in q.order_by(Objectif.created_at.asc(), Objectif.id.asc()).limit(limit).all():
            key = (obj.titre or '').strip().lower()
            if key and key not in direct_titles:
                suggestions[obj.id] = obj

    return (
        sorted(modules.values(), key=lambda m: (m.nom or '').lower()),
        sorted(projets.values(), key=lambda p: (p.nom or '').lower()),
        list(suggestions.values())[:limit],
    )


@bp.route('/')
@login_required
@require_perm('pedagogie:view')
def index():
    """Accueil simplifié : Suivi des apprentissages."""
    stats = {
        'referentiels': Referentiel.query.count(),
        'competences': Competence.query.count(),
        'modules': PedagogieModule.query.count(),
        'apprentissages': Objectif.query.filter(Objectif.session_id.isnot(None)).count(),
        'observations': ObjectifSuivi.query.count(),
        'notes_passeport': PasseportNote.query.count(),
        'pieces_passeport': PasseportPieceJointe.query.count(),
    }

    alerts = []
    if stats['apprentissages'] == 0:
        alerts.append({
            'level': 'info',
            'title': 'Aucun apprentissage de séance explicite',
            'text': "Commence par choisir une séance, puis indique ce qui est travaillé aujourd’hui.",
        })
    if stats['observations'] == 0:
        alerts.append({
            'level': 'info',
            'title': 'Aucune observation enregistrée',
            'text': "Les progrès apparaîtront ici dès qu’une séance aura été observée.",
        })

    recent_suivis = (
        ObjectifSuivi.query
        .order_by(ObjectifSuivi.date_saisie.desc(), ObjectifSuivi.id.desc())
        .limit(6)
        .all()
    )
    recent_evals = (
        Evaluation.query
        .order_by(Evaluation.date_evaluation.desc(), Evaluation.id.desc())
        .limit(6)
        .all()
    )

    return render_template(
        'pedagogie/index.html',
        stats=stats,
        alerts=alerts,
        recent_suivis=recent_suivis,
        recent_evals=recent_evals,
    )


@bp.route('/apprentissages')
@login_required
@require_perm('pedagogie:view')
def apprentissages_home():
    """Alias humain vers le parcours simple."""
    return redirect(url_for('pedagogie.parcours_pedagogique'))


@bp.route('/parcours', methods=['GET', 'POST'])
@login_required
@require_perm('pedagogie:view')
def parcours_pedagogique():
    """Séance → apprentissages choisis → observation → résultats."""
    session_id = request.values.get('session_id', type=int)
    atelier_id = request.values.get('atelier_id', type=int)

    if request.method == 'POST':
        action = request.form.get('action') or ''
        session = db.get_or_404(SessionActivite, session_id) if session_id else None

        if action == 'add_apprentissage' and session:
            titre = (request.form.get('titre') or '').strip()
            description = (request.form.get('description') or '').strip() or None
            if len(titre) < 3:
                flash('Indique ce qui est travaillé pendant cette séance.', 'warning')
            elif Objectif.query.filter_by(session_id=session.id, titre=titre).first():
                flash('Cet apprentissage est déjà rattaché à cette séance.', 'info')
            else:
                obj = Objectif(
                    titre=titre,
                    description=description,
                    type='operationnel',
                    atelier_id=session.atelier_id,
                    session_id=session.id,
                    seuil_validation=70,
                )
                db.session.add(obj)
                db.session.commit()
                flash('Apprentissage ajouté à cette séance.', 'success')
            return redirect(url_for('pedagogie.parcours_pedagogique', session_id=session.id, atelier_id=atelier_id or session.atelier_id))

        if action == 'attach_suggestion' and session:
            source_id = request.form.get('source_objectif_id', type=int)
            source = db.get_or_404(Objectif, source_id)
            titre = (source.titre or '').strip()
            if Objectif.query.filter_by(session_id=session.id, titre=titre).first():
                flash('Cet apprentissage est déjà rattaché à la séance.', 'info')
            else:
                obj = Objectif(
                    titre=titre,
                    description=source.description,
                    type='operationnel',
                    atelier_id=session.atelier_id,
                    session_id=session.id,
                    module_id=source.module_id,
                    seuil_validation=source.seuil_validation or 70,
                )
                db.session.add(obj)
                db.session.commit()
                flash('Suggestion ajoutée aux apprentissages de la séance.', 'success')
            return redirect(url_for('pedagogie.parcours_pedagogique', session_id=session.id, atelier_id=atelier_id or session.atelier_id))

    ateliers = (
        AtelierActivite.query
        .filter(AtelierActivite.is_deleted.is_(False))
        .order_by(AtelierActivite.nom.asc())
        .all()
    )

    sessions_q = (
        SessionActivite.query
        .filter(SessionActivite.is_deleted.is_(False))
        .order_by(SessionActivite.date_session.desc().nullslast(), SessionActivite.created_at.desc())
    )
    if atelier_id:
        sessions_q = sessions_q.filter(SessionActivite.atelier_id == atelier_id)
    sessions = sessions_q.limit(150).all()

    selected_session = db.session.get(SessionActivite, session_id) if session_id else (sessions[0] if sessions else None)

    apprentissages = []
    modules = []
    projets = []
    suggestions = []
    presences = []
    suivi_rows = []
    results_by_objectif = {}

    if selected_session:
        apprentissages = _session_apprentissages_directs(selected_session)
        modules, projets, suggestions = _session_context_suggestions(selected_session)
        presences = (
            PresenceActivite.query
            .filter(PresenceActivite.session_id == selected_session.id)
            .join(PresenceActivite.participant)
            .order_by(PresenceActivite.created_at.asc())
            .all()
        )
        suivi_rows = (
            ObjectifSuivi.query
            .filter(ObjectifSuivi.session_id == selected_session.id)
            .order_by(ObjectifSuivi.updated_at.desc())
            .all()
        )
        for row in suivi_rows:
            bucket = results_by_objectif.setdefault(row.objectif_id, {0: 0, 1: 0, 2: 0, 3: 0})
            if row.etat in (0, 1, 2, 3):
                bucket[row.etat] = bucket.get(row.etat, 0) + 1

    cards = []
    for obj in apprentissages:
        counts = results_by_objectif.get(obj.id, {})
        cards.append({
            'objectif': obj,
            'counts': counts,
            'status': _apprentissage_status_from_counts(counts),
        })

    missing = []
    if selected_session:
        if not apprentissages:
            missing.append('Aucun apprentissage choisi pour cette séance.')
        if not presences:
            missing.append('Aucun participant émargé sur cette séance.')
        if apprentissages and not suivi_rows:
            missing.append('Des apprentissages sont choisis, mais aucune observation n’est encore saisie.')

    return render_template(
        'pedagogie/parcours.html',
        ateliers=ateliers,
        sessions=sessions,
        selected_session=selected_session,
        selected_atelier_id=atelier_id,
        objectifs=cards,
        apprentissages=cards,
        linked_modules=modules,
        linked_projects=projets,
        suggestions=suggestions,
        presences=presences,
        participants_count=len(presences),
        suivi_rows=suivi_rows,
        missing=missing,
    )


# ============================================================
# Référentiels
# ============================================================

@bp.route("/referentiels", methods=["GET", "POST"])
@login_required
@require_perm("pedagogie:view")
def referentiels_list():
    if request.method == "POST" and not can("pedagogie:edit"):
        # Lecture seule : consultation autorisée, modification refusée.
        abort(403)
    if request.method == "POST":
        action = request.form.get("action") or ""
        if action == "create_referentiel":
            nom = (request.form.get("nom") or "").strip()
            description = (request.form.get("description") or "").strip() or None
            if not nom:
                flash("Nom du référentiel obligatoire.", "danger")
                return redirect(url_for("pedagogie.referentiels_list"))
            ref = Referentiel(nom=nom, description=description)
            db.session.add(ref)
            db.session.commit()
            flash("Référentiel créé.", "success")
            return redirect(url_for("pedagogie.referentiels_list"))

        if action == "delete_referentiel":
            ref_id = int(request.form.get("referentiel_id") or 0)
            ref = db.get_or_404(Referentiel, ref_id)
            comp_ids = [c.id for c in Competence.query.filter_by(referentiel_id=ref.id).all()]
            usage = {}
            if comp_ids:
                usage = {
                    "compétences": len(comp_ids),
                    "évaluations": Evaluation.query.filter(Evaluation.competence_id.in_(comp_ids)).count(),
                    "objectifs": ObjectifCompetenceMap.query.filter(ObjectifCompetenceMap.competence_id.in_(comp_ids)).count(),
                    "projets": db.session.execute(
                        db.select(db.func.count()).select_from(projet_competence).where(projet_competence.c.competence_id.in_(comp_ids))
                    ).scalar_one(),
                    "ateliers": db.session.execute(
                        db.select(db.func.count()).select_from(atelier_competence).where(atelier_competence.c.competence_id.in_(comp_ids))
                    ).scalar_one(),
                    "sessions": db.session.execute(
                        db.select(db.func.count()).select_from(session_competence).where(session_competence.c.competence_id.in_(comp_ids))
                    ).scalar_one(),
                    "modules": db.session.execute(
                        db.select(db.func.count()).select_from(module_competence).where(module_competence.c.competence_id.in_(comp_ids))
                    ).scalar_one(),
                    "objectifs_m2m": db.session.execute(
                        db.select(db.func.count()).select_from(objectif_competence).where(objectif_competence.c.competence_id.in_(comp_ids))
                    ).scalar_one(),
                }
            blocking = {label: count for label, count in usage.items() if count and label != "compétences"}
            if blocking:
                details = ", ".join(f"{count} {label}" for label, count in blocking.items())
                flash(
                    f"Impossible de supprimer le référentiel « {ref.nom} » : il est encore utilisé ({details}). "
                    "Retire d'abord ses compétences des évaluations, modules, ateliers, sessions, projets ou objectifs concernés.",
                    "danger",
                )
                return redirect(url_for("pedagogie.referentiels_list"))
            if comp_ids:
                db.session.execute(delete(projet_competence).where(projet_competence.c.competence_id.in_(comp_ids)))
                db.session.execute(delete(atelier_competence).where(atelier_competence.c.competence_id.in_(comp_ids)))
                db.session.execute(delete(session_competence).where(session_competence.c.competence_id.in_(comp_ids)))
                db.session.execute(delete(module_competence).where(module_competence.c.competence_id.in_(comp_ids)))
                db.session.execute(delete(objectif_competence).where(objectif_competence.c.competence_id.in_(comp_ids)))
            db.session.delete(ref)
            if commit_delete(
                f"le référentiel « {ref.nom} »",
                "Référentiel supprimé.",
                success_category="warning",
                blocked_message=f"Impossible de supprimer le référentiel « {ref.nom} » : des éléments y sont encore rattachés.",
            ):
                return redirect(url_for("pedagogie.referentiels_list"))
            return redirect(url_for("pedagogie.referentiels_list"))

    referentiels = Referentiel.query.order_by(Referentiel.nom.asc()).all()
    return render_template("pedagogie/referentiels.html", referentiels=referentiels)


@bp.route("/referentiels/<int:referentiel_id>", methods=["GET", "POST"])
@login_required
@require_perm("pedagogie:view")
def referentiels_edit(referentiel_id: int):
    if request.method == "POST" and not can("pedagogie:edit"):
        # Lecture seule : consultation autorisée, modification refusée.
        abort(403)
    referentiel = db.get_or_404(Referentiel, referentiel_id)

    if request.method == "POST":
        action = request.form.get("action") or ""

        if action == "update_referentiel":
            referentiel.nom = (request.form.get("nom") or "").strip()
            referentiel.description = (request.form.get("description") or "").strip() or None
            if not referentiel.nom:
                flash("Nom obligatoire.", "danger")
                return redirect(url_for("pedagogie.referentiels_edit", referentiel_id=referentiel.id))
            db.session.commit()
            flash("Référentiel mis à jour.", "success")
            return redirect(url_for("pedagogie.referentiels_edit", referentiel_id=referentiel.id))

        if action == "add_competence":
            code = (request.form.get("code") or "").strip()
            nom = (request.form.get("nom") or "").strip()
            description = (request.form.get("description") or "").strip() or None
            if not code or not nom:
                flash("Code et nom de compétence obligatoires.", "danger")
                return redirect(url_for("pedagogie.referentiels_edit", referentiel_id=referentiel.id))
            comp = Competence(
                referentiel_id=referentiel.id,
                code=code,
                nom=nom,
                description=description,
            )
            db.session.add(comp)
            db.session.commit()
            flash("Compétence ajoutée.", "success")
            return redirect(url_for("pedagogie.referentiels_edit", referentiel_id=referentiel.id))

        if action == "delete_competence":
            comp_id = int(request.form.get("competence_id") or 0)
            comp = db.get_or_404(Competence, comp_id)
            if comp.referentiel_id != referentiel.id:
                flash("Compétence invalide.", "danger")
                return redirect(url_for("pedagogie.referentiels_edit", referentiel_id=referentiel.id))

            usage = {
                "évaluations": Evaluation.query.filter_by(competence_id=comp.id).count(),
                "objectifs": ObjectifCompetenceMap.query.filter_by(competence_id=comp.id).count(),
                "projets": db.session.execute(
                    db.select(db.func.count()).select_from(projet_competence).where(projet_competence.c.competence_id == comp.id)
                ).scalar_one(),
                "ateliers": db.session.execute(
                    db.select(db.func.count()).select_from(atelier_competence).where(atelier_competence.c.competence_id == comp.id)
                ).scalar_one(),
                "sessions": db.session.execute(
                    db.select(db.func.count()).select_from(session_competence).where(session_competence.c.competence_id == comp.id)
                ).scalar_one(),
                "modules": db.session.execute(
                    db.select(db.func.count()).select_from(module_competence).where(module_competence.c.competence_id == comp.id)
                ).scalar_one(),
                "objectifs_m2m": db.session.execute(
                    db.select(db.func.count()).select_from(objectif_competence).where(objectif_competence.c.competence_id == comp.id)
                ).scalar_one(),
            }
            blocking = {label: count for label, count in usage.items() if count}
            if blocking:
                details = ", ".join(f"{count} {label}" for label, count in blocking.items())
                flash(
                    f"Impossible de supprimer la compétence « {comp.code} - {comp.nom} » : elle est encore utilisée ({details}). "
                    "Retire-la d'abord des évaluations, modules, ateliers, sessions ou objectifs concernés.",
                    "danger",
                )
                return redirect(url_for("pedagogie.referentiels_edit", referentiel_id=referentiel.id))

            db.session.execute(delete(projet_competence).where(projet_competence.c.competence_id == comp.id))
            db.session.execute(delete(atelier_competence).where(atelier_competence.c.competence_id == comp.id))
            db.session.execute(delete(session_competence).where(session_competence.c.competence_id == comp.id))
            db.session.execute(delete(module_competence).where(module_competence.c.competence_id == comp.id))
            db.session.execute(delete(objectif_competence).where(objectif_competence.c.competence_id == comp.id))
            db.session.delete(comp)
            if commit_delete(
                f"la compétence « {comp.code} - {comp.nom} »",
                "Compétence supprimée.",
                success_category="warning",
                blocked_message=f"Impossible de supprimer la compétence « {comp.code} - {comp.nom} » : elle est encore utilisée ailleurs.",
            ):
                return redirect(url_for("pedagogie.referentiels_edit", referentiel_id=referentiel.id))
            return redirect(url_for("pedagogie.referentiels_edit", referentiel_id=referentiel.id))

    competences = Competence.query.filter_by(referentiel_id=referentiel.id).order_by(Competence.code.asc()).all()
    return render_template(
        "pedagogie/referentiel_edit.html",
        referentiel=referentiel,
        competences=competences,
    )




@bp.route("/referentiels/<int:referentiel_id>/import_csv", methods=["POST"])
@login_required
@require_perm("pedagogie:edit")
def import_referentiel_csv(referentiel_id: int):
    """Import CSV de compétences dans un référentiel (upsert par code).

    Colonnes attendues (au minimum):
    - code
    - nom (ou label)
    Colonnes optionnelles:
    - description

    Les colonnes supplémentaires (ex: domain_code, sort_order...) sont ignorées.
    """
    referentiel = db.get_or_404(Referentiel, referentiel_id)

    f = request.files.get("csv_file")
    if not f or not f.filename:
        flash("Fichier CSV manquant.", "danger")
        return redirect(url_for("pedagogie.referentiels_edit", referentiel_id=referentiel.id))

    filename = secure_filename(f.filename)
    if not filename.lower().endswith(".csv"):
        flash("Le fichier doit être un .csv", "danger")
        return redirect(url_for("pedagogie.referentiels_edit", referentiel_id=referentiel.id))

    # Lecture en mémoire : UTF-8 BOM puis fallback Windows-1252/Latin-1
    raw = f.read()
    try:
        content = raw.decode("utf-8-sig")
    except Exception:
        try:
            content = raw.decode("cp1252")
        except Exception:
            content = raw.decode("latin-1")

    reader = csv.DictReader(StringIO(content))
    headers = [h.strip() for h in (reader.fieldnames or []) if h]
    headers_lower = [h.lower() for h in headers]

    if "code" not in headers_lower:
        flash("CSV invalide : colonne 'code' manquante.", "danger")
        return redirect(url_for("pedagogie.referentiels_edit", referentiel_id=referentiel.id))

    # nom peut s'appeler 'nom' ou 'label' (DigComp)
    has_nom = "nom" in headers_lower or "label" in headers_lower
    if not has_nom:
        flash("CSV invalide : colonne 'nom' ou 'label' manquante.", "danger")
        return redirect(url_for("pedagogie.referentiels_edit", referentiel_id=referentiel.id))

    created = 0
    updated = 0
    skipped = 0

    # index existants par code (case-insensitive)
    existing = {c.code.strip().lower(): c for c in Competence.query.filter_by(referentiel_id=referentiel.id).all()}


    for i, row in enumerate(reader, start=2):
        # DictReader met les colonnes excédentaires sous la clé None sous forme de liste.
        # Cas typique : CSV exporté avec des virgules non protégées dans "description".
        extras = row.get(None) or []

        def _clean(value):
            if value is None:
                return ""
            if isinstance(value, list):
                return ",".join(str(x) for x in value if x is not None).strip()
            return str(value).strip()

        norm = {(k or "").strip().lower(): _clean(v) for k, v in row.items() if k is not None}

        # Rattrapage des lignes avec colonnes en trop :
        # code,label,description,domain_code,...
        # devient parfois description="Capacité à localiser", domain_code=" filtrer...", etc.
        if extras:
            ordered_values = [_clean(row.get(h)) for h in (reader.fieldnames or [])]
            if len(ordered_values) >= 3:
                fixed_desc = ",".join(v for v in [ordered_values[2], *[_clean(x) for x in extras]] if v).strip()
                norm["description"] = fixed_desc

        code = norm.get("code", "").strip()
        if not code:
            skipped += 1
            continue

        nom = (norm.get("nom") or norm.get("label") or "").strip()
        if not nom:
            skipped += 1
            continue

        desc = (norm.get("description") or "").strip() or None

        key = code.lower()
        if key in existing:
            c = existing[key]
            changed = False
            if c.nom != nom:
                c.nom = nom
                changed = True
            if (c.description or None) != desc:
                c.description = desc
                changed = True
            if changed:
                updated += 1
        else:
            c = Competence(referentiel_id=referentiel.id, code=code, nom=nom, description=desc)
            db.session.add(c)
            existing[key] = c
            created += 1

    db.session.commit()
    flash(f"Import terminé : {created} créées, {updated} mises à jour, {skipped} ignorées.", "success")
    return redirect(url_for("pedagogie.referentiels_edit", referentiel_id=referentiel.id))

# ============================================================
# Modules pédagogiques
# ============================================================

@bp.route("/modules", methods=["GET", "POST"])
@login_required
@require_perm("pedagogie:view")
def modules_pedagogiques():
    if request.method == "POST" and not can("pedagogie:edit"):
        # Lecture seule : consultation autorisée, modification refusée.
        abort(403)
    if request.method == "POST":
        action = request.form.get("action") or ""
        if action == "create_module":
            nom = (request.form.get("nom") or "").strip()
            description = (request.form.get("description") or "").strip() or None
            competence_ids = [int(cid) for cid in request.form.getlist("competence_ids") if cid.isdigit()]
            if not nom:
                flash("Nom du module obligatoire.", "danger")
                return redirect(url_for("pedagogie.modules_pedagogiques"))

            mod = PedagogieModule(nom=nom, description=description)
            if competence_ids:
                mod.competences = Competence.query.filter(Competence.id.in_(competence_ids)).all()

            db.session.add(mod)
            db.session.commit()
            flash("Module pédagogique créé.", "success")
            return redirect(url_for("pedagogie.modules_pedagogiques"))

    referentiels = Referentiel.query.order_by(Referentiel.nom.asc()).all()
    modules = PedagogieModule.query.filter(PedagogieModule.actif.is_(True)).order_by(PedagogieModule.nom.asc()).all()
    return render_template("pedagogie/modules.html", referentiels=referentiels, modules=modules)


# ============================================================
# Objectifs
# ============================================================

@bp.route("/objectifs", methods=["GET", "POST"])
@login_required
@require_perm("pedagogie:view")
def objectifs():
    if request.method == "POST" and not can("pedagogie:edit"):
        # Lecture seule : consultation autorisée, modification refusée.
        abort(403)
    projet_id = request.args.get("projet_id", type=int)
    atelier_id = request.args.get("atelier_id", type=int)
    session_id = request.args.get("session_id", type=int)

    def _create_objectif(*, obj_type: str, titre: str, description: str | None, seuil: float, parent_id: int | None, projet_id_val: int | None, atelier_id_val: int | None, module_id_val: int | None, session_id_val: int | None):
        obj = Objectif(
            type=obj_type,
            titre=titre,
            description=description,
            seuil_validation=seuil,
            parent_id=parent_id,
            projet_id=projet_id_val,
            atelier_id=atelier_id_val,
            session_id=session_id_val,
            module_id=module_id_val,
        )
        db.session.add(obj)
        db.session.commit()

        if obj.type == "operationnel" and obj.module_id:
            mod = db.session.get(PedagogieModule, obj.module_id)
            if mod:
                for comp in mod.competences:
                    existing = ObjectifCompetenceMap.query.filter_by(objectif_id=obj.id, competence_id=comp.id).first()
                    if not existing:
                        db.session.add(ObjectifCompetenceMap(objectif_id=obj.id, competence_id=comp.id, poids=1.0, actif=True))
                db.session.commit()
        return obj

    if request.method == "POST":
        action = request.form.get("action") or ""
        if action == "quick_create_parcours":
            selected_projet_id = request.form.get("projet_id", type=int)
            selected_atelier_id = request.form.get("atelier_id", type=int)
            selected_module_id = request.form.get("module_id", type=int)
            selected_session_id = request.form.get("session_id", type=int)
            titre_base = (request.form.get("titre_base") or "").strip()

            if not (selected_projet_id and selected_atelier_id and selected_module_id and titre_base):
                flash("Parcours express incomplet: projet, atelier, module et intitulé sont obligatoires.", "danger")
                return redirect(url_for("pedagogie.objectifs", projet_id=projet_id, atelier_id=atelier_id, session_id=session_id))

            atelier_obj = db.session.get(AtelierActivite, selected_atelier_id)
            module_obj = db.session.get(PedagogieModule, selected_module_id)
            if not atelier_obj or not module_obj:
                flash("Atelier ou module introuvable.", "danger")
                return redirect(url_for("pedagogie.objectifs", projet_id=projet_id, atelier_id=atelier_id, session_id=session_id))

            try:
                og = Objectif.query.filter_by(type="general", titre=titre_base, projet_id=selected_projet_id).first()
                if not og:
                    og = _create_objectif(
                        obj_type="general",
                        titre=titre_base,
                        description=None,
                        seuil=60.0,
                        parent_id=None,
                        projet_id_val=selected_projet_id,
                        atelier_id_val=None,
                        module_id_val=None,
                    
                        session_id_val=None,
                    )

                os_titre = f"{titre_base} · {atelier_obj.nom}"
                os_obj = Objectif.query.filter_by(type="specifique", titre=os_titre, atelier_id=selected_atelier_id, parent_id=og.id).first()
                if not os_obj:
                    os_obj = _create_objectif(
                        obj_type="specifique",
                        titre=os_titre,
                        description=None,
                        seuil=60.0,
                        parent_id=og.id,
                        projet_id_val=selected_projet_id,
                        atelier_id_val=selected_atelier_id,
                        module_id_val=None,
                    
                        session_id_val=None,
                    )

                oo_titre = f"{titre_base} · {module_obj.nom}"
                oo_obj = Objectif.query.filter_by(type="operationnel", titre=oo_titre, module_id=selected_module_id, parent_id=os_obj.id).first()
                if not oo_obj:
                    oo_obj = _create_objectif(
                        obj_type="operationnel",
                        titre=oo_titre,
                        description=None,
                        seuil=60.0,
                        parent_id=os_obj.id,
                        projet_id_val=selected_projet_id,
                        atelier_id_val=selected_atelier_id,
                        module_id_val=selected_module_id,
                    
                        session_id_val=None,
                    )
            except Exception as exc:
                db.session.rollback()
                current_app.logger.exception("Erreur création parcours express")
                flash(f"Impossible de créer le parcours express: {exc}", "danger")
                return redirect(url_for("pedagogie.objectifs", projet_id=selected_projet_id, atelier_id=selected_atelier_id))

            flash("Parcours express créé (OG + OS + OO).", "success")
            return redirect(url_for("pedagogie.objectifs", projet_id=selected_projet_id, atelier_id=selected_atelier_id))

        if action == "create_objectif":
            obj_type = (request.form.get("type") or "").strip()
            titre = (request.form.get("titre") or "").strip()
            description = (request.form.get("description") or "").strip() or None
            seuil_validation = request.form.get("seuil_validation", type=float) or 0.0
            parent_id = request.form.get("parent_id", type=int)
            selected_atelier_id = request.form.get("atelier_id", type=int)
            selected_projet_id = request.form.get("projet_id", type=int)
            selected_module_id = request.form.get("module_id", type=int)
            selected_session_id = request.form.get("session_id", type=int)

            parent_obj = db.session.get(Objectif, parent_id) if parent_id else None
            if parent_id and not parent_obj:
                flash("Objectif parent introuvable.", "danger")
                return redirect(url_for("pedagogie.objectifs", projet_id=projet_id, atelier_id=atelier_id, session_id=session_id))

            if not obj_type or not titre:
                flash("Type et titre obligatoires.", "danger")
                return redirect(url_for("pedagogie.objectifs", projet_id=projet_id, atelier_id=atelier_id, session_id=session_id))

            # Hérite du projet parent si non renseigné
            if parent_obj and not selected_projet_id and parent_obj.projet_id:
                selected_projet_id = parent_obj.projet_id

            if obj_type == "specifique" and not selected_projet_id and selected_atelier_id:
                link = ProjetAtelier.query.filter_by(atelier_id=selected_atelier_id).first()
                if link:
                    selected_projet_id = link.projet_id

            # Règles métier
            if obj_type == "general":
                if not selected_projet_id:
                    flash("Un objectif général doit être lié à un projet.", "danger")
                    return redirect(url_for("pedagogie.objectifs", projet_id=projet_id))
                selected_atelier_id = None
                selected_module_id = None
            elif obj_type == "specifique":
                if not selected_atelier_id:
                    flash("Un objectif spécifique doit être lié à un atelier.", "danger")
                    return redirect(url_for("pedagogie.objectifs", projet_id=projet_id, atelier_id=atelier_id))
                selected_module_id = None
            elif obj_type == "operationnel":
                if not selected_module_id:
                    flash("Un objectif opérationnel doit être lié à un module pédagogique.", "danger")
                    return redirect(url_for("pedagogie.objectifs", projet_id=projet_id, atelier_id=atelier_id))
            else:
                flash("Type d'objectif invalide.", "danger")
                return redirect(url_for("pedagogie.objectifs"))

            try:
                if obj_type == "specifique" and parent_obj and parent_obj.type != "general":
                    flash("Un objectif spécifique doit avoir un parent général.", "danger")
                    return redirect(url_for("pedagogie.objectifs", projet_id=selected_projet_id or projet_id, atelier_id=selected_atelier_id or atelier_id, session_id=session_id))
                if obj_type == "operationnel" and parent_obj and parent_obj.type != "specifique":
                    flash("Un objectif opérationnel doit avoir un parent spécifique.", "danger")
                    return redirect(url_for("pedagogie.objectifs", projet_id=selected_projet_id or projet_id, atelier_id=selected_atelier_id or atelier_id, session_id=session_id))

                obj = _create_objectif(
                    obj_type=obj_type,
                    titre=titre,
                    description=description,
                    seuil=seuil_validation,
                    parent_id=parent_id,
                    projet_id_val=selected_projet_id,
                    atelier_id_val=selected_atelier_id,
                    module_id_val=selected_module_id,
                    session_id_val=(selected_session_id if obj_type == 'operationnel' else None),
                )
            except Exception as exc:
                db.session.rollback()
                current_app.logger.exception("Erreur création objectif")
                flash(f"Impossible de créer l'objectif: {exc}", "danger")
                return redirect(url_for("pedagogie.objectifs", projet_id=selected_projet_id or projet_id, atelier_id=selected_atelier_id or atelier_id, session_id=session_id))

            flash("Objectif ajouté.", "success")
            return redirect(url_for("pedagogie.objectifs", projet_id=selected_projet_id, atelier_id=selected_atelier_id))

        if action == "delete_objectif":
            obj_id = int(request.form.get("objectif_id") or 0)
            obj = db.get_or_404(Objectif, obj_id)
            db.session.delete(obj)
            db.session.commit()
            flash("Objectif supprimé.", "warning")
            return redirect(url_for("pedagogie.objectifs", projet_id=projet_id, atelier_id=atelier_id, session_id=session_id))

    projets = Projet.query.order_by(Projet.secteur.asc(), Projet.nom.asc()).all()
    ateliers = AtelierActivite.query.filter(AtelierActivite.is_deleted.is_(False)).order_by(AtelierActivite.nom.asc()).all()
    sessions = SessionActivite.query.filter(SessionActivite.is_deleted.is_(False)).order_by(SessionActivite.created_at.desc()).all()
    referentiels = Referentiel.query.order_by(Referentiel.nom.asc()).all()
    modules = PedagogieModule.query.filter(PedagogieModule.actif.is_(True)).order_by(PedagogieModule.nom.asc()).all()

    objectifs_q = Objectif.query
    if projet_id:
        objectifs_q = objectifs_q.filter(Objectif.projet_id == projet_id)
    if atelier_id:
        objectifs_q = objectifs_q.filter(Objectif.atelier_id == atelier_id)
    if session_id:
        objectifs_q = objectifs_q.filter(Objectif.session_id == session_id)
    objectifs_rows = objectifs_q.order_by(Objectif.created_at.asc()).all()

    parent_options = Objectif.query.order_by(Objectif.created_at.asc()).all()

    return render_template(
        "pedagogie/objectifs.html",
        projets=projets,
        ateliers=ateliers,
        sessions=sessions,
        referentiels=referentiels,
        modules=modules,
        objectifs=objectifs_rows,
        parent_options=parent_options,
        projet_id=projet_id,
        atelier_id=atelier_id,
        session_id=session_id,
    )


# ============================================================
# Suivi / Stats
# ============================================================

@bp.route("/suivi")
@login_required
@require_perm("pedagogie:view")
def suivi_pedagogique():
    projets = Projet.query.order_by(Projet.nom.asc()).all()
    ateliers = AtelierActivite.query.filter_by(is_deleted=False).order_by(AtelierActivite.nom.asc()).all()

    projet_id = request.args.get("projet_id", type=int)
    atelier_id = request.args.get("atelier_id", type=int)

    total_competences = Competence.query.count()
    total_evaluations = Evaluation.query.count()

    return render_template(
        "stats_pedagogie.html",
        projets=projets,
        ateliers=ateliers,
        selected_projet=projet_id,
        selected_atelier=atelier_id,
        total_competences=total_competences,
        total_evaluations=total_evaluations,
    )


# ============================================================
# Kiosk
# ============================================================

@bp.route("/kiosk", methods=["GET", "POST"])
@login_required
@require_perm("pedagogie:view")
def kiosk_pedagogique():
    if request.method == "POST" and not can("pedagogie:edit"):
        # Lecture seule : consultation autorisée, modification refusée.
        abort(403)
    session_id = request.args.get("session_id", type=int)
    participant_id = request.args.get("participant_id", type=int)

    sessions = (
        SessionActivite.query.filter(SessionActivite.is_deleted.is_(False))
        .order_by(SessionActivite.created_at.desc())
        .limit(200)
        .all()
    )
    session = db.session.get(SessionActivite, session_id) if session_id else None

    participants = []
    if session:
        participants = (
            db.session.query(PresenceActivite)
            .filter(PresenceActivite.session_id == session.id)
            .join(PresenceActivite.participant)
            .order_by(PresenceActivite.created_at.asc())
            .all()
        )

    objectifs_rows = []
    if session:
        # P0.4G : on n’évalue que les apprentissages explicitement choisis pour cette séance.
        objectifs_rows = _session_apprentissages_directs(session)

    if request.method == "POST" and session:
        mode = (request.form.get("mode") or "ressenti").strip()
        mode = mode if mode in {"ressenti", "competence", "mixte"} else "ressenti"

        participant_id_post = request.form.get("participant_id", type=int)
        objectif_ids = [int(x) for x in request.form.getlist("objectif_ids") if x.isdigit()]
        today = datetime.date.today()

        saved = 0
        for oid in objectif_ids:
            etat = request.form.get(f"etat_{oid}", type=int)
            ressenti = request.form.get(f"ressenti_{oid}", type=int)
            commentaire = (request.form.get(f"commentaire_{oid}") or "").strip() or None

            if etat is None and ressenti is None and commentaire is None:
                continue

            item = ObjectifSuivi.query.filter_by(
                objectif_id=oid,
                session_id=session.id,
                participant_id=participant_id_post,
                date_saisie=today,
            ).first()
            if not item:
                item = ObjectifSuivi(
                    objectif_id=oid,
                    session_id=session.id,
                    participant_id=participant_id_post,
                    date_saisie=today,
                    user_id=current_user.id,
                )
                db.session.add(item)

            item.mode = mode
            item.etat = etat if etat is not None else item.etat
            item.ressenti = ressenti if ressenti in (1, 2, 3, 4, 5) else None
            item.commentaire = commentaire
            item.user_id = current_user.id
            saved += 1

        db.session.commit()
        flash(f"{saved} observation(s) enregistrée(s).", "success")
        return redirect(url_for("pedagogie.kiosk_pedagogique", session_id=session.id, participant_id=participant_id_post))

    recent_rows = []
    if session:
        q = ObjectifSuivi.query.filter(ObjectifSuivi.session_id == session.id)
        if participant_id:
            q = q.filter(ObjectifSuivi.participant_id == participant_id)
        recent_rows = q.order_by(ObjectifSuivi.updated_at.desc()).limit(40).all()

    return render_template(
        "pedagogie/kiosk.html",
        sessions=sessions,
        selected_session=session,
        participants=participants,
        selected_participant_id=participant_id,
        objectifs=objectifs_rows,
        recent_rows=recent_rows,
    )


# ============================================================
# Plan projet
# ============================================================

@bp.route("/plan_projet", methods=["GET", "POST"])
@login_required
@require_perm("pedagogie:view")
def plan_projet():
    if request.method == "POST" and not can("pedagogie:edit"):
        # Lecture seule : consultation autorisée, modification refusée.
        abort(403)
    if request.method == "POST":
        action = request.form.get("action") or ""
        if action == "add_link":
            projet_id = request.form.get("projet_id", type=int)
            atelier_id = request.form.get("atelier_id", type=int)
            module_id = request.form.get("module_id", type=int)
            if not projet_id or not atelier_id or not module_id:
                flash("Projet, atelier et module sont obligatoires.", "danger")
                return redirect(url_for("pedagogie.plan_projet"))
            row = PlanProjetAtelierModule.query.filter_by(projet_id=projet_id, atelier_id=atelier_id, module_id=module_id).first()
            if not row:
                db.session.add(PlanProjetAtelierModule(projet_id=projet_id, atelier_id=atelier_id, module_id=module_id, actif=True))
                db.session.commit()
            flash("Lien projet/atelier/module enregistré.", "success")
            return redirect(url_for("pedagogie.plan_projet", projet_id=projet_id))

        if action == "delete_link":
            row_id = request.form.get("row_id", type=int)
            row = db.get_or_404(PlanProjetAtelierModule, row_id)
            db.session.delete(row)
            db.session.commit()
            flash("Lien supprimé.", "warning")
            return redirect(url_for("pedagogie.plan_projet"))

    projet_id = request.args.get("projet_id", type=int)
    projets = Projet.query.order_by(Projet.nom.asc()).all()
    ateliers = AtelierActivite.query.filter(AtelierActivite.is_deleted.is_(False)).order_by(AtelierActivite.nom.asc()).all()
    modules = PedagogieModule.query.filter(PedagogieModule.actif.is_(True)).order_by(PedagogieModule.nom.asc()).all()

    rows_q = PlanProjetAtelierModule.query
    if projet_id:
        rows_q = rows_q.filter(PlanProjetAtelierModule.projet_id == projet_id)
    rows = rows_q.order_by(PlanProjetAtelierModule.created_at.desc()).all()

    return render_template("pedagogie/plan_projet.html", projets=projets, ateliers=ateliers, modules=modules, rows=rows, projet_id=projet_id)


# ============================================================
# Passeport
# ============================================================

@bp.route("/participant/<int:participant_id>/passeport")
@login_required
@require_perm("pedagogie:view")
def participant_passeport(participant_id: int):
    participant, events, current_levels = participant_timeline(participant_id)

    # current_levels est déjà enrichi par le portail (max) dans participant_timeline.
    # On conserve la progression détaillée pour l'affichage spécifique du passeport.
    portail_progress = progression_portail(participant_id)

    selected_secteur = (request.args.get("secteur") or "").strip()
    selected_categorie = _normalize_note_category(request.args.get("categorie")) if request.args.get("categorie") else ""
    notes_page = max(request.args.get("notes_page", 1, type=int), 1)
    files_page = max(request.args.get("files_page", 1, type=int), 1)
    page_size = 20

    presence_rows = (
        db.session.query(PresenceActivite)
        .join(SessionActivite, PresenceActivite.session_id == SessionActivite.id)
        .filter(PresenceActivite.participant_id == participant_id)
        .all()
    )
    sessions_by_secteur = defaultdict(list)
    for row in presence_rows:
        if not row.session:
            continue
        sec = (row.session.secteur or "Sans secteur").strip()
        sessions_by_secteur[sec].append(row.session)

    notes_q = PasseportNote.query.filter_by(participant_id=participant_id)
    files_q = PasseportPieceJointe.query.filter_by(participant_id=participant_id)
    if selected_secteur:
        notes_q = notes_q.filter(PasseportNote.secteur == selected_secteur)
        files_q = files_q.filter(PasseportPieceJointe.secteur == selected_secteur)

    if selected_categorie:
        notes_q = notes_q.filter(PasseportNote.categorie == selected_categorie)

    notes_total = notes_q.count()
    files_total = files_q.count()

    notes = (
        notes_q.order_by(PasseportNote.created_at.desc())
        .offset((notes_page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    files = (
        files_q.order_by(PasseportPieceJointe.created_at.desc())
        .offset((files_page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    all_notes = PasseportNote.query.filter_by(participant_id=participant_id).all()
    sectors = sorted(
        {(e.session.secteur if e.session else "") for e in events if e.session and e.session.secteur}
        | {(n.secteur or "") for n in all_notes if n.secteur}
        | set(sessions_by_secteur.keys())
    )
    if not sectors:
        sectors = ["Global"]

    notes_scope = [n for n in all_notes if (n.secteur or "") == selected_secteur] if selected_secteur else all_notes
    notes_by_cat = defaultdict(int)
    for n in notes_scope:
        notes_by_cat[_normalize_note_category(n.categorie)] += 1

    comp_ids = set(current_levels.keys())
    comp_rows = Competence.query.filter(Competence.id.in_(comp_ids)).all() if comp_ids else []
    comp_map = {c.id: c for c in comp_rows}

    referentiel_stats = defaultdict(lambda: {"total": 0, "valides": 0})
    for cid, lvl in current_levels.items():
        comp = comp_map.get(cid)
        if not comp:
            continue
        ref_name = comp.referentiel.nom if comp.referentiel else "Sans référentiel"
        referentiel_stats[ref_name]["total"] += 1
        if lvl >= 2:
            referentiel_stats[ref_name]["valides"] += 1

    summary = {}
    for sec in sectors:
        sec_sessions = sessions_by_secteur.get(sec, []) if sec != "Global" else [s for values in sessions_by_secteur.values() for s in values]
        sec_notes = [n for n in all_notes if (n.secteur or "") == sec] if sec != "Global" else all_notes
        sec_events = [e for e in events if e.session and e.session.secteur == sec] if sec != "Global" else events
        last_date = None
        for sess in sec_sessions:
            d = sess.date_session or sess.rdv_date
            if d and (last_date is None or d > last_date):
                last_date = d
        validated = len({e.competence_id for e in sec_events if e.etat >= 2})

        # --- Intensité réelle basée sur les dates ---
        dates = []
        for sess in sec_sessions:
            d = sess.date_session or sess.rdv_date
            if d:
                dates.append(d)

        if len(dates) >= 2:
            first_date = min(dates)
            last_date = max(dates)
            delta_days = (last_date - first_date).days
            months = max(delta_days / 30.0, 1)  # évite division par zéro
            intensity = round(len(dates) / months, 1)
        elif len(dates) == 1:
            intensity = 1.0
        else:
            intensity = 0.0
        # validated = len({e.competence_id for e in sec_events if e.etat >= 2})
        # intensity = round((len(sec_sessions) / 90.0) * 30.0, 1)
        summary[sec] = {
            "nb_sessions": len(sec_sessions),
            "last_participation": last_date,
            "nb_notes": len(sec_notes),
            "nb_competences_validees": validated,
            "intensity": intensity,
        }

    # --- Evaluation rapide (hors session ou rattachée) ---
    all_competences = (
        Competence.query.join(Referentiel, Competence.referentiel_id == Referentiel.id, isouter=True)
        .order_by(Referentiel.nom.asc().nullslast(), Competence.code.asc())
        .all()
    )

    session_choices = []
    seen = set()
    for row in presence_rows:
        if not row.session:
            continue
        if row.session.id in seen:
            continue
        seen.add(row.session.id)
        session_choices.append(row.session)

    session_choices = sorted(
        session_choices,
        key=lambda ss: (ss.date_session or ss.rdv_date or ss.created_at or datetime.datetime.min),
        reverse=True,
    )[:60]

    from app.models import PortailAttempt
    from app.services.portail_apprenants import portail_configure
    portail_attempts = (
        PortailAttempt.query.filter_by(participant_id=participant_id)
        .order_by(PortailAttempt.finished_at.desc().nullslast())
        .all()
    )

    # --- Échelle de Hart (participation des habitants) ---
    from app.models import HartEvaluation, HART_TYPES_EVALUATION
    from app.services import hart as hart_service
    hart_evaluations = (
        HartEvaluation.query.filter_by(participant_id=participant_id)
        .order_by(HartEvaluation.date_evaluation.asc(), HartEvaluation.id.asc())
        .all()
    )
    hart_due = hart_service.evaluation_due(participant_id)

    # --- Bénévolat (heures données, pour l'onglet Participation) ---
    from app.services.benevolat import heures_participant
    benevolat = heures_participant(participant_id, annee=datetime.date.today().year)
    # Détail des compétences alimentées par le portail (traçabilité).
    portail_comp_ids = list(portail_progress.keys())
    portail_comp_lookup = {
        c.id: c for c in (Competence.query.filter(Competence.id.in_(portail_comp_ids)).all() if portail_comp_ids else [])
    }
    portail_competences = sorted(
        (
            {"competence": portail_comp_lookup[cid], **info}
            for cid, info in portail_progress.items() if cid in portail_comp_lookup
        ),
        key=lambda x: (x["competence"].code or ""),
    )

    return render_template(
        "pedagogie/participant_passeport.html",
        participant=participant,
        events=events,
        current_levels=current_levels,
        summary=summary,
        sectors=sectors,
        selected_secteur=selected_secteur,
        notes=notes,
        notes_by_cat=notes_by_cat,
        selected_categorie=selected_categorie,
        note_categories=PASSPORT_NOTE_CATEGORIES,
        files=files,
        notes_page=notes_page,
        files_page=files_page,
        notes_total=notes_total,
        files_total=files_total,
        page_size=page_size,
        all_competences=all_competences,
        session_choices=session_choices,
        comp_map=comp_map,
        referentiel_stats=referentiel_stats,
        portail_attempts=portail_attempts,
        portail_competences=portail_competences,
        portail_comp_ids=set(portail_progress.keys()),
        portail_configure=portail_configure(),
        hart_evaluations=hart_evaluations,
        hart_due=hart_due,
        hart_niveaux=hart_service.HART_NIVEAUX,
        hart_couleurs={n: info["couleur"] for n, info in hart_service.HART_NIVEAUX_DICT.items()},
        hart_types=HART_TYPES_EVALUATION,
        benevolat=benevolat,
    )


@bp.route("/portail-competences", methods=["GET"])
@login_required
@require_perm("pedagogie:view")
def portail_competences():
    """Correspondance globale : exercices du portail → compétences de l'ERP."""
    from app.models import Competence, PortailAttempt, PortailCompetenceMap

    activities = [
        r[0]
        for r in db.session.query(PortailAttempt.activity)
        .filter(PortailAttempt.activity.isnot(None))
        .distinct()
        .order_by(PortailAttempt.activity.asc())
        .all()
        if r[0]
    ]
    mappings = PortailCompetenceMap.query.order_by(PortailCompetenceMap.activity.asc()).all()
    competences = (
        Competence.query.join(Referentiel, Competence.referentiel_id == Referentiel.id, isouter=True)
        .order_by(Referentiel.nom.asc().nullslast(), Competence.code.asc())
        .all()
    )
    from app.models import PortailSyncState
    from app.services.portail_apprenants import portail_configure
    return render_template(
        "pedagogie/portail_competences.html",
        activities=activities,
        mappings=mappings,
        competences=competences,
        sync_state=db.session.get(PortailSyncState, 1),
        portail_configure=portail_configure(),
    )


@bp.route("/portail-competences/add", methods=["POST"])
@login_required
@require_perm("pedagogie:edit")
def portail_competences_add():
    from app.models import PortailCompetenceMap

    activity = (request.form.get("activity") or "").strip()
    competence_id = request.form.get("competence_id", type=int)
    seuil = request.form.get("seuil_pct", type=int)
    seuil = 80 if seuil is None else max(0, min(100, seuil))
    if not activity or not competence_id:
        flash("Choisissez un exercice et une compétence.", "danger")
        return redirect(url_for("pedagogie.portail_competences"))
    existing = PortailCompetenceMap.query.filter_by(activity=activity, competence_id=competence_id).first()
    if existing:
        existing.seuil_pct = seuil
        existing.actif = True
        flash("Correspondance mise à jour.", "success")
    else:
        db.session.add(
            PortailCompetenceMap(activity=activity, competence_id=competence_id, seuil_pct=seuil, actif=True)
        )
        flash("Correspondance ajoutée.", "success")
    db.session.commit()
    return redirect(url_for("pedagogie.portail_competences"))


@bp.route("/portail-competences/<int:map_id>/delete", methods=["POST"])
@login_required
@require_perm("pedagogie:edit")
def portail_competences_delete(map_id: int):
    from app.models import PortailCompetenceMap

    m = db.get_or_404(PortailCompetenceMap, map_id)
    db.session.delete(m)
    db.session.commit()
    flash("Correspondance supprimée.", "success")
    return redirect(url_for("pedagogie.portail_competences"))


@bp.route("/portail-competences/sync", methods=["POST"])
@login_required
@require_perm("pedagogie:edit")
def portail_competences_sync():
    """Lance la synchronisation des résultats du portail depuis l'ERP.

    Revient sur la page d'origine si un ``next`` local est fourni (ex. bouton
    du tableau de bord), sinon sur la page des correspondances.
    """
    from app.services.portail_apprenants import portail_configure, synchroniser_attempts

    nxt = (request.form.get("next") or "").strip()
    dest = nxt if (nxt.startswith("/") and not nxt.startswith("//")) else url_for("pedagogie.portail_competences")

    if not portail_configure():
        flash("Le portail n'est pas configuré (PORTAIL_BASE_URL / PORTAIL_TOKEN).", "warning")
        return redirect(dest)
    try:
        resume = synchroniser_attempts()
        flash(
            f"Synchronisation réussie : {resume['nouvelles']} nouvelle(s) tentative(s) "
            f"sur {resume['recues']} reçue(s).",
            "success",
        )
    except Exception as exc:  # réseau / portail indisponible : on ne plante pas
        current_app.logger.exception("Échec de la synchro portail (manuelle)")
        flash(f"La synchronisation a échoué : {exc}", "danger")
    return redirect(dest)


@bp.route("/participant/<int:participant_id>/passeport/portail", methods=["POST"])
@login_required
@require_perm("pedagogie:edit")
def participant_passeport_portail(participant_id: int):
    """Crée manuellement le profil portail (code apprenant) d'un participant déjà
    présent dans l'ERP, sans attendre une nouvelle inscription en parcours insertion."""
    from app.models import Participant
    from app.services.portail_apprenants import assurer_code_portail, portail_configure

    participant = db.get_or_404(Participant, participant_id)
    if not portail_configure():
        flash("L'intégration au portail n'est pas configurée (PORTAIL_BASE_URL / PORTAIL_TOKEN).", "warning")
    elif assurer_code_portail(participant):
        flash(f"Profil portail créé (code {participant.portail_code}).", "success")
    else:
        flash("Le portail n'a pas pu créer le profil pour le moment. Réessayez plus tard.", "danger")
    return redirect(url_for("pedagogie.participant_passeport", participant_id=participant_id, tab="portail"))


@bp.route("/participant/<int:participant_id>/passeport/evaluation", methods=["POST"])
@login_required
@require_perm("pedagogie:edit")
def participant_passeport_evaluation(participant_id: int):
    """Créer / mettre à jour une évaluation (hors session ou rattachée)."""
    comp_id = request.form.get("competence_id", type=int)
    secteur = (request.form.get("secteur") or "").strip() or ""

    if not comp_id:
        flash("Compétence manquante.", "danger")
        return redirect(url_for("pedagogie.participant_passeport", participant_id=participant_id, secteur=secteur))

    try:
        etat = int(request.form.get("etat", 0))
    except Exception:
        etat = 0
    if etat not in (0, 1, 2, 3):
        etat = 0

    commentaire = (request.form.get("commentaire") or "").strip() or None

    session_id = request.form.get("session_id", type=int)
    if session_id == 0:
        session_id = None

    q = (
        Evaluation.query
        .filter_by(participant_id=participant_id, competence_id=comp_id, session_id=session_id)
        .order_by(Evaluation.date_evaluation.desc(), Evaluation.id.desc())
    )

    existing = q.first()
    dupes = q.offset(1).all()
    for d in dupes:
        db.session.delete(d)


    eval_date = datetime.date.today()
    if existing:
        existing.etat = etat
        existing.commentaire = commentaire
        existing.user_id = current_user.id
        existing.date_evaluation = eval_date
    else:
        ev = Evaluation(
            participant_id=participant_id,
            competence_id=comp_id,
            session_id=session_id,
            user_id=current_user.id,
            etat=etat,
            date_evaluation=eval_date,
            commentaire=commentaire,
        )
        db.session.add(ev)

    db.session.commit()
    flash("Évaluation enregistrée.", "success")
    return redirect(url_for("pedagogie.participant_passeport", participant_id=participant_id, secteur=secteur))


# ============================================================
# Passeport : Notes
# ============================================================

@bp.route("/participant/<int:participant_id>/passeport/note", methods=["POST"])
@login_required
@require_perm("pedagogie:edit")
def participant_passeport_note(participant_id: int):
    contenu = (request.form.get("contenu") or "").strip()
    if not contenu:
        flash("Le texte de la note est obligatoire.", "danger")
        return redirect(url_for("pedagogie.participant_passeport", participant_id=participant_id))

    note = PasseportNote(
        participant_id=participant_id,
        session_id=request.form.get("session_id", type=int),
        secteur=(request.form.get("secteur") or "").strip() or None,
        categorie=_normalize_note_category(request.form.get("categorie")),
        contenu=contenu,
        created_by=current_user.id,
    )
    db.session.add(note)
    db.session.commit()
    flash("Note passeport enregistrée.", "success")
    return redirect(url_for("pedagogie.participant_passeport", participant_id=participant_id, secteur=note.secteur or ""))


@bp.route("/participant/<int:participant_id>/passeport/note/<int:note_id>/update", methods=["POST"])
@login_required
@require_perm("pedagogie:edit")
def participant_passeport_note_update(participant_id: int, note_id: int):
    note = db.get_or_404(PasseportNote, note_id)
    if note.participant_id != participant_id:
        flash("Note invalide.", "danger")
        return redirect(url_for("pedagogie.participant_passeport", participant_id=participant_id))

    contenu = (request.form.get("contenu") or "").strip()
    if not contenu:
        flash("Le texte de la note est obligatoire.", "danger")
        return redirect(url_for("pedagogie.participant_passeport", participant_id=participant_id, secteur=note.secteur or ""))

    note.contenu = contenu
    note.categorie = _normalize_note_category(request.form.get("categorie"))
    db.session.commit()

    flash("Note mise à jour.", "success")
    return redirect(url_for("pedagogie.participant_passeport", participant_id=participant_id, secteur=note.secteur or ""))


@bp.route("/participant/<int:participant_id>/passeport/note/<int:note_id>/delete", methods=["POST"])
@login_required
@require_perm("pedagogie:edit")
def participant_passeport_note_delete(participant_id: int, note_id: int):
    note = db.get_or_404(PasseportNote, note_id)
    if note.participant_id != participant_id:
        flash("Note invalide.", "danger")
        return redirect(url_for("pedagogie.participant_passeport", participant_id=participant_id))

    secteur = note.secteur or ""
    db.session.delete(note)
    db.session.commit()

    flash("Note supprimée.", "success")
    return redirect(url_for("pedagogie.participant_passeport", participant_id=participant_id, secteur=secteur))


# ============================================================
# Passeport : Pièces jointes (Upload / Download)
# ============================================================

@bp.route("/participant/<int:participant_id>/passeport/upload", methods=["POST"], endpoint="participant_passeport_file_upload")
@login_required
@require_perm("pedagogie:view")
def participant_passeport_file_upload(participant_id: int):
    f = request.files.get("file")
    if not f or not f.filename:
        flash("Fichier manquant.", "danger")
        return redirect(url_for("pedagogie.participant_passeport", participant_id=participant_id))

    filename = secure_filename(f.filename)
    if not filename:
        flash("Nom de fichier invalide.", "danger")
        return redirect(url_for("pedagogie.participant_passeport", participant_id=participant_id))

    root = os.path.join(current_app.instance_path, "passeport_uploads", str(participant_id))
    os.makedirs(root, exist_ok=True)

    save_name = f"{utcnow().strftime('%Y%m%d%H%M%S')}_{filename}"
    path = os.path.join(root, save_name)
    f.save(path)

    row = PasseportPieceJointe(
        participant_id=participant_id,
        session_id=request.form.get("session_id", type=int),
        secteur=(request.form.get("secteur") or "").strip() or None,
        categorie=(request.form.get("categorie") or "atelier").strip() or "atelier",
        titre=(request.form.get("titre") or "").strip() or None,
        file_path=path,
        original_name=filename,
        mime_type=f.mimetype or mimetypes.guess_type(filename)[0],
        created_by=current_user.id,
    )
    db.session.add(row)
    db.session.commit()
    flash("Pièce jointe ajoutée.", "success")
    return redirect(url_for("pedagogie.participant_passeport", participant_id=participant_id, secteur=row.secteur or ""))


@bp.route("/participant/<int:participant_id>/passeport/file/<int:file_id>")
@login_required
@require_perm("pedagogie:view")
def participant_passeport_file_download(participant_id: int, file_id: int):
    row = db.get_or_404(PasseportPieceJointe, file_id)
    if row.participant_id != participant_id:
        flash("Pièce jointe invalide.", "danger")
        return redirect(url_for("pedagogie.participant_passeport", participant_id=participant_id))
    return send_file(row.file_path, as_attachment=True, download_name=row.original_name)


# ============================================================
# Pilotage / Export
# ============================================================

@bp.route("/pilotage")
@login_required
@require_perm("pedagogie:view")
def pilotage_objectifs():
    projet_id = request.args.get("projet_id", type=int)
    start_date = request.args.get("start_date") or None
    end_date = request.args.get("end_date") or None

    start = datetime.datetime.strptime(start_date, "%Y-%m-%d").date() if start_date else None
    end = datetime.datetime.strptime(end_date, "%Y-%m-%d").date() if end_date else None

    rows = compute_objectif_scores(projet_id=projet_id, start_date=start, end_date=end)
    projets = Projet.query.order_by(Projet.nom.asc()).all()
    return render_template(
        "pedagogie/pilotage.html",
        rows=rows,
        projets=projets,
        projet_id=projet_id,
        start_date=start_date,
        end_date=end_date,
    )


@bp.route("/export_ra.csv")
@login_required
@require_perm("pedagogie:view")
def export_ra_csv():
    projet_id = request.args.get("projet_id", type=int)
    start_date = request.args.get("start_date") or None
    end_date = request.args.get("end_date") or None

    start = datetime.datetime.strptime(start_date, "%Y-%m-%d").date() if start_date else None
    end = datetime.datetime.strptime(end_date, "%Y-%m-%d").date() if end_date else None

    rows = compute_objectif_scores(projet_id=projet_id, start_date=start, end_date=end)

    sio = StringIO()
    w = csv.writer(sio)
    w.writerow(["Projet", "Type objectif", "Objectif", "Score atteinte %", "Nb évaluations", "Nb participants", "Progression moyenne"])
    for r in rows:
        obj = r["objectif"]
        w.writerow(
            [
                obj.projet.nom if obj.projet else "",
                obj.type,
                obj.titre,
                "" if r["score"] is None else r["score"],
                r["evaluations"],
                r["participants"],
                "" if r["progression_moyenne"] is None else r["progression_moyenne"],
            ]
        )

    return Response(
        sio.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=rapport_activite_pedagogie.csv"},
    )