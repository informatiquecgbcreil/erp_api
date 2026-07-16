"""Inscriptions préalables & listes d'attente — écrans par atelier / séance.

Mêmes gardes que le reste du module activité : permission dédiée
(inscriptions:view / inscriptions:edit) ET accessibilité secteur de
l'atelier (_atelier_est_accessible). Un rôle borné à son secteur ne voit
que les inscriptions de son secteur.
"""
from __future__ import annotations

import csv
import io
from datetime import date

from flask import Response, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.models import AtelierActivite, InscriptionActivite, Participant, SessionActivite
from app.rbac import require_perm
from app.services.inscriptions import (
    InscriptionErreur,
    a_pointer_session,
    annuler,
    compteurs,
    contacts_brevo,
    inscrire,
    liste_cible,
    pointer_present,
)

from . import bp
from .helpers import _atelier_est_accessible, _deny_activity_access, _session_est_accessible


def _atelier_autorise(atelier_id: int) -> AtelierActivite:
    atelier = db.get_or_404(AtelierActivite, atelier_id)
    if atelier.is_deleted or not _atelier_est_accessible(atelier):
        return None
    return atelier


def _recherche_participants(q: str, exclure_ids: set[int]) -> list[Participant]:
    q = (q or "").strip()
    if len(q) < 2:
        return []
    motif = f"%{q}%"
    trouves = (
        Participant.query
        .filter(db.or_(Participant.nom.ilike(motif), Participant.prenom.ilike(motif)))
        .order_by(Participant.nom.asc(), Participant.prenom.asc())
        .limit(20)
        .all()
    )
    return [p for p in trouves if p.id not in exclure_ids]


def _sessions_a_venir(atelier: AtelierActivite) -> list[dict]:
    eff = db.func.coalesce(SessionActivite.rdv_date, SessionActivite.date_session)
    sessions = (
        SessionActivite.query
        .filter(
            SessionActivite.atelier_id == atelier.id,
            SessionActivite.is_deleted.is_(False),
            eff >= date.today(),
        )
        .order_by(eff.asc())
        .limit(12)
        .all()
    )
    return [{"session": s, "etat": compteurs(atelier, s)} for s in sessions]


@bp.route("/atelier/<int:atelier_id>/inscriptions")
@login_required
@require_perm("inscriptions:view")
def inscriptions_atelier(atelier_id: int):
    atelier = _atelier_autorise(atelier_id)
    if atelier is None:
        return _deny_activity_access()

    listes = liste_cible(atelier)
    inscrits_ids = {i.participant_id for i in listes["inscrits"] + listes["attente"]}
    q = (request.args.get("q") or "").strip()

    return render_template(
        "activite/inscriptions.html",
        atelier=atelier,
        session=None,
        etat=compteurs(atelier),
        listes=listes,
        sessions_a_venir=_sessions_a_venir(atelier),
        a_pointer=None,
        q=q,
        resultats=_recherche_participants(q, inscrits_ids),
    )


@bp.route("/session/<int:session_id>/inscriptions")
@login_required
@require_perm("inscriptions:view")
def inscriptions_session(session_id: int):
    s = db.get_or_404(SessionActivite, session_id)
    atelier = db.get_or_404(AtelierActivite, s.atelier_id)
    if s.is_deleted or atelier.is_deleted or not _session_est_accessible(s):
        return _deny_activity_access()

    listes = liste_cible(atelier, s)
    inscrits_ids = {i.participant_id for i in listes["inscrits"] + listes["attente"]}
    q = (request.args.get("q") or "").strip()

    return render_template(
        "activite/inscriptions.html",
        atelier=atelier,
        session=s,
        etat=compteurs(atelier, s),
        listes=listes,
        sessions_a_venir=None,
        a_pointer=a_pointer_session(s),
        q=q,
        resultats=_recherche_participants(q, inscrits_ids),
    )


def _redirection_cible(atelier_id: int, session_id: int | None):
    if session_id:
        return redirect(url_for("activite.inscriptions_session", session_id=session_id))
    return redirect(url_for("activite.inscriptions_atelier", atelier_id=atelier_id))


