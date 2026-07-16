from collections import defaultdict
import re
import csv
from io import StringIO
from datetime import date, timedelta


from flask import (
    render_template, request, redirect, url_for, flash, current_app,
    Response
)
import unicodedata
from flask_login import login_required, current_user
from app.rbac import require_perm, can, can_access_secteur

from app.extensions import db
from app.models import (
    Subvention,
    LigneBudget,
    Depense,
    Projet,
    ChargeProjet,
    ProjetAction,
    AtelierActivite,
    SessionActivite,
    OrientationAccesDroit,
    SuiviRappel,
    Participant,
)


from app.main.common import bp
from app.main.suivi_rappels import (
    SUIVI_ORIENTATION_DOMAINES,
    _suivi_label,
    _suivi_money,
    _suivi_participant_label,
    _suivi_projects_for_scope,
)

QUALITE_SEVERITY_ORDER = {"bad": 0, "warn": 1, "info": 2, "ok": 3}
QUALITE_FAMILIES = {
    "participants": "Participants",
    "orientations": "Orientations",
    "projets": "Projets",
    "activites": "Activités",
    "finance": "Finances",
}
QUALITE_RAPPEL_CATEGORIES = {
    "participants": "general",
    "orientations": "orientation",
    "projets": "projet",
    "activites": "general",
    "finance": "finance",
}
QUALITE_RAPPEL_PRIORITES = {
    "bad": "danger",
    "warn": "warn",
    "info": "info",
}

def _quality_norm(value: str | None) -> str:
    value = (value or "").strip().lower()
    value = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in value if not unicodedata.combining(ch))


def _quality_phone(value: str | None) -> str:
    return re.sub(r"\D+", "", value or "")


def _quality_participant_label(p: Participant) -> str:
    return f"{p.nom or ''} {p.prenom or ''}".strip() or f"Participant #{p.id}"


def _quality_sector_allowed(secteur: str | None) -> bool:
    return can_access_secteur(secteur)


def _quality_item(label: str, detail: str, url: str, *, secteur: str | None = None, meta: list[str] | None = None) -> dict:
    meta = [m for m in (meta or []) if m]
    return {
        "label": label,
        "detail": detail,
        "url": url,
        "secteur": secteur or "Sans secteur",
        "meta": meta,
        "search_blob": _quality_norm(" ".join([label, detail, secteur or "", *meta])),
    }


def _quality_add_issue(
    issues: list[dict],
    *,
    family: str,
    severity: str,
    title: str,
    why: str,
    items: list[dict],
    action_label: str = "Corriger",
):
    severity = severity if severity in QUALITE_SEVERITY_ORDER else "info"
    issues.append({
        "family": family,
        "family_label": QUALITE_FAMILIES.get(family, family),
        "severity": severity,
        "title": title,
        "why": why,
        "items": items,
        "count": len(items),
        "action_label": action_label,
    })


def _quality_scope_participants():
    if not (can("participants:view") or can("participants:view_all")):
        return []
    q = Participant.query
    if not can("participants:view_all"):
        q = q.filter(Participant.created_secteur == getattr(current_user, "secteur_assigne", None))
    return q.order_by(Participant.created_at.desc()).all()


def _quality_scope_orientations():
    if not can("partenaires:view"):
        return []
    q = OrientationAccesDroit.query.filter(~OrientationAccesDroit.statut.in_(["resolu", "non_abouti"]))
    if not can("scope:all_secteurs"):
        q = q.filter(db.or_(
            OrientationAccesDroit.secteur.is_(None),
            OrientationAccesDroit.secteur == getattr(current_user, "secteur_assigne", None),
        ))
    return q.order_by(OrientationAccesDroit.date_orientation.desc(), OrientationAccesDroit.created_at.desc()).all()


def _quality_scope_projects():
    if not can("projets:view"):
        return []
    return _suivi_projects_for_scope()


