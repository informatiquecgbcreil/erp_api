import json
from datetime import datetime, date
from io import BytesIO

from flask import render_template, request, redirect, url_for, flash, abort, send_file
from flask_login import login_required, current_user
from app.rbac import require_perm, can

from app.extensions import db
from app.services.indicators import (
    INDICATOR_PACKS,
    INDICATOR_METRIC_GROUPS,
    INDICATOR_TEMPLATES,
    PERIOD_CHOICES,
    TARGET_OP_CHOICES,
    TARGET_OP_SYMBOLS,
    compute_project_indicators,
    compute_project_indicator_alerts,
    indicator_counts,
    indicator_gauge_pct,
    indicator_list_rows,
    indicator_metric_code,
    indicator_params,
    indicator_source,
    indicator_unique_code,
    indicator_value_type,
    parse_float_optional,
)

from app.models import (
    Projet,
    AtelierActivite,
    ProjetAtelier,
    ProjetIndicateur,
    ProjetIndicateurValeur,
    ProjetJournalEntry,
    ProjetAction,
    ProjetActionAtelier,
)
from app.statsimpact.engine import (
    StatsFilters,
    compute_demography_stats,
    compute_participation_frequency_stats,
    compute_volume_activity_stats,
)


from app.projets.helpers_projet import (
    _action_completion_payload,
    _action_linked_atelier_ids,
    _action_list_rows,
    _action_period,
    _clean_choice,
    _compute_action_stats,
    _parse_date_or_none,
    _project_completion_payload,
    _project_linked_atelier_ids,
    _projet_finance_years,
    _projet_selected_year,
)
from app.projets.projets_crud import (
    _projet_finance_context,
)
from app.projets.common import (
    PROJET_ACTION_CATEGORIES,
    PROJET_ACTION_STATUTS,
    PROJET_JOURNAL_CATEGORIES,
    bp,
    can_see_secteur,
)

