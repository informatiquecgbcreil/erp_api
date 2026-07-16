"""Calculateur de coût unitaire — justifier une subvention en 2 minutes.

À partir d'un montant (saisi, ou repris d'une subvention) et de l'activité
réelle mesurée par l'émargement (séances, heures, participants uniques,
présences) sur des ateliers et une période choisis, calcule les coûts
unitaires : par participant, par présence, par heure, par séance.
"""
from datetime import date
from io import BytesIO

from flask import render_template, request, Response
from flask_login import login_required, current_user

from app.rbac import require_perm, can
from app.extensions import db
from app.models import AtelierActivite, SessionActivite, PresenceActivite, Subvention

from app.main.common import bp


def _parse_hhmm_minutes(value) -> int | None:
    try:
        h, m = str(value or "").split(":")[:2]
        return int(h) * 60 + int(m)
    except Exception:
        return None


def _duree_session_minutes(s: SessionActivite) -> int:
    """Durée d'une séance en minutes (heures collectives, durée RDV, sinon 0)."""
    if s.session_type == "COLLECTIF":
        d1, d2 = _parse_hhmm_minutes(s.heure_debut), _parse_hhmm_minutes(s.heure_fin)
        if d1 is not None and d2 is not None and d2 > d1:
            return d2 - d1
        return 0
    if s.duree_minutes:
        return int(s.duree_minutes)
    d1, d2 = _parse_hhmm_minutes(s.rdv_debut), _parse_hhmm_minutes(s.rdv_fin)
    if d1 is not None and d2 is not None and d2 > d1:
        return d2 - d1
    return 0


def mesures_activite(atelier_ids: list[int], date_from: date, date_to: date) -> dict:
    """Séances, heures, participants uniques et présences sur le périmètre."""
    eff = db.func.coalesce(SessionActivite.date_session, SessionActivite.rdv_date)
    sess_q = (SessionActivite.query
              .filter(SessionActivite.is_deleted.is_(False))
              .filter(eff >= date_from, eff <= date_to))
    if atelier_ids:
        sess_q = sess_q.filter(SessionActivite.atelier_id.in_(atelier_ids))
    sessions = sess_q.all()
    session_ids = [s.id for s in sessions]

    minutes = sum(_duree_session_minutes(s) for s in sessions)
    presences = 0
    uniques = 0
    if session_ids:
        presences = (db.session.query(db.func.count(PresenceActivite.id))
                     .filter(PresenceActivite.session_id.in_(session_ids)).scalar() or 0)
        uniques = (db.session.query(db.func.count(db.distinct(PresenceActivite.participant_id)))
                   .filter(PresenceActivite.session_id.in_(session_ids)).scalar() or 0)
    return {
        "sessions": len(sessions),
        "heures": round(minutes / 60.0, 1),
        "presences": int(presences),
        "uniques": int(uniques),
    }


def couts_unitaires(montant: float, mesures: dict) -> dict:
    def _div(d):
        return round(montant / d, 2) if d else None
    return {
        "par_participant": _div(mesures["uniques"]),
        "par_presence": _div(mesures["presences"]),
        "par_heure": _div(mesures["heures"]),
        "par_seance": _div(mesures["sessions"]),
    }


def _contexte_requete():
    """Lit et résout les paramètres (période, ateliers, montant / subvention)."""
    def _parse_date(raw, fallback):
        try:
            return date.fromisoformat((raw or "").strip())
        except Exception:
            return fallback

    today = date.today()
    date_from = _parse_date(request.args.get("from"), date(today.year, 1, 1))
    date_to = _parse_date(request.args.get("to"), date(today.year, 12, 31))
    if date_to < date_from:
        date_from, date_to = date_to, date_from

    atelier_ids = []
    for raw in request.args.getlist("atelier_id"):
        try:
            atelier_ids.append(int(raw))
        except Exception:
            continue

    subvention_id = None
    try:
        subvention_id = int(request.args.get("subvention_id") or 0) or None
    except Exception:
        subvention_id = None

    montant = 0.0
    base_label = None
    subvention = None
    if subvention_id:
        subvention = db.session.get(Subvention, subvention_id)
        if subvention is not None:
            montant = float(subvention.montant_attribue or 0) or float(subvention.montant_demande or 0)
            base_label = f"Subvention « {subvention.nom} » ({'attribué' if subvention.montant_attribue else 'demandé'})"
    if not montant:
        try:
            montant = round(float(str(request.args.get("montant") or "0").replace(",", ".").replace(" ", "")), 2)
        except Exception:
            montant = 0.0
        if montant:
            base_label = "Montant saisi"

    return date_from, date_to, atelier_ids, montant, base_label, subvention


