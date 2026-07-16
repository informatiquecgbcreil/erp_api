from datetime import date


from flask import (
    render_template, request, url_for, abort, current_app, Response, flash, redirect
)
from flask_login import login_required, current_user
from app.rbac import can, require_perm

from app.extensions import db
from app.models import (
    Subvention,
    Projet,
    ProjetAction,
    OrientationAccesDroit,
    Questionnaire,
    DepenseAffectation,
    SessionActivite,
)


from app.main.common import bp



# ---------------------------------------------------------------------
# P0.3C — Hubs métier
# ---------------------------------------------------------------------

def _hub_forbidden_if_empty(cards):
    if not cards:
        abort(403)


@bp.route("/publics")
@login_required
def hub_publics():
    cards = []

    if can("participants:view") or can("participants:view_all"):
        cards.append({
            "title": "Tous les participants",
            "subtitle": "L'annuaire complet des habitants (tous secteurs) : retrouver, créer ou mettre à jour une fiche. La liste « Participants du secteur » reste accessible depuis Activités.",
            "primary_label": "Ouvrir l'annuaire",
            "primary_url": url_for("participants.list_participants"),
            "secondary": [
                {"label": "Attestations", "url": url_for("activite.attestations")} if can("participants:view") or can("participants:view_all") else None,
                {"label": "Ajouter une personne", "url": url_for("participants.new_participant")} if can("participants:edit") else None,
                {"label": "Importer un annuaire", "url": url_for("participants.import_annuaire")} if (can("participants:edit") and can("scope:all_secteurs")) else None,
                {"label": "Doublons potentiels", "url": url_for("participants.duplicates")} if can("participants:edit") else None,
            ],
            "tag": "Publics",
        })

    if can("insertion:view"):
        cards.append({
            "title": "Insertion",
            "subtitle": "Suivre les parcours, positionnements et données insertion.",
            "primary_label": "Ouvrir insertion",
            "primary_url": url_for("insertion.index"),
            "secondary": [],
            "tag": "Parcours",
        })

    if can("quartiers:view"):
        cards.append({
            "title": "Quartiers",
            "subtitle": "Consulter les quartiers, QPV et informations territoriales.",
            "primary_label": "Ouvrir quartiers",
            "primary_url": url_for("quartiers.index"),
            "secondary": [],
            "tag": "Territoire",
        })

    _hub_forbidden_if_empty(cards)
    return render_template(
        "hub_publics.html",
        title="Publics & parcours",
        intro="Une porte d’entrée pour tout ce qui concerne les habitants, leurs parcours et leur territoire.",
        cards=cards,
    )


@bp.route("/activites")
@login_required
def hub_activites():
    cards = []

    if can("emargement:view"):
        cards.append({
            "title": "Présences / émargement",
            "subtitle": "Faire l’émargement, ouvrir les ateliers et gérer les sessions.",
            "primary_label": "Ouvrir les présences",
            "primary_url": url_for("activite.index"),
            "secondary": [
                {"label": "Attestations", "url": url_for("activite.attestations")} if can("participants:view") or can("participants:view_all") else None,
                {"label": "Modèles d’émargement", "url": url_for("activite.emargement_models")} if can("emargement:edit") else None,
            ],
            "tag": "Quotidien",
        })

    if can("statsimpact:view") or can("stats:view"):
        cards.append({
            "title": "Fréquentation et résultats",
            "subtitle": "Suivre les présences, les publics accueillis et les résultats des activités.",
            "primary_label": "Voir les résultats",
            "primary_url": url_for("statsimpact.dashboard"),
            "secondary": [],
            "tag": "Analyse",
        })

    if can("pedagogie:view"):
        cards.append({
            "title": "Pédagogie",
            "subtitle": "Référentiels, compétences, modules et suivi pédagogique.",
            "primary_label": "Ouvrir pédagogie",
            "primary_url": url_for("pedagogie.referentiels_list"),
            "secondary": [
                {"label": "Suivi pédagogique", "url": url_for("pedagogie.suivi_pedagogique")},
            ],
            "tag": "Compétences",
        })

    _hub_forbidden_if_empty(cards)
    return render_template(
        "hub_activites.html",
        title="Activités & présences",
        intro="Un hub pour passer des séances aux présences, puis aux données d’activité et au suivi pédagogique.",
        cards=cards,
    )


