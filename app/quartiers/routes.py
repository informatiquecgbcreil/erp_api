from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta

from flask import render_template, request, redirect, url_for, flash, jsonify, current_app
from flask_login import login_required
from sqlalchemy import func

from app.extensions import db
from app.models import Quartier, Participant, PresenceActivite, SessionActivite, AtelierActivite
from app.utils.delete_guard import commit_delete
from app.rbac import require_perm
from app.statsimpact.engine import _session_date_expr

from . import bp


def _load_quartiers():
    return Quartier.query.order_by(Quartier.ville.asc(), Quartier.nom.asc()).all()


@bp.route("/")
@login_required
@require_perm("quartiers:view")
def index():
    quartiers = _load_quartiers()
    return render_template("quartiers/index.html", quartiers=quartiers)


@bp.route("/new", methods=["POST"])
@login_required
@require_perm("quartiers:edit")
def create():
    ville = (request.form.get("ville") or "").strip() or None
    nom = (request.form.get("nom") or "").strip() or None
    description = (request.form.get("description") or "").strip() or None
    is_qpv = request.form.get("is_qpv") == "1"

    if not ville or not nom:
        flash("Ville et nom sont obligatoires.", "danger")
        return redirect(url_for("quartiers.index"))

    existing = Quartier.query.filter_by(ville=ville, nom=nom).first()
    if existing:
        flash("Ce quartier existe déjà pour cette ville.", "warning")
        return redirect(url_for("quartiers.index"))

    db.session.add(Quartier(ville=ville, nom=nom, description=description, is_qpv=is_qpv))
    db.session.commit()
    flash("Quartier ajouté.", "success")
    return redirect(url_for("quartiers.index"))


@bp.route("/<int:quartier_id>/edit", methods=["GET", "POST"])
@login_required
@require_perm("quartiers:edit")
def edit(quartier_id: int):
    quartier = db.get_or_404(Quartier, quartier_id)
    if request.method == "POST":
        ville = (request.form.get("ville") or "").strip() or None
        nom = (request.form.get("nom") or "").strip() or None
        description = (request.form.get("description") or "").strip() or None
        is_qpv = request.form.get("is_qpv") == "1"

        if not ville or not nom:
            flash("Ville et nom sont obligatoires.", "danger")
            return redirect(url_for("quartiers.edit", quartier_id=quartier.id))

        existing = (
            Quartier.query.filter_by(ville=ville, nom=nom)
            .filter(Quartier.id != quartier.id)
            .first()
        )
        if existing:
            flash("Un quartier avec ce nom existe déjà pour cette ville.", "warning")
            return redirect(url_for("quartiers.edit", quartier_id=quartier.id))

        quartier.ville = ville
        quartier.nom = nom
        quartier.description = description
        quartier.is_qpv = is_qpv

        # Placement manuel sur la carte (prioritaire, protégé du géocodage auto).
        lat_raw = (request.form.get("latitude") or "").strip().replace(",", ".")
        lon_raw = (request.form.get("longitude") or "").strip().replace(",", ".")
        if lat_raw and lon_raw:
            try:
                quartier.latitude = float(lat_raw)
                quartier.longitude = float(lon_raw)
                quartier.geo_manuel = True
            except ValueError:
                flash("Coordonnées ignorées (format invalide).", "warning")
        elif not lat_raw and not lon_raw and request.form.get("effacer_position") == "1":
            quartier.latitude = None
            quartier.longitude = None
            quartier.geo_manuel = False

        db.session.commit()
        flash("Quartier mis à jour.", "success")
        return redirect(url_for("quartiers.index"))

    return render_template(
        "quartiers/edit.html",
        quartier=quartier,
        tile_url=current_app.config.get("CARTO_TILE_URL")
        or "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
        tile_attribution=current_app.config.get("CARTO_TILE_ATTRIBUTION")
        or "© contributeurs OpenStreetMap",
    )