@bp.route("/projets/<int:projet_id>/indicateurs", methods=["GET", "POST"])
@login_required
@require_perm("projets:view")
def projet_indicateurs(projet_id):
    p = db.get_or_404(Projet, projet_id)
    if not can_see_secteur(p.secteur):
        abort(403)

    ateliers = AtelierActivite.query.filter_by(secteur=p.secteur, is_deleted=False).order_by(AtelierActivite.nom.asc()).all()
    linked_atelier_ids = {
        link.atelier_id for link in ProjetAtelier.query.filter_by(projet_id=p.id).all()
    }

    def clean_target_params(params: dict) -> dict:
        target = parse_float_optional(request.form.get("target"))
        target_op = (request.form.get("target_op") or "ge").strip()
        if target_op not in TARGET_OP_CHOICES:
            target_op = "ge"
        params["target_op"] = target_op
        if target is None:
            params.pop("target", None)
        else:
            params["target"] = target
        return params

    def clean_period_params(params: dict) -> dict:
        period = (request.form.get("period") or "context").strip()
        if period not in PERIOD_CHOICES:
            period = "context"
        params["period"] = period
        if period == "custom":
            params["start"] = (request.form.get("start") or "").strip() or None
            params["end"] = (request.form.get("end") or "").strip() or None
        else:
            params.pop("start", None)
            params.pop("end", None)

        atelier_id = None
        raw = (request.form.get("atelier_id") or "").strip()
        if raw:
            try:
                candidate = int(raw)
                if candidate in linked_atelier_ids:
                    atelier_id = candidate
            except Exception:
                atelier_id = None
        if atelier_id:
            params["atelier_id"] = atelier_id
        else:
            params.pop("atelier_id", None)
        return params

    if request.method == "POST":
        if not can("projets:edit"):
            abort(403)

        action = request.form.get("action") or ""

        if action == "add_manual_number":
            label = (request.form.get("label") or "").strip()
            if not label:
                flash("Le libellé de l'indicateur est obligatoire.", "danger")
                return redirect(url_for("projets.projet_indicateurs", projet_id=p.id))

            params = {
                "source": "manual",
                "value_type": "number",
                "manual_value": parse_float_optional(request.form.get("manual_value")),
                "unit": (request.form.get("unit") or "").strip(),
            }
            clean_target_params(params)
            db.session.add(ProjetIndicateur(
                projet_id=p.id,
                code=indicator_unique_code("manual_number"),
                label=label,
                is_active=True,
                params_json=json.dumps(params, ensure_ascii=False),
            ))
            db.session.commit()
            flash("Indicateur manuel ajouté.", "success")
            return redirect(url_for("projets.projet_indicateurs", projet_id=p.id))

        if action == "add_manual_check":
            label = (request.form.get("label") or "").strip()
            if not label:
                flash("Le libellé de la coche est obligatoire.", "danger")
                return redirect(url_for("projets.projet_indicateurs", projet_id=p.id))

            params = {
                "source": "manual",
                "value_type": "check",
                "checked": bool(request.form.get("checked")),
            }
            db.session.add(ProjetIndicateur(
                projet_id=p.id,
                code=indicator_unique_code("manual_check"),
                label=label,
                is_active=True,
                params_json=json.dumps(params, ensure_ascii=False),
            ))
            db.session.commit()
            flash("Coche de validation ajoutée.", "success")
            return redirect(url_for("projets.projet_indicateurs", projet_id=p.id))

        if action == "add_stats_indicator":
            metric_code = (request.form.get("metric_code") or request.form.get("code") or "").strip()
            if metric_code not in INDICATOR_TEMPLATES:
                flash("La statistique sélectionnée est invalide.", "danger")
                return redirect(url_for("projets.projet_indicateurs", projet_id=p.id))

            label = (request.form.get("label") or "").strip() or INDICATOR_TEMPLATES[metric_code]
            params = {
                "source": "stats",
                "value_type": "number",
                "metric_code": metric_code,
            }
            clean_target_params(params)
            clean_period_params(params)
            db.session.add(ProjetIndicateur(
                projet_id=p.id,
                code=indicator_unique_code(f"stat_{metric_code}"),
                label=label,
                is_active=True,
                params_json=json.dumps(params, ensure_ascii=False),
            ))
            db.session.commit()
            flash("Indicateur connecté aux stats-impact ajouté.", "success")
            return redirect(url_for("projets.projet_indicateurs", projet_id=p.id))

        if action == "add_pack":
            pack = (request.form.get("pack") or "").strip()
            cfg = INDICATOR_PACKS.get(pack)
            if not cfg:
                flash("Le pack sélectionné est invalide.", "danger")
                return redirect(url_for("projets.projet_indicateurs", projet_id=p.id))

            existing_metrics = set()
            for ind in ProjetIndicateur.query.filter_by(projet_id=p.id).all():
                params = indicator_params(ind)
                existing_metrics.add(indicator_metric_code(ind, params) or ind.code)

            added = 0
            for metric_code in cfg["codes"]:
                if metric_code not in INDICATOR_TEMPLATES or metric_code in existing_metrics:
                    continue
                params = {
                    "source": "stats",
                    "value_type": "number",
                    "metric_code": metric_code,
                    "period": "context",
                    "target_op": "ge",
                }
                db.session.add(ProjetIndicateur(
                    projet_id=p.id,
                    code=indicator_unique_code(f"stat_{metric_code}"),
                    label=INDICATOR_TEMPLATES[metric_code],
                    is_active=True,
                    params_json=json.dumps(params, ensure_ascii=False),
                ))
                added += 1
            db.session.commit()
            flash(f"Le pack a bien été ajouté ({added} indicateur(s)).", "success")
            return redirect(url_for("projets.projet_indicateurs", projet_id=p.id))

        if action == "update_indicateur":
            indic_id = int(request.form.get("indicateur_id") or 0)
            ind = db.get_or_404(ProjetIndicateur, indic_id)
            if ind.projet_id != p.id:
                abort(400)

            params = indicator_params(ind)
            source = indicator_source(ind, params)
            value_type = indicator_value_type(ind, params)
            label = (request.form.get("label") or "").strip()
            if label:
                ind.label = label
            ind.is_active = bool(request.form.get("is_active"))

            if source == "stats":
                metric_code = (request.form.get("metric_code") or indicator_metric_code(ind, params)).strip()
                if metric_code not in INDICATOR_TEMPLATES:
                    flash("La statistique sélectionnée est invalide.", "danger")
                    return redirect(url_for("projets.projet_indicateurs", projet_id=p.id))
                params.update({
                    "source": "stats",
                    "value_type": "number",
                    "metric_code": metric_code,
                })
                clean_target_params(params)
                clean_period_params(params)
            elif value_type == "check":
                params = {
                    "source": "manual",
                    "value_type": "check",
                    "checked": bool(request.form.get("checked")),
                }
            else:
                ancienne_valeur = parse_float_optional(params.get("manual_value"))
                nouvelle_valeur = parse_float_optional(request.form.get("manual_value"))
                params.update({
                    "source": "manual",
                    "value_type": "number",
                    "manual_value": nouvelle_valeur,
                    "unit": (request.form.get("unit") or "").strip(),
                })
                clean_target_params(params)
                # Historise toute valeur effectivement modifiée (traçabilité).
                if nouvelle_valeur is not None and nouvelle_valeur != ancienne_valeur:
                    db.session.add(ProjetIndicateurValeur(
                        indicateur_id=ind.id,
                        valeur=nouvelle_valeur,
                        source="manual",
                        commentaire=(request.form.get("commentaire") or "").strip() or None,
                        saisie_par_user_id=getattr(current_user, "id", None),
                    ))

            ind.params_json = json.dumps(params, ensure_ascii=False)
            db.session.commit()
            flash("Indicateur enregistré.", "success")
            return redirect(url_for("projets.projet_indicateurs", projet_id=p.id))

        if action == "delete_indicateur":
            indic_id = int(request.form.get("indicateur_id") or 0)
            ind = db.get_or_404(ProjetIndicateur, indic_id)
            if ind.projet_id != p.id:
                abort(400)
            db.session.delete(ind)
            db.session.commit()
            flash("Indicateur supprimé.", "warning")
            return redirect(url_for("projets.projet_indicateurs", projet_id=p.id))

        abort(400)

    indicateurs = ProjetIndicateur.query.filter_by(projet_id=p.id).order_by(ProjetIndicateur.created_at.asc()).all()
    indicator_rows = indicator_list_rows(indicateurs)

    # Restitution : valeurs calculées (y compris stats), jauges et alertes « à traiter ».
    year = _projet_selected_year(p)
    finance = _projet_finance_context(p, year)
    computed = compute_project_indicators(p, selected_annee=year, subventions=finance.get("subventions"))
    for row in computed:
        row["gauge_pct"] = indicator_gauge_pct(row)
    alertes_indicateurs = compute_project_indicator_alerts(computed)

    # Dernier relevé historisé par indicateur (pour affichage rapide).
    derniers_releves = {}
    ind_ids = [i.id for i in indicateurs]
    if ind_ids:
        for v in (ProjetIndicateurValeur.query
                  .filter(ProjetIndicateurValeur.indicateur_id.in_(ind_ids))
                  .order_by(ProjetIndicateurValeur.saisie_le.desc()).all()):
            derniers_releves.setdefault(v.indicateur_id, v)

    return render_template(
        "projets_indicateurs.html",
        projet=p,
        ateliers=ateliers,
        linked_ateliers=linked_atelier_ids,
        indicator_rows=indicator_rows,
        indicator_counts=indicator_counts(indicator_rows),
        indicator_computed=computed,
        indicator_alertes=alertes_indicateurs,
        derniers_releves=derniers_releves,
        indicator_year=year,
        indicator_templates=INDICATOR_TEMPLATES,
        indicator_metric_groups=INDICATOR_METRIC_GROUPS,
        indicator_packs=INDICATOR_PACKS,
        period_choices=PERIOD_CHOICES,
        target_op_choices=TARGET_OP_CHOICES,
        target_op_symbols=TARGET_OP_SYMBOLS,
        can_edit_indicateurs=can("projets:edit"),
    )