def _quality_scope_sessions():
    if not can("emargement:view"):
        return []
    q = (
        SessionActivite.query
        .join(AtelierActivite, AtelierActivite.id == SessionActivite.atelier_id)
        .filter(SessionActivite.is_deleted.is_(False), AtelierActivite.is_deleted.is_(False))
    )
    if not can("scope:all_secteurs"):
        q = q.filter(SessionActivite.secteur == getattr(current_user, "secteur_assigne", None))
    return q.order_by(SessionActivite.created_at.desc()).limit(500).all()


def _quality_scope_depenses():
    if not can("depenses:view"):
        return []
    q = Depense.query.filter(Depense.est_supprimee.is_(False))
    if not can("scope:all_secteurs"):
        user_secteur = getattr(current_user, "secteur_assigne", None)
        q = (
            q.outerjoin(LigneBudget, Depense.ligne_budget_id == LigneBudget.id)
            .outerjoin(Subvention, LigneBudget.subvention_id == Subvention.id)
            .outerjoin(ChargeProjet, Depense.charge_projet_id == ChargeProjet.id)
            .outerjoin(Projet, ChargeProjet.projet_id == Projet.id)
            .filter(db.or_(Subvention.secteur == user_secteur, Projet.secteur == user_secteur))
        )
    return q.order_by(Depense.created_at.desc()).limit(500).all()