@bp.route("/<int:quartier_id>/delete", methods=["POST"])
@login_required
@require_perm("quartiers:delete")
def delete(quartier_id: int):
    quartier = db.get_or_404(Quartier, quartier_id)
    linked = Participant.query.filter_by(quartier_id=quartier.id).first()
    if linked:
        flash("Suppression impossible : ce quartier est lié à des participants.", "warning")
        return redirect(url_for("quartiers.index"))

    db.session.delete(quartier)
    commit_delete(
        f"le quartier « {quartier.nom} »",
        "Quartier supprimé.",
        blocked_message=f"Impossible de supprimer le quartier « {quartier.nom} » : il est encore utilisé ailleurs.",
    )
    return redirect(url_for("quartiers.index"))


@bp.route("/stats")
@login_required
@require_perm("quartiers:view")
def stats():
    quartiers = _load_quartiers()
    quartier_id = request.args.get("quartier_id")
    quartier = None

    period = (request.args.get("period") or "90").strip().lower()
    secteur_filter = (request.args.get("secteur") or "").strip()
    type_public_filter = (request.args.get("type_public") or "").strip()
    date_from_raw = (request.args.get("date_from") or "").strip()
    date_to_raw = (request.args.get("date_to") or "").strip()

    today = date.today()
    start_date = None
    end_date = today

    preset_days = {"30": 30, "90": 90, "180": 180, "365": 365}
    if period == "tout":
        # Tout l'historique : aucune borne de date.
        start_date = None
        end_date = None
    elif period.isdigit() and len(period) == 4:
        # Année de présence précise (ex. 2024) : du 1er janvier au 31 décembre.
        annee = int(period)
        start_date = date(annee, 1, 1)
        end_date = date(annee, 12, 31)
    elif period in preset_days:
        start_date = today - timedelta(days=preset_days[period] - 1)
    elif period == "year":
        start_date = date(today.year, 1, 1)
    elif period == "custom":
        try:
            start_date = datetime.strptime(date_from_raw, "%Y-%m-%d").date() if date_from_raw else None
        except ValueError:
            start_date = None
        try:
            end_date = datetime.strptime(date_to_raw, "%Y-%m-%d").date() if date_to_raw else today
        except ValueError:
            end_date = today
        if start_date and end_date and start_date > end_date:
            start_date, end_date = end_date, start_date
    else:
        period = "90"
        start_date = today - timedelta(days=89)

    stats_payload = None
    if quartier_id:
        try:
            quartier_id_int = int(quartier_id)
        except ValueError:
            quartier_id_int = None
        if quartier_id_int:
            quartier = db.session.get(Quartier, quartier_id_int)

    secteur_choices = [
        value for (value,) in db.session.query(SessionActivite.secteur)
        .filter(SessionActivite.secteur.isnot(None))
        .distinct()
        .order_by(SessionActivite.secteur.asc())
        .all() if value
    ]
    type_public_choices = [
        value for (value,) in db.session.query(Participant.type_public)
        .filter(Participant.type_public.isnot(None))
        .distinct()
        .order_by(Participant.type_public.asc())
        .all() if value
    ]

    if quartier:
        participants_base_q = Participant.query.filter(Participant.quartier_id == quartier.id)
        if type_public_filter:
            participants_base_q = participants_base_q.filter(Participant.type_public == type_public_filter)

        participants = participants_base_q.order_by(Participant.nom.asc(), Participant.prenom.asc()).all()
        participant_ids = [p.id for p in participants]
        participant_count = len(participants)
        ages = [p.age for p in participants if p.age is not None]
        avg_age = round(sum(ages) / len(ages), 1) if ages else None
        new_participants_count = 0
        if start_date:
            new_participants_count = participants_base_q.filter(func.date(Participant.created_at) >= start_date).filter(func.date(Participant.created_at) <= end_date).count()

        gender_counts = defaultdict(int)
        for p in participants:
            label = (p.genre or "").strip() or "Non renseigné"
            gender_counts[label] += 1

        session_date = _session_date_expr()
        filtered_presence_q = (
            db.session.query(PresenceActivite, Participant, SessionActivite, AtelierActivite)
            .join(Participant, Participant.id == PresenceActivite.participant_id)
            .join(SessionActivite, SessionActivite.id == PresenceActivite.session_id)
            .join(AtelierActivite, AtelierActivite.id == SessionActivite.atelier_id)
            .filter(Participant.quartier_id == quartier.id)
        )
        if type_public_filter:
            filtered_presence_q = filtered_presence_q.filter(Participant.type_public == type_public_filter)
        if secteur_filter:
            filtered_presence_q = filtered_presence_q.filter(SessionActivite.secteur == secteur_filter)
        if start_date:
            filtered_presence_q = filtered_presence_q.filter(session_date >= start_date)
        if end_date:
            filtered_presence_q = filtered_presence_q.filter(session_date <= end_date)

        presence_rows = filtered_presence_q.all()
        active_participant_ids = set()
        atelier_counter = defaultdict(lambda: {"participants": set(), "presences": 0})
        secteur_counter = defaultdict(lambda: {"participants": set(), "presences": 0})
        month_counter = defaultdict(int)
        participant_presence_counter = defaultdict(int)
        for presence, participant, session, atelier in presence_rows:
            active_participant_ids.add(participant.id)
            participant_presence_counter[participant.id] += 1
            atelier_bucket = atelier_counter[atelier.nom or "Atelier sans nom"]
            atelier_bucket["participants"].add(participant.id)
            atelier_bucket["presences"] += 1
            secteur_bucket = secteur_counter[(session.secteur or "Non renseigné")]
            secteur_bucket["participants"].add(participant.id)
            secteur_bucket["presences"] += 1
            try:
                raw_date = session.date_session if hasattr(session, "date_session") else None
            except Exception:
                raw_date = None
            if raw_date:
                month_counter[raw_date.strftime("%Y-%m")] += 1

        presence_count = sum(participant_presence_counter.values())
        active_participants_count = len(active_participant_ids)
        ateliers_count = len(atelier_counter)
        taux_activite = round((active_participants_count / participant_count) * 100, 1) if participant_count else 0

        secteur_rows = [
            (label, len(values["participants"]), values["presences"])
            for label, values in sorted(secteur_counter.items(), key=lambda item: (-item[1]["presences"], item[0].lower()))
        ]
        atelier_rows = [
            (label, len(values["participants"]), values["presences"])
            for label, values in sorted(atelier_counter.items(), key=lambda item: (-item[1]["presences"], item[0].lower()))
        ]
        month_rows = sorted(month_counter.items(), key=lambda item: item[0])

        top_participants = []
        if participant_ids:
            ranked = sorted(participants, key=lambda p: (-participant_presence_counter.get(p.id, 0), (p.nom or "").lower(), (p.prenom or "").lower()))[:10]
            for p in ranked:
                top_participants.append({
                    "id": p.id,
                    "label": f"{(p.prenom or '').strip()} {(p.nom or '').strip()}".strip(),
                    "presences": participant_presence_counter.get(p.id, 0),
                    "ville": p.ville or quartier.ville,
                    "type_public": p.type_public or "—",
                })

        participants_url = url_for("participants.list_participants", quartier_id=quartier.id)
        filters_summary = {
            "period_label": (
                f"Du {start_date.strftime('%d/%m/%Y')} au {end_date.strftime('%d/%m/%Y')}" if start_date else "Toutes les dates"
            ),
            "secteur": secteur_filter,
            "type_public": type_public_filter,
        }

        stats_payload = {
            "participant_count": participant_count,
            "active_participants_count": active_participants_count,
            "presence_count": int(presence_count),
            "avg_age": avg_age,
            "new_participants_count": new_participants_count,
            "ateliers_count": ateliers_count,
            "taux_activite": taux_activite,
            "gender_counts": dict(sorted(gender_counts.items(), key=lambda x: x[0].lower())),
            "secteurs": secteur_rows,
            "ateliers": atelier_rows,
            "months": month_rows,
            "top_participants": top_participants,
            "participants_url": participants_url,
            "filters_summary": filters_summary,
        }

    return render_template(
        "quartiers/stats.html",
        quartiers=quartiers,
        quartier=quartier,
        stats=stats_payload,
        period=period,
        date_from=(start_date.isoformat() if start_date else ""),
        date_to=(end_date.isoformat() if end_date else ""),
        secteur_filter=secteur_filter,
        type_public_filter=type_public_filter,
        secteur_choices=secteur_choices,
        type_public_choices=type_public_choices,
        annees=_annees_presence_cache(),
    )