@bp.route("/bilans-hub")
@login_required
def hub_bilans():
    cards = []

    if can("dashboard:view"):
        cards.append({
            "title": "Pilotage direction",
            "subtitle": "Vue annuelle centre social : publics, présences, budget, bénévolat, alertes et support comité/CA.",
            "primary_label": "Ouvrir le pilotage",
            "primary_url": url_for("main.direction_pilotage"),
            "secondary": [
                {"label": "Support comité / CA", "url": url_for("main.comite_pilotage")},
                {"label": "Journal métier", "url": url_for("main.journal_metier")} if (can("admin:rbac") or can("controle:view") or can("scope:all_secteurs")) else None,
            ],
            "tag": "Direction",
        })

    if can("dashboard:view"):
        cards.append({
            "title": "Documents prêts",
            "subtitle": "Retrouver les exports, fiches imprimables et rapports à transmettre.",
            "primary_label": "Ouvrir les documents",
            "primary_url": url_for("main.documents_exports"),
            "secondary": [
                {"label": "Qualité bilan", "url": url_for("main.qualite_bilans")},
                {"label": "Assistant bilan financeur", "url": url_for("main.assistant_bilan_financeur")},
                {"label": "Qualité transverse", "url": url_for("main.qualite_donnees_transverse")},
            ],
            "tag": "Sorties",
        })

    if can("stats:view"):
        cards.append({
            "title": "Résultats & bilans",
            "subtitle": "Consulter les chiffres clés et les indicateurs de pilotage.",
            "primary_label": "Ouvrir les résultats & bilans",
            "primary_url": url_for("main.stats_bilans"),
            "secondary": [
                {"label": "Bilan SENACS", "url": url_for("bilans.bilan_senacs")},
                {"label": "Bilan global", "url": url_for("main.bilan_global")},
                {"label": "Export bilan XLSX", "url": url_for("main.bilan_global_export_xlsx")},
                {"label": "Qualité transverse", "url": url_for("main.qualite_donnees_transverse")},
            ],
            "tag": "Pilotage",
        })

    if can("bilans:view"):
        cards.append({
            "title": "Bilans complets",
            "subtitle": "Préparer les bilans annuels, narratifs et exports complets.",
            "primary_label": "Ouvrir les bilans complets",
            "primary_url": url_for("bilans.bilans_lourds", year=date.today().year),
            "secondary": [
                {"label": "Bilans financeurs", "url": url_for("bilans.bilans_financeurs")},
                {"label": "Bilan secteur", "url": url_for("bilans.bilan_secteur")},
                {"label": "Bilan subvention", "url": url_for("bilans.bilan_subvention")},
                {"label": "Qualité des données", "url": url_for("bilans.qualite")},
            ],
            "tag": "Annuel",
        })

    if can("dashboard:view"):
        cards.append({
            "title": "Qualité des données",
            "subtitle": "Repérer les fiches, suivis et saisies à corriger dans les modules.",
            "primary_label": "Ouvrir qualité",
            "primary_url": url_for("main.qualite_donnees_transverse"),
            "secondary": [
                {"label": "Centre de suivi", "url": url_for("main.suivi_rappels")},
            ],
            "tag": "Contrôle",
        })

    if can("transitions:view"):
        cards.append({
            "title": "Transitions",
            "subtitle": "Suivi des actions de transition : thématiques, défis des habitants, mesures, éco-consommation — et bilan annuel prêt pour CSAT.",
            "primary_label": "Ouvrir le tableau de bord",
            "primary_url": url_for("transitions.dashboard"),
            "secondary": [
                {"label": "Étiqueter les ateliers", "url": url_for("transitions.ateliers")} if can("transitions:edit") else None,
                {"label": "Bilan annuel XLSX", "url": url_for("transitions.export_xlsx")},
            ],
            "tag": "Transitions",
        })

    if can("questionnaires:view"):
        cards.append({
            "title": "Questionnaires d’impact",
            "subtitle": "Consulter les questionnaires et retours qualitatifs.",
            "primary_label": "Ouvrir questionnaires",
            "primary_url": url_for("questionnaires.index"),
            "secondary": [],
            "tag": "Impact",
        })

    _hub_forbidden_if_empty(cards)
    return render_template(
        "hub_bilans.html",
        title="Bilans & exports",
        intro="La zone pour préparer les bilans, vérifier les données et sortir les exports utiles.",
        cards=cards,
    )


def _documents_compact_args(**kwargs) -> dict:
    return {key: value for key, value in kwargs.items() if value not in (None, "")}


