"""Comparaison annuelle (N vs N-1) et inter-secteurs — pilotage direction.

Agrège, par exercice et par secteur, les grands indicateurs financiers
(attribué / reçu / engagé / reste) et d'activité (séances / présences), puis
calcule les écarts d'une année sur l'autre. Lecture seule.
"""
from io import BytesIO
from datetime import date

from flask import render_template, request, Response
from flask_login import login_required, current_user

from sqlalchemy import func

from app.rbac import require_perm, can
from app.extensions import db
from app.models import Subvention, SessionActivite, PresenceActivite

from app.main.common import bp


METRIQUES = [
    ("attribue", "Attribué (€)"),
    ("recu", "Reçu (€)"),
    ("engage", "Engagé (€)"),
    ("reste", "Reste (€)"),
    ("sessions", "Séances"),
    ("presences", "Présences"),
]


def _vide():
    return {k: 0.0 for k, _ in METRIQUES}


def _scope_secteur():
    """Secteur imposé si l'utilisateur n'a pas la portée globale, sinon None."""
    if can("scope:all_secteurs") or can("stats:view_all"):
        return None
    return current_user.secteur_assigne


def _finances_par_secteur(year, scope_secteur):
    q = Subvention.query.filter_by(est_archive=False, annee_exercice=year)
    if scope_secteur:
        q = q.filter(Subvention.secteur == scope_secteur)
    out = {}
    for s in q.all():
        d = out.setdefault(s.secteur, _vide())
        d["attribue"] += float(s.montant_attribue or 0)
        d["recu"] += float(s.montant_recu or 0)
        d["engage"] += float(s.total_engage or 0)
        d["reste"] += float(s.total_reste or 0)
    return out


def _activite_par_secteur(year, scope_secteur):
    d1, d2 = date(year, 1, 1), date(year, 12, 31)
    eff = func.coalesce(SessionActivite.date_session, SessionActivite.rdv_date)

    sess_q = (db.session.query(SessionActivite.secteur, func.count(SessionActivite.id))
              .filter(SessionActivite.is_deleted.is_(False))
              .filter(eff >= d1, eff <= d2))
    pres_q = (db.session.query(SessionActivite.secteur, func.count(PresenceActivite.id))
              .join(PresenceActivite, PresenceActivite.session_id == SessionActivite.id)
              .filter(SessionActivite.is_deleted.is_(False))
              .filter(eff >= d1, eff <= d2))
    if scope_secteur:
        sess_q = sess_q.filter(SessionActivite.secteur == scope_secteur)
        pres_q = pres_q.filter(SessionActivite.secteur == scope_secteur)

    out = {}
    for sec, n in sess_q.group_by(SessionActivite.secteur).all():
        out.setdefault(sec, {"sessions": 0.0, "presences": 0.0})["sessions"] = float(n or 0)
    for sec, n in pres_q.group_by(SessionActivite.secteur).all():
        out.setdefault(sec, {"sessions": 0.0, "presences": 0.0})["presences"] = float(n or 0)
    return out


def _agrege_annee(year, scope_secteur):
    """Retourne {secteur: {metrique: valeur}} pour une année."""
    fin = _finances_par_secteur(year, scope_secteur)
    act = _activite_par_secteur(year, scope_secteur)
    secteurs = set(fin) | set(act)
    out = {}
    for sec in secteurs:
        d = _vide()
        d.update(fin.get(sec, {}))
        a = act.get(sec, {})
        d["sessions"] = a.get("sessions", 0.0)
        d["presences"] = a.get("presences", 0.0)
        out[sec] = {k: round(v, 2) for k, v in d.items()}
    return out


def _totaux(agrege):
    t = _vide()
    for d in agrege.values():
        for k, _ in METRIQUES:
            t[k] += d.get(k, 0.0)
    return {k: round(v, 2) for k, v in t.items()}


def _delta(n, n_1):
    out = {}
    for k, _ in METRIQUES:
        a, b = n.get(k, 0.0), n_1.get(k, 0.0)
        out[k] = {"abs": round(a - b, 2),
                  "pct": round((a - b) / b * 100, 1) if b else None}
    return out


