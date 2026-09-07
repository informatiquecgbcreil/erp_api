"""Inscriptions annuelles (campagne de rentrée) — logique métier.

Le module suit une personne depuis le bulletin papier rempli à l'accueil
jusqu'à sa première participation :

1. **Saisie** du bulletin : coordonnées, secteur qui fait venir, ateliers
   souhaités (cases + champ libre), envie de bénévolat (mission libre,
   créneaux jour × demi-journée, ou « je ne sais pas »).
2. **Transformation en fiche participant** : la fiche est créée — ou
   rattachée à une fiche existante quand c'est un ancien inscrit — avec le
   statut spécial ``attente_premiere_participation``. Les ateliers cochés
   deviennent des inscriptions dans le module Activité (avec sa jauge et sa
   liste d'attente).
3. **Règlement** : confirmé ou non à l'accueil au moment du paiement, avec
   création facultative de l'adhésion dans le module Adhésions.
4. **Première participation** : dès qu'une présence est pointée (émargement,
   kiosque, import, saisie en grille — peu importe la porte), le statut
   d'attente tombe automatiquement. Le garde-fou est posé sur la session
   SQLAlchemy pour n'oublier aucun point d'entrée.

Convention d'année : celle des cotisations — l'année scolaire court de
septembre à août et porte le nom de son année de rentrée (2026 -> « 2026-2027 »).
"""
from __future__ import annotations

from datetime import date
from io import BytesIO

from sqlalchemy import event
from sqlalchemy.orm import Session as SASession

from app.extensions import db
from app.models import (
    DEMI_JOURNEES,
    DEMI_JOURNEES_LABELS,
    JOURS_SEMAINE,
    JOURS_SEMAINE_LABELS,
    STATUT_PARTICIPANT_ACTIF,
    STATUT_PARTICIPANT_ATTENTE,
    STATUTS_INSCRIPTION_ANNUELLE_LABELS,
    AtelierActivite,
    Cotisation,
    InscriptionAnnuelle,
    InscriptionAnnuelleDispo,
    Paiement,
    Participant,
    PresenceActivite,
    SessionActivite,
)
from app.services.cotisations import (
    annee_scolaire_courante,
    cotisation_existante,
    libelle_annee_scolaire,
    tarif_en_vigueur,
)


class InscriptionAnnuelleErreur(ValueError):
    """Erreur métier à afficher telle quelle à l'utilisateur."""


# ---------------------------------------------------------------------------
# Années scolaires
# ---------------------------------------------------------------------------

def annees_disponibles() -> list[int]:
    """Années scolaires ayant déjà des bulletins, plus l'année courante."""
    annees = {annee_scolaire_courante()}
    try:
        for (a,) in db.session.query(InscriptionAnnuelle.annee_scolaire).distinct():
            if a:
                annees.add(int(a))
    except Exception:  # noqa: BLE001 — table absente (première installation)
        pass
    return sorted(annees, reverse=True)


# ---------------------------------------------------------------------------
# Saisie du bulletin
# ---------------------------------------------------------------------------

def appliquer_disponibilites(inscription: InscriptionAnnuelle, creneaux) -> None:
    """Remplace les créneaux bénévolat cochés.

    ``creneaux`` est une suite de chaînes « jour|demi_journee » (format des
    cases du formulaire) ou de couples ``(jour, demi_journee)``. Les valeurs
    hors référentiel sont ignorées silencieusement : une case inconnue ne
    doit jamais faire échouer une inscription à l'accueil.
    """
    valides: set[tuple[str, str]] = set()
    for brut in creneaux or []:
        if isinstance(brut, (tuple, list)):
            jour, demi = (list(brut) + ["", ""])[:2]
        else:
            jour, _, demi = str(brut).partition("|")
        jour, demi = jour.strip().lower(), demi.strip().lower()
        if jour in JOURS_SEMAINE and demi in DEMI_JOURNEES:
            valides.add((jour, demi))

    actuels = {(d.jour, d.demi_journee): d for d in list(inscription.disponibilites or [])}
    for cle, ligne in actuels.items():
        if cle not in valides:
            inscription.disponibilites.remove(ligne)
    for jour, demi in sorted(valides, key=lambda c: (JOURS_SEMAINE.index(c[0]), DEMI_JOURNEES.index(c[1]))):
        if (jour, demi) not in actuels:
            inscription.disponibilites.append(InscriptionAnnuelleDispo(jour=jour, demi_journee=demi))