def _documents_selected_year() -> int:
    try:
        year = int((request.args.get("year") or "").strip())
    except Exception:
        year = date.today().year
    return max(2000, min(2100, year))


def _documents_scope() -> tuple[int, str | None, list[str]]:
    year = _documents_selected_year()
    secteurs = current_app.config.get("SECTEURS", []) or []
    extra_secteurs = {
        row[0]
        for row in Subvention.query.with_entities(Subvention.secteur)
        .filter(Subvention.secteur.isnot(None))
        .distinct()
        .all()
    }
    extra_secteurs.update({
        row[0]
        for row in Projet.query.with_entities(Projet.secteur)
        .filter(Projet.secteur.isnot(None))
        .distinct()
        .all()
    })
    secteurs = secteurs + [s for s in sorted(extra_secteurs) if s and s not in secteurs]

    selected_secteur = (request.args.get("secteur") or "").strip() or None
    if not can("scope:all_secteurs"):
        selected_secteur = getattr(current_user, "secteur_assigne", None)
        secteurs = [selected_secteur] if selected_secteur else []

    return year, selected_secteur, secteurs


def _documents_subventions(year: int, secteur: str | None) -> list[Subvention]:
    if not (can("subventions:view") or can("bilans:view")):
        return []
    q = Subvention.query.filter(Subvention.est_archive.is_(False), Subvention.annee_exercice == year)
    if secteur:
        q = q.filter(Subvention.secteur == secteur)
    elif not can("scope:all_secteurs"):
        q = q.filter(Subvention.secteur == getattr(current_user, "secteur_assigne", None))
    return q.order_by(Subvention.montant_recu.desc(), Subvention.nom.asc()).limit(8).all()


def _documents_projects(secteur: str | None) -> list[Projet]:
    if not can("projets:view"):
        return []
    q = Projet.query
    if secteur:
        q = q.filter(Projet.secteur == secteur)
    elif not can("scope:all_secteurs"):
        q = q.filter(Projet.secteur == getattr(current_user, "secteur_assigne", None))
    return q.order_by(Projet.nom.asc()).limit(8).all()


def _documents_actions(year: int, secteur: str | None) -> list[ProjetAction]:
    if not can("projets:view"):
        return []
    start = date(year, 1, 1)
    end = date(year, 12, 31)
    q = ProjetAction.query.join(Projet)
    if secteur:
        q = q.filter(Projet.secteur == secteur)
    elif not can("scope:all_secteurs"):
        q = q.filter(Projet.secteur == getattr(current_user, "secteur_assigne", None))
    q = q.filter(db.or_(
        ProjetAction.date_debut.between(start, end),
        ProjetAction.date_fin.between(start, end),
        db.and_(ProjetAction.date_debut.is_(None), ProjetAction.date_fin.is_(None)),
    ))
    return q.order_by(ProjetAction.date_debut.desc(), ProjetAction.created_at.desc()).limit(8).all()


def _documents_questionnaires(secteur: str | None) -> list[Questionnaire]:
    if not can("questionnaires:view"):
        return []
    q = Questionnaire.query
    if secteur:
        q = q.filter(db.or_(
            Questionnaire.secteurs.any(secteur=secteur),
            ~Questionnaire.secteurs.any(),
        ))
    return q.order_by(Questionnaire.is_active.desc(), Questionnaire.nom.asc()).limit(8).all()


def _documents_orientation_count(year: int, secteur: str | None) -> int:
    if not can("partenaires:view"):
        return 0
    q = OrientationAccesDroit.query.filter(
        OrientationAccesDroit.date_orientation >= date(year, 1, 1),
        OrientationAccesDroit.date_orientation <= date(year, 12, 31),
    )
    if secteur:
        q = q.filter(OrientationAccesDroit.secteur == secteur)
    elif not can("scope:all_secteurs"):
        q = q.filter(OrientationAccesDroit.secteur == getattr(current_user, "secteur_assigne", None))
    return q.count()


@bp.route("/programme-public.html")
@login_required
@require_perm("emargement:view")
def programme_public_export():
    """Télécharge le programme des activités en page HTML autonome, à publier
    sur un site / hébergement externe. Données non nominatives."""
    from app.services.programme_public import rendu_html

    return Response(
        rendu_html(),
        mimetype="text/html",
        headers={"Content-Disposition": 'attachment; filename="programme.html"'},
    )


