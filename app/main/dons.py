"""Dons & reçus fiscaux (CERFA 11580) — registre numéroté et reçu imprimable.

Un reçu émis ne se supprime pas : il s'annule (le numéro reste au registre).
Les coordonnées de l'organisme sont figées sur chaque don pour que le reçu
reste reproductible à l'identique.
"""
from datetime import date
from io import BytesIO

from flask import render_template, request, redirect, url_for, flash, abort, current_app, Response
from flask_login import login_required, current_user

from app.rbac import require_perm, can
from app.extensions import db
from app.models import Don
from app.services.dons import prochain_numero, montant_en_lettres
from app.services.audit import journaliser

from app.main.common import bp


FORMES_DON = {"numeraire": "Numéraire", "nature": "En nature"}
MODES_VERSEMENT = {"especes": "Espèces", "cheque": "Chèque", "virement": "Virement", "autre": "Autre"}
TYPES_DONATEUR = {"particulier": "Particulier (art. 200 CGI)", "entreprise": "Entreprise (art. 238 bis CGI)"}


def _dernier_organisme() -> tuple[str, str]:
    dernier = Don.query.order_by(Don.id.desc()).first()
    nom = (dernier.organisme_nom if dernier else None) or current_app.config.get("APP_NAME") or ""
    adresse = (dernier.organisme_adresse if dernier else None) or ""
    return nom, adresse


@bp.route("/dons")
@login_required
@require_perm("dons:view")
def dons_registre():
    annees = sorted({a for (a,) in db.session.query(Don.annee).distinct().all() if a}, reverse=True)
    try:
        annee = int(request.args.get("annee") or (annees[0] if annees else date.today().year))
    except Exception:
        annee = annees[0] if annees else date.today().year

    dons = (Don.query.filter_by(annee=annee)
            .order_by(Don.numero.asc()).all())
    total_valide = round(sum(d.montant for d in dons if not d.est_annule), 2)
    org_nom, org_adresse = _dernier_organisme()
    return render_template(
        "dons_registre.html",
        dons=dons, annee=annee, annees=annees,
        total_valide=total_valide,
        nb_valides=sum(1 for d in dons if not d.est_annule),
        formes=FORMES_DON, modes=MODES_VERSEMENT, types_donateur=TYPES_DONATEUR,
        org_nom=org_nom, org_adresse=org_adresse,
        today=date.today(),
        can_edit=can("dons:edit"),
    )


@bp.route("/dons/nouveau", methods=["POST"])
@login_required
@require_perm("dons:edit")
def don_create():
    nom = (request.form.get("donateur_nom") or "").strip()
    if not nom:
        flash("Le nom du donateur est obligatoire.", "danger")
        return redirect(url_for("main.dons_registre"))

    try:
        montant = round(float(str(request.form.get("montant") or "0").replace(",", ".").replace(" ", "")), 2)
    except Exception:
        montant = 0.0
    if montant <= 0:
        flash("Le montant du don doit être supérieur à 0.", "danger")
        return redirect(url_for("main.dons_registre"))

    try:
        date_don = date.fromisoformat((request.form.get("date_don") or "").strip())
    except Exception:
        date_don = date.today()

    forme = (request.form.get("forme_don") or "numeraire").strip()
    if forme not in FORMES_DON:
        forme = "numeraire"
    mode = (request.form.get("mode_versement") or "virement").strip()
    if mode not in MODES_VERSEMENT:
        mode = "virement"
    type_donateur = (request.form.get("type_donateur") or "particulier").strip()
    if type_donateur not in TYPES_DONATEUR:
        type_donateur = "particulier"

    annee = date_don.year
    don = Don(
        numero=prochain_numero(annee),
        annee=annee,
        type_donateur=type_donateur,
        donateur_civilite=(request.form.get("donateur_civilite") or "").strip() or None,
        donateur_nom=nom,
        donateur_prenom=(request.form.get("donateur_prenom") or "").strip() or None,
        donateur_adresse=(request.form.get("donateur_adresse") or "").strip() or None,
        donateur_cp=(request.form.get("donateur_cp") or "").strip() or None,
        donateur_ville=(request.form.get("donateur_ville") or "").strip() or None,
        donateur_email=(request.form.get("donateur_email") or "").strip() or None,
        montant=montant,
        date_don=date_don,
        forme_don=forme,
        mode_versement=mode,
        nature_description=(request.form.get("nature_description") or "").strip() or None,
        organisme_nom=(request.form.get("organisme_nom") or "").strip() or None,
        organisme_adresse=(request.form.get("organisme_adresse") or "").strip() or None,
        created_by_user_id=getattr(current_user, "id", None),
    )
    db.session.add(don)
    db.session.commit()
    journaliser("don.create", cible=f"don#{don.id}",
                details={"numero": don.numero, "montant": don.montant, "donateur": don.donateur_nom})
    flash(f"Don enregistré — reçu n° {don.numero}.", "success")
    return redirect(url_for("main.don_recu", don_id=don.id))