def _annees_presence_cache():
    from app.services.cartographie import annees_de_presence

    return annees_de_presence()


# --------------------------------------------------------------------------
# Carte des habitants (agrégée par quartier, sans exposer de domicile)
# --------------------------------------------------------------------------
@bp.route("/carte")
@login_required
@require_perm("quartiers:view")
def carte():
    from app.services.geocodage import nombre_a_geocoder, nombre_quartiers_a_geocoder
    from app.services.cartographie import annees_de_presence

    secteur_choices = [
        v for (v,) in db.session.query(Participant.created_secteur)
        .filter(Participant.created_secteur.isnot(None))
        .distinct()
        .order_by(Participant.created_secteur.asc())
        .all() if v
    ]
    type_public_choices = [
        v for (v,) in db.session.query(Participant.type_public)
        .filter(Participant.type_public.isnot(None))
        .distinct()
        .order_by(Participant.type_public.asc())
        .all() if v
    ]

    return render_template(
        "quartiers/carte.html",
        secteur_choices=secteur_choices,
        type_public_choices=type_public_choices,
        annees=annees_de_presence(),
        restants=nombre_a_geocoder(),
        restants_quartiers=nombre_quartiers_a_geocoder(),
        tile_url=current_app.config.get("CARTO_TILE_URL")
        or "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
        tile_attribution=current_app.config.get("CARTO_TILE_ATTRIBUTION")
        or "© contributeurs OpenStreetMap",
    )