def appliquer_ateliers(inscription: InscriptionAnnuelle, atelier_ids) -> None:
    """Remplace les ateliers souhaités (ids ignorés s'ils n'existent pas)."""
    ids: set[int] = set()
    for brut in atelier_ids or []:
        try:
            ids.add(int(brut))
        except (TypeError, ValueError):
            continue
    inscription.ateliers = (
        AtelierActivite.query.filter(AtelierActivite.id.in_(ids)).all() if ids else []
    )


def ateliers_proposables(secteur: str | None = None) -> list[AtelierActivite]:
    """Ateliers cochables sur le bulletin : actifs, non supprimés.

    ``secteur`` restreint aux ateliers du secteur (les ateliers intersecteurs
    restent toujours proposés : ils sont ouverts à tout le monde)."""
    q = AtelierActivite.query.filter(
        AtelierActivite.is_deleted.is_(False),
        AtelierActivite.is_active.is_(True),
    )
    if secteur:
        q = q.filter(
            db.or_(AtelierActivite.secteur == secteur, AtelierActivite.est_intersecteur.is_(True))
        )
    return q.order_by(AtelierActivite.secteur.asc(), AtelierActivite.nom.asc()).all()


# ---------------------------------------------------------------------------
# Transformation en fiche participant
# ---------------------------------------------------------------------------

def doublons_possibles(inscription: InscriptionAnnuelle) -> list[Participant]:
    """Fiches ressemblantes — à proposer AVANT de créer un doublon."""
    from app.services.doublons import candidats_doublons

    return candidats_doublons(inscription.nom, inscription.prenom)


def _copier_coordonnees(inscription: InscriptionAnnuelle, participant: Participant, *, ecraser: bool) -> None:
    """Recopie les coordonnées du bulletin vers la fiche.

    ``ecraser=False`` (rattachement à une fiche existante) ne comble que les
    trous : on ne remplace jamais une donnée déjà présente par celle d'un
    bulletin, qui peut être plus ancienne ou moins fiable.
    """
    champs = {
        "adresse": (inscription.adresse or "").strip() or None,
        "ville": (inscription.ville or "").strip() or None,
        "email": (inscription.email or "").strip() or None,
        "telephone": (inscription.telephone or "").strip() or None,
        "genre": (inscription.genre or "").strip() or None,
        "date_naissance": inscription.date_naissance,
    }
    for champ, valeur in champs.items():
        if valeur is None:
            continue
        if ecraser or not getattr(participant, champ, None):
            setattr(participant, champ, valeur)


def _inscrire_aux_ateliers(inscription: InscriptionAnnuelle, participant: Participant, user_id: int | None) -> tuple[int, list[str]]:
    """Crée les inscriptions d'activité pour les ateliers cochés.

    Retourne (nombre créé, avertissements). Un atelier déjà pris ou complet
    n'interrompt jamais la transformation : on remonte le message et on
    continue — l'accueil ne doit pas rester bloqué avec la personne devant lui.
    """
    from app.services.inscriptions import InscriptionErreur, inscrire

    crees, avertissements = 0, []
    for atelier in list(inscription.ateliers or []):
        try:
            inscrire(
                participant, atelier,
                commentaire=f"Inscription annuelle {inscription.libelle_annee}",
                user_id=user_id,
            )
            crees += 1
        except InscriptionErreur as exc:
            avertissements.append(str(exc))
        except Exception as exc:  # noqa: BLE001 — jamais bloquant
            db.session.rollback()
            avertissements.append(f"{atelier.nom} : inscription impossible ({exc}).")
    return crees, avertissements


