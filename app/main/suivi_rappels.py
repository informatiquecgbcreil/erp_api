from datetime import date, timedelta

from app.utils.dates import utcnow


from app.services.indicators import compute_project_indicators
from flask import (
    render_template, request, redirect, url_for, flash, abort,
    current_app
)
from flask_login import login_required, current_user
from app.rbac import require_perm, can

from app.extensions import db
from app.models import (
    Projet,
    ChargeProjet,
    ProduitProjet,
    ProjetAction,
    ProjetJalon,
    OrientationAccesDroit,
    Subvention,
    SuiviRappel,
)


from app.main.common import bp


SUIVI_TONE_ORDER = {"danger": 0, "warn": 1, "info": 2, "ok": 3}
SUIVI_TYPE_ORDER = {
    "rappel": 0,
    "orientation": 1,
    "action": 2,
    "indicateur": 3,
    "budget": 4,
    "jalon": 5,
    "rgpd": 6,
}

SUIVI_ORIENTATION_STATUTS = {
    "oriente": "Orienté",
    "rdv_pris": "Rendez-vous pris",
    "accompagne": "Accompagné",
    "a_rappeler": "À rappeler",
    "resolu": "Résolu",
    "non_abouti": "Non abouti",
}

SUIVI_ORIENTATION_URGENCES = {
    "normale": "Normale",
    "prioritaire": "Prioritaire",
    "urgence": "Urgence",
}

SUIVI_ORIENTATION_DOMAINES = {
    "logement": "Logement",
    "sante": "Santé",
    "emploi": "Emploi / insertion",
    "famille": "Famille",
    "administratif": "Administratif",
    "budget": "Budget / dette",
    "mobilite": "Mobilité",
    "aide_alimentaire": "Aide alimentaire",
    "violences": "Violences / protection",
    "handicap": "Handicap",
    "education": "Éducation / parentalité",
    "autre": "Autre",
}

SUIVI_ACTION_STATUTS = {
    "prevue": "Prévue",
    "en_cours": "En cours",
    "realisee": "Réalisée",
    "annulee": "Annulée",
}

SUIVI_RAPPEL_CATEGORIES = {
    "general": "Général",
    "orientation": "Accès aux droits",
    "projet": "Projet",
    "finance": "Finance",
    "admin": "Administratif",
}

SUIVI_RAPPEL_PRIORITES = {
    "danger": "Critique",
    "warn": "À reprendre",
    "info": "Info",
}

SUIVI_RAPPEL_STATUTS = {
    "ouvert": "Ouvert",
    "fait": "Fait",
    "annule": "Annulé",
}


def _suivi_label(mapping: dict, code: str | None, fallback: str = "Non renseigné") -> str:
    if not code:
        return fallback
    return mapping.get(code, code.replace("_", " ").capitalize())


def _suivi_clean_choice(value: str | None, choices: dict, default: str) -> str:
    value = (value or "").strip()
    return value if value in choices else default


def _suivi_parse_date(value: str | None):
    try:
        value = (value or "").strip()
        if not value:
            return None
        return date.fromisoformat(value)
    except Exception:
        return None


def _suivi_as_date(value):
    if not value:
        return None
    if isinstance(value, date):
        return value
    date_method = getattr(value, "date", None)
    if callable(date_method):
        return date_method()
    return None


def _suivi_format_date(value) -> str:
    d = _suivi_as_date(value)
    if not d:
        return "Sans échéance"
    return d.strftime("%d/%m/%Y")


def _suivi_due_label(value, today: date) -> str:
    d = _suivi_as_date(value)
    if not d:
        return "Sans échéance"
    delta = (d - today).days
    if delta < 0:
        return f"En retard de {abs(delta)} j"
    if delta == 0:
        return "Aujourd'hui"
    if delta == 1:
        return "Demain"
    return f"Dans {delta} j"


def _suivi_float(value) -> float:
    try:
        return float(value or 0)
    except Exception:
        return 0.0


def _suivi_money(value) -> str:
    amount = _suivi_float(value)
    return f"{amount:,.0f} €".replace(",", " ")


def _suivi_indicator_value(row: dict) -> str:
    if row.get("value_type") == "check":
        return row.get("display_value") or ("Validé" if row.get("value") else "Non validé")
    value = row.get("value")
    if value is None:
        return "Non renseigné"
    try:
        value = float(value)
        rendered = f"{value:.2f}".rstrip("0").rstrip(".")
    except Exception:
        rendered = str(value)
    return f"{rendered}{row.get('unit') or ''}"