def _quality_build_issues() -> list[dict]:
    today = date.today()
    issues: list[dict] = []

    participants = _quality_scope_participants()
    if participants:
        no_contact = []
        weak_demo = []
        groups: dict[str, list[Participant]] = defaultdict(list)
        for p in participants:
            label = _quality_participant_label(p)
            edit_url = url_for("participants.edit_participant", participant_id=p.id) if can("participants:edit") else url_for("participants.synthese_participant", participant_id=p.id)
            if not (p.telephone or p.email):
                no_contact.append(_quality_item(
                    label,
                    "Aucun téléphone ni email renseigné.",
                    edit_url,
                    secteur=p.created_secteur,
                    meta=["Contact"],
                ))

            missing = []
            if not p.date_naissance:
                missing.append("naissance")
            if not p.genre:
                missing.append("genre")
            if not (p.ville or p.quartier):
                missing.append("ville/quartier")
            if not p.created_secteur:
                missing.append("secteur")
            if missing:
                weak_demo.append(_quality_item(
                    label,
                    "Manque : " + ", ".join(missing) + ".",
                    edit_url,
                    secteur=p.created_secteur,
                    meta=["Démographie", "Territoire"],
                ))

            email_key = _quality_norm(p.email)
            phone_key = _quality_phone(p.telephone)
            name_key = f"{_quality_norm(p.nom)}|{_quality_norm(p.prenom)}|{p.date_naissance or ''}"
            if email_key:
                groups[f"email:{email_key}"].append(p)
            if len(phone_key) >= 8:
                groups[f"tel:{phone_key}"].append(p)
            if p.date_naissance and _quality_norm(p.nom) and _quality_norm(p.prenom):
                groups[f"nom:{name_key}"].append(p)

        duplicate_items = []
        seen_signatures = set()
        for key, rows in groups.items():
            ids = tuple(sorted(p.id for p in rows))
            if len(rows) < 2 or ids in seen_signatures:
                continue
            seen_signatures.add(ids)
            duplicate_items.append(_quality_item(
                f"{len(rows)} fiches possiblement doublonnées",
                " / ".join(_quality_participant_label(p) for p in rows[:4]),
                url_for("participants.duplicates"),
                secteur=rows[0].created_secteur,
                meta=[key.split(":", 1)[0]],
            ))

        _quality_add_issue(
            issues,
            family="participants",
            severity="warn",
            title="Participants sans contact",
            why="Ces fiches sont difficiles à relancer ou à mobiliser.",
            items=no_contact,
        )
        _quality_add_issue(
            issues,
            family="participants",
            severity="warn",
            title="Démographie ou territoire incomplet",
            why="Ces champs fiabilisent les bilans, les tris QPV et les exports.",
            items=weak_demo,
        )
        _quality_add_issue(
            issues,
            family="participants",
            severity="bad",
            title="Doublons potentiels",
            why="Les doublons faussent les uniques, les historiques et les statistiques.",
            items=duplicate_items,
            action_label="Vérifier",
        )

    orientations = _quality_scope_orientations()
    if orientations:
        no_partner = []
        no_next_step = []
        old_open = []
        for row in orientations:
            label = _suivi_participant_label(row.participant, row.demandeur_libre)
            url = url_for("partenaires.orientations", year=(row.date_orientation or today).year, q=label)
            detail = row.demande or _suivi_label(SUIVI_ORIENTATION_DOMAINES, row.domaine)
            if not row.partenaire_id:
                no_partner.append(_quality_item(label, f"{detail} · partenaire non renseigné.", url, secteur=row.secteur))
            if row.statut == "a_rappeler" and not row.suite_prevue:
                no_next_step.append(_quality_item(label, f"{detail} · à rappeler sans date.", url, secteur=row.secteur))
            if row.date_orientation and row.date_orientation < today - timedelta(days=60) and not row.suite_prevue:
                old_open.append(_quality_item(label, f"{detail} · ouverte depuis plus de 60 jours.", url, secteur=row.secteur))

        _quality_add_issue(
            issues,
            family="orientations",
            severity="warn",
            title="Orientations sans partenaire",
            why="L'orientation perd en lisibilité statistique et opérationnelle.",
            items=no_partner,
        )
        _quality_add_issue(
            issues,
            family="orientations",
            severity="bad",
            title="À rappeler sans date",
            why="Un suivi sans échéance a de fortes chances de disparaître du radar.",
            items=no_next_step,
        )
        _quality_add_issue(
            issues,
            family="orientations",
            severity="warn",
            title="Orientations anciennes encore ouvertes",
            why="À confirmer, clôturer ou reprogrammer pour garder une cartographie fiable.",
            items=old_open,
        )

    projects = _quality_scope_projects()
    if projects:
        no_ateliers = []
        no_indicators = []
        no_description = []
        for projet in projects:
            if not (projet.description or "").strip():
                no_description.append(_quality_item(projet.nom, "Description de projet non renseignée.", url_for("projets.projets_edit", projet_id=projet.id), secteur=projet.secteur))
            if not getattr(projet, "ateliers", []):
                no_ateliers.append(_quality_item(projet.nom, "Aucun atelier lié au projet.", url_for("projets.projets_edit", projet_id=projet.id), secteur=projet.secteur))
            if not any(ind.is_active for ind in getattr(projet, "indicateurs", []) or []):
                no_indicators.append(_quality_item(projet.nom, "Aucun indicateur actif configuré.", url_for("projets.projet_indicateurs", projet_id=projet.id), secteur=projet.secteur))

        action_rows = (
            ProjetAction.query
            .join(Projet)
            .filter(~ProjetAction.statut.in_(["realisee", "annulee"]))
        )
        if not can("scope:all_secteurs"):
            action_rows = action_rows.filter(Projet.secteur == getattr(current_user, "secteur_assigne", None))
        action_no_period = []
        for action in action_rows.order_by(ProjetAction.created_at.desc()).limit(200).all():
            if not action.date_debut and not action.date_fin:
                action_no_period.append(_quality_item(
                    action.titre,
                    f"{action.projet.nom if action.projet else 'Projet'} · aucune période renseignée.",
                    url_for("projets.projet_action_detail", projet_id=action.projet_id, action_id=action.id),
                    secteur=action.projet.secteur if action.projet else None,
                ))

        _quality_add_issue(issues, family="projets", severity="info", title="Projets sans description", why="La fiche est moins partageable et moins exploitable en bilan.", items=no_description)
        _quality_add_issue(issues, family="projets", severity="warn", title="Projets sans atelier lié", why="Les indicateurs stats-impact ne peuvent pas remonter proprement.", items=no_ateliers)
        _quality_add_issue(issues, family="projets", severity="warn", title="Projets sans indicateur actif", why="Le pilotage devient plus difficile à suivre dans le temps.", items=no_indicators)
        _quality_add_issue(issues, family="projets", severity="info", title="Fiches actions sans période", why="Une action sans période est plus difficile à relier aux bilans et aux rappels.", items=action_no_period)

    sessions = _quality_scope_sessions()
    if sessions:
        no_date = []
        no_presence = []
        for session_row in sessions:
            atelier = session_row.atelier
            label = atelier.nom if atelier else f"Séance #{session_row.id}"
            if not session_row.date_reference:
                no_date.append(_quality_item(label, "Séance sans date de référence.", url_for("activite.session_edit_schedule", session_id=session_row.id), secteur=session_row.secteur))
                continue
            if session_row.statut == "realisee" and session_row.date_reference <= today and len(session_row.presences or []) == 0:
                no_presence.append(_quality_item(label, f"{session_row.date_reference.strftime('%d/%m/%Y')} · aucune présence saisie.", url_for("activite.emargement", session_id=session_row.id), secteur=session_row.secteur))
        _quality_add_issue(issues, family="activites", severity="bad", title="Séances réalisées sans présence", why="Ces séances peuvent fausser les volumes et les exports stats-impact.", items=no_presence)
        _quality_add_issue(issues, family="activites", severity="warn", title="Séances sans date", why="Sans date, la séance devient difficile à filtrer et exporter.", items=no_date)

    depenses = _quality_scope_depenses()
    if depenses:
        no_date = []
        no_doc = []
        no_imputation = []
        for dep in depenses:
            url = url_for("budget.depense_edit", depense_id=dep.id)
            secteur = None
            if dep.budget_source and dep.budget_source.source_sub:
                secteur = dep.budget_source.source_sub.secteur
            elif dep.charge_projet and dep.charge_projet.projet:
                secteur = dep.charge_projet.projet.secteur
            if dep.statut == "valide" and not dep.date_paiement:
                no_date.append(_quality_item(dep.libelle, "Dépense validée sans date de paiement.", url, secteur=secteur, meta=[_suivi_money(dep.montant)]))
            if dep.statut == "valide" and not getattr(dep, "documents", []):
                no_doc.append(_quality_item(dep.libelle, "Aucun justificatif attaché.", url, secteur=secteur, meta=[_suivi_money(dep.montant)]))
            if dep.statut == "valide" and not dep.ligne_budget_id and not dep.charge_projet_id and not getattr(dep, "affectations", []):
                no_imputation.append(_quality_item(dep.libelle, "Aucune imputation lisible.", url, secteur=secteur, meta=[_suivi_money(dep.montant)]))
        _quality_add_issue(issues, family="finance", severity="warn", title="Dépenses sans date de paiement", why="La lecture annuelle et les exports financiers peuvent devenir ambigus.", items=no_date)
        _quality_add_issue(issues, family="finance", severity="info", title="Dépenses sans justificatif", why="Utile pour préparer les contrôles et bilans financeurs.", items=no_doc)
        _quality_add_issue(issues, family="finance", severity="bad", title="Dépenses sans imputation", why="Ces dépenses ne sont pas reliées clairement à une enveloppe ou un projet.", items=no_imputation)

    issues.sort(key=lambda row: (QUALITE_SEVERITY_ORDER.get(row["severity"], 9), row["family"], row["title"]))
    return issues