def creer_participant(
    inscription: InscriptionAnnuelle,
    *,
    user_id: int | None = None,
    secteur: str | None = None,
    quartier_id: int | None = None,
    marquer_benevole: bool = False,
    inscrire_ateliers: bool = True,
) -> tuple[Participant, list[str]]:
    """Crée la fiche participant en attente de 1re participation.

    La fiche est immédiatement utilisable partout dans l'application (elle
    apparaît dans l'annuaire, l'émargement, les recherches) mais porte le
    statut ``attente_premiere_participation`` tant que personne ne l'a
    pointée présente."""
    if inscription.participant_id:
        raise InscriptionAnnuelleErreur(
            f"{inscription.nom_complet} a déjà une fiche participant rattachée à cette inscription."
        )
    if inscription.statut == "annulee":
        raise InscriptionAnnuelleErreur("Cette inscription est annulée : réactive-la avant de créer la fiche.")

    participant = Participant(
        nom=(inscription.nom or "").strip(),
        prenom=(inscription.prenom or "").strip(),
        statut_inscription=STATUT_PARTICIPANT_ATTENTE,
        est_benevole=bool(marquer_benevole and inscription.benevolat_souhaite),
        created_by_user_id=user_id,
        created_secteur=(secteur or inscription.secteur_orienteur or inscription.created_secteur or None),
    )
    _copier_coordonnees(inscription, participant, ecraser=True)
    if quartier_id:
        from app.services.quartiers import normalize_quartier_for_ville

        participant.quartier_id = normalize_quartier_for_ville(participant.ville, quartier_id)

    db.session.add(participant)
    db.session.flush()

    inscription.participant_id = participant.id
    inscription.statut = "en_attente"
    db.session.commit()

    avertissements: list[str] = []
    if inscrire_ateliers:
        _, avertissements = _inscrire_aux_ateliers(inscription, participant, user_id)
    return participant, avertissements


def rattacher_participant(
    inscription: InscriptionAnnuelle,
    participant: Participant,
    *,
    user_id: int | None = None,
    inscrire_ateliers: bool = True,
) -> list[str]:
    """Rattache le bulletin à une fiche EXISTANTE (ancien inscrit, homonyme
    déjà connu) plutôt que de créer un doublon.

    Une fiche déjà venue au centre garde son statut « actif » : elle n'a rien
    à attendre. Une fiche jamais pointée passe, elle, en attente de 1re
    participation."""
    if inscription.participant_id:
        raise InscriptionAnnuelleErreur(
            f"{inscription.nom_complet} a déjà une fiche participant rattachée à cette inscription."
        )

    _copier_coordonnees(inscription, participant, ecraser=False)
    deja_venu = (
        db.session.query(PresenceActivite.id)
        .filter(PresenceActivite.participant_id == participant.id)
        .first()
        is not None
    )
    if deja_venu:
        participant.statut_inscription = STATUT_PARTICIPANT_ACTIF
        inscription.statut = "active"
        inscription.premiere_participation_le = inscription.premiere_participation_le or date.today()
    else:
        participant.statut_inscription = STATUT_PARTICIPANT_ATTENTE
        inscription.statut = "en_attente"

    inscription.participant_id = participant.id
    db.session.commit()

    if inscrire_ateliers:
        _, avertissements = _inscrire_aux_ateliers(inscription, participant, user_id)
        return avertissements
    return []


# ---------------------------------------------------------------------------
# Règlement
# ---------------------------------------------------------------------------

def montant_adhesion_suggere(annee_scolaire: int, a_la_date: date | None = None) -> float | None:
    """Tarif d'adhésion individuelle en vigueur (None si aucun barème saisi)."""
    ligne = tarif_en_vigueur(annee_scolaire, "adhesion_individuelle", a_la_date)
    return float(ligne.montant) if ligne is not None else None


def confirmer_reglement(
    inscription: InscriptionAnnuelle,
    *,
    montant: float | None = None,
    mode: str | None = None,
    date_reglement: date | None = None,
    commentaire: str | None = None,
    creer_adhesion: bool = False,
    user_id: int | None = None,
) -> str | None:
    """Marque le règlement comme confirmé à l'accueil.

    Si ``creer_adhesion`` et qu'une fiche participant existe, l'adhésion
    individuelle de l'année et son règlement sont enregistrés dans le module
    Adhésions & participation (source unique pour les impayés, la caisse et
    les bilans). Retourne un message d'information, ou None."""
    inscription.reglement_confirme = True
    inscription.reglement_date = date_reglement or date.today()
    inscription.reglement_mode = (mode or "").strip() or None
    inscription.reglement_montant = float(montant) if montant not in (None, "") else None
    inscription.reglement_commentaire = (commentaire or "").strip() or None

    info = None
    if creer_adhesion:
        info = _enregistrer_adhesion(inscription, user_id=user_id)
    db.session.commit()
    return info


