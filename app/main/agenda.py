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
from app.models import AgendaCreneau, AtelierActivite, Subvention, TYPES_CRENEAU, TYPES_CRENEAU_LABELS, User
from app.rbac import require_perm
from app.services.calendrier import (
    CHAMPS_DESCRIPTION,
    CHAMPS_DESCRIPTION_LABELS,
    TITRE_PRESETS,
    apercu_evenement,
    charger_options,
    evenements_pour_periode,
    generer_ics,
    grouper_par_jour,
    regenerer_token,
    sauvegarder_options,
    token_ou_creer,
)
from app.services.poste_travail import JOURS_FR, MOIS_FR
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

    from app.services import google_agenda as ga

    google_configure = ga.est_configure()
    google_compte = getattr(current_user, "google_agenda", None)
    google_acces_a_reconnecter = ga.acces_a_reconnecter(google_compte)
    try:
        google_uri_retour = ga.uri_de_retour()
    except Exception:
        google_uri_retour = None

    return render_template(
        "mon_agenda.html",
        google_configure=google_configure,
        google_compte=google_compte,
        google_acces_a_reconnecter=google_acces_a_reconnecter,
        google_uri_retour=google_uri_retour,
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


@bp.get("/mon-agenda/calendrier/nouvelle-seance")
@login_required
@require_perm("emargement:view")
def aller_creer_seance():
    """Aiguillage depuis le calendrier vers le formulaire de séance habituel.

    Un formulaire GET ne peut pas fabriquer une URL à segment variable : cette
    route traduit « atelier + date » en redirection vers l'écran de création
    existant, qui garde toutes ses règles métier.
    """
    from app.activite.helpers import _atelier_est_accessible

    atelier_id = request.args.get("atelier_id", type=int)
    jour = _parse_date(request.args.get("date"))
    atelier = db.session.get(AtelierActivite, atelier_id) if atelier_id else None
    if atelier is None or atelier.is_deleted or not _atelier_est_accessible(atelier):
        flash("Atelier introuvable ou hors de ta portée.", "danger")
        return redirect(url_for("main.mon_agenda_calendrier"))
    return redirect(url_for(
        "activite.session_new",
        atelier_id=atelier.id,
        date=jour.isoformat() if jour else None,
    ))


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


def _retour_apres_creneau(defaut_date: date | None = None):
    """Redirection après action sur un créneau.

    Quand la demande vient de la vue calendrier, le formulaire poste un champ
    ``retour_calendrier`` (date ISO) : on revient sur le mois concerné plutôt
    que sur la page de réglages. Aucune URL libre n'est acceptée — seulement
    une date et une vue connue — donc pas de redirection ouverte possible.
    """
    if "retour_calendrier" not in request.form:
        # Formulaire de la page Mon agenda : comportement historique inchangé.
        return redirect(url_for("main.mon_agenda"))
    ancre = _parse_date(request.form.get("retour_calendrier")) or defaut_date or date.today()
    vue = (request.form.get("retour_vue") or "mois").strip().lower()
    if vue not in {"mois", "semaine"}:
        vue = "mois"
    return redirect(url_for("main.mon_agenda_calendrier", vue=vue, ancre=ancre.isoformat()))


def _champs_creneau_du_formulaire() -> dict | None:
    """Champs communs à la création et à la modification d'un créneau.
    Renvoie None si le minimum (titre + date) manque."""
    titre = (request.form.get("titre") or "").strip()
    d = _parse_date(request.form.get("date_creneau"))
    if not titre or d is None:
        return None
    type_creneau = (request.form.get("type_creneau") or "reunion").strip()
    if type_creneau not in TYPES_CRENEAU:
        type_creneau = "autre"
    try:
        subvention_id = int(request.form.get("subvention_id") or 0) or None
    except Exception:
        subvention_id = None
    if subvention_id is not None and db.session.get(Subvention, subvention_id) is None:
        subvention_id = None
    return {
        "titre": titre[:200],
        "date_creneau": d,
        "type_creneau": type_creneau,
        "heure_debut": (request.form.get("heure_debut") or "").strip() or None,
        "heure_fin": (request.form.get("heure_fin") or "").strip() or None,
        "description": (request.form.get("description") or "").strip() or None,
        "subvention_id": subvention_id,
    }


@bp.post("/mon-agenda/creneau")
@login_required
@require_perm("emargement:view")
def mon_agenda_creneau_creer():
    champs = _champs_creneau_du_formulaire()
    if champs is None:
        flash("Le titre et la date du créneau sont obligatoires.", "danger")
        return _retour_apres_creneau()
    try:
        repetitions = max(0, min(52, int(request.form.get("repeter_semaines") or 0)))
    except Exception:
        repetitions = 0

    for i in range(repetitions + 1):
        db.session.add(AgendaCreneau(
            user_id=current_user.id,
            **{**champs, "date_creneau": champs["date_creneau"] + timedelta(weeks=i)},
        ))
    db.session.commit()
    titre = champs["titre"]
    if repetitions:
        flash(f"Créneau « {titre} » ajouté ({repetitions + 1} occurrences hebdomadaires).", "success")
    else:
        flash(f"Créneau « {titre} » ajouté à ton agenda.", "success")
    return _retour_apres_creneau(champs["date_creneau"])


@bp.post("/mon-agenda/creneau/<int:creneau_id>/modifier")
@login_required
@require_perm("emargement:view")
def mon_agenda_creneau_modifier(creneau_id: int):
    """Modifie un créneau existant — y compris sa date, ce qui permet de le
    déplacer depuis la vue calendrier."""
    c = db.session.get(AgendaCreneau, creneau_id)
    if c is None or c.user_id != current_user.id:
        abort(404)
    champs = _champs_creneau_du_formulaire()
    if champs is None:
        flash("Le titre et la date du créneau sont obligatoires.", "danger")
        return _retour_apres_creneau(c.date_creneau)
    for cle, valeur in champs.items():
        setattr(c, cle, valeur)
    db.session.commit()
    flash(f"Créneau « {c.titre} » mis à jour.", "success")
    return _retour_apres_creneau(c.date_creneau)


@bp.post("/mon-agenda/creneau/<int:creneau_id>/supprimer")
@login_required
@require_perm("emargement:view")
def mon_agenda_creneau_supprimer(creneau_id: int):
    c = db.session.get(AgendaCreneau, creneau_id)
    if c is None or c.user_id != current_user.id:
        abort(404)
    jour = c.date_creneau
    db.session.delete(c)
    db.session.commit()
    flash("Créneau supprimé.", "info")
    return _retour_apres_creneau(jour)


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


# ---------------------------------------------------------------------------
# Vue calendrier (lecture) : le même contenu que le flux, mais dans l'app
# ---------------------------------------------------------------------------

def _mois_decale(reference: date, pas: int) -> date:
    """1er du mois situé `pas` mois avant/après la date de référence."""
    mois = reference.month - 1 + pas
    annee = reference.year + mois // 12
    return date(annee, mois % 12 + 1, 1)


@bp.route("/mon-agenda/calendrier")
@login_required
@require_perm("emargement:view")
def mon_agenda_calendrier():
    """Agenda mois par mois (ou semaine) directement dans l'application.

    Lecture seule : la saisie reste sur les écrans qui portent les règles
    métier (formulaire de séance, créneaux de Mon agenda). Le contenu vient
    des mêmes fonctions que le flux iCal, donc rien ne peut diverger.
    """
    import calendar as _calendar

    vue = (request.args.get("vue") or "mois").strip().lower()
    if vue not in {"mois", "semaine"}:
        vue = "mois"
    ancre = _parse_date(request.args.get("ancre")) or date.today()

    if vue == "semaine":
        debut = ancre - timedelta(days=ancre.weekday())
        semaines = [[debut + timedelta(days=i) for i in range(7)]]
        du, au = debut, debut + timedelta(days=6)
        titre_periode = f"Semaine du {debut.strftime('%d/%m')} au {(debut + timedelta(days=6)).strftime('%d/%m/%Y')}"
        precedent, suivant = debut - timedelta(days=7), debut + timedelta(days=7)
    else:
        premier = ancre.replace(day=1)
        semaines = _calendar.Calendar(firstweekday=0).monthdatescalendar(premier.year, premier.month)
        du, au = semaines[0][0], semaines[-1][-1]
        titre_periode = f"{MOIS_FR[premier.month - 1].capitalize()} {premier.year}"
        precedent, suivant = _mois_decale(premier, -1), _mois_decale(premier, 1)

    options = charger_options(current_user)
    evenements = evenements_pour_periode(current_user, du=du, au=au, options=options)

    # Panneau de saisie : ouvert soit sur un jour vide (« + »), soit sur un
    # créneau existant à modifier. Tout passe par l'URL, donc pas une ligne de
    # JavaScript et un bouton « retour » du navigateur qui fonctionne.
    jour_saisie = _parse_date(request.args.get("jour"))
    creneau_actif = None
    creneau_id = request.args.get("creneau", type=int)
    if creneau_id:
        candidat = db.session.get(AgendaCreneau, creneau_id)
        if candidat is not None and candidat.user_id == current_user.id:
            creneau_actif = candidat
            jour_saisie = candidat.date_creneau

    ateliers_disponibles = []
    subventions = []
    if jour_saisie is not None:
        subventions = (
            Subvention.query
            .filter(Subvention.est_archive.is_(False))
            .order_by(Subvention.annee_exercice.desc(), Subvention.nom.asc())
            .limit(100)
            .all()
        )
        # Ateliers proposés pour le raccourci « nouvelle séance » : ceux que la
        # personne peut réellement utiliser (son secteur, ou tous si portée
        # globale), plus les ateliers intersecteurs ouverts à tout le monde.
        aq = AtelierActivite.query.filter(
            AtelierActivite.is_deleted.is_(False),
            AtelierActivite.is_active.is_(True),
        )
        secteur_perso = (getattr(current_user, "secteur_assigne", None) or "").strip()
        if secteur_perso and not current_user.has_perm("scope:all_secteurs"):
            aq = aq.filter(db.or_(
                AtelierActivite.secteur == secteur_perso,
                AtelierActivite.est_intersecteur.is_(True),
            ))
        ateliers_disponibles = aq.order_by(
            AtelierActivite.secteur.asc(), AtelierActivite.nom.asc()
        ).limit(300).all()

    return render_template(
        "mon_agenda_calendrier.html",
        vue=vue,
        ancre=ancre,
        semaines=semaines,
        par_jour=grouper_par_jour(evenements),
        total_evenements=len(evenements),
        titre_periode=titre_periode,
        precedent=precedent,
        suivant=suivant,
        aujourdhui=date.today(),
        mois_affiche=(ancre.replace(day=1) if vue == "mois" else None),
        jours_fr=[j.capitalize() for j in JOURS_FR],
        options=options,
        jour_saisie=jour_saisie,
        creneau_actif=creneau_actif,
        ateliers_disponibles=ateliers_disponibles,
        subventions=subventions,
        types_creneau=TYPES_CRENEAU,
        types_creneau_labels=TYPES_CRENEAU_LABELS,
        peut_creer_seance=current_user.has_perm("ateliers:edit"),
    )
