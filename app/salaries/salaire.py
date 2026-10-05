"""Profils salariaux : coûts horaires confidentiels et calculateur.

Le salarié voit SON profil et calcule ce que coûte un volume d'heures
(utile pour monter un budget d'action). Seule la permission
``salaires:gerer`` (direction) ouvre les profils des autres ; elle ne se
donne que par quelqu'un qui l'a déjà (garde-fou de l'écran des droits).

Les charges détaillées (mutuelle, prévoyance…) sont un DÉTAIL du taux
chargé, jamais un supplément : on ne les additionne pas au taux.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.models import ProfilSalarial, ProfilSalarialCharge, Salarie
from app.rbac import require_perm
from app.services import espace_salarie as es
from app.services.audit import journaliser

from . import bp
from .commun import erreur, ma_fiche_ou_explication


def _nombre(valeur: str | None) -> Decimal | None:
    brut = (valeur or "").strip().replace(" ", "").replace(" ", "").replace(",", ".")
    if not brut:
        return None
    try:
        nombre = Decimal(brut)
    except InvalidOperation:
        raise ValueError(f"Nombre illisible : « {valeur} ».") from None
    if nombre < 0:
        raise ValueError("Les valeurs négatives ne sont pas acceptées.")
    return nombre


def _centimes(montant: Decimal) -> int:
    return int(montant.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def calculer(profil: ProfilSalarial, form) -> dict:
    """Calculateur : heures ↔ coût, et plan « heures par semaine × semaines ».

    Renvoie heures retenues, coûts brut / chargé / charges, détail des charges.
    Mode « cout_vers_heures » : combien d'heures pour un budget donné.
    """
    mode = form.get("mode") or "heures_vers_cout"
    heures = _nombre(form.get("heures"))
    hebdo = _nombre(form.get("heures_semaine"))
    semaines = _nombre(form.get("semaines"))
    budget = _nombre(form.get("budget"))
    brut, charge = profil.taux_horaire_brut_centimes or 0, profil.taux_horaire_charge_centimes or 0

    if mode == "cout_vers_heures":
        if budget is None:
            raise ValueError("Indique le budget disponible (en €).")
        if not charge:
            raise ValueError("Le coût horaire chargé n'est pas renseigné.")
        heures = (budget * 100 / charge).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    elif heures is None:
        if hebdo is None or semaines is None:
            raise ValueError("Indique un total d'heures, ou des heures par semaine et un nombre de semaines.")
        heures = hebdo * semaines
    if heures <= 0:
        raise ValueError("Le nombre d'heures doit être positif.")

    cout_brut, cout_charge = _centimes(heures * brut), _centimes(heures * charge)
    return {
        "mode": mode,
        "heures": heures,
        "semaines_equivalentes": (heures / hebdo).quantize(Decimal("0.01")) if hebdo else None,
        "cout_brut": cout_brut,
        "cout_charge": cout_charge,
        "charges": cout_charge - cout_brut,
        "detail": [{"libelle": c.libelle, "par_heure": c.centimes_par_heure,
                    "total": _centimes(heures * c.centimes_par_heure)} for c in profil.charges],
    }


def _rendu_calculateur(template: str, profil, **ctx):
    calcul = None
    if request.method == "POST" and request.form.get("action") == "calculer" and profil is not None:
        try:
            calcul = calculer(profil, request.form)
        except ValueError as exc:
            flash(str(exc), "danger")
    return render_template(template, profil=profil, calcul=calcul, saisie=request.form, es=es, **ctx)


@bp.route("/salaire", methods=["GET", "POST"])
@login_required
@require_perm("salarie:espace")
@ma_fiche_ou_explication
def mon_salaire(salarie):
    profil = ProfilSalarial.query.filter_by(salarie_id=salarie.id).first()
    return _rendu_calculateur("salaries/mon_salaire.html", profil, salarie=salarie)


@bp.route("/equipe/salaires")
@login_required
@require_perm("salaires:gerer")
def equipe_salaires():
    salaries = Salarie.query.order_by(Salarie.nom.asc(), Salarie.prenom.asc()).all()
    profils = {p.salarie_id: p for p in ProfilSalarial.query.all()}
    return render_template("salaries/equipe_salaires.html", salaries=salaries, profils=profils, es=es)


@bp.route("/equipe/salaires/<int:salarie_id>", methods=["GET", "POST"])
@login_required
@require_perm("salaires:gerer")
def profil_salarial(salarie_id: int):
    salarie = db.get_or_404(Salarie, salarie_id)
    profil = ProfilSalarial.query.filter_by(salarie_id=salarie.id).first()
    if request.method == "POST" and request.form.get("action") != "calculer":
        return _enregistrer_profil(salarie, profil)
    return _rendu_calculateur("salaries/profil_salarial.html", profil, salarie=salarie,
                              estimation=profil.cout_annuel_estime(salarie.etp) if profil else None)


def _enregistrer_profil(salarie, profil):
    url = url_for("salaries.profil_salarial", salarie_id=salarie.id)
    try:
        brut = es.parse_euros(request.form.get("taux_brut"), obligatoire=True)
        charge = es.parse_euros(request.form.get("taux_charge"), obligatoire=True)
        semaines = int(request.form.get("semaines_travaillees") or 46)
        if not 1 <= semaines <= 52:
            raise ValueError("Le nombre de semaines travaillées doit être entre 1 et 52.")
        if charge and brut and charge < brut:
            raise ValueError("Le coût chargé ne peut pas être inférieur au taux brut.")
        charges = []
        for libelle, valeur, note in zip(request.form.getlist("charge_libelle"),
                                         request.form.getlist("charge_valeur"),
                                         request.form.getlist("charge_note")):
            libelle = (libelle or "").strip()
            if libelle:
                charges.append((libelle[:120], es.parse_euros(valeur), (note or "").strip()[:500] or None))
    except ValueError as exc:
        return erreur(str(exc), url)

    if profil is None:
        profil = ProfilSalarial(salarie_id=salarie.id)
        db.session.add(profil)
    profil.taux_horaire_brut_centimes = brut
    profil.taux_horaire_charge_centimes = charge
    profil.semaines_travaillees = semaines
    profil.updated_by_user_id = current_user.id
    profil.charges = [ProfilSalarialCharge(libelle=l, centimes_par_heure=c, note=n) for l, c, n in charges]
    db.session.flush()

    reporte = None
    if request.form.get("reporter_masse") == "1":
        reporte = profil.cout_annuel_estime(salarie.etp)
        if reporte is not None:
            salarie.salaire_brut_charge = reporte
    db.session.commit()
    # Le journal ne contient jamais les montants : il est lisible par d'autres rôles.
    journaliser("rh.profil_salarial", cible=f"salarie#{salarie.id}",
                details={"charges": len(charges), "masse_salariale_mise_a_jour": reporte is not None})
    message = "Profil salarial enregistré."
    if reporte is not None:
        message += f" Coût annuel estimé reporté dans la fiche RH : {es.format_euros(int(reporte * 100))}."
    flash(message, "success")
    return redirect(url)