def compute_comparaison(year_n, scope_secteur):
    year_n_1 = year_n - 1
    agr_n = _agrege_annee(year_n, scope_secteur)
    agr_n1 = _agrege_annee(year_n_1, scope_secteur)
    tot_n, tot_n1 = _totaux(agr_n), _totaux(agr_n1)

    secteurs = sorted(set(agr_n) | set(agr_n1))
    lignes_secteur = []
    for sec in secteurs:
        n, n1 = agr_n.get(sec, _vide()), agr_n1.get(sec, _vide())
        lignes_secteur.append({"secteur": sec, "n": n, "n_1": n1, "delta": _delta(n, n1)})

    # Classement inter-secteurs sur l'année N (taux de consommation = engagé/reçu).
    classement = []
    for sec in agr_n:
        d = agr_n[sec]
        taux = round(d["engage"] / d["recu"] * 100, 1) if d["recu"] else None
        classement.append({"secteur": sec, **d, "taux_conso": taux})
    classement.sort(key=lambda r: (-(r["recu"] or 0), r["secteur"]))

    return {
        "n": year_n, "n_1": year_n_1,
        "totaux": {"n": tot_n, "n_1": tot_n1, "delta": _delta(tot_n, tot_n1)},
        "lignes_secteur": lignes_secteur,
        "classement": classement,
    }


def _annees_disponibles(scope_secteur):
    q = db.session.query(Subvention.annee_exercice).filter_by(est_archive=False)
    if scope_secteur:
        q = q.filter(Subvention.secteur == scope_secteur)
    return sorted({y for (y,) in q.distinct().all() if y}, reverse=True)


def _selected_year(annees):
    raw = (request.args.get("annee") or "").strip()
    if raw:
        try:
            return int(raw)
        except ValueError:
            pass
    return annees[0] if annees else date.today().year


@bp.route("/comparaison")
@login_required
@require_perm("stats:view")
def comparaison():
    scope_secteur = _scope_secteur()
    annees = _annees_disponibles(scope_secteur)
    year_n = _selected_year(annees)
    data = compute_comparaison(year_n, scope_secteur)
    return render_template(
        "comparaison.html",
        data=data, metriques=METRIQUES, annees=annees, year_n=year_n,
        portee_globale=(scope_secteur is None),
    )


@bp.route("/comparaison/export.xlsx")
@login_required
@require_perm("stats:view")
def comparaison_export_xlsx():
    from openpyxl import Workbook

    scope_secteur = _scope_secteur()
    annees = _annees_disponibles(scope_secteur)
    year_n = _selected_year(annees)
    data = compute_comparaison(year_n, scope_secteur)

    wb = Workbook()
    ws = wb.active
    ws.title = f"{data['n']} vs {data['n_1']}"
    ws.append(["Secteur", "Indicateur", str(data["n"]), str(data["n_1"]), "Écart", "Écart %"])
    for lig in data["lignes_secteur"]:
        for key, label in METRIQUES:
            d = lig["delta"][key]
            ws.append([lig["secteur"], label, lig["n"][key], lig["n_1"][key], d["abs"],
                       d["pct"] if d["pct"] is not None else ""])
    ws.append([])
    for key, label in METRIQUES:
        d = data["totaux"]["delta"][key]
        ws.append(["TOTAL", label, data["totaux"]["n"][key], data["totaux"]["n_1"][key], d["abs"],
                   d["pct"] if d["pct"] is not None else ""])

    ws2 = wb.create_sheet(f"Classement {data['n']}")
    ws2.append(["Secteur", "Reçu (€)", "Engagé (€)", "Reste (€)", "Séances", "Présences", "Taux conso (%)"])
    for r in data["classement"]:
        ws2.append([r["secteur"], r["recu"], r["engage"], r["reste"], r["sessions"], r["presences"],
                    r["taux_conso"] if r["taux_conso"] is not None else ""])

    for w in (ws, ws2):
        for col in w.columns:
            length = max((len(str(c.value)) if c.value is not None else 0) for c in col)
            w.column_dimensions[col[0].column_letter].width = max(12, min(length + 2, 36))

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return Response(
        buf.getvalue(),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=comparaison_{data['n']}_{data['n_1']}.xlsx"},
    )