@bp.route("/inscriptions/creer", methods=["POST"])
@login_required
@require_perm("inscriptions:edit")
def inscription_creer():
    atelier_id = request.form.get("atelier_id", type=int)
    session_id = request.form.get("session_id", type=int)
    participant_id = request.form.get("participant_id", type=int)

    atelier = _atelier_autorise(atelier_id) if atelier_id else None
    participant = db.session.get(Participant, participant_id) if participant_id else None
    if atelier is None:
        return _deny_activity_access()
    if participant is None:
        flash("Personne introuvable.", "danger")
        return _redirection_cible(atelier_id, session_id)

    s = None
    if session_id:
        s = db.session.get(SessionActivite, session_id)
        if s is None or s.is_deleted or s.atelier_id != atelier.id:
            flash("Séance introuvable pour cet atelier.", "danger")
            return _redirection_cible(atelier_id, None)

    try:
        inscription = inscrire(
            participant, atelier, s,
            commentaire=request.form.get("commentaire"),
            user_id=getattr(current_user, "id", None),
        )
    except InscriptionErreur as exc:
        flash(str(exc), "warning")
        return _redirection_cible(atelier_id, session_id)

    if inscription.statut == "attente":
        flash(
            f"{participant.prenom} {participant.nom} est en LISTE D'ATTENTE (jauge atteinte). "
            "Une annulation le/la fera passer automatiquement en inscrit·e.",
            "warning",
        )
    else:
        flash(f"{participant.prenom} {participant.nom} est inscrit·e ✅", "success")
    return _redirection_cible(atelier_id, session_id)


@bp.route("/inscriptions/<int:inscription_id>/annuler", methods=["POST"])
@login_required
@require_perm("inscriptions:edit")
def inscription_annuler(inscription_id: int):
    inscription = db.get_or_404(InscriptionActivite, inscription_id)
    atelier = _atelier_autorise(inscription.atelier_id)
    if atelier is None:
        return _deny_activity_access()

    p = inscription.participant
    promue = annuler(inscription)
    message = f"Inscription de {p.prenom} {p.nom} annulée."
    if promue is not None and promue.participant is not None:
        message += (
            f" {promue.participant.prenom} {promue.participant.nom} passe de la "
            "liste d'attente aux inscrits ✅ (pensez à le/la prévenir)."
        )
    flash(message, "info")
    return _redirection_cible(inscription.atelier_id, inscription.session_id)


@bp.route("/session/<int:session_id>/inscriptions/pointer", methods=["POST"])
@login_required
@require_perm("emargement:edit")
def inscription_pointer(session_id: int):
    """Pré-remplit l'émargement depuis une inscription (« pointer présent »)."""
    s = db.get_or_404(SessionActivite, session_id)
    if s.is_deleted or not _session_est_accessible(s):
        return _deny_activity_access()
    participant_id = request.form.get("participant_id", type=int)
    participant = db.session.get(Participant, participant_id) if participant_id else None
    if participant is None:
        flash("Personne introuvable.", "danger")
    else:
        pointer_present(s, participant.id)
        flash(f"{participant.prenom} {participant.nom} pointé·e présent·e ✅", "success")

    if (request.form.get("retour") or "") == "emargement":
        return redirect(url_for("activite.emargement", session_id=session_id))
    return redirect(url_for("activite.inscriptions_session", session_id=session_id))


@bp.route("/atelier/<int:atelier_id>/inscriptions/export-brevo.csv")
@login_required
@require_perm("inscriptions:view")
def inscriptions_export_brevo(atelier_id: int):
    """Contacts des inscrits au format d'import Brevo (EMAIL;NOM;PRENOM;SMS).

    À importer tel quel dans Brevo (Contacts → Importer) pour écrire aux
    inscrits d'un atelier ou d'une séance — l'e-mailing reste dans Brevo."""
    atelier = _atelier_autorise(atelier_id)
    if atelier is None:
        return _deny_activity_access()

    s = None
    session_id = request.args.get("session_id", type=int)
    if session_id:
        s = db.session.get(SessionActivite, session_id)
        if s is None or s.atelier_id != atelier.id:
            s = None

    contacts = contacts_brevo(atelier, s)
    sortie = io.StringIO()
    writer = csv.writer(sortie, delimiter=";")
    writer.writerow(["EMAIL", "NOM", "PRENOM", "SMS"])
    for c in contacts:
        writer.writerow([c["EMAIL"], c["NOM"], c["PRENOM"], c["SMS"]])

    nom_fichier = f"contacts_brevo_atelier_{atelier.id}"
    if s is not None:
        nom_fichier += f"_seance_{s.id}"
    return Response(
        "﻿" + sortie.getvalue(),  # BOM : accents corrects dans Excel
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={nom_fichier}.csv"},
    )