def _enregistrer_adhesion(inscription: InscriptionAnnuelle, *, user_id: int | None) -> str | None:
    """Crée (si besoin) l'adhésion individuelle de l'année + son versement."""
    if not inscription.participant_id:
        return (
            "Règlement confirmé sur le bulletin. L'adhésion n'a pas été créée : "
            "il faut d'abord transformer l'inscription en fiche participant."
        )

    montant = inscription.reglement_montant
    if montant is None:
        montant = montant_adhesion_suggere(inscription.annee_scolaire, inscription.reglement_date)
    if montant is None:
        return (
            "Règlement confirmé sur le bulletin. L'adhésion n'a pas été créée : "
            f"aucun tarif d'adhésion n'est saisi pour {inscription.libelle_annee} "
            "(barème des tarifs) et aucun montant n'a été indiqué."
        )

    cotisation = cotisation_existante(
        annee_scolaire=inscription.annee_scolaire,
        type_cotisation="adhesion_individuelle",
        participant_id=inscription.participant_id,
    )
    if cotisation is None:
        cotisation = Cotisation(
            annee_scolaire=inscription.annee_scolaire,
            type_cotisation="adhesion_individuelle",
            participant_id=inscription.participant_id,
            montant_du=float(montant),
            date_reference=inscription.reglement_date or date.today(),
            created_by_user_id=user_id,
        )
        db.session.add(cotisation)
        db.session.flush()

    inscription.cotisation_id = cotisation.id

    if float(montant) > 0 and cotisation.reste_du > 0:
        db.session.add(Paiement(
            cotisation_id=cotisation.id,
            montant=min(float(montant), cotisation.reste_du),
            date_paiement=inscription.reglement_date or date.today(),
            mode=(inscription.reglement_mode or "especes"),
            commentaire=f"Inscription annuelle {inscription.libelle_annee}",
            created_by_user_id=user_id,
        ))
        return f"Adhésion {libelle_annee_scolaire(inscription.annee_scolaire)} et règlement enregistrés."
    return f"Adhésion {libelle_annee_scolaire(inscription.annee_scolaire)} enregistrée."


def annuler_reglement(inscription: InscriptionAnnuelle) -> None:
    """Repasse le bulletin en « à régler ».

    L'adhésion et les versements déjà enregistrés dans le module Adhésions ne
    sont PAS touchés : un mouvement de caisse ne se réécrit pas depuis ici
    (il s'annule dans le module Adhésions, qui en tient le journal)."""
    inscription.reglement_confirme = False
    inscription.reglement_date = None
    db.session.commit()


# ---------------------------------------------------------------------------
# Première participation : bascule automatique du statut d'attente
# ---------------------------------------------------------------------------

def constater_premiere_participation(participant_id: int, jour: date | None = None) -> bool:
    """Fait tomber le statut d'attente d'une fiche (et de son bulletin).

    Retourne True si quelque chose a changé. Ne commit pas : l'appelant
    reste maître de sa transaction."""
    participant = db.session.get(Participant, participant_id)
    if participant is None:
        return False

    change = False
    if (participant.statut_inscription or STATUT_PARTICIPANT_ACTIF) == STATUT_PARTICIPANT_ATTENTE:
        participant.statut_inscription = STATUT_PARTICIPANT_ACTIF
        change = True

    inscriptions = (
        InscriptionAnnuelle.query
        .filter(
            InscriptionAnnuelle.participant_id == participant_id,
            InscriptionAnnuelle.statut == "en_attente",
        )
        .all()
    )
    for inscription in inscriptions:
        inscription.statut = "active"
        inscription.premiere_participation_le = jour or date.today()
        change = True
    return change


