"""Vue globale des impayés : qui doit quoi, tous participants confondus.

Complète le suivi par fiche (module cotisations) par un tableau de bord
transversal : la liste triée des cotisations non soldées, filtrable par
année scolaire et par secteur, avec le total du reste à recouvrer et la
relance en un clic.

Portée : les rôles à vue globale (scope:all_secteurs ou
participants:view_all) voient tout, avec un filtre secteur ; un rôle borné
ne voit que les impayés de son secteur — même logique que la liste des
participants et l'export e-mailing.
"""
from flask import render_template, request
from flask_login import current_user, login_required

from app.extensions import db
from app.main.common import bp
from app.models import Cotisation, Paiement, Participant
from app.rbac import require_perm
from app.services.cotisations import (
    annee_scolaire_courante,
    annees_scolaires_disponibles,
    libelle_annee_scolaire,
)
from app.secteurs import get_secteur_labels


def _a_vue_globale() -> bool:
    return current_user.has_perm("scope:all_secteurs") or current_user.has_perm("participants:view_all")


@bp.route("/impayes")
@login_required
@require_perm("cotisations:view")
def impayes():
    try:
        annee = int(request.args.get("annee") or annee_scolaire_courante())
    except Exception:
        annee = annee_scolaire_courante()
    secteur_filtre = (request.args.get("secteur") or "").strip() or None

    # Somme des règlements par cotisation, en une seule requête (pas de N+1).
    regle_subq = (
        db.session.query(
            Paiement.cotisation_id.label("cid"),
            db.func.coalesce(db.func.sum(Paiement.montant), 0.0).label("regle"),
        )
        .group_by(Paiement.cotisation_id)
        .subquery()
    )
    regle_col = db.func.coalesce(regle_subq.c.regle, 0.0)

    q = (
        db.session.query(Cotisation, regle_col.label("regle"))
        .outerjoin(regle_subq, regle_subq.c.cid == Cotisation.id)
        .filter(
            Cotisation.annee_scolaire == annee,
            Cotisation.montant_du - regle_col > 0.009,  # non soldé (tolérance centime)
        )
    )

    # Bornage secteur : un rôle sans vue globale ne voit que son secteur.
    if not _a_vue_globale():
        sec = (getattr(current_user, "secteur_assigne", "") or "").strip()
        if not sec:
            return render_template("impayes.html", lignes=[], total=0.0, nb=0,
                                   annee=annee, libelle_annee=libelle_annee_scolaire(annee),
                                   annees=[annee], secteurs=[], secteur_filtre=None,
                                   peut_choisir_secteur=False, sans_secteur=True)
        secteur_filtre = sec

    lignes = []
    total = 0.0
    for cot, regle in q.all():
        # Personne représentative : le participant, ou un membre du foyer.
        if cot.participant_id:
            personne = cot.participant
        else:
            personne = (Participant.query.filter(Participant.foyer_id == cot.foyer_id)
                        .order_by(Participant.nom.asc()).first())
        if personne is None:
            continue
        secteur_ligne = personne.created_secteur or "—"
        if secteur_filtre:
            # Pour un foyer, on regarde tous ses membres.
            if cot.foyer_id and not cot.participant_id:
                membres_secteurs = {
                    (m.created_secteur or "").strip()
                    for m in Participant.query.filter(Participant.foyer_id == cot.foyer_id).all()
                }
                if secteur_filtre not in membres_secteurs:
                    continue
            elif (personne.created_secteur or "").strip() != secteur_filtre:
                continue
        reste = round(float(cot.montant_du or 0) - float(regle or 0), 2)
        total += reste
        lignes.append({
            "cotisation": cot,
            "personne": personne,
            "est_foyer": bool(cot.foyer_id and not cot.participant_id),
            "secteur": secteur_ligne,
            "regle": round(float(regle or 0), 2),
            "reste": reste,
        })

    lignes.sort(key=lambda x: -x["reste"])

    return render_template(
        "impayes.html",
        lignes=lignes,
        total=round(total, 2),
        nb=len(lignes),
        annee=annee,
        libelle_annee=libelle_annee_scolaire(annee),
        annees=sorted(set(annees_scolaires_disponibles()) | {annee}, reverse=True),
        secteurs=get_secteur_labels(active_only=True),
        secteur_filtre=secteur_filtre,
        peut_choisir_secteur=_a_vue_globale(),
        sans_secteur=False,
    )