@bp.route("/programme/publication")
@login_required
@require_perm("emargement:view")
def publication_programme():
    """Publication du programme sur l'hébergement (statut + bouton manuel)."""
    from app.services.publication_web import (
        config_publication,
        derniere_publication,
        publication_configuree,
    )

    return render_template(
        "programme_publication.html",
        configuree=publication_configuree(),
        config=config_publication(),
        derniere=derniere_publication(),
    )


@bp.route("/programme/publication/publier", methods=["POST"])
@login_required
@require_perm("emargement:view")
def publication_programme_publier():
    from app.services.publication_web import publier_programme

    try:
        info = publier_programme()
        current_app.logger.info(
            "Programme publié en ligne par %s (%s octets vers %s)",
            getattr(current_user, "email", "?"),
            info.get("octets"),
            info.get("hote"),
        )
        flash("Programme publié en ligne avec succès ✅", "success")
    except Exception as exc:  # message lisible remonté à l'utilisateur
        current_app.logger.exception("Échec de la publication du programme")
        flash(f"La publication a échoué : {exc}", "danger")
    return redirect(url_for("main.publication_programme"))


@bp.route("/documents")
@login_required
def documents_exports():
    year, selected_secteur, secteurs = _documents_scope()
    stat_args = _documents_compact_args(
        date_from=date(year, 1, 1).isoformat(),
        date_to=date(year, 12, 31).isoformat(),
        secteur=selected_secteur,
    )
    finance_args = _documents_compact_args(annee=year, secteur=selected_secteur)
    finance_sector_args = _documents_compact_args(year=year, secteur=selected_secteur)
    bilan_args = _documents_compact_args(year=year, secteur=selected_secteur)
    orientation_args = _documents_compact_args(year=year, secteur_filter=selected_secteur)
    quality_args = _documents_compact_args(secteur=selected_secteur)

    groups: list[dict] = []

    def add_group(title: str, intro: str, cards: list[dict]):
        cards = [card for card in cards if card]
        if cards:
            groups.append({"title": title, "intro": intro, "cards": cards})

    if can("statsimpact:view") or can("stats:view"):
        add_group("Fréquentation et résultats", "Exports alimentés par les présences d'émargement.", [
            {
                "title": "Exports des activités",
                "meta": "Présences, ateliers, démographie",
                "actions": [
                    {"label": "Ouvrir", "url": url_for("statsimpact.exports", **stat_args), "primary": True},
                    {"label": "XLSX complet", "url": url_for("statsimpact.magatomatique_export", **stat_args)},
                    {"label": "XLSX annuel", "url": url_for("statsimpact.magatomatique_export", export_mode="per_atelier", **stat_args)},
                    {"label": "CSV présences", "url": url_for("statsimpact.magatomatique_export_csv", **stat_args)},
                ],
            },
        ])

    if can("bilans:view"):
        add_group("Rapports annuels et financeurs", "Sorties consolidées pour bilan, pilotage et financeurs.", [
            {
                "title": f"Rapport annuel {year}",
                "meta": "DOCX narratif et indicateurs",
                "actions": [
                    {"label": "Préparer", "url": url_for("bilans.bilans_lourds", year=year), "primary": True},
                    {"label": "Export DOCX", "url": url_for("bilans.bilans_lourds_export_docx", year=year)},
                ],
            },
            {
                "title": "Bilan de pilotage",
                "meta": "Synthèse annuelle et alertes",
                "actions": [
                    {"label": "Ouvrir", "url": url_for("bilans.dashboard", **bilan_args), "primary": True},
                    {"label": "Export XLSX", "url": url_for("bilans.dashboard_export_xlsx", year=year)},
                ],
            },
            {
                "title": "Bilan financier global",
                "meta": "Subventions, engagé, reste",
                "actions": [
                    {"label": "Ouvrir", "url": url_for("main.bilan_global", **finance_args), "primary": True},
                    {"label": "Export XLSX", "url": url_for("main.bilan_global_export_xlsx", **finance_args)},
                    {"label": "Dépenses CSV", "url": url_for("main.export_depenses_csv")} if can("depenses:view") else None,
                ],
            },
        ])

    if can("partenaires:view") or can("dashboard:view"):
        add_group("Suivi et accès aux droits", "Exports pour cartographier les demandes, les orientations et les points à reprendre.", [
            {
                "title": f"Orientations accès aux droits {year}",
                "meta": f"{_documents_orientation_count(year, selected_secteur)} orientation(s) dans le périmètre",
                "actions": [
                    {"label": "Ouvrir", "url": url_for("partenaires.orientations", **orientation_args), "primary": True},
                    {"label": "Export XLSX", "url": url_for("partenaires.export_orientations_xlsx", **orientation_args)},
                ],
            } if can("partenaires:view") else None,
            {
                "title": "Qualité des données",
                "meta": "Anomalies et points à transformer en rappels",
                "actions": [
                    {"label": "Ouvrir", "url": url_for("main.qualite_donnees_transverse", **quality_args), "primary": True},
                    {"label": "Export CSV", "url": url_for("main.qualite_donnees_transverse_export_csv", **quality_args)},
                    {"label": "Centre de suivi", "url": url_for("main.suivi_rappels")},
                ],
            } if can("dashboard:view") else None,
        ])

    if can("projets:view"):
        add_group("Projets et actions", "Fiches projet, actions détaillées, budgets et indicateurs.", [
            {
                "title": "Liste des projets",
                "meta": "Accès aux fiches projet",
                "actions": [
                    {"label": "Ouvrir", "url": url_for("projets.projets_list"), "primary": True},
                ],
            },
            {
                "title": "Pilotage financier projet",
                "meta": "Exports annuels par secteur",
                "actions": [
                    {"label": "Ouvrir", "url": url_for("projets.finance_secteur", **finance_sector_args), "primary": True},
                    {"label": "Export XLSX", "url": url_for("projets.finance_secteur_export", **finance_sector_args)},
                ],
            },
        ])

    if can("questionnaires:view"):
        add_group("Questionnaires et retours", "Exports des réponses qualitatives ou d'impact.", [
            {
                "title": "Questionnaires d'impact",
                "meta": "Réponses exportables en CSV",
                "actions": [
                    {"label": "Ouvrir", "url": url_for("questionnaires.index"), "primary": True},
                ],
            },
        ])

    if can("emargement:view"):
        add_group(
            "Communication",
            "Le programme des activités à publier en ligne (sur votre site / hébergement).",
            [
                {
                    "title": "Programme public des activités",
                    "meta": "Page HTML autonome — ateliers collectifs à venir, sans données personnelles",
                    "actions": [
                        {"label": "Télécharger (HTML)", "url": url_for("main.programme_public_export"), "primary": True},
                        {"label": "Publier en ligne", "url": url_for("main.publication_programme")},
                    ],
                },
            ],
        )

    _hub_forbidden_if_empty(groups)
    subventions = _documents_subventions(year, selected_secteur)
    projects = _documents_projects(selected_secteur)
    actions = _documents_actions(year, selected_secteur)
    questionnaires = _documents_questionnaires(selected_secteur)

    return render_template(
        "documents_exports.html",
        year=year,
        selected_secteur=selected_secteur,
        secteurs=secteurs,
        groups=groups,
        subventions=subventions,
        projects=projects,
        actions=actions,
        questionnaires=questionnaires,
        summary={
            "sorties": sum(len(group["cards"]) for group in groups),
            "subventions": len(subventions),
            "projects": len(projects),
            "actions": len(actions),
            "questionnaires": len(questionnaires),
            "orientations": _documents_orientation_count(year, selected_secteur),
        },
    )
