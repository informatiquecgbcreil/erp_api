

from flask import (
    render_template,
    request,
    redirect,
    url_for,
    flash,
    abort,
)
from flask_login import login_required, current_user


from ..rbac import require_perm
from . import bp
from .services.emargement_models import (
    ENGINE_REGISTRY,
    GLOBAL_SCOPE,
    list_models_for_scope,
    get_model,
    upsert_model,
    delete_model,
)
from app.activite.helpers import (
    _available_secteur_labels,
    _can_manage_model_for_secteur,
    _deny_activity_access,
    _has_all_secteurs_scope,
    _is_admin_global,
    _user_secteur,
)



# ------------------ Modèles d'émargement ------------------


@bp.route('/modeles-emargement')
@login_required
def emargement_models():
    require_perm('ateliers:edit')(lambda: None)()
    secteur_ctx = _user_secteur()
    requested_secteur = (request.args.get('secteur') or '').strip()
    secteur_filter = requested_secteur or (None if (_is_admin_global() or _has_all_secteurs_scope()) else secteur_ctx)

    if secteur_filter and not _can_manage_model_for_secteur(secteur_filter) and not (_is_admin_global() or _has_all_secteurs_scope()):
        return _deny_activity_access()

    rows = list_models_for_scope(secteur=secteur_filter, include_inactive=True)
    secteurs = _available_secteur_labels()
    return render_template(
        'activite/emargement_models.html',
        rows=rows,
        secteurs=secteurs,
        selected_secteur=secteur_filter or '',
        is_admin_global=_is_admin_global() or _has_all_secteurs_scope(),
        global_scope=GLOBAL_SCOPE,
        engine_registry=ENGINE_REGISTRY,
    )


@bp.route('/modeles-emargement/new', methods=['GET', 'POST'])
@login_required
def emargement_model_new():
    require_perm('ateliers:edit')(lambda: None)()
    secteurs = _available_secteur_labels()
    default_secteur = (request.args.get('secteur') or getattr(current_user, 'secteur_assigne', None) or '').strip() or (secteurs[0] if secteurs else '')

    if request.method == 'POST':
        label = ' '.join((request.form.get('label') or '').split())
        secteur = (request.form.get('secteur') or '').strip() or default_secteur
        description = (request.form.get('description') or '').strip()
        engine = (request.form.get('engine') or '').strip()
        sort_order_raw = (request.form.get('sort_order') or '0').strip()
        is_active = bool(request.form.get('is_active'))
        if engine not in ENGINE_REGISTRY:
            flash('Le moteur sélectionné est invalide.', 'danger')
            return render_template('activite/emargement_model_form.html', row=None, values=request.form, secteurs=secteurs, engine_registry=ENGINE_REGISTRY, global_scope=GLOBAL_SCOPE)
        if not label:
            flash('Le libellé est obligatoire.', 'danger')
            return render_template('activite/emargement_model_form.html', row=None, values=request.form, secteurs=secteurs, engine_registry=ENGINE_REGISTRY, global_scope=GLOBAL_SCOPE)
        if not _can_manage_model_for_secteur(secteur):
            return _deny_activity_access()
        try:
            sort_order = int(sort_order_raw or 0)
        except ValueError:
            flash("L'ordre doit être un entier.", "danger")
            return render_template('activite/emargement_model_form.html', row=None, values=request.form, secteurs=secteurs, engine_registry=ENGINE_REGISTRY, global_scope=GLOBAL_SCOPE)
        try:
            upsert_model({
                'label': label,
                'secteur': secteur,
                'description': description,
                'engine': engine,
                'sort_order': sort_order,
                'is_active': is_active,
            })
        except ValueError as exc:
            flash(str(exc), 'warning')
            return render_template('activite/emargement_model_form.html', row=None, values=request.form, secteurs=secteurs, engine_registry=ENGINE_REGISTRY, global_scope=GLOBAL_SCOPE)
        flash("Le modèle d'émargement a bien été créé.", "success")
        return redirect(url_for('activite.emargement_models', secteur=secteur if secteur != GLOBAL_SCOPE else None))

    values = {'secteur': default_secteur, 'sort_order': '100', 'is_active': '1', 'engine': 'collectif_standard'}
    return render_template('activite/emargement_model_form.html', row=None, values=values, secteurs=secteurs, engine_registry=ENGINE_REGISTRY, global_scope=GLOBAL_SCOPE)


