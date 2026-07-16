"""Inscriptions préalables & listes d'attente — logique métier.

Règles :
- la jauge d'une séance = ``session.capacite`` sinon ``atelier.capacite_defaut``
  (vide = pas de limite) ; la jauge d'une inscription « atelier entier » =
  ``atelier.capacite_defaut`` ;
- au-delà de la jauge, l'inscription bascule en LISTE D'ATTENTE (jamais de
  refus : l'accueil garde la main) ;
- une annulation promeut automatiquement l'inscription la plus ancienne de
  la liste d'attente ;
- les « attendus » d'une séance = inscrits de la séance + inscrits de
  l'atelier entier ; les absents = attendus sans présence pointée ;
- « pointer présent » crée la ligne d'émargement (PresenceActivite) : les
  inscriptions pré-remplissent l'émargement, jamais l'inverse.
"""
from __future__ import annotations

from datetime import date

from app.extensions import db
from app.models import (
    AtelierActivite,
    InscriptionActivite,
    Participant,
    PresenceActivite,
    SessionActivite,
)


class InscriptionErreur(ValueError):
    """Erreur métier à afficher telle quelle à l'utilisateur."""


def capacite_cible(atelier: AtelierActivite, session: SessionActivite | None) -> int | None:
    """Jauge applicable (None = illimitée)."""
    if session is not None and session.capacite is not None:
        return int(session.capacite)
    if atelier.capacite_defaut is not None:
        return int(atelier.capacite_defaut)
    return None


def _actives_cible(atelier_id: int, session_id: int | None):
    q = InscriptionActivite.query.filter(
        InscriptionActivite.atelier_id == atelier_id,
        InscriptionActivite.statut != "annule",
    )
    if session_id is None:
        q = q.filter(InscriptionActivite.session_id.is_(None))
    else:
        q = q.filter(InscriptionActivite.session_id == session_id)
    return q


def compteurs(atelier: AtelierActivite, session: SessionActivite | None = None) -> dict:
    """Inscrits / attente / capacité / places restantes pour une cible."""
    session_id = session.id if session is not None else None
    inscrits = _actives_cible(atelier.id, session_id).filter_by(statut="inscrit").count()
    attente = _actives_cible(atelier.id, session_id).filter_by(statut="attente").count()
    capacite = capacite_cible(atelier, session)
    return {
        "inscrits": inscrits,
        "attente": attente,
        "capacite": capacite,
        "places_restantes": (max(0, capacite - inscrits) if capacite is not None else None),
        "complet": (capacite is not None and inscrits >= capacite),
    }


def inscrire(
    participant: Participant,
    atelier: AtelierActivite,
    session: SessionActivite | None = None,
    *,
    commentaire: str | None = None,
    user_id: int | None = None,
) -> InscriptionActivite:
    """Inscrit (ou met en liste d'attente si la jauge est atteinte).

    Refuse le doublon : une seule inscription active par personne et par
    cible (séance précise, ou atelier entier).
    """
    session_id = session.id if session is not None else None

    deja = _actives_cible(atelier.id, session_id).filter_by(participant_id=participant.id).first()
    if deja is not None:
        cible = "cette séance" if session_id else "cet atelier"
        raise InscriptionErreur(
            f"{participant.prenom} {participant.nom} est déjà "
            f"{'inscrit·e' if deja.statut == 'inscrit' else 'en liste d’attente'} pour {cible}."
        )

    etat = compteurs(atelier, session)
    statut = "attente" if etat["complet"] else "inscrit"

    inscription = InscriptionActivite(
        atelier_id=atelier.id,
        session_id=session_id,
        participant_id=participant.id,
        statut=statut,
        commentaire=(commentaire or "").strip() or None,
        created_by_user_id=user_id,
    )
    db.session.add(inscription)
    db.session.commit()
    return inscription