# ---------------------------------------------------------------------
# C1-C4 — Pilotage direction / comité / journal métier
# ---------------------------------------------------------------------

def _direction_year() -> int:
    try:
        year = int((request.args.get("year") or request.args.get("annee") or "").strip())
    except Exception:
        year = date.today().year
    return max(2000, min(2100, year))


def _direction_scope() -> tuple[int, str | None, list[str]]:
    year = _direction_year()
    secteurs = current_app.config.get("SECTEURS", []) or []
    extra = {
        row[0]
        for row in Subvention.query.with_entities(Subvention.secteur).filter(Subvention.secteur.isnot(None)).distinct().all()
    }
    extra.update({
        row[0]
        for row in Projet.query.with_entities(Projet.secteur).filter(Projet.secteur.isnot(None)).distinct().all()
    })
    extra.update({
        row[0]
        for row in SessionActivite.query.with_entities(SessionActivite.secteur).filter(SessionActivite.secteur.isnot(None)).distinct().all()
    })
    secteurs = secteurs + [s for s in sorted(extra) if s and s not in secteurs]
    selected = (request.args.get("secteur") or "").strip() or None
    if not can("scope:all_secteurs"):
        selected = getattr(current_user, "secteur_assigne", None)
        secteurs = [selected] if selected else []
    return year, selected, secteurs


