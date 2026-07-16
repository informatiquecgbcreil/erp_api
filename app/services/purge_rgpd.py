"""Purge RGPD : anonymisation automatique des participants inactifs.

Principe de limitation de conservation (RGPD art. 5) : les données
identifiantes d'une personne sans aucune activité depuis
``PURGE_INACTIFS_ANNEES`` ans (3 par défaut) sont anonymisées.
Les statistiques historiques restent exploitables (la fiche devient
« ANONYME P<id> », les présences et données chiffrées sont conservées).

La « dernière activité » est la date la plus récente parmi : la fiche
elle-même (création/modification), les présences aux activités (et la
date des sessions), les orientations, les questionnaires, les notes et
pièces du passeport pédagogique, les évaluations.

La purge se déclenche automatiquement une fois par jour (voir
app/__init__.py) et peut être lancée manuellement depuis la page
Contrôle -> Purge RGPD. Chaque anonymisation est journalisée.
"""
import os
from datetime import date, datetime, timedelta

from flask import current_app
from sqlalchemy import func

from app.extensions import db
from app.models import (
    BenevoleHeures,
    Cotisation,
    Evaluation,
    OrientationAccesDroit,
    Paiement,
    Participant,
    ParticipantInsertionParcours,
    PasseportNote,
    PasseportPieceJointe,
    PresenceActivite,
    QuestionnaireResponseGroup,
    SessionActivite,
    TachePlanifiee,
)
from app.utils.dates import utcnow

NOM_ANONYME = "ANONYME"
NOM_TACHE = "purge_rgpd"


def _reglages_instance():
    """Ligne des paramètres d'instance, ou None.

    Défensif : au premier démarrage (table pas encore migrée), la requête
    peut échouer — on retombe alors sur les variables d'environnement.
    """
    try:
        from app.models import InstanceSettings
        return InstanceSettings.query.first()
    except Exception:
        db.session.rollback()
        return None


def annees_inactivite() -> int:
    """Délai d'inactivité (années) avant anonymisation automatique.

    Réglable depuis la page Contrôle → Purge RGPD (prioritaire) ;
    sinon variable d'environnement PURGE_INACTIFS_ANNEES (3 par défaut).
    """
    reglages = _reglages_instance()
    valeur = getattr(reglages, "purge_rgpd_annees", None)
    if valeur:
        return max(1, min(int(valeur), 10))
    return int(os.environ.get("PURGE_INACTIFS_ANNEES", "3"))


def purge_auto_active() -> bool:
    reglages = _reglages_instance()
    valeur = getattr(reglages, "purge_rgpd_auto", None)
    if valeur is not None:
        return bool(valeur)
    return os.environ.get("PURGE_INACTIFS_AUTO", "1") in {"1", "true", "True", "yes"}


def _vers_datetime(valeur) -> datetime | None:
    if isinstance(valeur, datetime):
        return valeur
    if isinstance(valeur, date):
        return datetime(valeur.year, valeur.month, valeur.day)
    return None


def _max_par_participant(colonne_pid, colonne_date, jointure=None, base=None) -> dict[int, datetime]:
    q = db.session.query(colonne_pid, func.max(colonne_date))
    if base is not None:
        q = q.select_from(base)
    if jointure is not None:
        q = q.join(*jointure)
    resultat = {}
    for pid, valeur in q.group_by(colonne_pid).all():
        dt = _vers_datetime(valeur)
        if pid and dt:
            resultat[int(pid)] = dt
    return resultat


def derniere_activite_par_participant() -> dict[int, datetime]:
    eff_session = db.func.coalesce(SessionActivite.rdv_date, SessionActivite.date_session)
    sources = [
        _max_par_participant(PresenceActivite.participant_id, PresenceActivite.created_at),
        _max_par_participant(
            PresenceActivite.participant_id,
            eff_session,
            jointure=(SessionActivite, PresenceActivite.session_id == SessionActivite.id),
        ),
        _max_par_participant(OrientationAccesDroit.participant_id, OrientationAccesDroit.updated_at),
        _max_par_participant(OrientationAccesDroit.participant_id, OrientationAccesDroit.date_orientation),
        _max_par_participant(QuestionnaireResponseGroup.participant_id, QuestionnaireResponseGroup.created_at),
        _max_par_participant(PasseportNote.participant_id, PasseportNote.created_at),
        _max_par_participant(PasseportPieceJointe.participant_id, PasseportPieceJointe.created_at),
        _max_par_participant(Evaluation.participant_id, Evaluation.date_evaluation),
        # Un adhérent, un bénévole ou une personne en parcours reste « actif »
        # même sans présence en atelier : sans ça, la purge anonymiserait des
        # contacts encore en lien avec la structure.
        _max_par_participant(BenevoleHeures.participant_id, BenevoleHeures.date_action),
        _max_par_participant(Cotisation.participant_id, Cotisation.updated_at),
        _max_par_participant(Cotisation.participant_id, Cotisation.date_reference),
        # Règlements d'une cotisation individuelle : Paiement -> Cotisation -> participant.
        _max_par_participant(
            Cotisation.participant_id,
            Paiement.date_paiement,
            jointure=(Cotisation, Paiement.cotisation_id == Cotisation.id),
            base=Paiement,
        ),
        # Adhésion familiale : la cotisation est portée par le foyer, elle vaut
        # activité pour tous ses membres.
        _max_par_participant(
            Participant.id,
            Cotisation.updated_at,
            jointure=(Cotisation, Cotisation.foyer_id == Participant.foyer_id),
        ),
        _max_par_participant(ParticipantInsertionParcours.participant_id, ParticipantInsertionParcours.updated_at),
    ]
    fusion: dict[int, datetime] = {}
    for source in sources:
        for pid, dt in source.items():
            if pid not in fusion or dt > fusion[pid]:
                fusion[pid] = dt
    return fusion