def rafraichir_statuts(annee_scolaire: int | None = None) -> int:
    """Filet de sécurité : recale les bulletins dont la personne est déjà venue.

    Le garde-fou sur la session couvre les présences créées par
    l'application ; ce balayage rattrape les autres cas (reprise de données,
    import massif, correction en base). Retourne le nombre de bulletins
    recalés."""
    q = InscriptionAnnuelle.query.filter(
        InscriptionAnnuelle.statut == "en_attente",
        InscriptionAnnuelle.participant_id.isnot(None),
    )
    if annee_scolaire is not None:
        q = q.filter(InscriptionAnnuelle.annee_scolaire == annee_scolaire)

    recales = 0
    for inscription in q.all():
        premiere = (
            db.session.query(SessionActivite.date_session)
            .join(PresenceActivite, PresenceActivite.session_id == SessionActivite.id)
            .filter(PresenceActivite.participant_id == inscription.participant_id)
            .order_by(SessionActivite.date_session.asc())
            .first()
        )
        if premiere is None:
            continue
        if constater_premiere_participation(inscription.participant_id, premiere[0] or date.today()):
            recales += 1
    if recales:
        db.session.commit()
    return recales


_GARDE_FOU_POSE = False


def _installer_garde_fou_presences() -> None:
    """Pose l'écoute qui fait tomber le statut d'attente à la 1re présence.

    Les présences se créent depuis cinq endroits (émargement, saisie en
    grille, kiosque, import Excel, pointage des inscrits). Plutôt que de
    répéter — et d'oublier — l'appel dans chacun, on écoute la session
    SQLAlchemy : aucune porte d'entrée ne peut passer à travers."""
    global _GARDE_FOU_POSE
    if _GARDE_FOU_POSE:
        return

    @event.listens_for(SASession, "before_flush")
    def _bascule_statut_attente(session, flush_context, instances):  # noqa: ANN001
        nouvelles = [obj for obj in session.new if isinstance(obj, PresenceActivite)]
        if not nouvelles:
            return
        for presence in nouvelles:
            participant_id = getattr(presence, "participant_id", None)
            if not participant_id:
                continue
            try:
                participant = session.get(Participant, participant_id)
                if participant is None:
                    continue
                if (participant.statut_inscription or STATUT_PARTICIPANT_ACTIF) != STATUT_PARTICIPANT_ATTENTE:
                    continue
                jour = None
                seance = getattr(presence, "session", None)
                if seance is None and getattr(presence, "session_id", None):
                    seance = session.get(SessionActivite, presence.session_id)
                if seance is not None:
                    jour = seance.date_session or seance.rdv_date
                participant.statut_inscription = STATUT_PARTICIPANT_ACTIF
                for inscription in (
                    session.query(InscriptionAnnuelle)
                    .filter(
                        InscriptionAnnuelle.participant_id == participant_id,
                        InscriptionAnnuelle.statut == "en_attente",
                    )
                    .all()
                ):
                    inscription.statut = "active"
                    inscription.premiere_participation_le = jour or date.today()
            except Exception:  # noqa: BLE001 — un émargement ne doit jamais échouer pour ça
                continue

    _GARDE_FOU_POSE = True


_installer_garde_fou_presences()


# ---------------------------------------------------------------------------
# Tableau de bord
# ---------------------------------------------------------------------------

def synthese(annee_scolaire: int, inscriptions: list[InscriptionAnnuelle] | None = None) -> dict:
    """Compteurs de la campagne : où en est-on de la rentrée ?"""
    if inscriptions is None:
        inscriptions = InscriptionAnnuelle.query.filter_by(annee_scolaire=annee_scolaire).all()

    actives = [i for i in inscriptions if i.statut != "annulee"]
    par_statut = {code: 0 for code in STATUTS_INSCRIPTION_ANNUELLE_LABELS}
    par_secteur: dict[str, int] = {}
    par_atelier: dict[str, int] = {}
    par_creneau_benevolat: dict[tuple[str, str], int] = {}

    for inscription in inscriptions:
        par_statut[inscription.statut] = par_statut.get(inscription.statut, 0) + 1
        if inscription.statut == "annulee":
            continue
        secteur = (inscription.secteur_orienteur or "Non précisé").strip() or "Non précisé"
        par_secteur[secteur] = par_secteur.get(secteur, 0) + 1
        for atelier in inscription.ateliers or []:
            par_atelier[atelier.nom] = par_atelier.get(atelier.nom, 0) + 1
        if inscription.benevolat_souhaite:
            for cle in inscription.creneaux_benevolat:
                par_creneau_benevolat[cle] = par_creneau_benevolat.get(cle, 0) + 1

    return {
        "annee_scolaire": annee_scolaire,
        "libelle_annee": libelle_annee_scolaire(annee_scolaire),
        "total": len(inscriptions),
        "actives": len(actives),
        "par_statut": par_statut,
        "sans_fiche": sum(1 for i in actives if not i.participant_id),
        "en_attente": sum(1 for i in actives if i.statut == "en_attente"),
        "a_regler": sum(1 for i in actives if not i.reglement_confirme),
        "regles": sum(1 for i in actives if i.reglement_confirme),
        "montant_regle": round(sum(float(i.reglement_montant or 0) for i in actives if i.reglement_confirme), 2),
        "benevoles": sum(1 for i in actives if i.benevolat_souhaite),
        "benevoles_sans_dispo": sum(
            1 for i in actives if i.benevolat_souhaite and (i.benevolat_dispo_inconnue or not i.disponibilites)
        ),
        "par_secteur": dict(sorted(par_secteur.items(), key=lambda kv: (-kv[1], kv[0]))),
        "par_atelier": dict(sorted(par_atelier.items(), key=lambda kv: (-kv[1], kv[0]))),
        "grille_benevolat": [
            {
                "jour": jour,
                "jour_label": JOURS_SEMAINE_LABELS[jour],
                "cellules": [
                    {
                        "demi_journee": demi,
                        "demi_journee_label": DEMI_JOURNEES_LABELS[demi],
                        "nb": par_creneau_benevolat.get((jour, demi), 0),
                    }
                    for demi in DEMI_JOURNEES
                ],
            }
            for jour in JOURS_SEMAINE
        ],
    }


