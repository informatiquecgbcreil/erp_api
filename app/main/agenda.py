"""Mon agenda : flux iCal personnalisable + créneaux hors ateliers + export.

Contexte : pour certains financeurs (plateforme CSAT…), l'agenda est
extrait automatiquement chaque mois comme feuille de temps. Cette page
permet donc de contrôler finement ce que le flux contient (titre,
description, périmètre, fenêtre), d'y ajouter les temps hors ateliers
(réunions, préparation…), et d'exporter un fichier .ics sur une période
choisie pour vérifier ou archiver ce qui sera extrait.

Routes :
- ``/calendrier/<token>.ics`` : le flux, PUBLIC (jeton secret) pour que
  Google/Apple le relisent ; passe aussi par le tunnel « hors les murs » ;
- ``/mon-agenda`` : page personnelle (lien, réglages, créneaux, export) ;
- ``/mon-agenda/preferences`` : enregistre les réglages ;
- ``/mon-agenda/creneau`` (+ suppression) : temps hors ateliers ;
- ``/mon-agenda/export.ics`` : export ponctuel sur une période.
"""
from datetime import date, datetime, timedelta

from flask import Response, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.main.common import bp
from app.models import AgendaCreneau, Subvention, TYPES_CRENEAU, TYPES_CRENEAU_LABELS, User
from app.rbac import require_perm
from app.services.calendrier import (
    CHAMPS_DESCRIPTION,
    CHAMPS_DESCRIPTION_LABELS,
    TITRE_PRESETS,
    apercu_evenement,
    charger_options,
    generer_ics,
    regenerer_token,
    sauvegarder_options,
    token_ou_creer,
)
from app.services.public_urls import kiosk_public_base_url, public_base_url


def _parse_date(raw: str | None) -> date | None:
    try:
        return datetime.strptime((raw or "").strip(), "%Y-%m-%d").date()
    except Exception:
        return None


@bp.route("/calendrier/<token>.ics")
def calendrier_ics(token: str):
    """Flux iCal public (par jeton secret). Aucune connexion requise :
    c'est Google/Apple qui relit l'URL, pas un humain connecté."""
    token = (token or "").strip()
    user = User.query.filter_by(calendar_token=token).first() if token else None
    if user is None or not user.actif:
        abort(404)
    ics = generer_ics(
        user,
        base_url=kiosk_public_base_url(),
        lien_base=public_base_url(),
        nom_calendrier=f"Séances — {user.nom}",
    )
    return Response(ics, mimetype="text/calendar; charset=utf-8", headers={
        "Content-Disposition": "inline; filename=seances.ics",
        "Cache-Control": "no-cache",
    })


@bp.route("/mon-agenda")
@login_required
@require_perm("emargement:view")
def mon_agenda():
    token = token_ou_creer(current_user)
    base = kiosk_public_base_url().rstrip("/")
    url_https = f"{base}{url_for('main.calendrier_ics', token=token)}"
    url_webcal = url_https.replace("https://", "webcal://").replace("http://", "webcal://")

    options = charger_options(current_user)
    today = date.today()
    creneaux = (
        AgendaCreneau.query
        .filter(AgendaCreneau.user_id == current_user.id,
                AgendaCreneau.date_creneau >= today - timedelta(days=7))
        .order_by(AgendaCreneau.date_creneau.asc(), AgendaCreneau.id.asc())
        .limit(60)
        .all()
    )
    # Export par défaut : le mois précédent (c'est lui que la plateforme extrait).
    premier_du_mois = today.replace(day=1)
    fin_mois_prec = premier_du_mois - timedelta(days=1)
    debut_mois_prec = fin_mois_prec.replace(day=1)

    # Subventions proposables au rattachement d'un créneau (feuille de temps).
    subventions = (
        Subvention.query
        .filter(Subvention.est_archive.is_(False))
        .order_by(Subvention.annee_exercice.desc(), Subvention.nom.asc())
        .limit(100)
        .all()
    )

    return render_template(
        "mon_agenda.html",
        subventions=subventions,
        url_https=url_https,
        url_webcal=url_webcal,
        options=options,
        apercu=apercu_evenement(options),
        champs_description=CHAMPS_DESCRIPTION,
        champs_labels=CHAMPS_DESCRIPTION_LABELS,
        titre_presets=TITRE_PRESETS,
        creneaux=creneaux,
        types_creneau=TYPES_CRENEAU,
        types_creneau_labels=TYPES_CRENEAU_LABELS,
        export_du=debut_mois_prec,
        export_au=fin_mois_prec,
        today=today,
    )