def annuler(inscription: InscriptionActivite) -> InscriptionActivite | None:
    """Annule l'inscription et promeut le plus ancien de la liste d'attente.

    Retourne l'inscription promue (ou None). La promotion ne se fait que si
    l'annulée occupait une place (statut « inscrit »)."""
    liberait_une_place = inscription.statut == "inscrit"
    inscription.statut = "annule"
    db.session.flush()

    promue = None
    if liberait_une_place:
        etat = compteurs(inscription.atelier, inscription.session)
        if not etat["complet"]:
            promue = (
                _actives_cible(inscription.atelier_id, inscription.session_id)
                .filter_by(statut="attente")
                .order_by(InscriptionActivite.created_at.asc(), InscriptionActivite.id.asc())
                .first()
            )
            if promue is not None:
                promue.statut = "inscrit"
    db.session.commit()
    return promue


def liste_cible(atelier: AtelierActivite, session: SessionActivite | None = None) -> dict:
    """Inscrits et liste d'attente d'une cible, dans l'ordre d'arrivée."""
    session_id = session.id if session is not None else None
    actives = (
        _actives_cible(atelier.id, session_id)
        .order_by(InscriptionActivite.created_at.asc(), InscriptionActivite.id.asc())
        .all()
    )
    return {
        "inscrits": [i for i in actives if i.statut == "inscrit"],
        "attente": [i for i in actives if i.statut == "attente"],
    }


def attendus_session(session: SessionActivite) -> list[InscriptionActivite]:
    """Inscrits attendus à une séance : séance précise + atelier entier."""
    seance = (
        _actives_cible(session.atelier_id, session.id)
        .filter_by(statut="inscrit").all()
    )
    atelier_entier = (
        _actives_cible(session.atelier_id, None)
        .filter_by(statut="inscrit").all()
    )
    vus: set[int] = set()
    attendus = []
    for inscription in seance + atelier_entier:
        if inscription.participant_id not in vus:
            vus.add(inscription.participant_id)
            attendus.append(inscription)
    return attendus


def a_pointer_session(session: SessionActivite) -> list[InscriptionActivite]:
    """Attendus qui n'ont pas encore de ligne d'émargement sur la séance."""
    presents = {
        pid for (pid,) in db.session.query(PresenceActivite.participant_id)
        .filter(PresenceActivite.session_id == session.id).all()
    }
    return [i for i in attendus_session(session) if i.participant_id not in presents]


def pointer_present(session: SessionActivite, participant_id: int) -> PresenceActivite:
    """Crée la ligne d'émargement pour un inscrit (idempotent)."""
    existante = PresenceActivite.query.filter_by(
        session_id=session.id, participant_id=participant_id
    ).first()
    if existante is not None:
        return existante
    presence = PresenceActivite(session_id=session.id, participant_id=participant_id)
    db.session.add(presence)
    db.session.commit()
    return presence


# ---------------------------------------------------------------------------
# Export contacts au format d'import Brevo
# ---------------------------------------------------------------------------

def contacts_brevo(atelier: AtelierActivite, session: SessionActivite | None = None) -> list[dict]:
    """Contacts des inscrits actifs (inscrits + attente), format Brevo.

    Colonnes attendues par l'import Brevo : EMAIL, NOM, PRENOM, SMS.
    Une personne sans e-mail NI téléphone n'est pas exportable (ligne omise).
    Dédoublonné par participant.
    """
    if session is not None:
        rows = (
            _actives_cible(atelier.id, session.id).all()
            + _actives_cible(atelier.id, None).all()
        )
    else:
        rows = (
            InscriptionActivite.query
            .filter(
                InscriptionActivite.atelier_id == atelier.id,
                InscriptionActivite.statut != "annule",
            )
            .all()
        )

    vus: set[int] = set()
    contacts = []
    for inscription in rows:
        p = inscription.participant
        if p is None or p.id in vus:
            continue
        vus.add(p.id)
        email = (p.email or "").strip().lower()
        tel = "".join(c for c in (p.telephone or "") if c.isdigit() or c == "+")
        if not email and not tel:
            continue
        contacts.append({
            "EMAIL": email,
            "NOM": (p.nom or "").strip(),
            "PRENOM": (p.prenom or "").strip(),
            "SMS": tel,
        })
    contacts.sort(key=lambda c: (c["NOM"].lower(), c["PRENOM"].lower()))
    return contacts