# ---------------------------------------------------------------------------
# Export XLSX
# ---------------------------------------------------------------------------

COLONNES_EXPORT = [
    "Année scolaire", "Date d'inscription", "Statut", "Nom", "Prénom",
    "Date de naissance", "Genre", "Adresse", "Code postal", "Ville",
    "E-mail", "Téléphone", "Secteur qui fait venir",
    "Ateliers choisis", "Autres souhaits (champ libre)",
    "Bénévolat souhaité", "Bénévolat — pour quoi faire",
    "Bénévolat — disponibilités", "Bénévolat — ne sait pas encore",
    "Règlement confirmé", "Montant réglé", "Mode de règlement", "Date de règlement",
    "Note sur le règlement", "Fiche participant", "N° de fiche",
    "1re participation", "Commentaire", "Saisi par", "Saisi le",
]


def _ligne_export(inscription: InscriptionAnnuelle, utilisateurs: dict[int, str]) -> list:
    from app.models import MODES_PAIEMENT_LABELS

    return [
        inscription.libelle_annee,
        inscription.date_inscription.isoformat() if inscription.date_inscription else "",
        inscription.statut_label,
        inscription.nom or "",
        inscription.prenom or "",
        inscription.date_naissance.isoformat() if inscription.date_naissance else "",
        inscription.genre or "",
        inscription.adresse or "",
        inscription.code_postal or "",
        inscription.ville or "",
        inscription.email or "",
        inscription.telephone or "",
        inscription.secteur_orienteur or "",
        " ; ".join(a.nom for a in sorted(inscription.ateliers or [], key=lambda a: a.nom)),
        inscription.ateliers_libre or "",
        "Oui" if inscription.benevolat_souhaite else "Non",
        inscription.benevolat_mission or "",
        inscription.creneaux_benevolat_libelle if inscription.benevolat_souhaite else "",
        "Oui" if inscription.benevolat_dispo_inconnue else "",
        "Oui" if inscription.reglement_confirme else "Non",
        inscription.reglement_montant if inscription.reglement_montant is not None else "",
        MODES_PAIEMENT_LABELS.get(inscription.reglement_mode or "", inscription.reglement_mode or ""),
        inscription.reglement_date.isoformat() if inscription.reglement_date else "",
        inscription.reglement_commentaire or "",
        "Oui" if inscription.participant_id else "Non",
        inscription.participant_id or "",
        inscription.premiere_participation_le.isoformat() if inscription.premiere_participation_le else "",
        inscription.commentaire or "",
        utilisateurs.get(inscription.created_by_user_id or 0, ""),
        inscription.created_at.strftime("%d/%m/%Y %H:%M") if inscription.created_at else "",
    ]