def _suivi_participant_label(participant, fallback: str | None = None) -> str:
    if participant:
        label = f"{participant.nom or ''} {participant.prenom or ''}".strip()
        if label:
            return label
    return (fallback or "").strip() or "Demandeur non renseigné"


def _suivi_add_item(
    items: list[dict],
    *,
    kind: str,
    type_label: str,
    tone: str,
    title: str,
    subtitle: str = "",
    meta: list[str] | None = None,
    due_date=None,
    secteur: str | None = None,
    url: str = "#",
    action_label: str = "Ouvrir",
    bucket: str | None = None,
    today: date | None = None,
    manual_id: int | None = None,
    can_quick_edit: bool = False,
    created_by: str | None = None,
    is_private: bool = False,
):
    today = today or date.today()
    due = _suivi_as_date(due_date)
    meta = [m for m in (meta or []) if m]
    if not bucket:
        bucket = "now" if tone == "danger" else ("soon" if tone == "warn" else "watch")
    search_blob = " ".join([kind, type_label, title, subtitle, secteur or "", *meta]).lower()
    items.append({
        "kind": kind,
        "type_label": type_label,
        "tone": tone if tone in SUIVI_TONE_ORDER else "info",
        "title": title,
        "subtitle": subtitle,
        "meta": meta,
        "due_date": due,
        "due_label": _suivi_due_label(due, today),
        "date_label": _suivi_format_date(due),
        "secteur": secteur or "Sans secteur",
        "url": url,
        "action_label": action_label,
        "bucket": bucket,
        "search_blob": search_blob,
        "manual_id": manual_id,
        "can_quick_edit": can_quick_edit,
        "created_by": created_by,
        "is_private": is_private,
    })


def _suivi_sort_key(item: dict):
    due = item.get("due_date") or date.max
    return (
        SUIVI_TONE_ORDER.get(item.get("tone"), 9),
        due,
        SUIVI_TYPE_ORDER.get(item.get("kind"), 9),
        (item.get("title") or "").lower(),
    )


def _suivi_projects_for_scope(secteur_filter: str | None = None) -> list[Projet]:
    q = Projet.query
    if not can("scope:all_secteurs"):
        q = q.filter(Projet.secteur == getattr(current_user, "secteur_assigne", None))
    elif secteur_filter:
        q = q.filter(Projet.secteur == secteur_filter)
    return q.order_by(Projet.nom.asc()).all()


def _suivi_rappel_visible_query(secteur_filter: str | None = None):
    q = SuiviRappel.query.filter(SuiviRappel.statut == "ouvert")
    current_user_id = getattr(current_user, "id", None)
    if can("scope:all_secteurs"):
        q = q.filter(db.or_(
            SuiviRappel.is_private.is_(False),
            SuiviRappel.created_by_user_id == current_user_id,
        ))
        if secteur_filter:
            q = q.filter(SuiviRappel.secteur == secteur_filter)
    else:
        user_secteur = getattr(current_user, "secteur_assigne", None)
        q = q.filter(db.or_(
            SuiviRappel.created_by_user_id == current_user_id,
            db.and_(SuiviRappel.is_private.is_(False), SuiviRappel.secteur == user_secteur),
            db.and_(SuiviRappel.is_private.is_(False), SuiviRappel.secteur.is_(None)),
        ))
    return q


def _suivi_can_edit_rappel(rappel: SuiviRappel) -> bool:
    if can("scope:all_secteurs"):
        return True
    if rappel.created_by_user_id == getattr(current_user, "id", None):
        return True
    if not rappel.is_private and rappel.secteur and rappel.secteur == getattr(current_user, "secteur_assigne", None):
        return True
    return False


def _suivi_rappel_bucket_and_tone(rappel: SuiviRappel, today: date, soon: date):
    due = rappel.echeance
    if due and due < today:
        return "danger", "now"
    if rappel.priorite == "danger":
        return "danger", "now"
    if due and due <= soon:
        return "warn", "soon"
    if rappel.priorite == "warn":
        return "warn", "soon"
    return "info", "watch"


def _suivi_redirect_back():
    return redirect(request.referrer or url_for("main.suivi_rappels"))


