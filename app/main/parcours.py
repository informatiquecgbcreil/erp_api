from __future__ import annotations

from datetime import date

from flask import abort, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.main.common import bp
from app.models import Subvention, SuiviRappel
from app.rbac import can, require_perm


def _compact_args(**kwargs) -> dict:
    return {key: value for key, value in kwargs.items() if value not in (None, "")}


@bp.route("/parcours-metier")
@login_required
@require_perm("dashboard:view")
def parcours_metier():
    """B1 — Porte d'entrée par profils réels plutôt que par modules."""
    profils: list[dict] = []

    def add(title: str, intro: str, icon: str, actions: list[dict], when: bool = True):
        actions = [a for a in actions if a]
        if when and actions:
            profils.append({"title": title, "intro": intro, "icon": icon, "actions": actions})

    add(
        "Accueil",
        "Inscrire, retrouver une personne, encaisser ou orienter rapidement.",
        "👋",
        [
            {"label": "Créer une personne", "url": url_for("participants.new_participant"), "primary": True} if can("participants:edit") else None,
            {"label": "Annuaire participants", "url": url_for("participants.list_participants")} if (can("participants:view") or can("participants:view_all")) else None,
            {"label": "Impayés", "url": url_for("main.impayes")} if can("cotisations:view") else None,
            {"label": "Orientations", "url": url_for("partenaires.orientations")} if can("partenaires:view") else None,
        ],
    )
    add(
        "Animateur / intervenant",
        "Préparer les séances, faire l’émargement et retrouver les présences.",
        "🧑‍🏫",
        [
            {"label": "Présences du jour", "url": url_for("activite.index"), "primary": True} if can("emargement:view") else None,
            {"label": "Mon agenda", "url": url_for("main.mon_agenda")} if can("emargement:view") else None,
            {"label": "Saisie en grille", "url": url_for("activite.saisie_grille")} if can("emargement:edit") else None,
            {"label": "Apprentissages", "url": url_for("pedagogie.index")} if can("pedagogie:view") else None,
        ],
    )
    add(
        "Coordinateur secteur",
        "Piloter l’activité, corriger les données et préparer les éléments de bilan.",
        "🧭",
        [
            {"label": "À traiter", "url": url_for("main.suivi_rappels"), "primary": True},
            {"label": "Qualité bilan", "url": url_for("main.qualite_bilans")},
            {"label": "Résultats & bilans", "url": url_for("main.stats_bilans")} if can("stats:view") else None,
            {"label": "Projets", "url": url_for("projets.projets_list")} if can("projets:view") else None,
        ],
    )
    add(
        "Direction",
        "Décider, arbitrer, préparer les instances et vérifier les signaux faibles.",
        "📊",
        [
            {"label": "Pilotage direction", "url": url_for("main.direction_pilotage"), "primary": True} if (can("stats:view") or can("bilans:view") or can("scope:all_secteurs")) else None,
            {"label": "Support comité / CA", "url": url_for("main.comite_pilotage")} if (can("stats:view") or can("bilans:view") or can("scope:all_secteurs")) else None,
            {"label": "Assistant bilan financeur", "url": url_for("main.assistant_bilan_financeur")} if (can("subventions:view") or can("bilans:view")) else None,
            {"label": "Journal métier", "url": url_for("main.journal_metier")} if (can("admin:rbac") or can("controle:view") or can("scope:all_secteurs")) else None,
        ],
    )
    add(
        "Finance",
        "Suivre subventions, dépenses, budgets demandés et justificatifs.",
        "💶",
        [
            {"label": "Accueil finances", "url": url_for("projets.finance_home"), "primary": True} if (can("projets:view") or can("subventions:view") or can("depenses:view")) else None,
            {"label": "Dépenses", "url": url_for("budget.depenses_list")} if can("depenses:view") else None,
            {"label": "Enveloppes", "url": url_for("main.subventions_list")} if can("subventions:view") else None,
            {"label": "Budgets demandés", "url": url_for("previsionnel.index")} if can("subventions:view") else None,
        ],
    )
    add(
        "Administration",
        "Gérer l’équipe, les droits, la santé système et les référentiels.",
        "🛠️",
        [
            {"label": "Équipe", "url": url_for("admin.users"), "primary": True} if can("admin:users") else None,
            {"label": "Matrice des droits", "url": url_for("admin.matrice_droits")} if can("admin:rbac") else None,
            {"label": "Santé système", "url": url_for("admin.sante_systeme")} if can("admin:rbac") else None,
            {"label": "Vérifier les liens", "url": url_for("main.controle_navigation")} if can("controle:view") else None,
        ],
    )

    return render_template("parcours_metier.html", profils=profils)