@bp.route("/carte/geocoder-quartiers", methods=["POST"])
@login_required
@require_perm("quartiers:edit")
def carte_geocoder_quartiers():
    from app.services import geocodage as geo

    force = request.form.get("force") == "1"
    try:
        resume = geo.synchroniser_geocodages_quartiers(force=force)
    except geo.GeocodageError as exc:
        flash(f"Géocodage indisponible : {exc}", "warning")
        return redirect(url_for("quartiers.carte"))

    if resume["erreurs"]:
        flash(
            f"Géocodage interrompu (réseau) : {resume['localises']} quartier(s) placé(s), "
            f"{resume['restants']} restant(s). Réessayez plus tard.",
            "warning",
        )
    else:
        msg = f"{resume['localises']} quartier(s) placé(s)"
        if resume["sans_resultat"]:
            msg += f", {resume['sans_resultat']} introuvable(s) (à placer à la main)"
        flash(msg + ".", "success")
    return redirect(url_for("quartiers.carte"))


@bp.route("/carte/data")
@login_required
@require_perm("quartiers:view")
def carte_data():
    from app.services.cartographie import repartition_par_quartier

    secteur = (request.args.get("secteur") or "").strip() or None
    type_public = (request.args.get("type_public") or "").strip() or None

    def _d(val):
        val = (val or "").strip()
        try:
            return datetime.strptime(val, "%Y-%m-%d").date() if val else None
        except ValueError:
            return None

    date_from = _d(request.args.get("date_from"))
    date_to = _d(request.args.get("date_to"))
    return jsonify(
        repartition_par_quartier(
            secteur=secteur, type_public=type_public, date_from=date_from, date_to=date_to
        )
    )


@bp.route("/carte/geocoder", methods=["POST"])
@login_required
@require_perm("quartiers:edit")
def carte_geocoder():
    from app.services import geocodage as geo

    try:
        limit = int(current_app.config.get("GEOCODAGE_BATCH") or 50)
    except (TypeError, ValueError):
        limit = 50
    try:
        resume = geo.synchroniser_geocodages(limit=limit)
    except geo.GeocodageError as exc:
        flash(f"Géocodage indisponible : {exc}", "warning")
        return redirect(url_for("quartiers.carte"))

    if resume["erreurs"]:
        flash(
            f"Géocodage interrompu (réseau) : {resume['localises']} adresse(s) localisée(s), "
            f"{resume['restants']} restante(s). Réessayez plus tard.",
            "warning",
        )
    else:
        msg = f"{resume['localises']} adresse(s) localisée(s)"
        if resume["non_localises"]:
            msg += f", {resume['non_localises']} sans coordonnées"
        if resume["restants"]:
            msg += f" · {resume['restants']} restante(s) — relancez pour continuer"
        flash(msg + ".", "success")
    return redirect(url_for("quartiers.carte"))
