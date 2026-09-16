"""Ce qu'on vérifie avant d'imprimer une feuille d'émargement.

Une feuille d'émargement n'est pas un écran de plus : c'est la pièce
justificative qu'un financeur regarde en premier, et la première chose
qu'il y cherche, ce sont les cases de signature vides.

Or l'application permet — et c'est une bonne chose — de **pré-émarger** :
on pointe les inscrits avant la séance, puis on fait signer sur place au
kiosque, ou à distance par un lien personnel envoyé à chacun. Le revers,
c'est qu'une personne pré-émargée qui n'est finalement pas venue reste sur
la liste, et ressort sur la feuille imprimée avec une signature vide.

Rien ne le rappelait au moment de générer.

## La règle qui évite de crier au loup

Toutes les présences non signées ne sont pas des oublis. Une personne
déclarée **absente excusée** n'a aucune raison de signer : sa ligne
documente précisément le fait qu'elle n'est pas venue, et elle a sa place
sur la feuille. La compter comme « manquante » ferait sonner
l'avertissement à chaque séance, et un avertissement qui sonne toujours
n'est plus lu par personne.

Seules les présences déclarées **présent** ou **en retard** sans signature
appellent une décision.
"""
from __future__ import annotations

from app.extensions import db
from app.models import Participant, PresenceActivite

#: Statuts pour lesquels une signature est attendue.
STATUTS_SIGNATURE_ATTENDUE = ("present", "retard")


def presences_sans_signature(session) -> list[PresenceActivite]:
    """Les lignes qui devraient porter une signature et n'en ont pas.

    Triées par nom, comme la feuille imprimée : on doit pouvoir suivre du
    doigt de l'écran au papier.
    """
    return (
        PresenceActivite.query
        .filter(PresenceActivite.session_id == session.id)
        .filter(PresenceActivite.signature_path.is_(None))
        .filter(PresenceActivite.presence_type.in_(STATUTS_SIGNATURE_ATTENDUE))
        .join(Participant, Participant.id == PresenceActivite.participant_id)
        .order_by(Participant.nom.asc(), Participant.prenom.asc())
        .all()
    )


def resume_signatures(session) -> dict:
    """De quoi écrire une phrase exacte à l'écran.

    ``{total, signees, manquantes, excusees}`` — ``manquantes`` ne compte
    QUE ce qui appelle une décision, jamais les absences excusées.
    """
    presences = PresenceActivite.query.filter_by(session_id=session.id).all()
    signees = sum(1 for pr in presences if pr.signature_path)
    excusees = sum(
        1 for pr in presences
        if not pr.signature_path and pr.presence_type not in STATUTS_SIGNATURE_ATTENDUE
    )
    return {
        "total": len(presences),
        "signees": signees,
        "manquantes": len(presences) - signees - excusees,
        "excusees": excusees,
    }


def retirer_les_non_signees(session, *, journaliser_action=None) -> list[str]:
    """Retire les présences en attente de signature. Retourne les noms retirés.

    Le geste du « pré-émargement raté » : on avait pointé dix personnes,
    trois ne sont pas venues, on les enlève d'un coup avant d'imprimer.

    Les absences excusées ne sont JAMAIS retirées : elles sont voulues, et
    leur trace a de la valeur dans le dossier.
    """
    retires: list[str] = []
    for presence in presences_sans_signature(session):
        participant = presence.participant
        nom = (
            f"{participant.nom} {participant.prenom}".strip()
            if participant else f"participant #{presence.participant_id}"
        )
        retires.append(nom)
        if journaliser_action is not None:
            journaliser_action(nom)
        db.session.delete(presence)
    if retires:
        db.session.commit()
    return retires


# ---------------------------------------------------------------------------
# Où en est chaque personne présente
# ---------------------------------------------------------------------------

def situations(session, participant_ids) -> dict[int, dict]:
    """Inscription à l'atelier, bulletin de l'année, règlement — par personne.

    Pendant l'émargement, la question qui revient à l'accueil est toujours
    la même : « elle est inscrite, celle-là ? elle a payé ? ». Les trois
    réponses vivent dans trois modules différents, et il fallait ouvrir
    trois écrans pour les obtenir — pendant que la personne attend.

    Chaque entrée : ``{annee_scolaire, libelle_annee, atelier, bulletin,
    reglement}`` où

    - ``atelier`` vaut « inscrit », « attente » (liste d'attente) ou
      « aucune » — venue sans inscription, ce qui est parfaitement
      légitime en accueil libre mais mérite d'être su ;
    - ``bulletin`` dit si la personne a un bulletin d'inscription pour
      l'année scolaire DE LA SÉANCE ;
    - ``reglement`` est l'état d'adhésion de cette même année.

    L'année est celle de la séance et non celle d'aujourd'hui : rouvrir
    l'émargement d'une séance de juin doit montrer la situation de juin,
    pas celle d'aujourd'hui.

    Trois requêtes au total, quel que soit le nombre de présents : afficher
    l'état de vingt personnes ne doit pas coûter soixante allers-retours.
    """
    from app.models import InscriptionActivite, InscriptionAnnuelle, Participant
    from app.services.cotisations import (
        annee_scolaire_de,
        annee_scolaire_courante,
        etats_reglement_par_participant,
        libelle_annee_scolaire,
    )

    ids = sorted({int(i) for i in participant_ids if i})
    if not ids:
        return {}

    jour = getattr(session, "date_session", None) or getattr(session, "rdv_date", None)
    annee = annee_scolaire_de(jour) if jour else annee_scolaire_courante()

    # 1. Inscription à l'atelier. « inscrit » l'emporte sur « attente » :
    #    quelqu'un inscrit à l'atelier ET en attente sur une séance
    #    précise est bien inscrit.
    par_atelier: dict[int, str] = {}
    lignes = (
        InscriptionActivite.query
        .filter(InscriptionActivite.atelier_id == session.atelier_id)
        .filter(InscriptionActivite.participant_id.in_(ids))
        .filter(InscriptionActivite.statut != "annule")
        .all()
    )
    for ligne in lignes:
        if par_atelier.get(ligne.participant_id) == "inscrit":
            continue
        par_atelier[ligne.participant_id] = ligne.statut

    # 2. Bulletin de l'année. Rattachement par identifiant (certain), puis
    #    par nom complet : un bulletin saisi avant que la fiche existe n'a
    #    pas encore d'identifiant.
    fiches = Participant.query.filter(Participant.id.in_(ids)).all()
    avec_bulletin: set[int] = set()
    noms = {}
    for fiche in fiches:
        noms[((fiche.nom or "").strip().lower(), (fiche.prenom or "").strip().lower())] = fiche.id
    for ins in InscriptionAnnuelle.query.filter_by(annee_scolaire=annee).all():
        if ins.participant_id in ids:
            avec_bulletin.add(ins.participant_id)
            continue
        cle = ((ins.nom or "").strip().lower(), (ins.prenom or "").strip().lower())
        if cle in noms:
            avec_bulletin.add(noms[cle])

    # 3. Règlement, en un seul aller-retour (fonction déjà prévue pour ça).
    reglements = etats_reglement_par_participant(ids, annee_scolaire=annee)

    libelle = libelle_annee_scolaire(annee)
    return {
        pid: {
            "annee_scolaire": annee,
            "libelle_annee": libelle,
            "atelier": par_atelier.get(pid, "aucune"),
            "bulletin": pid in avec_bulletin,
            "reglement": reglements.get(pid),
        }
        for pid in ids
    }
