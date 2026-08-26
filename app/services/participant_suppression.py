"""Suppression définitive d'une fiche participant.

L'anonymisation reste la voie normale : elle efface l'identité tout en
conservant présences et compteurs, donc les bilans déjà rendus aux
financeurs restent justes. La suppression totale répond à un autre besoin —
la fiche créée par erreur — et détruit réellement toutes les données liées.

Ce module fait deux choses :
- ``analyser`` : ce que la fiche porte comme historique, pour l'afficher
  avant de décider et pour le consigner dans le journal ;
- ``supprimer_definitivement`` : l'effacement, dépendances comprises.

Les suppressions sont explicites plutôt que confiées aux règles ON DELETE
de la base : le comportement est ainsi identique sur PostgreSQL et SQLite,
et l'ordre reste lisible pour qui reprendra ce code.
"""
from __future__ import annotations

import os

from app.extensions import db
from app.models import (
    BenevoleHeures,
    Cotisation,
    DefiTransition,
    Evaluation,
    HartEvaluation,
    InscriptionActivite,
    ObjectifSuivi,
    OrientationAccesDroit,
    Paiement,
    Participant,
    ParticipantInsertionCertification,
    ParticipantInsertionParcours,
    ParticipantInsertionPositionnement,
    ParticipantInsertionProfile,
    PasseportNote,
    PasseportPieceJointe,
    PortailAttempt,
    PresenceActivite,
    PresenceMaterielConsommation,
    QuestionResponse,
    QuestionnaireResponseGroup,
)

#: Ce qu'on montre avant de supprimer et ce qu'on inscrit au journal.
#: (clé, libellé lisible, modèle) — l'ordre guide l'affichage.
INVENTAIRE = [
    ("presences", "présences à des séances", PresenceActivite),
    ("inscriptions", "inscriptions à des activités", InscriptionActivite),
    ("evaluations", "évaluations de compétences", Evaluation),
    ("notes_passeport", "notes de passeport", PasseportNote),
    ("pieces_jointes", "pièces jointes de passeport", PasseportPieceJointe),
    ("suivis_objectifs", "suivis d'objectifs", ObjectifSuivi),
    ("hart", "évaluations échelle de Hart", HartEvaluation),
    ("benevolat", "heures de bénévolat", BenevoleHeures),
    ("cotisations", "adhésions / cotisations", Cotisation),
    ("questionnaires", "réponses à des questionnaires", QuestionnaireResponseGroup),
    ("orientations", "orientations accès aux droits", OrientationAccesDroit),
    ("defis", "défis transition", DefiTransition),
    ("insertion_parcours", "parcours d'insertion", ParticipantInsertionParcours),
    ("insertion_positionnements", "positionnements d'insertion", ParticipantInsertionPositionnement),
    ("insertion_certifications", "certifications d'insertion", ParticipantInsertionCertification),
]


def analyser(participant: Participant) -> dict:
    """Compte l'historique rattaché à la fiche.

    Renvoie ``{"total": n, "details": [(libellé, nombre), …]}`` — total à zéro
    signifiant une fiche sans aucune donnée d'activité, donc typiquement une
    erreur de saisie qu'on peut retirer sans conséquence sur les bilans.
    """
    details = []
    total = 0
    for _cle, libelle, modele in INVENTAIRE:
        nombre = (
            db.session.query(db.func.count(modele.id))
            .filter(modele.participant_id == participant.id)
            .scalar()
        ) or 0
        if nombre:
            details.append((libelle, int(nombre)))
            total += int(nombre)
    return {"total": total, "details": details}


def instantane(participant: Participant) -> dict:
    """Copie des informations d'identité, pour le journal d'audit.

    Une suppression ne laisse rien derrière elle : sans cette trace, plus
    aucun moyen de savoir qui a été effacé, ni de le recréer en cas de
    fausse manœuvre.
    """
    return {
        "id": participant.id,
        "nom": participant.nom,
        "prenom": participant.prenom,
        "date_naissance": participant.date_naissance.isoformat() if participant.date_naissance else None,
        "genre": participant.genre,
        "ville": participant.ville,
        "adresse": participant.adresse,
        "email": participant.email,
        "telephone": participant.telephone,
        "quartier_id": participant.quartier_id,
        "type_public": participant.type_public,
        "created_secteur": participant.created_secteur,
        "cree_le": participant.created_at.isoformat() if getattr(participant, "created_at", None) else None,
    }


def _effacer_fichier(chemin: str | None) -> None:
    """Retire un fichier du disque sans jamais faire échouer la suppression."""
    if not chemin:
        return
    try:
        if os.path.exists(chemin):
            os.remove(chemin)
    except OSError:
        pass


def supprimer_definitivement(participant: Participant) -> dict:
    """Efface la fiche et tout ce qui s'y rattache.

    Ne commite pas : l'appelant décide du moment, ce qui lui permet
    d'inscrire d'abord la trace au journal dans la même transaction.
    """
    pid = participant.id
    resume = analyser(participant)

    # Fichiers d'abord : une fois les lignes parties, plus moyen de les retrouver.
    for piece in PasseportPieceJointe.query.filter_by(participant_id=pid).all():
        _effacer_fichier(piece.file_path)
    for presence in PresenceActivite.query.filter_by(participant_id=pid).all():
        _effacer_fichier(presence.signature_path)

    def _supprimer(modele, **filtres):
        db.session.query(modele).filter_by(**filtres).delete(synchronize_session=False)

    # Petits-enfants avant enfants, enfants avant la fiche.
    groupes = [
        g.id for g in QuestionnaireResponseGroup.query.filter_by(participant_id=pid).all()
    ]
    if groupes:
        db.session.query(QuestionResponse).filter(
            QuestionResponse.response_group_id.in_(groupes)
        ).delete(synchronize_session=False)

    cotisations = [c.id for c in Cotisation.query.filter_by(participant_id=pid).all()]
    if cotisations:
        db.session.query(Paiement).filter(
            Paiement.cotisation_id.in_(cotisations)
        ).delete(synchronize_session=False)

    _supprimer(PresenceMaterielConsommation, participant_id=pid)
    for modele in (
        PresenceActivite, InscriptionActivite, Evaluation, PasseportNote,
        PasseportPieceJointe, ObjectifSuivi, HartEvaluation, BenevoleHeures,
        Cotisation, QuestionnaireResponseGroup, OrientationAccesDroit,
        DefiTransition, ParticipantInsertionCertification,
        ParticipantInsertionPositionnement, ParticipantInsertionParcours,
        ParticipantInsertionProfile,
    ):
        _supprimer(modele, participant_id=pid)

    # Trace technique du portail : on garde la ligne, on coupe le lien.
    db.session.query(PortailAttempt).filter_by(participant_id=pid).update(
        {"participant_id": None}, synchronize_session=False
    )

    db.session.delete(participant)
    return resume
