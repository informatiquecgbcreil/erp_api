"""Bénévolat — statistiques annuelles et valorisation.

Alimente la page Ressources « Bénévolat », l'onglet « vitalité démocratique »
du SENACS et la valorisation comptable des contributions volontaires
(compte 87 : heures × taux horaire configurable).
"""
from __future__ import annotations

from datetime import date

from flask import current_app

from app.extensions import db
from app.models import BenevoleHeures, Participant


def taux_horaire() -> float:
    """Taux horaire de valorisation : réglage d'instance (modifiable dans
    l'application, permission benevolat:taux) sinon config, sinon 13 €/h."""
    try:
        from app.models import InstanceSettings
        row = InstanceSettings.query.first()
        if row is not None and row.benevolat_taux_horaire:
            return float(row.benevolat_taux_horaire)
    except Exception:
        pass
    try:
        return float(current_app.config.get("BENEVOLAT_TAUX_HORAIRE") or 13.0)
    except Exception:
        return 13.0


def stats_annee(annee: int, secteur: str | None = None) -> dict:
    """Photo du bénévolat pour un exercice.

    - « actifs » : bénévoles avec au moins une heure saisie dans l'année ;
    - « déclarés » : fiches marquées bénévoles (drapeau), même sans heure ;
    - valorisation = heures totales × taux horaire.
    """
    q = (BenevoleHeures.query
         .filter(BenevoleHeures.date_action >= date(annee, 1, 1))
         .filter(BenevoleHeures.date_action <= date(annee, 12, 31)))
    if secteur:
        q = q.filter(BenevoleHeures.secteur == secteur)
    lignes = q.order_by(BenevoleHeures.date_action.desc(), BenevoleHeures.id.desc()).all()

    par_benevole: dict[int, dict] = {}
    par_mission: dict[str, float] = {}
    total_heures = 0.0
    for l in lignes:
        total_heures += float(l.heures or 0)
        b = par_benevole.setdefault(l.participant_id, {"heures": 0.0, "nb_actions": 0})
        b["heures"] = round(b["heures"] + float(l.heures or 0), 2)
        b["nb_actions"] += 1
        mission = (l.mission or "Autre").strip() or "Autre"
        par_mission[mission] = round(par_mission.get(mission, 0.0) + float(l.heures or 0), 2)

    participants = {}
    if par_benevole:
        for p in Participant.query.filter(Participant.id.in_(list(par_benevole.keys()))).all():
            participants[p.id] = p

    recap = sorted(
        ({"participant": participants.get(pid), **infos}
         for pid, infos in par_benevole.items() if participants.get(pid)),
        key=lambda r: -r["heures"],
    )

    declares_q = Participant.query.filter(Participant.est_benevole.is_(True))
    if secteur:
        declares_q = declares_q.filter(Participant.created_secteur == secteur)
    nb_declares = declares_q.count()
    taux = taux_horaire()

    return {
        "annee": annee,
        "secteur": secteur,
        "lignes": lignes,
        "nb_actifs": len(par_benevole),
        "nb_declares": nb_declares,
        "total_heures": round(total_heures, 2),
        "taux_horaire": taux,
        "valorisation": round(total_heures * taux, 2),
        "par_benevole": recap,
        "par_mission": dict(sorted(par_mission.items(), key=lambda kv: -kv[1])),
    }


def heures_participant(participant_id: int, annee: int | None = None) -> dict:
    """Total d'heures d'un bénévole (année donnée + global) pour le passeport."""
    base = BenevoleHeures.query.filter_by(participant_id=participant_id)
    total = round(sum(float(l.heures or 0) for l in base.all()), 2)
    annee_val = None
    if annee:
        annee_val = round(sum(
            float(l.heures or 0)
            for l in base.filter(BenevoleHeures.date_action >= date(annee, 1, 1))
                        .filter(BenevoleHeures.date_action <= date(annee, 12, 31)).all()
        ), 2)
    dernieres = (BenevoleHeures.query.filter_by(participant_id=participant_id)
                 .order_by(BenevoleHeures.date_action.desc(), BenevoleHeures.id.desc())
                 .limit(3).all())
    return {"total": total, "annee": annee_val, "dernieres": dernieres}