@bp.route("/cout-unitaire")
@login_required
@require_perm("stats:view")
def cout_unitaire():
    portee_globale = can("scope:all_secteurs")
    secteur_impose = None if portee_globale else getattr(current_user, "secteur_assigne", None)

    ateliers_q = AtelierActivite.query.filter_by(is_deleted=False)
    subs_q = Subvention.query.filter_by(est_archive=False)
    if secteur_impose:
        ateliers_q = ateliers_q.filter(AtelierActivite.secteur == secteur_impose)
        subs_q = subs_q.filter(Subvention.secteur == secteur_impose)
    ateliers = ateliers_q.order_by(AtelierActivite.secteur.asc(), AtelierActivite.nom.asc()).all()
    subventions = subs_q.order_by(Subvention.annee_exercice.desc(), Subvention.nom.asc()).limit(200).all()

    date_from, date_to, atelier_ids, montant, base_label, subvention = _contexte_requete()
    # Périmètre secteur : un responsable ne mesure que ses ateliers.
    if secteur_impose:
        autorises = {a.id for a in ateliers}
        atelier_ids = [i for i in atelier_ids if i in autorises]
        if not atelier_ids:
            atelier_ids = list(autorises)

    mesures = mesures_activite(atelier_ids, date_from, date_to)
    couts = couts_unitaires(montant, mesures) if montant else None

    return render_template(
        "cout_unitaire.html",
        ateliers=ateliers,
        subventions=subventions,
        atelier_ids=atelier_ids,
        date_from=date_from, date_to=date_to,
        montant=montant, base_label=base_label, subvention=subvention,
        mesures=mesures, couts=couts,
        portee_globale=portee_globale,
    )


@bp.route("/cout-unitaire/export.xlsx")
@login_required
@require_perm("stats:view")
def cout_unitaire_export():
    from openpyxl import Workbook

    portee_globale = can("scope:all_secteurs")
    secteur_impose = None if portee_globale else getattr(current_user, "secteur_assigne", None)

    date_from, date_to, atelier_ids, montant, base_label, _sub = _contexte_requete()
    if secteur_impose:
        autorises = {a.id for a in AtelierActivite.query.filter_by(is_deleted=False, secteur=secteur_impose).all()}
        atelier_ids = [i for i in atelier_ids if i in autorises] or list(autorises)

    mesures = mesures_activite(atelier_ids, date_from, date_to)
    couts = couts_unitaires(montant, mesures)

    noms = [a.nom for a in AtelierActivite.query.filter(AtelierActivite.id.in_(atelier_ids)).all()] if atelier_ids else ["Tous"]

    wb = Workbook()
    ws = wb.active
    ws.title = "Coût unitaire"
    ws.append(["Période", f"{date_from.strftime('%d/%m/%Y')} → {date_to.strftime('%d/%m/%Y')}"])
    ws.append(["Ateliers", ", ".join(noms)])
    ws.append(["Base financière", base_label or "—"])
    ws.append(["Montant (€)", round(montant, 2)])
    ws.append([])
    ws.append(["Mesure", "Valeur", "Coût unitaire (€)"])
    ws.append(["Participants uniques", mesures["uniques"], couts["par_participant"] if couts["par_participant"] is not None else ""])
    ws.append(["Présences", mesures["presences"], couts["par_presence"] if couts["par_presence"] is not None else ""])
    ws.append(["Heures d'activité", mesures["heures"], couts["par_heure"] if couts["par_heure"] is not None else ""])
    ws.append(["Séances", mesures["sessions"], couts["par_seance"] if couts["par_seance"] is not None else ""])
    for col, width in zip("ABC", (26, 40, 18)):
        ws.column_dimensions[col].width = width

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return Response(
        buf.getvalue(),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=cout_unitaire.xlsx"},
    )