@bp.route('/modeles-emargement/<model_key>/edit', methods=['GET', 'POST'])
@login_required
def emargement_model_edit(model_key: str):
    require_perm('ateliers:edit')(lambda: None)()
    row = get_model(model_key)
    if not row:
        abort(404)
    if not _can_manage_model_for_secteur(row.get('secteur')):
        return _deny_activity_access()
    secteurs = _available_secteur_labels()
    if request.method == 'POST':
        label = ' '.join((request.form.get('label') or '').split())
        secteur = (request.form.get('secteur') or '').strip() or row.get('secteur') or GLOBAL_SCOPE
        description = (request.form.get('description') or '').strip()
        engine = (request.form.get('engine') or '').strip()
        sort_order_raw = (request.form.get('sort_order') or '0').strip()
        is_active = bool(request.form.get('is_active'))
        if engine not in ENGINE_REGISTRY:
            flash('Le moteur sélectionné est invalide.', 'danger')
            return render_template('activite/emargement_model_form.html', row=row, values=request.form, secteurs=secteurs, engine_registry=ENGINE_REGISTRY, global_scope=GLOBAL_SCOPE)
        if not label:
            flash('Le libellé est obligatoire.', 'danger')
            return render_template('activite/emargement_model_form.html', row=row, values=request.form, secteurs=secteurs, engine_registry=ENGINE_REGISTRY, global_scope=GLOBAL_SCOPE)
        if not _can_manage_model_for_secteur(secteur):
            return _deny_activity_access()
        try:
            sort_order = int(sort_order_raw or 0)
        except ValueError:
            flash("L'ordre doit être un entier.", "danger")
            return render_template('activite/emargement_model_form.html', row=row, values=request.form, secteurs=secteurs, engine_registry=ENGINE_REGISTRY, global_scope=GLOBAL_SCOPE)
        try:
            upsert_model({
                'key': row['key'],
                'label': label,
                'secteur': secteur,
                'description': description,
                'engine': engine,
                'sort_order': sort_order,
                'is_active': is_active,
                'is_builtin': row.get('is_builtin'),
            }, original_key=row['key'])
        except ValueError as exc:
            flash(str(exc), 'warning')
            return render_template('activite/emargement_model_form.html', row=row, values=request.form, secteurs=secteurs, engine_registry=ENGINE_REGISTRY, global_scope=GLOBAL_SCOPE)
        flash("Le modèle d'émargement a bien été mis à jour.", "success")
        return redirect(url_for('activite.emargement_models', secteur=secteur if secteur != GLOBAL_SCOPE else None))
    return render_template('activite/emargement_model_form.html', row=row, values=row, secteurs=secteurs, engine_registry=ENGINE_REGISTRY, global_scope=GLOBAL_SCOPE)


@bp.route('/modeles-emargement/<model_key>/delete', methods=['POST'])
@login_required
def emargement_model_delete(model_key: str):
    require_perm('ateliers:edit')(lambda: None)()
    row = get_model(model_key)
    if not row:
        abort(404)
    if not _can_manage_model_for_secteur(row.get('secteur')):
        return _deny_activity_access()
    if row.get('is_builtin'):
        flash('Ce modèle de base ne peut pas être supprimé. Désactivez-le ou créez un autre modèle si besoin.', 'warning')
        return redirect(url_for('activite.emargement_models', secteur=row.get('secteur') if row.get('secteur') != GLOBAL_SCOPE else None))
    delete_model(model_key)
    flash("Le modèle d'émargement a bien été supprimé.", "success")
    return redirect(url_for('activite.emargement_models', secteur=row.get('secteur') if row.get('secteur') != GLOBAL_SCOPE else None))