@bp.route("/projets/<int:projet_id>/indicateurs.xlsx")
@login_required
@require_perm("projets:view")
def projet_indicateurs_export(projet_id):
    from openpyxl import Workbook

    p = db.get_or_404(Projet, projet_id)
    if not can_see_secteur(p.secteur):
        abort(403)

    year = _projet_selected_year(p)
    finance = _projet_finance_context(p, year)
    computed = compute_project_indicators(p, selected_annee=year, subventions=finance.get("subventions"))

    statut_labels = {"ok": "Atteint", "warn": "À surveiller", "bad": "Non atteint", None: "—"}

    wb = Workbook()
    ws = wb.active
    ws.title = "Indicateurs"
    ws.append(["Indicateur", "Source", "Valeur", "Unité", "Règle", "Objectif", "Statut", "Période"])
    for row in computed:
        value = row.get("display_value")
        if value is None:
            value = row.get("value")
        ws.append([
            row.get("label") or row.get("code") or "",
            "Stats-impact" if row.get("source") == "stats" else "Manuel",
            value if value is not None else "",
            row.get("unit") or "",
            row.get("target_symbol") or "",
            row.get("target") if row.get("target") is not None else "",
            statut_labels.get(row.get("status"), row.get("status") or "—"),
            row.get("period") or "",
        ])
    for col, width in zip("ABCDEFGH", (40, 14, 12, 10, 8, 12, 16, 12)):
        ws.column_dimensions[col].width = width

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(
        buf,
        as_attachment=True,
        download_name=f"indicateurs_projet_{p.id}_{year}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@bp.route("/projets/<int:projet_id>/synthese", methods=["GET", "POST"])
@login_required
@require_perm("projets:view")
def projet_synthese(projet_id):
    p = db.get_or_404(Projet, projet_id)
    if not can_see_secteur(p.secteur):
        abort(403)

    year = _projet_selected_year(p)
    if request.method == "POST":
        if not can("projets:edit"):
            abort(403)

        action = request.form.get("action") or ""
        if action == "add_journal":
            entry_date = _parse_date_or_none(request.form.get("entry_date")) or date.today()
            categorie = (request.form.get("categorie") or "fait_marquant").strip()
            if categorie not in PROJET_JOURNAL_CATEGORIES:
                categorie = "fait_marquant"
            titre = (request.form.get("titre") or "").strip() or None
            contenu = (request.form.get("contenu") or "").strip()
            if not contenu:
                flash("Le contenu du journal est obligatoire.", "danger")
                return redirect(url_for("projets.projet_synthese", projet_id=p.id, year=year))

            db.session.add(ProjetJournalEntry(
                projet_id=p.id,
                entry_date=entry_date,
                categorie=categorie,
                titre=titre,
                contenu=contenu,
                created_by_user_id=current_user.id,
            ))
            db.session.commit()
            flash("Note ajoutée au journal de bord.", "success")
            return redirect(url_for("projets.projet_synthese", projet_id=p.id, year=year))

        if action == "delete_journal":
            entry_id = int(request.form.get("entry_id") or 0)
            entry = db.get_or_404(ProjetJournalEntry, entry_id)
            if entry.projet_id != p.id:
                abort(400)
            db.session.delete(entry)
            db.session.commit()
            flash("Note supprimée du journal de bord.", "warning")
            return redirect(url_for("projets.projet_synthese", projet_id=p.id, year=year))

        if action == "add_project_action":
            titre = (request.form.get("titre") or "").strip()
            if not titre:
                flash("Le titre de la fiche action est obligatoire.", "danger")
                return redirect(url_for("projets.projet_synthese", projet_id=p.id, year=year))

            item = ProjetAction(
                projet_id=p.id,
                titre=titre,
                categorie=_clean_choice(request.form.get("categorie"), PROJET_ACTION_CATEGORIES, "atelier"),
                statut=_clean_choice(request.form.get("statut"), PROJET_ACTION_STATUTS, "prevue"),
                date_debut=_parse_date_or_none(request.form.get("date_debut")),
                date_fin=_parse_date_or_none(request.form.get("date_fin")),
                description=(request.form.get("description") or "").strip() or None,
                created_by_user_id=current_user.id,
            )
            db.session.add(item)
            db.session.commit()
            flash("Fiche action créée.", "success")
            return redirect(url_for("projets.projet_action_detail", projet_id=p.id, action_id=item.id, year=year))

        abort(400)

    finance = _projet_finance_context(p, year)
    linked_atelier_ids = [
        row.atelier_id for row in ProjetAtelier.query.filter_by(projet_id=p.id).all()
    ]
    linked_ateliers = []
    if linked_atelier_ids:
        linked_ateliers = (
            AtelierActivite.query
            .filter(AtelierActivite.id.in_(linked_atelier_ids))
            .order_by(AtelierActivite.nom.asc())
            .all()
        )

    flt = StatsFilters(
        secteur=p.secteur,
        atelier_ids=linked_atelier_ids,
        date_from=date(year, 1, 1),
        date_to=date(year, 12, 31),
    )
    if linked_atelier_ids:
        activity_stats = compute_volume_activity_stats(flt)
        freq_stats = compute_participation_frequency_stats(flt)
        demo_stats = compute_demography_stats(flt)
    else:
        activity_stats = {"kpi": {"presences": 0, "uniques": 0, "sessions": 0, "hours_people": 0}}
        freq_stats = {"returning": 0, "returning_rate": 0, "freq_avg": 0}
        demo_stats = {
            "age_avg": None,
            "qpv": {"qpv": 0, "hors_qpv": 0, "inconnu": 0},
            "genre": {},
            "age_buckets": {},
        }

    indicators = compute_project_indicators(p, selected_annee=year, subventions=finance.get("subventions"))
    indicator_summary = {
        "total": len(indicators),
        "ok": sum(1 for row in indicators if row.get("status") == "ok"),
        "warn": sum(1 for row in indicators if row.get("status") == "warn"),
        "bad": sum(1 for row in indicators if row.get("status") == "bad"),
        "pending": sum(1 for row in indicators if not row.get("status")),
    }

    journal_entries = (
        ProjetJournalEntry.query
        .filter_by(projet_id=p.id)
        .order_by(ProjetJournalEntry.entry_date.desc(), ProjetJournalEntry.created_at.desc())
        .all()
    )
    actions = (
        ProjetAction.query
        .filter_by(projet_id=p.id)
        .order_by(ProjetAction.date_debut.desc(), ProjetAction.created_at.desc())
        .all()
    )
    action_rows = _action_list_rows(p, actions, year)
    project_quality = _project_completion_payload(
        p,
        linked_ateliers,
        action_rows,
        indicators,
        journal_entries,
        finance,
    )

    return render_template(
        "projets_synthese.html",
        projet=p,
        year=year,
        years=_projet_finance_years(p),
        finance=finance,
        linked_ateliers=linked_ateliers,
        activity_stats=activity_stats,
        freq_stats=freq_stats,
        demo_stats=demo_stats,
        indicators=indicators,
        indicator_summary=indicator_summary,
        action_rows=action_rows,
        project_quality=project_quality,
        action_categories=PROJET_ACTION_CATEGORIES,
        action_statuts=PROJET_ACTION_STATUTS,
        journal_entries=journal_entries,
        journal_categories=PROJET_JOURNAL_CATEGORIES,
        can_edit_journal=can("projets:edit"),
        can_edit_project=can("projets:edit"),
    )


@bp.route("/projets/<int:projet_id>/fiche")
@login_required
@require_perm("projets:view")
def projet_fiche_document(projet_id):
    p = db.get_or_404(Projet, projet_id)
    if not can_see_secteur(p.secteur):
        abort(403)

    year = _projet_selected_year(p)
    finance = _projet_finance_context(p, year)
    linked_atelier_ids = [
        row.atelier_id for row in ProjetAtelier.query.filter_by(projet_id=p.id).all()
    ]
    linked_ateliers = []
    if linked_atelier_ids:
        linked_ateliers = (
            AtelierActivite.query
            .filter(AtelierActivite.id.in_(linked_atelier_ids))
            .order_by(AtelierActivite.nom.asc())
            .all()
        )

    flt = StatsFilters(
        secteur=p.secteur,
        atelier_ids=linked_atelier_ids,
        date_from=date(year, 1, 1),
        date_to=date(year, 12, 31),
    )
    if linked_atelier_ids:
        activity_stats = compute_volume_activity_stats(flt)
        freq_stats = compute_participation_frequency_stats(flt)
        demo_stats = compute_demography_stats(flt)
    else:
        activity_stats = {"kpi": {"presences": 0, "uniques": 0, "sessions": 0, "hours_people": 0}}
        freq_stats = {"returning": 0, "returning_rate": 0, "freq_avg": 0}
        demo_stats = {
            "age_avg": None,
            "qpv": {"qpv": 0, "hors_qpv": 0, "inconnu": 0},
            "genre": {},
            "age_buckets": {},
        }

    indicators = compute_project_indicators(p, selected_annee=year, subventions=finance.get("subventions"))
    indicator_summary = {
        "total": len(indicators),
        "ok": sum(1 for row in indicators if row.get("status") == "ok"),
        "warn": sum(1 for row in indicators if row.get("status") == "warn"),
        "bad": sum(1 for row in indicators if row.get("status") == "bad"),
        "pending": sum(1 for row in indicators if not row.get("status")),
    }

    journal_entries = (
        ProjetJournalEntry.query
        .filter_by(projet_id=p.id)
        .order_by(ProjetJournalEntry.entry_date.desc(), ProjetJournalEntry.created_at.desc())
        .limit(8)
        .all()
    )
    actions = (
        ProjetAction.query
        .filter_by(projet_id=p.id)
        .order_by(ProjetAction.date_debut.desc(), ProjetAction.created_at.desc())
        .all()
    )
    action_rows = _action_list_rows(p, actions, year)
    project_quality = _project_completion_payload(
        p,
        linked_ateliers,
        action_rows,
        indicators,
        journal_entries,
        finance,
    )

    return render_template(
        "projets_fiche_document.html",
        projet=p,
        year=year,
        years=_projet_finance_years(p),
        finance=finance,
        linked_ateliers=linked_ateliers,
        activity_stats=activity_stats,
        freq_stats=freq_stats,
        demo_stats=demo_stats,
        indicators=indicators,
        indicator_summary=indicator_summary,
        action_rows=action_rows,
        project_quality=project_quality,
        journal_entries=journal_entries,
        journal_categories=PROJET_JOURNAL_CATEGORIES,
        generated_at=datetime.now(),
    )


@bp.route("/projets/<int:projet_id>/actions/<int:action_id>", methods=["GET", "POST"])
@login_required
@require_perm("projets:view")
def projet_action_detail(projet_id, action_id):
    p = db.get_or_404(Projet, projet_id)
    if not can_see_secteur(p.secteur):
        abort(403)
    action = ProjetAction.query.filter_by(id=action_id, projet_id=p.id).first_or_404()
    year = _projet_selected_year(p)

    linked_project_atelier_ids = _project_linked_atelier_ids(p.id)
    ateliers = []
    if linked_project_atelier_ids:
        ateliers = (
            AtelierActivite.query
            .filter(AtelierActivite.id.in_(linked_project_atelier_ids))
            .order_by(AtelierActivite.nom.asc())
            .all()
        )
    action_atelier_ids = set(_action_linked_atelier_ids(action))

    if request.method == "POST":
        if not can("projets:edit"):
            abort(403)

        form_action = request.form.get("action") or ""
        if form_action == "update_project_action":
            titre = (request.form.get("titre") or "").strip()
            if not titre:
                flash("Le titre de la fiche action est obligatoire.", "danger")
                return redirect(url_for("projets.projet_action_detail", projet_id=p.id, action_id=action.id, year=year))

            action.titre = titre
            action.categorie = _clean_choice(request.form.get("categorie"), PROJET_ACTION_CATEGORIES, "atelier")
            action.statut = _clean_choice(request.form.get("statut"), PROJET_ACTION_STATUTS, "prevue")
            action.referent = (request.form.get("referent") or "").strip() or None
            action.date_debut = _parse_date_or_none(request.form.get("date_debut"))
            action.date_fin = _parse_date_or_none(request.form.get("date_fin"))
            action.lieu = (request.form.get("lieu") or "").strip() or None
            action.public_vise = (request.form.get("public_vise") or "").strip() or None
            action.territoire = (request.form.get("territoire") or "").strip() or None
            action.objectifs = (request.form.get("objectifs") or "").strip() or None
            action.description = (request.form.get("description") or "").strip() or None
            action.partenaires_text = (request.form.get("partenaires_text") or "").strip() or None
            action.bilan_qualitatif = (request.form.get("bilan_qualitatif") or "").strip() or None

            selected_ateliers = set()
            for raw in request.form.getlist("atelier_ids"):
                try:
                    atelier_id = int(raw)
                except Exception:
                    continue
                if atelier_id in linked_project_atelier_ids:
                    selected_ateliers.add(atelier_id)

            ProjetActionAtelier.query.filter_by(action_id=action.id).delete(synchronize_session=False)
            for atelier_id in sorted(selected_ateliers):
                db.session.add(ProjetActionAtelier(action_id=action.id, atelier_id=atelier_id))

            db.session.commit()
            flash("Fiche action enregistrée.", "success")
            return redirect(url_for("projets.projet_action_detail", projet_id=p.id, action_id=action.id, year=year))

        if form_action == "delete_project_action":
            db.session.delete(action)
            db.session.commit()
            flash("Fiche action supprimée.", "warning")
            return redirect(url_for("projets.projet_synthese", projet_id=p.id, year=year))

        abort(400)

    stats = _compute_action_stats(p, action, year)
    dmin, dmax = _action_period(action, year)
    action_quality = _action_completion_payload(action, list(action_atelier_ids))

    return render_template(
        "projets_action.html",
        projet=p,
        action=action,
        year=year,
        years=_projet_finance_years(p),
        action_categories=PROJET_ACTION_CATEGORIES,
        action_statuts=PROJET_ACTION_STATUTS,
        ateliers=ateliers,
        action_atelier_ids=action_atelier_ids,
        stats=stats,
        action_quality=action_quality,
        period_start=dmin,
        period_end=dmax,
        can_edit_project=can("projets:edit"),
    )


@bp.route("/projets/<int:projet_id>/actions/<int:action_id>/fiche")
@login_required
@require_perm("projets:view")
def projet_action_fiche_document(projet_id, action_id):
    p = db.get_or_404(Projet, projet_id)
    if not can_see_secteur(p.secteur):
        abort(403)
    action = ProjetAction.query.filter_by(id=action_id, projet_id=p.id).first_or_404()
    year = _projet_selected_year(p)

    linked_project_atelier_ids = _project_linked_atelier_ids(p.id)
    ateliers = []
    if linked_project_atelier_ids:
        ateliers = (
            AtelierActivite.query
            .filter(AtelierActivite.id.in_(linked_project_atelier_ids))
            .order_by(AtelierActivite.nom.asc())
            .all()
        )
    action_atelier_ids = set(_action_linked_atelier_ids(action))
    linked_action_ateliers = [atelier for atelier in ateliers if atelier.id in action_atelier_ids]
    stats = _compute_action_stats(p, action, year)
    period_start, period_end = _action_period(action, year)
    action_quality = _action_completion_payload(action, list(action_atelier_ids))

    return render_template(
        "projets_action_fiche_document.html",
        projet=p,
        action=action,
        year=year,
        years=_projet_finance_years(p),
        action_categories=PROJET_ACTION_CATEGORIES,
        action_statuts=PROJET_ACTION_STATUTS,
        action_atelier_ids=action_atelier_ids,
        linked_action_ateliers=linked_action_ateliers,
        stats=stats,
        action_quality=action_quality,
        period_start=period_start,
        period_end=period_end,
        generated_at=datetime.now(),
    )




# ---------------------------------------------------------------------
# Fiche action au format imposé : édition du narratif + génération .docx
# ---------------------------------------------------------------------
from app.projets import fiche_action as _fiche
from app.projets import objectifs as _objectifs


@bp.route("/projets/<int:projet_id>/actions/<int:action_id>/fiche/editer", methods=["GET", "POST"])
@login_required
def projet_action_fiche_editer(projet_id, action_id):
    p = db.get_or_404(Projet, projet_id)
    if not can_see_secteur(p.secteur):
        abort(403)
    action = ProjetAction.query.filter_by(id=action_id, projet_id=p.id).first_or_404()

    if request.method == "POST":
        if not can("projets:edit"):
            abort(403)
        existant = _fiche.charger(action.fiche_json)
        data = _fiche.depuis_formulaire(request.form, existant)
        action.fiche_json = _fiche.vers_json(data)
        _objectifs.enregistrer_contributions(action, request.form)
        db.session.commit()
        flash("Fiche action enregistrée.", "success")
        return redirect(url_for("projets.projet_action_fiche_editer", projet_id=p.id, action_id=action.id))

    fiche = _fiche.charger(action.fiche_json)
    contributions = {c["objectif"].id: c["contribution"] for c in _objectifs.contributions_de_action(action)}
    return render_template(
        "projets/action_fiche_editer.html",
        projet=p, action=action,
        entete=fiche.get("entete", {}),
        contenu=fiche.get("sections", {}),
        sections=_fiche.SECTIONS,
        champs_entete=_fiche.CHAMPS_ENTETE,
        objectifs_secteur=_objectifs.objectifs_du_secteur(p.secteur, actif_only=True),
        contributions=contributions,
    )


@bp.route("/projets/<int:projet_id>/actions/<int:action_id>/fiche.docx")
@login_required
def projet_action_fiche_docx(projet_id, action_id):
    p = db.get_or_404(Projet, projet_id)
    if not can_see_secteur(p.secteur):
        abort(403)
    action = ProjetAction.query.filter_by(id=action_id, projet_id=p.id).first_or_404()

    year = _projet_selected_year(p)
    fiche = _fiche.charger(action.fiche_json)
    stats = _compute_action_stats(p, action, year)
    objectifs = _objectifs.contributions_de_action(action)
    doc = _fiche.generer_docx(p, action, fiche, stats, year, objectifs=objectifs)

    sortie = BytesIO()
    doc.save(sortie)
    sortie.seek(0)
    nom = "".join(c if c.isalnum() else "-" for c in action.titre.lower()).strip("-") or "action"
    return send_file(
        sortie,
        as_attachment=True,
        download_name=f"fiche-action-{nom}-{year}.docx",
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