def _quality_request_filters():
    family_filter = (request.args.get("famille") or "").strip()
    if family_filter not in QUALITE_FAMILIES:
        family_filter = ""

    severity_filter = (request.args.get("niveau") or "").strip()
    if severity_filter not in QUALITE_SEVERITY_ORDER:
        severity_filter = ""

    q_raw = (request.args.get("q") or "").strip()
    q_filter = _quality_norm(q_raw)

    secteur_filter = (request.args.get("secteur") or "").strip()
    if secteur_filter and not can("scope:all_secteurs"):
        secteur_filter = ""

    return family_filter, severity_filter, q_raw, q_filter, secteur_filter


def _quality_filter_items(items: list[dict], q_filter: str = "", secteur_filter: str = "") -> list[dict]:
    rows = list(items or [])
    if secteur_filter:
        rows = [row for row in rows if row.get("secteur") == secteur_filter]
    if q_filter:
        rows = [row for row in rows if q_filter in row.get("search_blob", "")]
    return rows


def _quality_filtered_issues(
    raw_issues: list[dict],
    *,
    family_filter: str = "",
    severity_filter: str = "",
    q_filter: str = "",
    secteur_filter: str = "",
    limit: int | None = 12,
) -> list[dict]:
    issues = []
    for issue in raw_issues:
        if family_filter and issue["family"] != family_filter:
            continue
        if severity_filter and issue["severity"] != severity_filter:
            continue
        rows = _quality_filter_items(issue["items"], q_filter, secteur_filter)
        if not rows:
            continue
        issue = dict(issue)
        issue["count_total"] = len(rows)
        issue["items"] = rows if limit is None else rows[:limit]
        issue["has_more"] = limit is not None and len(rows) > limit
        issues.append(issue)
    return issues