def _direction_compact_args(**kwargs) -> dict:
    return {k: v for k, v in kwargs.items() if v not in (None, "")}


def _direction_context() -> dict:
    from app.models import AuditLog, BenevoleHeures, Depense, Participant, PresenceActivite, Salarie, SessionActivite, SuiviRappel

    year, selected_secteur, secteurs = _direction_scope()
    start, end = date(year, 1, 1), date(year, 12, 31)

    session_date = db.func.coalesce(SessionActivite.rdv_date, SessionActivite.date_session)
    sessions_q = SessionActivite.query.filter(SessionActivite.is_deleted.is_(False), session_date >= start, session_date <= end)
    presences_q = PresenceActivite.query.join(SessionActivite).filter(SessionActivite.is_deleted.is_(False), session_date >= start, session_date <= end)
    participants_q = Participant.query
    subventions_q = Subvention.query.filter(Subvention.est_archive.is_(False), Subvention.annee_exercice == year)
    depenses_q = Depense.query.filter(Depense.est_supprimee.is_(False))
    rappels_q = SuiviRappel.query.filter(SuiviRappel.statut == "ouvert")
    benevolat_q = BenevoleHeures.query.filter(BenevoleHeures.date_action >= start, BenevoleHeures.date_action <= end)
    salaries_q = Salarie.query

    if selected_secteur:
        sessions_q = sessions_q.filter(SessionActivite.secteur == selected_secteur)
        presences_q = presences_q.filter(SessionActivite.secteur == selected_secteur)
        participants_q = participants_q.filter(Participant.created_secteur == selected_secteur)
        subventions_q = subventions_q.filter(Subvention.secteur == selected_secteur)
        depenses_q = depenses_q.join(Depense.affectations, isouter=True).join(Subvention, Subvention.id == DepenseAffectation.subvention_id, isouter=True).filter(db.or_(Subvention.secteur == selected_secteur, Subvention.id.is_(None)))
        rappels_q = rappels_q.filter(db.or_(SuiviRappel.secteur == selected_secteur, SuiviRappel.secteur.is_(None)))
        benevolat_q = benevolat_q.filter(BenevoleHeures.secteur == selected_secteur)
        salaries_q = salaries_q.filter(Salarie.secteur == selected_secteur)
    elif not can("scope:all_secteurs"):
        secteur_user = getattr(current_user, "secteur_assigne", None)
        sessions_q = sessions_q.filter(SessionActivite.secteur == secteur_user)
        presences_q = presences_q.filter(SessionActivite.secteur == secteur_user)
        participants_q = participants_q.filter(Participant.created_secteur == secteur_user)
        subventions_q = subventions_q.filter(Subvention.secteur == secteur_user)
        rappels_q = rappels_q.filter(db.or_(SuiviRappel.secteur == secteur_user, SuiviRappel.secteur.is_(None)))
        benevolat_q = benevolat_q.filter(BenevoleHeures.secteur == secteur_user)
        salaries_q = salaries_q.filter(Salarie.secteur == secteur_user)

    subventions = subventions_q.all() if (can("subventions:view") or can("bilans:view") or can("stats:view")) else []
    total_attribue = sum(float(s.montant_attribue or 0) for s in subventions)
    total_recu = sum(float(s.montant_recu or 0) for s in subventions)
    total_engage = sum(float(s.total_engage or 0) for s in subventions)
    consommation_pct = round((total_engage / total_attribue) * 100, 1) if total_attribue else 0.0

    sessions_count = sessions_q.count() if (can("emargement:view") or can("stats:view")) else 0
    presences_count = presences_q.count() if (can("emargement:view") or can("stats:view")) else 0
    unique_participants = presences_q.with_entities(PresenceActivite.participant_id).distinct().count() if (can("participants:view") or can("stats:view")) else 0
    total_participants = participants_q.count() if can("participants:view") or can("participants:view_all") else 0
    depenses_total = float(depenses_q.with_entities(db.func.coalesce(db.func.sum(Depense.montant), 0)).scalar() or 0) if can("depenses:view") else 0.0
    heures_benevoles = float(benevolat_q.with_entities(db.func.coalesce(db.func.sum(BenevoleHeures.heures), 0)).scalar() or 0) if can("stats:view") else 0.0
    etp = float(sum(float(s.etp or 0) for s in salaries_q.all() if s.actif_sur(year))) if can("rh:view") else 0.0

    alert_cards = []
    if can("dashboard:view"):
        alert_cards.append({"label": "Rappels ouverts", "value": rappels_q.count(), "tone": "warn", "url": url_for("main.suivi_rappels")})
    bilans_attendus = [s for s in subventions if s.date_bilan_prevu and s.date_bilan_prevu <= end]
    bilans_en_retard = [s for s in bilans_attendus if s.date_bilan_prevu and s.date_bilan_prevu < date.today()]
    if can("subventions:view"):
        alert_cards.append({"label": "Bilans financeurs attendus", "value": len(bilans_attendus), "tone": "info", "url": url_for("main.documents_exports", year=year, secteur=selected_secteur or "")})
        alert_cards.append({"label": "Bilans financeurs en retard", "value": len(bilans_en_retard), "tone": "danger" if bilans_en_retard else "ok", "url": url_for("main.documents_exports", year=year, secteur=selected_secteur or "")})
    if can("admin:rbac"):
        alert_cards.append({"label": "Actions tracées", "value": AuditLog.query.count(), "tone": "info", "url": url_for("main.journal_metier")})

    ratios = [
        {"label": "Présences par séance", "value": round(presences_count / sessions_count, 1) if sessions_count else 0, "help": "Moyenne annuelle"},
        {"label": "Présences par participant", "value": round(presences_count / unique_participants, 1) if unique_participants else 0, "help": "Intensité de fréquentation"},
        {"label": "Consommation budget", "value": f"{consommation_pct:.1f}%", "help": "Engagé / attribué"},
    ]

    quick_links = [
        {"label": "Documents prêts", "url": url_for("main.documents_exports", year=year, secteur=selected_secteur or "")},
        {"label": "Qualité des données", "url": url_for("main.qualite_donnees_transverse", secteur=selected_secteur or "")},
        {"label": "Vue comité / CA", "url": url_for("main.comite_pilotage", year=year, secteur=selected_secteur or "")},
        {"label": "Journal métier", "url": url_for("main.journal_metier")},
    ]

    return {
        "year": year,
        "selected_secteur": selected_secteur,
        "secteurs": secteurs,
        "kpis": [
            {"label": "Participants uniques", "value": unique_participants, "help": f"Présents au moins une fois en {year}"},
            {"label": "Présences", "value": presences_count, "help": "Lignes d’émargement"},
            {"label": "Séances", "value": sessions_count, "help": "Ateliers et rendez-vous réalisés"},
            {"label": "Budget engagé", "value": f"{total_engage:,.0f} €".replace(",", " "), "help": f"sur {total_attribue:,.0f} € attribués".replace(",", " ")},
            {"label": "Dépenses", "value": f"{depenses_total:,.0f} €".replace(",", " "), "help": "Dépenses non supprimées"},
            {"label": "Bénévolat", "value": f"{heures_benevoles:,.1f} h".replace(",", " "), "help": "Heures valorisables"},
            {"label": "ETP", "value": f"{etp:.2f}", "help": "Salariés actifs sur l’exercice"},
            {"label": "Fiches créées", "value": total_participants, "help": "Participants du périmètre"},
        ],
        "alert_cards": alert_cards,
        "ratios": ratios,
        "subventions": sorted(subventions, key=lambda s: (s.date_bilan_prevu or date(2100, 1, 1), s.nom))[:8],
        "quick_links": quick_links,
        "print_args": _direction_compact_args(year=year, secteur=selected_secteur),
    }


