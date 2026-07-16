"""Bénévolat — page Ressources : saisie des heures, missions, valorisation.

La saisie d'heures marque automatiquement la fiche comme bénévole. La photo
annuelle (bénévoles actifs, heures, valorisation €) alimente le SENACS et la
valorisation comptable des contributions volontaires (compte 87).
"""
from datetime import date
from io import BytesIO

from flask import render_template, request, redirect, url_for, flash, current_app, Response
from flask_login import login_required, current_user

from app.rbac import require_perm, can
from app.extensions import db
from app.models import Participant, BenevoleHeures
from app.services.benevolat import stats_annee
from app.services.audit import journaliser

from app.main.common import bp


def _annee_demandee() -> int:
    try:
        return int(request.args.get("annee") or date.today().year)
    except Exception:
        return date.today().year


@bp.route("/benevolat")
@login_required
@require_perm("participants:view")
def benevolat():
    annee = _annee_demandee()
    secteur = (request.args.get("secteur") or "").strip() or None
    if not can("scope:all_secteurs"):
        secteur = getattr(current_user, "secteur_assigne", None)

    stats = stats_annee(annee, secteur)
    annees = sorted({l.date_action.year for l in BenevoleHeures.query.all() if l.date_action} | {date.today().year}, reverse=True)
    secteurs = current_app.config.get("SECTEURS", []) or []

    return render_template(
        "benevolat.html",
        stats=stats,
        annee=annee,
        annees=annees,
        secteur=secteur,
        secteurs=secteurs,
        portee_globale=can("scope:all_secteurs"),
        today=date.today(),
        can_edit=can("participants:edit"),
        peut_regler_taux=can("benevolat:taux"),
    )


@bp.route("/benevolat/taux", methods=["POST"])
@login_required
@require_perm("benevolat:taux")
def benevolat_taux_update():
    """Règle le taux horaire de valorisation (réservé direction/finance)."""
    from app.services.instance_settings import get_or_create_instance_settings

    try:
        taux = round(float(str(request.form.get("taux") or "0").replace(",", ".")), 2)
    except Exception:
        taux = 0.0
    if not (0 < taux <= 100):
        flash("Le taux horaire doit être compris entre 0 et 100 €.", "danger")
        return redirect(url_for("main.benevolat"))

    row = get_or_create_instance_settings()
    ancien = row.benevolat_taux_horaire
    row.benevolat_taux_horaire = taux
    db.session.commit()
    journaliser("benevolat.taux", cible="instance_settings",
                details={"ancien": ancien, "nouveau": taux})
    flash(f"Taux horaire de valorisation réglé à {taux:g} €/h.", "success")
    return redirect(url_for("main.benevolat", annee=_annee_demandee()))


@bp.route("/benevolat/heures", methods=["POST"])
@login_required
@require_perm("participants:edit")
def benevolat_heures_create():
    try:
        participant_id = int(request.form.get("participant_id") or 0)
    except Exception:
        participant_id = 0
    participant = db.session.get(Participant, participant_id)
    if participant is None:
        flash("Choisis un bénévole dans la liste (recherche par nom).", "danger")
        return redirect(url_for("main.benevolat"))

    try:
        heures = round(float(str(request.form.get("heures") or "0").replace(",", ".")), 2)
    except Exception:
        heures = 0.0
    if heures <= 0:
        flash("Le nombre d'heures doit être supérieur à 0.", "danger")
        return redirect(url_for("main.benevolat"))

    try:
        date_action = date.fromisoformat((request.form.get("date_action") or "").strip())
    except Exception:
        date_action = date.today()

    ligne = BenevoleHeures(
        participant_id=participant.id,
        date_action=date_action,
        heures=heures,
        mission=(request.form.get("mission") or "").strip() or None,
        secteur=(request.form.get("secteur") or "").strip() or getattr(current_user, "secteur_assigne", None),
        commentaire=(request.form.get("commentaire") or "").strip() or None,
        created_by_user_id=getattr(current_user, "id", None),
    )
    # La saisie d'heures vaut déclaration de bénévolat.
    participant.est_benevole = True
    db.session.add(ligne)
    db.session.commit()
    journaliser("benevolat.heures", cible=f"participant#{participant.id}",
                details={"heures": heures, "date": date_action.isoformat(), "mission": ligne.mission})
    flash(f"{heures:g} h de bénévolat enregistrées pour {participant.prenom} {participant.nom}.", "success")
    return redirect(url_for("main.benevolat", annee=date_action.year))


@bp.route("/benevolat/heures/<int:ligne_id>/supprimer", methods=["POST"])
@login_required
@require_perm("participants:edit")
def benevolat_heures_supprimer(ligne_id: int):
    ligne = db.get_or_404(BenevoleHeures, ligne_id)
    annee = ligne.date_action.year if ligne.date_action else date.today().year
    pid = ligne.participant_id
    db.session.delete(ligne)
    db.session.commit()
    journaliser("benevolat.heures_suppr", cible=f"participant#{pid}", details={"ligne": ligne_id})
    flash("Ligne de bénévolat supprimée.", "warning")
    return redirect(url_for("main.benevolat", annee=annee))


@bp.route("/benevolat/export.xlsx")
@login_required
@require_perm("participants:view")
def benevolat_export_xlsx():
    from openpyxl import Workbook

    annee = _annee_demandee()
    secteur = (request.args.get("secteur") or "").strip() or None
    if not can("scope:all_secteurs"):
        secteur = getattr(current_user, "secteur_assigne", None)
    stats = stats_annee(annee, secteur)

    wb = Workbook()
    ws = wb.active
    ws.title = f"Bénévolat {annee}"
    ws.append(["Synthèse", ""])
    ws.append(["Bénévoles actifs (avec heures)", stats["nb_actifs"]])
    ws.append(["Heures totales", stats["total_heures"]])
    ws.append(["Taux horaire de valorisation (€)", stats["taux_horaire"]])
    ws.append(["Valorisation (compte 87, €)", stats["valorisation"]])
    ws.append([])
    ws.append(["Bénévole", "Heures", "Nb actions"])
    for r in stats["par_benevole"]:
        p = r["participant"]
        ws.append([f"{p.prenom} {p.nom}", r["heures"], r["nb_actions"]])
    ws.append([])
    ws.append(["Détail", "", "", "", ""])
    ws.append(["Date", "Bénévole", "Heures", "Mission", "Secteur"])
    for l in stats["lignes"]:
        p = l.participant
        ws.append([
            l.date_action.strftime("%d/%m/%Y") if l.date_action else "",
            f"{p.prenom} {p.nom}" if p else "",
            l.heures, l.mission or "", l.secteur or "",
        ])
    for col, width in zip("ABCDE", (32, 12, 12, 26, 18)):
        ws.column_dimensions[col].width = width

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return Response(
        buf.getvalue(),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=benevolat_{annee}.xlsx"},
    )