def _quality_family_choices(raw_issues: list[dict], *, severity_filter: str, q_filter: str, secteur_filter: str) -> list[dict]:
    choices = []
    for key, label in QUALITE_FAMILIES.items():
        count = 0
        for issue in raw_issues:
            if issue["family"] != key:
                continue
            if severity_filter and issue["severity"] != severity_filter:
                continue
            count += len(_quality_filter_items(issue["items"], q_filter, secteur_filter))
        choices.append({"key": key, "label": label, "count": count})
    return choices


def _quality_available_secteurs(raw_issues: list[dict]) -> list[str]:
    if not can("scope:all_secteurs"):
        return []
    configured = [s for s in (current_app.config.get("SECTEURS", []) or []) if s]
    extras = sorted({
        row.get("secteur")
        for issue in raw_issues
        for row in issue.get("items", [])
        if row.get("secteur") and row.get("secteur") != "Sans secteur"
    })
    return configured + [s for s in extras if s not in configured]


def _quality_redirect_back():
    next_url = (request.form.get("next") or "").strip()
    if next_url.startswith("/") and not next_url.startswith("//"):
        return redirect(next_url)
    return redirect(request.referrer or url_for("main.qualite_donnees_transverse"))


@bp.route("/qualite-donnees/export.csv")
@login_required
@require_perm("dashboard:view")
def qualite_donnees_transverse_export_csv():
    family_filter, severity_filter, _q_raw, q_filter, secteur_filter = _quality_request_filters()
    raw_issues = _quality_build_issues()
    issues = _quality_filtered_issues(
        raw_issues,
        family_filter=family_filter,
        severity_filter=severity_filter,
        q_filter=q_filter,
        secteur_filter=secteur_filter,
        limit=None,
    )

    out = StringIO()
    writer = csv.writer(out, delimiter=";")
    writer.writerow(["Famille", "Niveau", "Controle", "Pourquoi", "Element", "Detail", "Secteur", "Meta", "URL"])
    for issue in issues:
        for item in issue["items"]:
            writer.writerow([
                issue["family_label"],
                issue["severity"],
                issue["title"],
                issue["why"],
                item["label"],
                item["detail"],
                item.get("secteur") or "",
                " | ".join(item.get("meta") or []),
                item.get("url") or "",
            ])

    content = out.getvalue().encode("utf-8-sig")
    filename = f"qualite_donnees_{date.today().isoformat()}.csv"
    return Response(content, mimetype="text/csv", headers={
        "Content-Disposition": f"attachment; filename={filename}"
    })