@bp.route("/suivi-rappels/rappel/new", methods=["POST"])
@login_required
@require_perm("dashboard:view")
def suivi_rappel_create():
    titre = (request.form.get("titre") or "").strip()
    if not titre:
        flash("Le titre du rappel est obligatoire.", "danger")
        return _suivi_redirect_back()

    is_private = request.form.get("is_private") == "1"
    secteur = (request.form.get("secteur") or "").strip() or None
    if not can("scope:all_secteurs"):
        secteur = getattr(current_user, "secteur_assigne", None)
    if is_private:
        secteur = None

    lien_url = (request.form.get("lien_url") or "").strip() or None
    if lien_url and not (lien_url.startswith("/") or lien_url.startswith("http://") or lien_url.startswith("https://")):
        lien_url = None

    rappel = SuiviRappel(
        titre=titre,
        description=(request.form.get("description") or "").strip() or None,
        categorie=_suivi_clean_choice(request.form.get("categorie"), SUIVI_RAPPEL_CATEGORIES, "general"),
        priorite=_suivi_clean_choice(request.form.get("priorite"), SUIVI_RAPPEL_PRIORITES, "warn"),
        secteur=secteur,
        echeance=_suivi_parse_date(request.form.get("echeance")),
        lien_url=lien_url,
        is_private=is_private,
        created_by_user_id=getattr(current_user, "id", None),
    )
    db.session.add(rappel)
    db.session.commit()
    flash("Rappel ajouté au centre de suivi.", "success")
    return redirect(url_for("main.suivi_rappels", type="rappel"))


@bp.route("/suivi-rappels/rappel/<int:rappel_id>/action", methods=["POST"])
@login_required
@require_perm("dashboard:view")
def suivi_rappel_action(rappel_id: int):
    rappel = db.get_or_404(SuiviRappel, rappel_id)
    if not _suivi_can_edit_rappel(rappel):
        abort(403)

    action = (request.form.get("action") or "").strip()
    if action == "done":
        rappel.statut = "fait"
        rappel.done_at = utcnow()
        flash("Rappel marqué comme fait.", "success")
    elif action == "postpone_7":
        base = rappel.echeance if rappel.echeance and rappel.echeance > date.today() else date.today()
        rappel.echeance = base + timedelta(days=7)
        rappel.statut = "ouvert"
        rappel.done_at = None
        flash("Rappel reporté de 7 jours.", "success")
    elif action == "cancel":
        rappel.statut = "annule"
        rappel.done_at = utcnow()
        flash("Rappel annulé.", "warning")
    else:
        abort(400)

    rappel.updated_at = utcnow()
    db.session.commit()
    return _suivi_redirect_back()


