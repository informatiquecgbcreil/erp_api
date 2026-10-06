"""Exports RH : classeur Excel global, relevé individuel, état de frais km.

Droits :
- classeur complet (heures, récupérations, frais) : ``rh:view`` (direction) ;
- frais km seuls : ``frais_km:suivi`` (comptabilité) ;
- relevé individuel : la personne elle-même, ``rh:view`` ou ``recup:decider`` ;
- état de frais km : la personne elle-même, ``frais_km:suivi`` ou ``rh:view``.
Aucun export ne contient de coût horaire ni de salaire.
"""
from __future__ import annotations

from flask import Response, abort, render_template, request
from flask_login import current_user, login_required

from app.extensions import db
from app.models import Salarie
from app.rbac import can, require_perm
from app.services import espace_salarie as es
from app.services import exports_rh as ex
from app.services import frais_km as fk
from app.services.audit import journaliser

from . import bp


def _droits_export() -> tuple[bool, bool]:
    """(heures et récupérations, frais km) visibles par la personne connectée."""
    return can("rh:view"), can("rh:view") or can("frais_km:suivi")


def _filtres_depuis_requete() -> ex.Filtres:
    du, au, _ = ex.periode_demandee(request.args)
    voir_recup, voir_km = _droits_export()
    contenu = request.args.getlist("contenu") or ["recup", "km"]
    return ex.Filtres(
        du=du, au=au,
        secteurs=[s for s in request.args.getlist("secteur") if s.strip()],
        salarie_ids=[int(i) for i in request.args.getlist("salarie_id") if str(i).isdigit()],
        recup=voir_recup and "recup" in contenu,
        km=voir_km and "km" in contenu,
        onglet_par_salarie=request.args.get("par_salarie") == "1",
    )


@bp.route("/equipe/exports")
@login_required
@require_perm("salarie:espace")
def exports_rh():
    voir_recup, voir_km = _droits_export()
    if not (voir_recup or voir_km):
        abort(403)
    f = _filtres_depuis_requete()
    _, _, mois = ex.periode_demandee(request.args)
    secteurs = sorted({s for (s,) in db.session.query(Salarie.secteur).distinct().all() if s})
    return render_template(
        "salaries/exports.html", f=f, mois=mois, donnees=ex.synthese(f), secteurs=secteurs,
        salaries=Salarie.query.order_by(Salarie.nom, Salarie.prenom).all(),
        voir_recup=voir_recup, voir_km=voir_km, args=request.args, es=es,
    )


@bp.route("/equipe/exports/classeur.xlsx")
@login_required
@require_perm("salarie:espace")
def exports_rh_classeur():
    voir_recup, voir_km = _droits_export()
    if not (voir_recup or voir_km):
        abort(403)
    f = _filtres_depuis_requete()
    if not (f.recup or f.km):
        abort(400)
    contenu = ex.classeur(f)
    journaliser("rh.export_classeur", details={"du": f.du.isoformat(), "au": f.au.isoformat(),
                                               "secteurs": f.secteurs, "salaries": len(f.salarie_ids),
                                               "recup": f.recup, "km": f.km})
    nom = f"export_rh_{f.du.isoformat()}_{f.au.isoformat()}.xlsx"
    return Response(contenu, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                     headers={"Content-Disposition": f"attachment; filename={nom}",
                              "Cache-Control": "private, no-store"})


def _salarie_autorise(salarie_id: int, *permissions: str) -> Salarie:
    salarie = db.get_or_404(Salarie, salarie_id)
    if salarie.user_id != current_user.id and not any(can(p) for p in permissions):
        abort(403)
    return salarie


@bp.route("/releve/<int:salarie_id>")
@login_required
@require_perm("salarie:espace")
def releve(salarie_id: int):
    salarie = _salarie_autorise(salarie_id, "rh:view", "recup:decider")
    du, au, mois = ex.periode_demandee(request.args)
    if salarie.user_id != current_user.id:
        journaliser("rh.releve_consulte", cible=f"salarie#{salarie.id}", details={"du": du.isoformat(), "au": au.isoformat()})
    return render_template("salaries/releve.html", r=ex.releve(salarie, du, au), mois=mois, es=es, fk=fk)


@bp.route("/frais-km/etat/<int:salarie_id>")
@login_required
@require_perm("salarie:espace")
def etat_frais_km(salarie_id: int):
    salarie = _salarie_autorise(salarie_id, "frais_km:suivi", "rh:view")
    du, au, mois = ex.periode_demandee(request.args)
    notes = ex.frais_km(ex.Filtres(du=du, au=au, salarie_ids=[salarie.id]))
    return render_template("salaries/etat_frais_km.html", salarie=salarie, notes=notes, du=du, au=au, mois=mois,
                           total=sum(n.montant_centimes for n in notes), km=sum(n.distance_km for n in notes),
                           es=es, fk=fk)