@bp.post("/mon-agenda/preferences")
@login_required
@require_perm("emargement:view")
def mon_agenda_preferences():
    options = {
        "titre_format": (request.form.get("titre_format") or "").strip() or "{atelier}",
        "champs_description": request.form.getlist("champs_description"),
        "inclure_lien": request.form.get("inclure_lien") == "1",
        "inclure_annulees": request.form.get("inclure_annulees") == "1",
        "evenements_tous_secteurs": request.form.get("evenements_tous_secteurs") == "1",
        "inclure_creneaux": request.form.get("inclure_creneaux") == "1",
        "preparation_minutes": request.form.get("preparation_minutes"),
        "jours_passe": request.form.get("jours_passe"),
        "jours_futur": request.form.get("jours_futur"),
    }
    sauvegarder_options(current_user, options)
    flash("Réglages de l'agenda enregistrés. Ils s'appliquent au flux et aux exports (l'agenda abonné se mettra à jour à son prochain rafraîchissement).", "success")
    return redirect(url_for("main.mon_agenda"))


@bp.post("/mon-agenda/creneau")
@login_required
@require_perm("emargement:view")
def mon_agenda_creneau_creer():
    titre = (request.form.get("titre") or "").strip()
    d = _parse_date(request.form.get("date_creneau"))
    if not titre or d is None:
        flash("Le titre et la date du créneau sont obligatoires.", "danger")
        return redirect(url_for("main.mon_agenda"))
    type_creneau = (request.form.get("type_creneau") or "reunion").strip()
    if type_creneau not in TYPES_CRENEAU:
        type_creneau = "autre"
    heure_debut = (request.form.get("heure_debut") or "").strip() or None
    heure_fin = (request.form.get("heure_fin") or "").strip() or None
    description = (request.form.get("description") or "").strip() or None
    try:
        repetitions = max(0, min(52, int(request.form.get("repeter_semaines") or 0)))
    except Exception:
        repetitions = 0
    try:
        subvention_id = int(request.form.get("subvention_id") or 0) or None
    except Exception:
        subvention_id = None
    if subvention_id is not None and db.session.get(Subvention, subvention_id) is None:
        subvention_id = None

    for i in range(repetitions + 1):
        db.session.add(AgendaCreneau(
            user_id=current_user.id,
            type_creneau=type_creneau,
            titre=titre,
            date_creneau=d + timedelta(weeks=i),
            heure_debut=heure_debut,
            heure_fin=heure_fin,
            description=description,
            subvention_id=subvention_id,
        ))
    db.session.commit()
    if repetitions:
        flash(f"Créneau « {titre} » ajouté ({repetitions + 1} occurrences hebdomadaires).", "success")
    else:
        flash(f"Créneau « {titre} » ajouté à ton agenda.", "success")
    return redirect(url_for("main.mon_agenda"))


@bp.post("/mon-agenda/creneau/<int:creneau_id>/supprimer")
@login_required
@require_perm("emargement:view")
def mon_agenda_creneau_supprimer(creneau_id: int):
    c = db.session.get(AgendaCreneau, creneau_id)
    if c is None or c.user_id != current_user.id:
        abort(404)
    db.session.delete(c)
    db.session.commit()
    flash("Créneau supprimé.", "info")
    return redirect(url_for("main.mon_agenda"))


@bp.route("/mon-agenda/export.ics")
@login_required
@require_perm("emargement:view")
def mon_agenda_export():
    """Export ponctuel : fichier .ics sur la période choisie, avec les
    mêmes réglages que le flux — pratique pour vérifier ou archiver ce que
    la plateforme du financeur va extraire."""
    du = _parse_date(request.args.get("du"))
    au = _parse_date(request.args.get("au"))
    if du is None or au is None or du > au:
        flash("Choisis une période valide (date de début puis date de fin).", "danger")
        return redirect(url_for("main.mon_agenda"))
    if (au - du).days > 400:
        flash("La période d'export est limitée à 400 jours.", "danger")
        return redirect(url_for("main.mon_agenda"))
    ics = generer_ics(
        current_user,
        base_url=kiosk_public_base_url(),
        lien_base=public_base_url(),
        nom_calendrier=f"Séances — {current_user.nom}",
        du=du, au=au,
    )
    nom_fichier = f"agenda_{du.isoformat()}_{au.isoformat()}.ics"
    return Response(ics, mimetype="text/calendar; charset=utf-8", headers={
        "Content-Disposition": f"attachment; filename={nom_fichier}",
    })


@bp.post("/mon-agenda/regenerer")
@login_required
@require_perm("emargement:view")
def mon_agenda_regenerer():
    regenerer_token(current_user)
    flash("Nouveau lien généré : l'ancien ne fonctionne plus. Mets à jour ton agenda avec le nouveau lien.", "success")
    return redirect(url_for("main.mon_agenda"))