def export_xlsx(annee_scolaire: int, inscriptions: list[InscriptionAnnuelle]) -> BytesIO:
    """Classeur complet de la campagne : toutes les données récoltées.

    Trois feuilles : le détail nominatif (une ligne par bulletin, toutes les
    colonnes du formulaire), la synthèse de la campagne, et la grille des
    disponibilités bénévolat prête à imprimer pour la réunion d'équipe."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    from app.models import User

    utilisateurs = {u.id: (u.nom or u.email or "") for u in User.query.all()}
    data = synthese(annee_scolaire, inscriptions)

    wb = Workbook()

    detail = wb.active
    detail.title = "Inscriptions"
    detail.append([f"Inscriptions annuelles {data['libelle_annee']}"])
    detail.append([f"{len(inscriptions)} bulletin(s) — export du {date.today().strftime('%d/%m/%Y')}"])
    detail.append([])
    detail.append(COLONNES_EXPORT)
    for inscription in inscriptions:
        detail.append(_ligne_export(inscription, utilisateurs))
    detail.freeze_panes = "A5"
    if len(inscriptions):
        detail.auto_filter.ref = f"A4:{get_column_letter(len(COLONNES_EXPORT))}{4 + len(inscriptions)}"

    resume = wb.create_sheet("Synthèse")
    resume.append([f"Campagne {data['libelle_annee']}"])
    resume.append([])
    resume.append(["Indicateur", "Valeur"])
    for label, valeur in (
        ("Bulletins saisis", data["total"]),
        ("Inscriptions actives (hors annulées)", data["actives"]),
        ("Sans fiche participant", data["sans_fiche"]),
        ("En attente de 1re participation", data["en_attente"]),
        ("Règlements confirmés", data["regles"]),
        ("Restant à régler", data["a_regler"]),
        ("Montant réglé (€)", data["montant_regle"]),
        ("Envies de bénévolat", data["benevoles"]),
        ("Bénévoles sans créneau précisé", data["benevoles_sans_dispo"]),
    ):
        resume.append([label, valeur])

    resume.append([])
    resume.append(["Secteur qui fait venir", "Inscriptions"])
    for label, nb in data["par_secteur"].items():
        resume.append([label, nb])

    resume.append([])
    resume.append(["Atelier souhaité", "Inscriptions"])
    for label, nb in data["par_atelier"].items():
        resume.append([label, nb])

    benevolat = wb.create_sheet("Bénévolat")
    benevolat.append([f"Disponibilités bénévolat — {data['libelle_annee']}"])
    benevolat.append([])
    benevolat.append(["Jour"] + [DEMI_JOURNEES_LABELS[d] for d in DEMI_JOURNEES])
    for ligne in data["grille_benevolat"]:
        benevolat.append([ligne["jour_label"]] + [c["nb"] for c in ligne["cellules"]])
    benevolat.append([])
    benevolat.append(["Personne", "Pour quoi faire", "Disponibilités", "Téléphone", "E-mail"])
    for inscription in inscriptions:
        if not inscription.benevolat_souhaite or inscription.statut == "annulee":
            continue
        benevolat.append([
            inscription.nom_complet,
            inscription.benevolat_mission or "",
            inscription.creneaux_benevolat_libelle,
            inscription.telephone or "",
            inscription.email or "",
        ])

    entete = PatternFill("solid", fgColor="E8EEF9")
    for feuille in wb.worksheets:
        feuille["A1"].font = Font(bold=True, size=13)
        for ligne in feuille.iter_rows():
            for cellule in ligne:
                valeur = cellule.value
                if isinstance(valeur, str) and valeur in (
                    "Indicateur", "Jour", "Personne", "Secteur qui fait venir",
                    "Atelier souhaité", "Année scolaire",
                ):
                    for suivante in feuille[cellule.row]:
                        suivante.font = Font(bold=True)
                        suivante.fill = entete
                        suivante.alignment = Alignment(vertical="center", wrap_text=True)
        for col in range(1, feuille.max_column + 1):
            longueurs = [
                len(str(feuille.cell(row=r, column=col).value or ""))
                for r in range(1, min(feuille.max_row, 400) + 1)
            ]
            feuille.column_dimensions[get_column_letter(col)].width = min(44, max(12, max(longueurs or [0]) + 2))

    sortie = BytesIO()
    wb.save(sortie)
    sortie.seek(0)
    return sortie
