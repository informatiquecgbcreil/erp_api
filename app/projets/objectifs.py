"""Objectifs sectoriels : référentiel par secteur, rattachement, consolidation.

Chaque secteur définit ses propres objectifs (ex. OS1/OS2/OS3). Les actions
s'y rattachent avec une contribution. On peut alors :
- pré-remplir la section « Objectifs spécifiques » de la fiche action ;
- consolider, par secteur, ce que chaque objectif reçoit comme actions et
  comme indicateurs réels (vue projet social).
"""
from __future__ import annotations

from flask_login import current_user

from app.extensions import db
from app.models import ActionObjectifSectoriel, ObjectifSectoriel, Projet, ProjetAction
from app.rbac import can


def objectifs_du_secteur(secteur: str, actif_only: bool = True) -> list[ObjectifSectoriel]:
    q = ObjectifSectoriel.query.filter_by(secteur=secteur)
    if actif_only:
        q = q.filter_by(actif=True)
    return q.order_by(ObjectifSectoriel.ordre.asc(), ObjectifSectoriel.code.asc()).all()


def peut_gerer_secteur(secteur: str | None) -> bool:
    """Direction (portée globale) gère tous les secteurs ; un responsable, le sien."""
    if not can("projets:edit"):
        return False
    if can("scope:all_secteurs"):
        return True
    propre = (getattr(current_user, "secteur_assigne", None) or "").strip()
    return bool(secteur) and secteur == propre


def secteurs_gerables() -> list[str]:
    from app.secteurs import get_secteur_labels

    tous = get_secteur_labels(active_only=True)
    if can("scope:all_secteurs"):
        return tous
    propre = (getattr(current_user, "secteur_assigne", None) or "").strip()
    return [s for s in tous if s == propre]


def contributions_de_action(action: ProjetAction) -> list[dict]:
    """Liste ordonnée {objectif, contribution} rattachée à une action."""
    liens = sorted(
        action.objectif_links or [],
        key=lambda l: (getattr(l.objectif, "ordre", 0), getattr(l.objectif, "code", "")),
    )
    return [{"objectif": l.objectif, "contribution": (l.contribution or "").strip()} for l in liens if l.objectif]


def enregistrer_contributions(action: ProjetAction, form) -> None:
    """Met à jour les rattachements OS d'une action depuis le formulaire.

    Champs attendus : ``os_<id>`` (case cochée) et ``contrib_<id>`` (texte).
    """
    objectifs = objectifs_du_secteur(action_secteur(action), actif_only=True)
    existants = {l.objectif_id: l for l in (action.objectif_links or [])}

    for obj in objectifs:
        coche = form.get(f"os_{obj.id}") in {"1", "true", "on", "yes"}
        contribution = (form.get(f"contrib_{obj.id}") or "").strip()
        lien = existants.get(obj.id)
        if coche:
            if lien is None:
                db.session.add(ActionObjectifSectoriel(
                    action_id=action.id, objectif_id=obj.id, contribution=contribution or None))
            else:
                lien.contribution = contribution or None
        elif lien is not None:
            db.session.delete(lien)


def action_secteur(action: ProjetAction) -> str:
    projet = db.session.get(Projet, action.projet_id)
    return projet.secteur if projet else ""


def synthese_secteur(secteur: str, annee: int) -> list[dict]:
    """Pour chaque objectif du secteur : actions rattachées + indicateurs agrégés."""
    from app.projets.helpers_projet import _compute_action_stats

    objectifs = objectifs_du_secteur(secteur, actif_only=False)
    projets = {p.id: p for p in Projet.query.filter_by(secteur=secteur).all()}

    resultat = []
    for obj in objectifs:
        liens = ActionObjectifSectoriel.query.filter_by(objectif_id=obj.id).all()
        lignes = []
        tot_sessions = tot_uniques = tot_presences = 0
        for lien in liens:
            action = db.session.get(ProjetAction, lien.action_id)
            if not action or action.projet_id not in projets:
                continue
            stats = _compute_action_stats(projets[action.projet_id], action, annee)
            kpi = stats.get("kpi", {})
            tot_sessions += int(kpi.get("sessions", 0) or 0)
            tot_uniques += int(kpi.get("uniques", 0) or 0)
            tot_presences += int(kpi.get("presences", 0) or 0)
            lignes.append({
                "action": action,
                "projet": projets[action.projet_id],
                "contribution": (lien.contribution or "").strip(),
                "kpi": kpi,
            })
        resultat.append({
            "objectif": obj,
            "actions": lignes,
            "totaux": {"sessions": tot_sessions, "uniques": tot_uniques, "presences": tot_presences},
        })
    return resultat