@bp.route("/dons/<int:don_id>/recu")
@login_required
@require_perm("dons:view")
def don_recu(don_id: int):
    don = db.get_or_404(Don, don_id)
    return render_template(
        "don_recu.html",
        don=don,
        montant_lettres=montant_en_lettres(don.montant),
        formes=FORMES_DON, modes=MODES_VERSEMENT,
        organisme_nom=don.organisme_nom or current_app.config.get("APP_NAME") or "",
        organisme_adresse=don.organisme_adresse or "",
        today=date.today(),
    )


@bp.route("/dons/<int:don_id>/annuler", methods=["POST"])
@login_required
@require_perm("dons:edit")
def don_annuler(don_id: int):
    don = db.get_or_404(Don, don_id)
    don.est_annule = True
    don.annulation_motif = (request.form.get("motif") or "").strip() or None
    db.session.commit()
    journaliser("don.annule", cible=f"don#{don.id}", details={"numero": don.numero, "motif": don.annulation_motif})
    flash(f"Reçu n° {don.numero} annulé (le numéro reste au registre).", "warning")
    return redirect(url_for("main.dons_registre", annee=don.annee))


@bp.route("/dons/export.xlsx")
@login_required
@require_perm("dons:view")
def dons_export_xlsx():
    from openpyxl import Workbook

    try:
        annee = int(request.args.get("annee") or date.today().year)
    except Exception:
        annee = date.today().year
    dons = Don.query.filter_by(annee=annee).order_by(Don.numero.asc()).all()

    wb = Workbook()
    ws = wb.active
    ws.title = f"Registre {annee}"
    ws.append(["N° reçu", "Date du don", "Donateur", "Type", "Adresse", "Montant (€)",
               "Forme", "Mode de versement", "Statut"])
    for d in dons:
        adresse = " ".join(x for x in [d.donateur_adresse, d.donateur_cp, d.donateur_ville] if x)
        ws.append([
            d.numero,
            d.date_don.strftime("%d/%m/%Y") if d.date_don else "",
            " ".join(x for x in [d.donateur_civilite, d.donateur_prenom, d.donateur_nom] if x),
            TYPES_DONATEUR.get(d.type_donateur, d.type_donateur),
            adresse,
            round(d.montant, 2),
            FORMES_DON.get(d.forme_don, d.forme_don),
            MODES_VERSEMENT.get(d.mode_versement, d.mode_versement),
            "ANNULÉ" if d.est_annule else "Valide",
        ])
    ws.append([])
    ws.append(["TOTAL (reçus valides)", "", "", "", "",
               round(sum(d.montant for d in dons if not d.est_annule), 2)])
    for col, width in zip("ABCDEFGHI", (12, 12, 28, 24, 34, 12, 12, 16, 10)):
        ws.column_dimensions[col].width = width

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return Response(
        buf.getvalue(),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=registre_dons_{annee}.xlsx"},
    )