@bp.route("/direction/pilotage")
@login_required
@require_perm("dashboard:view")
def direction_pilotage():
    if not (can("stats:view") or can("bilans:view") or can("subventions:view") or can("scope:all_secteurs")):
        abort(403)
    return render_template("direction_pilotage.html", **_direction_context())


@bp.route("/direction/comite-pilotage")
@login_required
@require_perm("dashboard:view")
def comite_pilotage():
    if not (can("stats:view") or can("bilans:view") or can("subventions:view") or can("scope:all_secteurs")):
        abort(403)
    return render_template("comite_pilotage.html", **_direction_context())


@bp.route("/journal-metier")
@login_required
@require_perm("dashboard:view")
def journal_metier():
    from app.models import AuditLog

    if not (can("admin:rbac") or can("controle:view") or can("scope:all_secteurs")):
        abort(403)
    action = (request.args.get("action") or "").strip()
    cible = (request.args.get("cible") or "").strip()
    q = AuditLog.query
    if action:
        q = q.filter(AuditLog.action == action)
    if cible:
        q = q.filter(AuditLog.cible.ilike(f"%{cible}%"))
    entries = q.order_by(AuditLog.created_at.desc()).limit(200).all()
    actions = [a for (a,) in db.session.query(AuditLog.action).distinct().order_by(AuditLog.action).all()]
    return render_template("journal_metier.html", entries=entries, actions=actions, action=action, cible=cible)