def participants_inactifs(annees: int | None = None) -> list[dict]:
    """Participants dont TOUTE activité date de plus de `annees` ans.

    Retourne [{"participant": Participant, "derniere_activite": datetime}],
    du plus ancien au plus récent. Les fiches déjà anonymisées sont ignorées.
    """
    annees = annees_inactivite() if annees is None else annees
    seuil = utcnow() - timedelta(days=annees * 365)
    activites = derniere_activite_par_participant()

    en_attente = []
    for p in Participant.query.filter(Participant.nom != NOM_ANONYME).all():
        candidates = [
            _vers_datetime(p.updated_at),
            _vers_datetime(p.created_at),
            activites.get(p.id),
        ]
        derniere = max((c for c in candidates if c is not None), default=None)
        if derniere is not None and derniere < seuil:
            en_attente.append({"participant": p, "derniere_activite": derniere})

    en_attente.sort(key=lambda item: item["derniere_activite"])
    return en_attente


def anonymiser_participant(p: Participant, actor_id: int | None = None) -> None:
    """Efface les données identifiantes en conservant l'exploitabilité statistique."""
    from app.services.insertion import sync_legacy_insertion_fields

    p.nom = NOM_ANONYME
    p.prenom = f"P{p.id}"
    p.adresse = None
    p.ville = None
    p.email = None
    p.telephone = None
    p.pays_origine = None
    p.titre_sejour_type = None
    p.diplome_obtenu = None
    p.cir_obtenu = None
    p.date_entree_dispositif = None
    p.date_sortie_dispositif = None
    p.droit_image_statut = "non_renseigne"
    p.droit_image_date = None
    p.droit_image_recueilli_par = None
    sync_legacy_insertion_fields(p, actor_id=actor_id)


def derniere_purge() -> datetime | None:
    tache = TachePlanifiee.query.filter_by(nom=NOM_TACHE).first()
    return tache.derniere_execution if tache else None


def _marquer_purge_executee() -> None:
    tache = TachePlanifiee.query.filter_by(nom=NOM_TACHE).first()
    if tache is None:
        tache = TachePlanifiee(nom=NOM_TACHE)
        db.session.add(tache)
    tache.derniere_execution = utcnow()
    db.session.commit()


def purger_participants_inactifs(annees: int | None = None, declenchement: str = "automatique") -> int:
    """Anonymise les participants inactifs. Retourne le nombre traité."""
    en_attente = participants_inactifs(annees)
    for item in en_attente:
        p = item["participant"]
        anonymiser_participant(p)
        current_app.logger.warning(
            "Purge RGPD: participant #%s anonymisé (dernière activité: %s, déclenchement: %s)",
            p.id,
            item["derniere_activite"].date(),
            declenchement,
        )
    if en_attente:
        db.session.commit()
    _marquer_purge_executee()
    return len(en_attente)


def purge_quotidienne_si_necessaire() -> None:
    """Lance la purge au plus une fois par jour. Ne doit jamais casser une requête."""
    try:
        derniere = derniere_purge()
        if derniere is None:
            # Première activation: on s'arme sans rien effacer, pour laisser
            # une journée à l'équipe pour consulter la liste des fiches en
            # attente (Contrôle -> Purge RGPD) avant la première exécution.
            _marquer_purge_executee()
            current_app.logger.warning(
                "Purge RGPD activée. Première exécution automatique demain ; "
                "%s fiche(s) en attente. Vérifiez la liste dans Contrôle -> Purge RGPD.",
                len(participants_inactifs()),
            )
            return
        if derniere.date() >= utcnow().date():
            return
        nombre = purger_participants_inactifs(declenchement="automatique (quotidien)")
        if nombre:
            current_app.logger.warning("Purge RGPD quotidienne: %s participant(s) anonymisé(s).", nombre)
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Échec de la purge RGPD quotidienne")