# --------- Centre de suivi / rappels ---------
@bp.route("/suivi-rappels")
@login_required
@require_perm("dashboard:view")
def suivi_rappels():
    today = date.today()
    soon = today + timedelta(days=7)
    action_soon = today + timedelta(days=14)
    items: list[dict] = []

    type_filter = (request.args.get("type") or "").strip()
    tone_filter = (request.args.get("priorite") or "").strip()
    q_filter = (request.args.get("q") or "").strip().lower()
    secteur_filter = (request.args.get("secteur") or "").strip()
    if secteur_filter and not can("scope:all_secteurs"):
        secteur_filter = ""

    projects = _suivi_projects_for_scope(secteur_filter) if can("projets:view") else []

    rappels = (
        _suivi_rappel_visible_query(secteur_filter)
        .order_by(SuiviRappel.echeance.asc(), SuiviRappel.created_at.desc())
        .limit(200)
        .all()
    )
    for rappel in rappels:
        tone, bucket = _suivi_rappel_bucket_and_tone(rappel, today, soon)
        creator = getattr(rappel.user, "nom", None) or getattr(rappel.user, "email", None) or ""
        _suivi_add_item(
            items,
            kind="rappel",
            type_label="Rappel",
            tone=tone,
            title=rappel.titre,
            subtitle=rappel.description or "",
            meta=[
                _suivi_label(SUIVI_RAPPEL_CATEGORIES, rappel.categorie),
                _suivi_label(SUIVI_RAPPEL_PRIORITES, rappel.priorite),
                "Personnel" if rappel.is_private else "Partagé",
                f"Créé par : {creator}" if creator else "",
            ],
            due_date=rappel.echeance,
            secteur="Personnel" if rappel.is_private else rappel.secteur,
            url=rappel.lien_url or "#",
            action_label="Ouvrir le lien" if rappel.lien_url else "Rappel manuel",
            bucket=bucket,
            today=today,
            manual_id=rappel.id,
            can_quick_edit=_suivi_can_edit_rappel(rappel),
            created_by=creator,
            is_private=rappel.is_private,
        )

    # --- Échéances de justification des subventions (radar financeurs) ---
    if can("subventions:view"):
        horizon_bilan = today + timedelta(days=45)
        sub_q = Subvention.query.filter(
            Subvention.est_archive.is_(False),
            ~Subvention.statut_cycle.in_(["soldee", "refusee"]),
        )
        if not can("scope:all_secteurs"):
            sub_q = sub_q.filter(Subvention.secteur == getattr(current_user, "secteur_assigne", None))
        elif secteur_filter:
            sub_q = sub_q.filter(Subvention.secteur == secteur_filter)

        for sub in sub_q.all():
            financeur = f" ({sub.financeur})" if sub.financeur else ""
            # Bilan / compte-rendu à rendre
            if sub.date_bilan_prevu and sub.date_bilan_prevu <= horizon_bilan:
                en_retard = sub.date_bilan_prevu < today
                _suivi_add_item(
                    items,
                    kind="subvention",
                    type_label="Justification",
                    tone="danger" if en_retard else "warn",
                    title=f"Bilan à rendre : {sub.nom}{financeur}",
                    subtitle=(
                        "Échéance dépassée — le solde peut être bloqué tant que le bilan n'est pas rendu."
                        if en_retard else
                        "La feuille de temps et le justificatif d'activité sont prêts à générer."
                    ),
                    meta=[f"Exercice {sub.annee_exercice}", sub.statut_label],
                    due_date=sub.date_bilan_prevu,
                    secteur=sub.secteur,
                    url=url_for("main.subvention_justificatif", subvention_id=sub.id),
                    action_label="Ouvrir le justificatif",
                    bucket="now" if en_retard else "soon",
                    today=today,
                )
            # Versement attendu non reçu
            if (sub.date_versement_prevu and sub.date_versement_prevu < today
                    and float(sub.montant_recu or 0) < float(sub.montant_attribue or 0)):
                _suivi_add_item(
                    items,
                    kind="subvention",
                    type_label="Justification",
                    tone="warn",
                    title=f"Versement attendu non reçu : {sub.nom}{financeur}",
                    subtitle=f"Prévu le {sub.date_versement_prevu.strftime('%d/%m/%Y')} — reçu {float(sub.montant_recu or 0):.2f} € sur {float(sub.montant_attribue or 0):.2f} €.",
                    meta=[f"Exercice {sub.annee_exercice}", sub.statut_label],
                    due_date=sub.date_versement_prevu,
                    secteur=sub.secteur,
                    url=url_for("main.subvention_pilotage", subvention_id=sub.id),
                    action_label="Ouvrir le pilotage",
                    bucket="soon",
                    today=today,
                )

    if can("partenaires:view"):
        orientation_q = OrientationAccesDroit.query.filter(
            ~OrientationAccesDroit.statut.in_(["resolu", "non_abouti"])
        ).filter(
            db.or_(
                OrientationAccesDroit.suite_prevue.isnot(None),
                OrientationAccesDroit.statut == "a_rappeler",
                OrientationAccesDroit.urgence.in_(["prioritaire", "urgence"]),
            )
        )
        if not can("scope:all_secteurs"):
            orientation_q = orientation_q.filter(db.or_(
                OrientationAccesDroit.secteur.is_(None),
                OrientationAccesDroit.secteur == getattr(current_user, "secteur_assigne", None),
            ))
        elif secteur_filter:
            orientation_q = orientation_q.filter(OrientationAccesDroit.secteur == secteur_filter)

        orientations = orientation_q.order_by(
            OrientationAccesDroit.suite_prevue.asc(),
            OrientationAccesDroit.created_at.desc(),
        ).limit(200).all()
        for row in orientations:
            due = row.suite_prevue
            tone = "info"
            bucket = "watch"
            if due and due < today:
                tone, bucket = "danger", "now"
            elif due and due <= soon:
                tone, bucket = "warn", "soon"
            elif row.urgence == "urgence":
                tone, bucket = "danger", "now"
            elif row.statut == "a_rappeler" or row.urgence == "prioritaire":
                tone, bucket = "warn", "soon"

            person_label = _suivi_participant_label(row.participant, row.demandeur_libre)
            year = (row.date_orientation or today).year
            _suivi_add_item(
                items,
                kind="orientation",
                type_label="Orientation",
                tone=tone,
                title=f"Suivi orientation : {person_label}",
                subtitle=row.demande or "",
                meta=[
                    _suivi_label(SUIVI_ORIENTATION_DOMAINES, row.domaine),
                    _suivi_label(SUIVI_ORIENTATION_STATUTS, row.statut),
                    _suivi_label(SUIVI_ORIENTATION_URGENCES, row.urgence),
                    row.partenaire.nom if row.partenaire else "Partenaire non renseigné",
                ],
                due_date=due,
                secteur=row.secteur,
                url=url_for("partenaires.orientations", year=year, statut=row.statut or "", q=person_label),
                action_label="Voir l'orientation",
                bucket=bucket,
                today=today,
            )

    if can("projets:view"):
        actions = (
            ProjetAction.query
            .join(Projet)
            .filter(~ProjetAction.statut.in_(["realisee", "annulee"]))
        )
        if not can("scope:all_secteurs"):
            actions = actions.filter(Projet.secteur == getattr(current_user, "secteur_assigne", None))
        elif secteur_filter:
            actions = actions.filter(Projet.secteur == secteur_filter)
        actions = actions.order_by(ProjetAction.date_fin.asc(), ProjetAction.created_at.desc()).limit(200).all()

        for action in actions:
            due = action.date_fin
            tone = bucket = None
            if due and due < today:
                tone, bucket = "danger", "now"
            elif due and due <= action_soon:
                tone, bucket = "warn", "soon"
            elif action.statut == "en_cours" and not due:
                tone, bucket = "info", "watch"
            if not tone:
                continue

            _suivi_add_item(
                items,
                kind="action",
                type_label="Fiche action",
                tone=tone,
                title=action.titre,
                subtitle=action.description or "Action projet à suivre.",
                meta=[
                    action.projet.nom if action.projet else "Projet non renseigné",
                    _suivi_label(SUIVI_ACTION_STATUTS, action.statut),
                    f"Référent : {action.referent}" if action.referent else "",
                    f"Lieu : {action.lieu}" if action.lieu else "",
                ],
                due_date=due,
                secteur=action.projet.secteur if action.projet else None,
                url=url_for("projets.projet_action_detail", projet_id=action.projet_id, action_id=action.id),
                action_label="Ouvrir la fiche",
                bucket=bucket,
                today=today,
            )

        indicator_count = 0
        for projet in projects:
            if indicator_count >= 80:
                break
            try:
                indicator_rows = compute_project_indicators(projet, selected_annee=today.year)
            except Exception:
                current_app.logger.exception("Centre de suivi: indicateurs impossibles à calculer pour projet %s", projet.id)
                continue
            for row in indicator_rows:
                if indicator_count >= 80:
                    break
                status = row.get("status")
                value_type = row.get("value_type")
                source = row.get("source")
                title = None
                tone = None
                bucket = "watch"

                if value_type == "check" and status == "bad":
                    title, tone, bucket = "Validation d'indicateur à cocher", "warn", "soon"
                elif source == "manual" and row.get("value") is None:
                    title, tone, bucket = "Valeur d'indicateur à renseigner", "warn", "soon"
                elif value_type != "check" and row.get("target") is None:
                    title, tone = "Objectif d'indicateur à préciser", "info"
                elif status == "bad":
                    title, tone, bucket = "Indicateur sous objectif", "danger", "now"
                elif status == "warn":
                    title, tone, bucket = "Indicateur à surveiller", "warn", "soon"

                if not title or not tone:
                    continue

                target = row.get("target")
                target_label = ""
                if target is not None:
                    target_label = f"Objectif : {row.get('target_symbol') or ''} {target}{row.get('unit') or ''}"
                _suivi_add_item(
                    items,
                    kind="indicateur",
                    type_label="Indicateur",
                    tone=tone,
                    title=title,
                    subtitle=row.get("label") or "Indicateur projet",
                    meta=[
                        projet.nom,
                        "Stats-impact" if source == "stats" else "Manuel",
                        f"Valeur : {_suivi_indicator_value(row)}",
                        target_label,
                    ],
                    due_date=None,
                    secteur=projet.secteur,
                    url=url_for("projets.projet_indicateurs", projet_id=projet.id),
                    action_label="Ouvrir indicateurs",
                    bucket=bucket,
                    today=today,
                )
                indicator_count += 1

        budget_count = 0
        for projet in projects:
            if budget_count >= 80:
                break
            for charge in ChargeProjet.query.filter_by(projet_id=projet.id).order_by(ChargeProjet.id.asc()).all():
                if budget_count >= 80:
                    break
                prev = _suivi_float(charge.montant_previsionnel)
                real = _suivi_float(charge.montant_reel)
                engaged = _suivi_float(charge.engage)
                base = real if real > 0 else prev
                if prev > 0 and real > prev + 0.01:
                    _suivi_add_item(
                        items,
                        kind="budget",
                        type_label="Budget",
                        tone="danger",
                        title="Charge au-dessus du prévisionnel",
                        subtitle=charge.libelle,
                        meta=[projet.nom, f"Prévu : {_suivi_money(prev)}", f"Réel : {_suivi_money(real)}"],
                        due_date=None,
                        secteur=projet.secteur,
                        url=url_for("projets.projet_budget_charges", projet_id=projet.id),
                        action_label="Voir les charges",
                        bucket="now",
                        today=today,
                    )
                    budget_count += 1
                elif base > 0 and engaged > base + 0.01:
                    _suivi_add_item(
                        items,
                        kind="budget",
                        type_label="Budget",
                        tone="danger",
                        title="Dépenses imputées au-delà du budget",
                        subtitle=charge.libelle,
                        meta=[projet.nom, f"Budget : {_suivi_money(base)}", f"Engagé : {_suivi_money(engaged)}"],
                        due_date=None,
                        secteur=projet.secteur,
                        url=url_for("projets.projet_finance", projet_id=projet.id, tab="depenses"),
                        action_label="Voir les dépenses",
                        bucket="now",
                        today=today,
                    )
                    budget_count += 1

            for produit in ProduitProjet.query.filter_by(projet_id=projet.id).order_by(ProduitProjet.id.asc()).all():
                if budget_count >= 80:
                    break
                accorde = _suivi_float(produit.montant_accorde)
                recu = _suivi_float(produit.montant_recu)
                if accorde > 0 and recu + 0.01 < accorde:
                    _suivi_add_item(
                        items,
                        kind="budget",
                        type_label="Budget",
                        tone="warn",
                        title="Financement accordé non reçu",
                        subtitle=produit.financeur,
                        meta=[projet.nom, f"Accordé : {_suivi_money(accorde)}", f"Reçu : {_suivi_money(recu)}"],
                        due_date=None,
                        secteur=projet.secteur,
                        url=url_for("projets.projet_budget_produits", projet_id=projet.id),
                        action_label="Voir les produits",
                        bucket="soon",
                        today=today,
                    )
                    budget_count += 1

            reste = _suivi_float(projet.reste_a_financer)
            if budget_count < 80 and _suivi_float(projet.total_charges_previsionnel) > 0 and reste > 0:
                _suivi_add_item(
                    items,
                    kind="budget",
                    type_label="Budget",
                    tone="info",
                    title="Reste à financer sur projet",
                    subtitle=projet.nom,
                    meta=[f"Charges prévues : {_suivi_money(projet.total_charges_previsionnel)}", f"Reste : {_suivi_money(reste)}"],
                    due_date=None,
                    secteur=projet.secteur,
                    url=url_for("projets.projet_finance", projet_id=projet.id),
                    action_label="Ouvrir finance",
                    bucket="watch",
                    today=today,
                )
                budget_count += 1

        # Jalons de projet (échéancier de pilotage)
        jalon_count = 0
        for projet in projects:
            if jalon_count >= 80:
                break
            for jal in sorted(getattr(projet, "jalons", []) or [], key=lambda j: (j.date_echeance or date.max)):
                if jalon_count >= 80:
                    break
                if jal.statut == "fait":
                    continue
                due = jal.date_echeance
                if due and due < today:
                    tone, bucket = "danger", "now"
                elif due and due <= soon:
                    tone, bucket = "warn", "soon"
                elif due:
                    tone, bucket = "info", "watch"
                else:
                    continue  # jalon sans échéance : rien à suivre dans le temps
                _suivi_add_item(
                    items,
                    kind="jalon",
                    type_label="Jalon",
                    tone=tone,
                    title=jal.libelle,
                    subtitle=f"Échéance du projet {projet.nom}",
                    meta=[projet.nom],
                    due_date=due,
                    secteur=projet.secteur,
                    url=url_for("projets.projet_jalons", projet_id=projet.id),
                    action_label="Ouvrir l'échéancier",
                    bucket=bucket,
                    today=today,
                )
                jalon_count += 1

    # RGPD : participants inactifs en attente d'anonymisation (vue direction).
    if can("scope:all_secteurs") and not secteur_filter:
        try:
            from app.services.purge_rgpd import participants_inactifs
            nb_rgpd = len(participants_inactifs())
        except Exception:
            nb_rgpd = 0
        if nb_rgpd:
            _suivi_add_item(
                items,
                kind="rgpd",
                type_label="RGPD",
                tone="warn",
                title=f"{nb_rgpd} participant(s) à anonymiser",
                subtitle="Fiches inactives au-delà du délai de conservation.",
                meta=["Conformité RGPD"],
                due_date=None,
                secteur="Tous secteurs",
                url=url_for("main.purge_rgpd"),
                action_label="Ouvrir la purge RGPD",
                bucket="soon",
                today=today,
            )

    items.sort(key=_suivi_sort_key)

    filtered_items = []
    for item in items:
        if type_filter and item["kind"] != type_filter:
            continue
        if tone_filter and item["tone"] != tone_filter:
            continue
        if q_filter and q_filter not in item["search_blob"]:
            continue
        filtered_items.append(item)

    sections = [
        {
            "key": "now",
            "title": "À traiter maintenant",
            "subtitle": "Retards, urgences et blocages visibles.",
            "items": [item for item in filtered_items if item["bucket"] == "now"],
        },
        {
            "key": "soon",
            "title": "Cette semaine",
            "subtitle": "Échéances proches et suivis à reprendre.",
            "items": [item for item in filtered_items if item["bucket"] == "soon"],
        },
        {
            "key": "watch",
            "title": "À surveiller",
            "subtitle": "Points ouverts sans urgence immédiate.",
            "items": [item for item in filtered_items if item["bucket"] == "watch"],
        },
    ]

    summary = {
        "total": len(items),
        "filtered": len(filtered_items),
        "now": sum(1 for item in filtered_items if item["bucket"] == "now"),
        "soon": sum(1 for item in filtered_items if item["bucket"] == "soon"),
        "watch": sum(1 for item in filtered_items if item["bucket"] == "watch"),
        "danger": sum(1 for item in filtered_items if item["tone"] == "danger"),
        "warn": sum(1 for item in filtered_items if item["tone"] == "warn"),
        "info": sum(1 for item in filtered_items if item["tone"] == "info"),
    }
    type_choices = [
        {"key": "rappel", "label": "Rappels", "count": sum(1 for item in filtered_items if item["kind"] == "rappel")},
        {"key": "orientation", "label": "Orientations", "count": sum(1 for item in filtered_items if item["kind"] == "orientation")},
        {"key": "action", "label": "Fiches actions", "count": sum(1 for item in filtered_items if item["kind"] == "action")},
        {"key": "indicateur", "label": "Indicateurs", "count": sum(1 for item in filtered_items if item["kind"] == "indicateur")},
        {"key": "budget", "label": "Budget", "count": sum(1 for item in filtered_items if item["kind"] == "budget")},
        {"key": "jalon", "label": "Jalons", "count": sum(1 for item in filtered_items if item["kind"] == "jalon")},
        {"key": "rgpd", "label": "RGPD", "count": sum(1 for item in filtered_items if item["kind"] == "rgpd")},
    ]
    secteurs = current_app.config.get("SECTEURS", []) or []
    if can("scope:all_secteurs") and not secteurs:
        secteurs = sorted({item["secteur"] for item in items if item.get("secteur") and item["secteur"] != "Sans secteur"})

    return render_template(
        "suivi_rappels.html",
        today=today,
        summary=summary,
        sections=sections,
        type_choices=type_choices,
        secteurs=secteurs,
        filters={
            "type": type_filter,
            "priorite": tone_filter,
            "q": request.args.get("q", ""),
            "secteur": secteur_filter,
        },
        can_scope_all=can("scope:all_secteurs"),
        can_view_orientations=can("partenaires:view"),
        can_view_projects=can("projets:view"),
        rappel_categories=SUIVI_RAPPEL_CATEGORIES,
        rappel_priorites=SUIVI_RAPPEL_PRIORITES,
    )




