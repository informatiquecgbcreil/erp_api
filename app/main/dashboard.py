from datetime import date


from flask import (
    render_template, request, redirect, url_for, flash, session
)
from flask_login import login_required, current_user
from app.rbac import require_perm

from app.extensions import db
from app.services.dashboard_service import build_dashboard_context
from app.services.dashboard_customization import (
    available_quick_actions,
    available_widgets,
    load_dashboard_pref,
    reset_dashboard_pref,
    save_dashboard_pref,
)
from app.services.poste_travail import build_poste_travail


from app.main.common import bp

@bp.post("/ui-mode")
@login_required
def set_ui_mode():
    mode = (request.form.get("mode") or "simple").strip().lower()
    if mode not in {"simple", "expert"}:
        mode = "simple"
    session["ui_mode"] = mode
    try:
        save_dashboard_pref(current_user, ui_mode=mode, customized=True)
    except Exception:
        db.session.rollback()
    flash("Mode simplifié activé." if mode == "simple" else "Mode expert activé.", "success")
    next_url = (request.form.get("next") or request.referrer or url_for("main.dashboard")).strip()
    return redirect(next_url)


@bp.post("/ui-device-mode")
@login_required
def set_ui_device_mode():
    mode = (request.form.get("device_mode") or "desktop").strip().lower()
    if mode not in {"desktop", "mobile", "tablet", "kiosk"}:
        mode = "desktop"
    session["ui_device_mode"] = mode
    flash(f"Mode d’écran activé : {mode}.", "success")
    next_url = (request.form.get("next") or request.referrer or url_for("main.dashboard")).strip()
    resp = redirect(next_url)
    resp.set_cookie("ui_device_mode", mode, max_age=60 * 60 * 24 * 365, samesite="Lax")
    return resp



@bp.route("/dashboard/personnaliser", methods=["GET", "POST"])
@login_required
@require_perm("dashboard:view")
def dashboard_customize():
    if request.method == "POST":
        quick_actions = []
        for idx in range(1, 7):
            key = (request.form.get(f"quick_action_{idx}") or "").strip()
            if key and key not in quick_actions:
                quick_actions.append(key)

        widgets = []
        for key in ["alerts", "charts", "recents"]:
            try:
                position = int(request.form.get(f"widget_{key}_position") or 99)
            except Exception:
                position = 99
            widgets.append({
                "key": key,
                "visible": request.form.get(f"widget_{key}_visible") == "1",
                "position": position,
            })

        try:
            save_dashboard_pref(
                current_user,
                quick_actions=quick_actions,
                widgets=widgets,
                ui_mode=(request.form.get("ui_mode") or None),
                customized=True,
            )
            session["ui_mode"] = (request.form.get("ui_mode") or session.get("ui_mode") or "simple").strip().lower()
            flash("L’accueil a bien été personnalisé.", "success")
        except Exception:
            db.session.rollback()
            flash("Impossible d’enregistrer la personnalisation de l’accueil.", "danger")
        return redirect(url_for("main.dashboard_customize"))

    prefs = load_dashboard_pref(current_user)
    quick_actions_catalog = available_quick_actions(current_user)
    widgets_catalog = available_widgets()
    widget_prefs = {item["key"]: item for item in prefs["widgets"]}
    quick_slots = prefs["quick_actions"][:6]
    while len(quick_slots) < 6:
        quick_slots.append("")
    return render_template(
        "dashboard_customize.html",
        prefs=prefs,
        quick_actions_catalog=quick_actions_catalog,
        widgets_catalog=widgets_catalog,
        widget_prefs=widget_prefs,
        quick_slots=quick_slots,
    )


@bp.post("/dashboard/reinitialiser")
@login_required
@require_perm("dashboard:view")
def dashboard_reset():
    try:
        prefs = reset_dashboard_pref(current_user)
        session["ui_mode"] = prefs.get("ui_mode") or "simple"
        flash("L’accueil par défaut a bien été rétabli.", "success")
    except Exception:
        db.session.rollback()
        flash("Impossible de réinitialiser l’accueil.", "danger")
    return redirect(request.form.get("next") or url_for("main.dashboard"))


# --------- Permissions ---------

# --------- Dashboard ---------
@bp.route("/dashboard")
@login_required
@require_perm("dashboard:view")
def dashboard():
    raw_period = (request.args.get("period") or "").strip().lower()
    try:
        days = int(request.args.get("days") or 90)
    except Exception:
        days = 90
    if raw_period in {"30", "90", "365"}:
        try:
            days = int(raw_period)
        except Exception:
            days = 90
    try:
        budget_year = int(request.args.get("budget_year") or date.today().year)
    except Exception:
        budget_year = date.today().year

    ctx = build_dashboard_context(
        current_user,
        days=days,
        budget_year=budget_year,
        period_key=raw_period or str(days),
    )
    prefs = load_dashboard_pref(current_user)
    ctx["dashboard_prefs"] = prefs
    ctx["dashboard_widgets"] = prefs.get("widgets") or []
    try:
        ctx["poste_travail"] = build_poste_travail(current_user)
    except Exception:
        # L'accueil doit s'afficher même si le poste de travail échoue.
        ctx["poste_travail"] = None
    return render_template("dashboard.html", **ctx)