@bp.route("/qualite-donnees/rappel", methods=["POST"])
@login_required
@require_perm("dashboard:view")
def qualite_donnees_rappel():
    titre = (request.form.get("titre") or "").strip()
    if not titre:
        flash("Impossible de créer le rappel : titre manquant.", "danger")
        return _quality_redirect_back()

    famille = (request.form.get("famille") or "").strip()
    niveau = (request.form.get("niveau") or "").strip()
    controle = (request.form.get("controle") or "").strip()
    element = (request.form.get("element") or "").strip()
    detail = (request.form.get("detail") or "").strip()
    why = (request.form.get("why") or "").strip()
    secteur = (request.form.get("secteur") or "").strip()
    if secteur == "Sans secteur":
        secteur = ""
    if not can("scope:all_secteurs"):
        secteur = getattr(current_user, "secteur_assigne", None) or ""

    lien_url = (request.form.get("lien_url") or "").strip()
    if lien_url and not (lien_url.startswith("/") or lien_url.startswith("http://") or lien_url.startswith("https://")):
        lien_url = ""

    description_lines = [
        f"Controle qualite : {controle}" if controle else "",
        f"Element : {element}" if element else "",
        detail,
        why,
    ]
    description = "\n".join(line for line in description_lines if line)
    titre = titre[:200]
    categorie = QUALITE_RAPPEL_CATEGORIES.get(famille, "general")
    priorite = QUALITE_RAPPEL_PRIORITES.get(niveau, "warn")

    duplicate_q = SuiviRappel.query.filter(
        SuiviRappel.statut == "ouvert",
        SuiviRappel.titre == titre,
    )
    duplicate_q = duplicate_q.filter(SuiviRappel.lien_url == lien_url) if lien_url else duplicate_q.filter(SuiviRappel.lien_url.is_(None))
    duplicate_q = duplicate_q.filter(SuiviRappel.secteur == secteur) if secteur else duplicate_q.filter(SuiviRappel.secteur.is_(None))
    if duplicate_q.first():
        flash("Ce point est déjà présent dans le centre de suivi.", "info")
        return _quality_redirect_back()

    rappel = SuiviRappel(
        titre=titre,
        description=description or None,
        categorie=categorie,
        priorite=priorite,
        secteur=secteur or None,
        echeance=date.today() + timedelta(days=7),
        lien_url=lien_url or None,
        is_private=False,
        created_by_user_id=getattr(current_user, "id", None),
    )
    db.session.add(rappel)
    db.session.commit()
    flash("Point ajouté au centre de suivi avec une échéance à 7 jours.", "success")
    return _quality_redirect_back()


@bp.route("/qualite-donnees")
@login_required
@require_perm("dashboard:view")
def qualite_donnees_transverse():
    family_filter, severity_filter, q_raw, q_filter, secteur_filter = _quality_request_filters()
    raw_issues = _quality_build_issues()
    issues = _quality_filtered_issues(
        raw_issues,
        family_filter=family_filter,
        severity_filter=severity_filter,
        q_filter=q_filter,
        secteur_filter=secteur_filter,
        limit=12,
    )

    total_points = sum(issue["count_total"] for issue in issues)
    bad_count = sum(issue["count_total"] for issue in issues if issue["severity"] == "bad")
    warn_count = sum(issue["count_total"] for issue in issues if issue["severity"] == "warn")
    info_count = sum(issue["count_total"] for issue in issues if issue["severity"] == "info")
    penalty = min(95, bad_count * 8 + warn_count * 4 + info_count * 2)
    score = max(0, 100 - penalty)
    family_choices = _quality_family_choices(
        raw_issues,
        severity_filter=severity_filter,
        q_filter=q_filter,
        secteur_filter=secteur_filter,
    )

    return render_template(
        "qualite_donnees_transverse.html",
        issues=issues,
        summary={
            "score": score,
            "total": total_points,
            "bad": bad_count,
            "warn": warn_count,
            "info": info_count,
            "families": sum(1 for row in family_choices if row["count"] > 0),
        },
        filters={
            "famille": family_filter,
            "niveau": severity_filter,
            "q": q_raw,
            "secteur": secteur_filter,
        },
        family_choices=family_choices,
        secteurs=_quality_available_secteurs(raw_issues),
        can_scope_all=can("scope:all_secteurs"),
    )