def _assistant_year() -> int:
    try:
        year = int((request.args.get("year") or request.args.get("annee") or date.today().year))
    except Exception:
        year = date.today().year
    return max(2000, min(2100, year))


def _assistant_subventions(year: int) -> list[Subvention]:
    q = Subvention.query.filter(Subvention.est_archive.is_(False), Subvention.annee_exercice == year)
    if not can("scope:all_secteurs"):
        q = q.filter(Subvention.secteur == getattr(current_user, "secteur_assigne", None))
    return q.order_by(Subvention.date_bilan_prevu.asc().nullslast(), Subvention.nom.asc()).all()


@bp.route("/bilans/assistant-financeur")
@login_required
@require_perm("dashboard:view")
def assistant_bilan_financeur():
    """B2 — Assistant simple pour préparer un bilan financeur."""
    if not (can("subventions:view") or can("bilans:view")):
        abort(403)
    year = _assistant_year()
    subventions = _assistant_subventions(year)
    selected_id = request.args.get("subvention_id", type=int)
    selected = next((s for s in subventions if s.id == selected_id), subventions[0] if subventions else None)

    checks = []
    if selected:
        checks = [
            {
                "label": "Dossier financeur identifié",
                "ok": bool(selected.financeur and selected.reference),
                "help": "Renseigner financeur et référence facilite le suivi.",
                "url": url_for("main.subvention_pilotage", subvention_id=selected.id),
            },
            {
                "label": "Budget réel renseigné",
                "ok": float(selected.total_reel_lignes or 0) > 0,
                "help": "Les lignes réelles servent de base au justificatif.",
                "url": url_for("main.subvention_pilotage", subvention_id=selected.id),
            },
            {
                "label": "Dépenses imputées",
                "ok": float(selected.total_impute_affectations or 0) > 0 or float(selected.total_engage or 0) > 0,
                "help": "Les dépenses prouvent l’utilisation de la subvention.",
                "url": url_for("main.subvention_pilotage", subvention_id=selected.id),
            },
            {
                "label": "Échéance de bilan connue",
                "ok": bool(selected.date_bilan_prevu),
                "help": "Permet d’anticiper les relances et instances.",
                "url": url_for("main.subvention_pilotage", subvention_id=selected.id),
            },
        ]
    ready = sum(1 for c in checks if c["ok"])
    return render_template(
        "assistant_bilan_financeur.html",
        year=year,
        subventions=subventions,
        selected=selected,
        checks=checks,
        ready=ready,
        print_args=_compact_args(year=year, subvention_id=selected.id if selected else None),
    )


@bp.route("/qualite/bilans")
@login_required
@require_perm("dashboard:view")
def qualite_bilans():
    """B3 — Lecture métier des anomalies qui gênent les bilans."""
    from app.main.qualite_donnees import _quality_build_issues

    raw = _quality_build_issues()
    priority = {"bad": 0, "warn": 1, "info": 2, "ok": 3}
    issues = sorted(raw, key=lambda i: (priority.get(i.get("severity"), 9), -int(i.get("count", 0))))
    bloquants = [i for i in issues if i.get("severity") == "bad"]
    importants = [i for i in issues if i.get("severity") == "warn"]
    confort = [i for i in issues if i.get("severity") not in {"bad", "warn"}]
    return render_template(
        "qualite_bilans.html",
        groups=[
            {"title": "Bloquant pour les bilans", "intro": "À corriger avant d’envoyer un dossier financeur.", "issues": bloquants, "tone": "danger"},
            {"title": "Important", "intro": "À traiter pour fiabiliser les chiffres et commentaires.", "issues": importants, "tone": "warn"},
            {"title": "Confort", "intro": "Améliorations utiles mais rarement bloquantes.", "issues": confort, "tone": "info"},
        ],
        total=sum(int(i.get("count", 0)) for i in issues),
    )