@bp.route("/ressources")
@login_required
def hub_ressources():
    cards = []

    if can("inventaire:view"):
        cards.append({
            "title": "Inventaire matériel",
            "subtitle": "Suivre le matériel, les achats et les équipements liés aux dépenses.",
            "primary_label": "Ouvrir inventaire",
            "primary_url": url_for("inventaire_materiel.list_items"),
            "secondary": [
                {"label": "Ajouter du matériel", "url": url_for("inventaire_materiel.new_item")} if can("inventaire:edit") else None,
            ],
            "tag": "Matériel",
        })

    if can("partenaires:view"):
        cards.append({
            "title": "Annuaire partenaires",
            "subtitle": "Retrouver les structures, contacts et interventions partenaires.",
            "primary_label": "Ouvrir partenaires",
            "primary_url": url_for("partenaires.index"),
            "secondary": [
                {"label": "Ajouter un partenaire", "url": url_for("partenaires.create")} if can("partenaires:edit") else None,
            ],
            "tag": "Réseau",
        })

    if can("subventions:view"):
        cards.append({
            "title": "Générateur de budget prévisionnel",
            "subtitle": "Saisir un compte de résultat prévisionnel (modèle CERFA / CAF-FADO), l'exporter en Excel ou l'envoyer vers une subvention ou un projet.",
            "primary_label": "Ouvrir le générateur",
            "primary_url": url_for("previsionnel.generateur"),
            "secondary": [
                {"label": "Budgets demandés", "url": url_for("previsionnel.index")},
            ],
            "tag": "Budget",
        })

    if can("participants:view"):
        cards.append({
            "title": "Participation des habitants",
            "subtitle": "Pyramide de la participation des habitants : répartition par étage, évolution entre périodes, détail par participant. Alimentée par l'émargement.",
            "primary_label": "Ouvrir la pyramide",
            "primary_url": url_for("main.hart_collectif"),
            "secondary": [],
            "tag": "Participation",
        })
        cards.append({
            "title": "Bénévolat",
            "subtitle": "Heures données par les habitants, missions, valorisation € (compte 87). Alimente l'onglet vitalité démocratique du SENACS.",
            "primary_label": "Ouvrir le bénévolat",
            "primary_url": url_for("main.benevolat"),
            "secondary": [],
            "tag": "Participation",
        })

    if can("rh:view"):
        cards.append({
            "title": "Équipe salariée",
            "subtitle": "Réservé direction : salariés, affectation secteur/poste, ETP, masse salariale. Alimente le SENACS et les finances. Import depuis votre outil RH.",
            "primary_label": "Ouvrir le module RH",
            "primary_url": url_for("main.rh"),
            "secondary": [],
            "tag": "Direction",
        })

    if can("dons:view"):
        cards.append({
            "title": "Dons & reçus fiscaux",
            "subtitle": "Registre numéroté des dons et reçus fiscaux (modèle CERFA 11580) : montant en lettres, mentions légales, impression PDF, export Excel.",
            "primary_label": "Ouvrir le registre",
            "primary_url": url_for("main.dons_registre"),
            "secondary": [],
            "tag": "Fiscal",
        })

    if can("stats:view"):
        cards.append({
            "title": "Coût unitaire d'une action",
            "subtitle": "Croise un montant (ou une subvention) avec l'activité réelle : coût par participant, par présence, par heure — prêt à citer dans un dossier.",
            "primary_label": "Ouvrir le calculateur",
            "primary_url": url_for("main.cout_unitaire"),
            "secondary": [],
            "tag": "Budget",
        })

    _hub_forbidden_if_empty(cards)
    return render_template(
        "hub_ressources.html",
        title="Ressources",
        intro="Les ressources utiles autour des projets : matériel, partenaires et outils de soutien.",
        cards=cards,
    )





