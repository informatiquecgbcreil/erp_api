"""Pilotage de trésorerie & échéances consolidées (vue direction).

Agrège, pour toutes les subventions visibles (respect du périmètre RBAC) :
- un plan de trésorerie prévisionnel : encaissements attendus par mois
  (montant attribué non encore reçu, positionné sur ``date_versement_prevu``),
- un tableau consolidé des échéances (retards / à venir), tous secteurs,
- le taux de réalisation du financement par financeur.
"""
from io import BytesIO
from datetime import date

from flask import render_template, request, Response
from flask_login import login_required, current_user

from app.rbac import require_perm, can
from app.extensions import db
from app.models import Subvention, SUBVENTION_STATUTS_DICT

from app.main.common import bp
from app.main.subventions import alertes_echeances


MOIS_FR = ["", "janvier", "février", "mars", "avril", "mai", "juin",
           "juillet", "août", "septembre", "octobre", "novembre", "décembre"]


def _subventions_en_scope(annee=None):
    q = Subvention.query.filter_by(est_archive=False)
    if not can("scope:all_secteurs"):
        q = q.filter(Subvention.secteur == current_user.secteur_assigne)
    if annee is not None:
        q = q.filter(Subvention.annee_exercice == annee)
    return q.order_by(Subvention.date_versement_prevu.asc(), Subvention.nom.asc()).all()


def _reste_a_percevoir(s) -> float:
    """Montant encore attendu = attribué − reçu (jamais négatif)."""
    return round(max(0.0, float(s.montant_attribue or 0) - float(s.montant_recu or 0)), 2)


def plan_tresorerie(subs, today=None) -> dict:
    """Encaissements attendus, regroupés par mois puis par financeur."""
    if today is None:
        today = date.today()
    mois = {}
    sans_date = []
    for s in subs:
        if (s.statut_cycle or "sollicitee") in ("soldee", "refusee"):
            continue
        reste = _reste_a_percevoir(s)
        if reste <= 0:
            continue
        d = s.date_versement_prevu
        if not d:
            sans_date.append({"sub": s, "montant": reste})
            continue
        cle = (d.year, d.month)
        bloc = mois.setdefault(cle, {"annee": d.year, "mois": d.month,
                                     "label": f"{MOIS_FR[d.month]} {d.year}",
                                     "total": 0.0, "en_retard": False, "financeurs": {}})
        bloc["total"] = round(bloc["total"] + reste, 2)
        if d < today.replace(day=1):
            bloc["en_retard"] = True
        fin = (s.financeur or "").strip() or "— Non renseigné —"
        bloc["financeurs"][fin] = round(bloc["financeurs"].get(fin, 0.0) + reste, 2)

    lignes = [mois[k] for k in sorted(mois.keys())]
    total_attendu = round(sum(b["total"] for b in lignes) + sum(x["montant"] for x in sans_date), 2)
    total_retard = round(sum(b["total"] for b in lignes if b["en_retard"]), 2)
    return {"lignes": lignes, "sans_date": sans_date,
            "total_attendu": total_attendu, "total_retard": total_retard}


def taux_par_financeur(subs) -> list[dict]:
    groupes = {}
    for s in subs:
        if (s.statut_cycle or "sollicitee") == "refusee":
            continue
        fin = (s.financeur or "").strip() or "— Non renseigné —"
        g = groupes.setdefault(fin, {"financeur": fin, "attribue": 0.0, "recu": 0.0, "nb": 0})
        g["attribue"] = round(g["attribue"] + float(s.montant_attribue or 0), 2)
        g["recu"] = round(g["recu"] + float(s.montant_recu or 0), 2)
        g["nb"] += 1
    out = []
    for g in groupes.values():
        g["taux"] = round(g["recu"] / g["attribue"] * 100, 1) if g["attribue"] else None
        g["reste"] = round(g["attribue"] - g["recu"], 2)
        out.append(g)
    out.sort(key=lambda g: (-(g["attribue"] or 0), g["financeur"].lower()))
    return out


def _annees_disponibles(subs):
    return sorted({s.annee_exercice for s in subs if s.annee_exercice}, reverse=True)


@bp.route("/tresorerie")
@login_required
@require_perm("subventions:view")
def tresorerie():
    annee_raw = (request.args.get("annee") or "").strip()
    annee = None
    if annee_raw:
        try:
            annee = int(annee_raw)
        except ValueError:
            annee = None

    # Toutes les subventions du périmètre (pour la liste d'années), puis filtrées.
    toutes = _subventions_en_scope()
    subs = [s for s in toutes if annee is None or s.annee_exercice == annee]

    plan = plan_tresorerie(subs)
    alertes = alertes_echeances(subs)
    financeurs = taux_par_financeur(subs)
    totaux = {
        "attribue": round(sum(float(s.montant_attribue or 0) for s in subs), 2),
        "recu": round(sum(float(s.montant_recu or 0) for s in subs), 2),
    }
    totaux["reste"] = round(totaux["attribue"] - totaux["recu"], 2)
    totaux["taux"] = round(totaux["recu"] / totaux["attribue"] * 100, 1) if totaux["attribue"] else None

    return render_template(
        "tresorerie.html",
        plan=plan, alertes=alertes, financeurs=financeurs, totaux=totaux,
        annee=annee, annees=_annees_disponibles(toutes),
        portee_globale=can("scope:all_secteurs"),
    )


@bp.route("/tresorerie/export.xlsx")
@login_required
@require_perm("subventions:view")
def tresorerie_export_xlsx():
    from openpyxl import Workbook

    annee_raw = (request.args.get("annee") or "").strip()
    annee = None
    if annee_raw:
        try:
            annee = int(annee_raw)
        except ValueError:
            annee = None

    subs = _subventions_en_scope(annee)
    plan = plan_tresorerie(subs)
    financeurs = taux_par_financeur(subs)

    wb = Workbook()
    ws = wb.active
    ws.title = "Trésorerie prévisionnelle"
    ws.append(["Mois", "Encaissement attendu (€)", "En retard", "Détail par financeur"])
    for b in plan["lignes"]:
        detail = " · ".join(f"{f} {m:.0f}€" for f, m in sorted(b["financeurs"].items(), key=lambda kv: -kv[1]))
        ws.append([b["label"], b["total"], "Oui" if b["en_retard"] else "", detail])
    if plan["sans_date"]:
        ws.append([])
        ws.append(["Sans date de versement prévue :", "", "", ""])
        for x in plan["sans_date"]:
            ws.append([x["sub"].nom, x["montant"], "", x["sub"].financeur or ""])
    ws.append([])
    ws.append(["TOTAL attendu", plan["total_attendu"], f"dont en retard : {plan['total_retard']:.0f}€", ""])

    ws2 = wb.create_sheet("Taux par financeur")
    ws2.append(["Financeur", "Nb dossiers", "Attribué (€)", "Reçu (€)", "Reste (€)", "Taux (%)"])
    for g in financeurs:
        ws2.append([g["financeur"], g["nb"], g["attribue"], g["recu"], g["reste"],
                    g["taux"] if g["taux"] is not None else ""])

    for w in (ws, ws2):
        for col in w.columns:
            length = max((len(str(c.value)) if c.value is not None else 0) for c in col)
            w.column_dimensions[col[0].column_letter].width = max(12, min(length + 2, 40))

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    suffixe = f"_{annee}" if annee else ""
    return Response(
        buf.getvalue(),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=tresorerie{suffixe}.xlsx"},
    )
