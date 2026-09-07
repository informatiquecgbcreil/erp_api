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
    Foyer,
    InscriptionAnnuelle,
    InscriptionAnnuelleDispo,
    InscriptionAnnuelleMembre,
    Participant,
    PresenceActivite,
    SessionActivite,
)
from app.services.cotisations import (
    ETATS_REGLEMENT_LABELS,
    ETATS_REGLEMENT_TONS,
    annee_scolaire_courante,
    cotisation_existante,
    cout_inscription,
    libelle_annee_scolaire,
    regrouper_en_foyer,
    repartir_versement,
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


def appliquer_membres(inscription: InscriptionAnnuelle, lignes) -> None:
    """Remplace les autres membres du foyer déclarés sur le bulletin.

    ``lignes`` est une suite de dicts ``{prenom, nom, date_naissance,
    lien_filiation}`` (le formulaire en envoie autant que la famille en
    compte). Une ligne sans prénom est ignorée : c'est une ligne vide qu'on a
    ajoutée sans la remplir, pas une erreur à signaler.

    Un membre déjà rattaché à une fiche participant n'est jamais supprimé
    silencieusement par une modification du bulletin : on met ses champs à
    jour, sinon la fiche créée se retrouverait orpheline de son bulletin.
    """
    existants = {m.id: m for m in list(inscription.membres or [])}
    gardes: set[int] = set()
    ordre = 0

    for ligne in lignes or []:
        prenom = (ligne.get("prenom") or "").strip()
        if not prenom:
            continue
        membre_id = ligne.get("id")
        membre = existants.get(int(membre_id)) if str(membre_id or "").isdigit() else None
        if membre is None:
            membre = InscriptionAnnuelleMembre(prenom=prenom)
            inscription.membres.append(membre)
        else:
            gardes.add(membre.id)
        membre.prenom = prenom[:120]
        membre.nom = (ligne.get("nom") or "").strip()[:120] or None
        membre.date_naissance = ligne.get("date_naissance") or None
        membre.lien_filiation = (ligne.get("lien_filiation") or "").strip()[:80] or None
        membre.ordre = ordre
        ordre += 1

    for membre_id, membre in existants.items():
        if membre_id in gardes:
            continue
        if membre.participant_id:
            # Une fiche existe déjà pour cette personne : on ne l'efface pas
            # d'un coup de formulaire, on la garde en fin de liste.
            membre.ordre = ordre
            ordre += 1
            continue
        inscription.membres.remove(membre)


def personnes_couvertes(inscription: InscriptionAnnuelle) -> list[dict]:
    """Toutes les personnes que le bulletin fait payer, l'inscrit·e en tête.

    Chaque entrée : ``{nom_complet, date_naissance, lien, participant_id,
    principal}``. C'est la liste qui multiplie la participation et celle qui
    peuple le foyer."""
    personnes = [{
        "nom_complet": inscription.nom_complet,
        "date_naissance": inscription.date_naissance,
        "lien": None,
        "participant_id": inscription.participant_id,
        "principal": True,
    }]
    for membre in inscription.membres or []:
        personnes.append({
            "nom_complet": membre.nom_complet,
            "date_naissance": membre.date_naissance,
            "lien": membre.lien_filiation,
            "participant_id": membre.participant_id,
            "principal": False,
        })
    return personnes


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


def _fiche_existante_pour_membre(membre: InscriptionAnnuelleMembre) -> Participant | None:
    """Fiche déjà connue pour ce membre, ou None.

    On ne rattache que sur un signal FORT : mêmes nom et prénom **et** même
    date de naissance. Un homonyme sans date de naissance reste une personne
    différente — mieux vaut une fiche à fusionner ensuite (l'application sait
    le faire) qu'un enfant rattaché à la mauvaise famille.
    """
    from app.services.doublons import candidats_doublons

    if not membre.date_naissance:
        return None
    for candidat in candidats_doublons(membre.nom_effectif(), membre.prenom):
        if candidat.date_naissance == membre.date_naissance:
            return candidat
    return None


def _creer_fiches_membres(
    inscription: InscriptionAnnuelle,
    *,
    user_id: int | None,
    secteur: str | None,
    quartier_id: int | None,
) -> list[str]:
    """Ouvre (ou retrouve) une fiche participant pour chaque membre déclaré.

    Les coordonnées du foyer sont recopiées — adresse, ville, téléphone : à
    l'accueil, le téléphone d'un enfant est celui de son parent. L'e-mail,
    lui, reste sur la fiche de l'inscrit·e principal·e : il est personnel et
    sert aux envois individuels.
    """
    avertissements: list[str] = []
    for membre in inscription.membres or []:
        if membre.participant_id:
            continue

        existante = _fiche_existante_pour_membre(membre)
        if existante is not None:
            membre.participant_id = existante.id
            avertissements.append(
                f"{membre.nom_complet} a été rattaché·e à sa fiche existante (n°{existante.id})."
            )
            continue

        fiche = Participant(
            nom=membre.nom_effectif() or inscription.nom,
            prenom=(membre.prenom or "").strip(),
            date_naissance=membre.date_naissance,
            adresse=(inscription.adresse or "").strip() or None,
            ville=(inscription.ville or "").strip() or None,
            telephone=(inscription.telephone or "").strip() or None,
            statut_inscription=STATUT_PARTICIPANT_ATTENTE,
            created_by_user_id=user_id,
            created_secteur=(secteur or inscription.secteur_orienteur or inscription.created_secteur or None),
        )
        if quartier_id:
            from app.services.quartiers import normalize_quartier_for_ville

            fiche.quartier_id = normalize_quartier_for_ville(fiche.ville, quartier_id)
        db.session.add(fiche)
        db.session.flush()
        membre.participant_id = fiche.id
    return avertissements


def _constituer_foyer(inscription: InscriptionAnnuelle) -> list[str]:
    """Regroupe l'inscrit·e et les membres dans un même foyer.

    Réutilise ``regrouper_en_foyer`` du module Adhésions — celui-là même que
    l'on déclenche à la main depuis une fiche participant — pour que la
    famille composée à l'inscription soit strictement la même chose qu'une
    famille composée après coup. Sa prudence s'applique donc aussi : il
    refuse de fusionner deux familles déjà constituées, et on remonte alors
    son message plutôt que de mélanger des adhésions déjà réglées.
    """
    fiches = participants_couverts(inscription)
    if not fiches:
        return []

    if len(fiches) == 1:
        # Famille déclarée mais un seul membre a une fiche : on ouvre quand
        # même le foyer, il accueillera les autres au fur et à mesure.
        fiche = fiches[0]
        if not fiche.foyer_id:
            foyer = Foyer(nom=f"Famille {fiche.nom}")
            db.session.add(foyer)
            db.session.flush()
            fiche.foyer_id = foyer.id
        inscription.foyer_id = fiche.foyer_id
        db.session.flush()
        return []

    ok, message = regrouper_en_foyer(fiches)
    if not ok:
        return [message]
    inscription.foyer_id = fiches[0].foyer_id
    db.session.flush()
    return []


def creer_participant(
    inscription: InscriptionAnnuelle,
    *,
    user_id: int | None = None,
    secteur: str | None = None,
    quartier_id: int | None = None,
    marquer_benevole: bool = False,
    inscrire_ateliers: bool = True,
    creer_cotisations: bool = True,
) -> tuple[Participant, list[str]]:
    """Crée la fiche participant en attente de 1re participation.

    La fiche est immédiatement utilisable partout dans l'application (elle
    apparaît dans l'annuaire, l'émargement, les recherches) mais porte le
    statut ``attente_premiere_participation`` tant que personne ne l'a
    pointée présente.

    Pour une inscription **familiale**, la même opération ouvre une fiche par
    membre déclaré et regroupe tout le monde dans un ``Foyer`` — le même objet
    que celui composé à la main depuis une fiche participant. Les cotisations
    (adhésion + une participation par personne) sont générées dans la foulée,
    sauf demande contraire."""
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
    if inscription.est_familiale:
        avertissements += _creer_fiches_membres(
            inscription, user_id=user_id, secteur=secteur, quartier_id=quartier_id
        )
        avertissements += _constituer_foyer(inscription)
        db.session.commit()

    if inscrire_ateliers:
        _, messages = _inscrire_aux_ateliers(inscription, participant, user_id)
        avertissements += messages

    if creer_cotisations:
        _, messages = generer_cotisations(inscription, user_id=user_id)
        avertissements += messages

    return participant, avertissements


def rattacher_participant(
    inscription: InscriptionAnnuelle,
    participant: Participant,
    *,
    user_id: int | None = None,
    inscrire_ateliers: bool = True,
    creer_cotisations: bool = True,
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

    avertissements: list[str] = []
    if inscription.est_familiale:
        avertissements += _creer_fiches_membres(
            inscription, user_id=user_id,
            secteur=(inscription.secteur_orienteur or participant.created_secteur),
            quartier_id=participant.quartier_id,
        )
        avertissements += _constituer_foyer(inscription)
        db.session.commit()

    if inscrire_ateliers:
        _, messages = _inscrire_aux_ateliers(inscription, participant, user_id)
        avertissements += messages

    if creer_cotisations:
        _, messages = generer_cotisations(inscription, user_id=user_id)
        avertissements += messages

    return avertissements


# ---------------------------------------------------------------------------
# Coût de l'inscription et règlement
# ---------------------------------------------------------------------------

def calculer_cout(inscription: InscriptionAnnuelle, a_la_date: date | None = None) -> dict:
    """Ce que doit payer ce bulletin : adhésion + participation par personne.

    Le détail vient du barème du module Adhésions, lu **à la date de
    référence** (par défaut la date d'inscription) : c'est ainsi qu'un tarif
    revu en cours d'année s'applique aux nouvelles inscriptions sans toucher
    à celles déjà enregistrées."""
    return cout_inscription(
        inscription.annee_scolaire,
        familiale=inscription.est_familiale,
        nb_personnes=inscription.nb_personnes,
        a_la_date=a_la_date or inscription.date_inscription or date.today(),
    )


def montant_adhesion_suggere(annee_scolaire: int, a_la_date: date | None = None) -> float | None:
    """Tarif d'adhésion individuelle en vigueur (None si aucun barème saisi)."""
    ligne = tarif_en_vigueur(annee_scolaire, "adhesion_individuelle", a_la_date)
    return float(ligne.montant) if ligne is not None else None


def participants_couverts(inscription: InscriptionAnnuelle) -> list[Participant]:
    """Fiches participant existantes du bulletin : l'inscrit·e et les membres."""
    ids = [p["participant_id"] for p in personnes_couvertes(inscription) if p["participant_id"]]
    if not ids:
        return []
    fiches = {p.id: p for p in Participant.query.filter(Participant.id.in_(ids)).all()}
    return [fiches[i] for i in ids if i in fiches]


def cotisations_du_bulletin(inscription: InscriptionAnnuelle) -> list[Cotisation]:
    """Les cotisations de l'année qui matérialisent ce bulletin.

    L'adhésion (portée par la personne ou par le foyer) d'abord, puis les
    participations dans l'ordre des personnes : c'est l'ordre dans lequel un
    versement partiel se ventile."""
    ids = [p["participant_id"] for p in personnes_couvertes(inscription) if p["participant_id"]]
    if not ids and not inscription.foyer_id:
        return []

    conditions = []
    if ids:
        conditions.append(Cotisation.participant_id.in_(ids))
    if inscription.foyer_id:
        conditions.append(Cotisation.foyer_id == inscription.foyer_id)

    lignes = (
        Cotisation.query
        .filter(Cotisation.annee_scolaire == inscription.annee_scolaire)
        .filter(db.or_(*conditions))
        .all()
    )
    rang_personne = {pid: i for i, pid in enumerate(ids)}

    def cle(c: Cotisation):
        est_adhesion = 0 if c.type_cotisation != "participation" else 1
        return (est_adhesion, rang_personne.get(c.participant_id or -1, 99), c.id)

    return sorted(lignes, key=cle)


def etat_reglement(inscription: InscriptionAnnuelle) -> dict:
    """Où en est ce bulletin : rien / partiel / complet, avec les montants.

    Deux sources selon l'avancement, jamais les deux à la fois :
    - **les cotisations** dès qu'elles existent (le module Adhésions fait
      foi : un versement saisi depuis une fiche participant compte ici) ;
    - **le bulletin lui-même** avant la transformation, quand l'accueil a
      encaissé sans qu'aucune fiche n'existe encore.
    """
    cotisations = cotisations_du_bulletin(inscription)
    if cotisations:
        du = round(sum(float(c.montant_du or 0) for c in cotisations), 2)
        regle = round(sum(c.montant_regle for c in cotisations), 2)
        source = "cotisations"
    else:
        du = calculer_cout(inscription)["total"]
        regle = round(float(inscription.reglement_montant or 0), 2)
        source = "bulletin"

    reste = round(max(0.0, du - regle), 2)
    if du <= 0.009 and regle <= 0.009:
        statut = "rien"
    elif reste <= 0.009:
        statut = "complet"
    elif regle > 0.009:
        statut = "partiel"
    else:
        statut = "rien"

    return {
        "statut": statut,
        "libelle": ETATS_REGLEMENT_LABELS[statut],
        "ton": ETATS_REGLEMENT_TONS[statut],
        "du": du,
        "regle": regle,
        "reste": reste,
        "source": source,
        "cotisations": cotisations,
    }


def resynchroniser_reglement(inscription: InscriptionAnnuelle) -> dict:
    """Recopie l'état de règlement sur le bulletin (sans commit).

    Les colonnes ``reglement_statut`` / ``reglement_du`` /
    ``reglement_montant`` / ``reglement_confirme`` sont un miroir : elles
    permettent de filtrer et d'exporter sans recalculer, mais la vérité
    reste dans les cotisations. Ce recalage est rejoué à l'ouverture de la
    liste, pour qu'un règlement saisi depuis une fiche participant se voie
    ici aussi."""
    etat = etat_reglement(inscription)
    inscription.reglement_statut = etat["statut"]
    inscription.reglement_du = etat["du"]
    if etat["source"] == "cotisations":
        inscription.reglement_montant = etat["regle"]
    inscription.reglement_confirme = etat["statut"] == "complet"
    return etat


def generer_cotisations(
    inscription: InscriptionAnnuelle,
    *,
    user_id: int | None = None,
    a_la_date: date | None = None,
) -> tuple[list[Cotisation], list[str]]:
    """Crée dans le module Adhésions ce que le bulletin doit générer.

    Une adhésion (familiale portée par le foyer, individuelle portée par la
    personne) et **une participation par personne ayant une fiche**. Les
    montants sont figés au tarif en vigueur à la date de référence — un
    changement de barème plus tard ne réécrit jamais une dette déjà posée.

    Idempotent : relancer la génération ne crée pas de doublon, elle complète
    ce qui manque (utile quand un membre reçoit sa fiche après coup).
    """
    if not inscription.participant_id:
        raise InscriptionAnnuelleErreur(
            "Crée d'abord la fiche participant : sans elle, il n'y a personne à qui rattacher l'adhésion."
        )

    a_la_date = a_la_date or inscription.date_inscription or date.today()
    cout = calculer_cout(inscription, a_la_date)
    avertissements: list[str] = []
    if cout["manquants"]:
        libelles = ", ".join(cout["manquants"])
        avertissements.append(
            f"Aucun tarif {libelles} au barème {inscription.libelle_annee} : "
            "le montant correspondant est à 0 €, à corriger depuis la fiche participant "
            "ou en complétant le barème des tarifs."
        )

    creees: list[Cotisation] = []

    # --- Adhésion ---------------------------------------------------------
    if inscription.est_familiale and inscription.foyer_id:
        adhesion = cotisation_existante(
            annee_scolaire=inscription.annee_scolaire,
            type_cotisation="adhesion_familiale",
            foyer_id=inscription.foyer_id,
        )
        if adhesion is None:
            adhesion = Cotisation(
                annee_scolaire=inscription.annee_scolaire,
                type_cotisation="adhesion_familiale",
                foyer_id=inscription.foyer_id,
                montant_du=cout["montant_adhesion"],
                date_reference=a_la_date,
                created_by_user_id=user_id,
            )
            db.session.add(adhesion)
            creees.append(adhesion)
    else:
        montant_adhesion = cout["montant_adhesion"]
        if inscription.est_familiale:
            # Repli : le bulletin dit « familiale » mais aucun foyer n'a été
            # constitué (aucun autre membre n'a de fiche). On facture au tarif
            # individuel plutôt que d'appliquer un tarif famille à une personne
            # seule — et on le dit.
            avertissements.append(
                "Inscription familiale sans foyer constitué : l'adhésion a été enregistrée "
                "au tarif individuel. Regroupe les membres depuis la fiche participant pour "
                "basculer sur le tarif familial."
            )
            ligne = tarif_en_vigueur(inscription.annee_scolaire, "adhesion_individuelle", a_la_date)
            montant_adhesion = round(float(ligne.montant), 2) if ligne else 0.0

        adhesion = cotisation_existante(
            annee_scolaire=inscription.annee_scolaire,
            type_cotisation="adhesion_individuelle",
            participant_id=inscription.participant_id,
        )
        if adhesion is None:
            adhesion = Cotisation(
                annee_scolaire=inscription.annee_scolaire,
                type_cotisation="adhesion_individuelle",
                participant_id=inscription.participant_id,
                montant_du=montant_adhesion,
                date_reference=a_la_date,
                created_by_user_id=user_id,
            )
            db.session.add(adhesion)
            creees.append(adhesion)

    db.session.flush()
    inscription.cotisation_id = adhesion.id

    # --- Une participation par personne ayant une fiche -------------------
    for fiche in participants_couverts(inscription):
        existante = cotisation_existante(
            annee_scolaire=inscription.annee_scolaire,
            type_cotisation="participation",
            participant_id=fiche.id,
        )
        if existante is not None:
            continue
        participation = Cotisation(
            annee_scolaire=inscription.annee_scolaire,
            type_cotisation="participation",
            participant_id=fiche.id,
            montant_du=cout["montant_participation_unitaire"],
            date_reference=a_la_date,
            created_by_user_id=user_id,
        )
        db.session.add(participation)
        creees.append(participation)

    db.session.flush()

    # --- Report d'un encaissement fait avant la transformation ------------
    deja_verse = round(float(inscription.reglement_montant or 0), 2)
    if deja_verse > 0 and not any(c.paiements for c in cotisations_du_bulletin(inscription)):
        repartir_versement(
            cotisations_du_bulletin(inscription),
            deja_verse,
            date_paiement=inscription.reglement_date or a_la_date,
            mode=inscription.reglement_mode or "especes",
            commentaire=f"Inscription annuelle {inscription.libelle_annee}",
            user_id=user_id,
        )

    resynchroniser_reglement(inscription)
    db.session.commit()
    return creees, avertissements


def encaisser(
    inscription: InscriptionAnnuelle,
    montant: float,
    *,
    mode: str | None = None,
    date_paiement: date | None = None,
    commentaire: str | None = None,
    user_id: int | None = None,
) -> tuple[float, str]:
    """Enregistre une somme reçue à l'accueil — totale ou partielle.

    Quand les cotisations existent, la somme se ventile dessus (adhésion
    d'abord, puis les participations) : le règlement remonte dans les
    impayés, la caisse et les bilans sans double saisie. Sinon elle
    s'accumule sur le bulletin en attendant la fiche participant, et sera
    reportée telle quelle à la génération des cotisations.

    Retourne (montant réellement encaissé, message).
    """
    montant = round(float(montant or 0), 2)
    if montant <= 0:
        raise InscriptionAnnuelleErreur("Le montant encaissé doit être supérieur à 0 €.")

    mode = (mode or "especes").strip() or "especes"
    date_paiement = date_paiement or date.today()
    inscription.reglement_mode = mode
    inscription.reglement_date = date_paiement
    if commentaire:
        inscription.reglement_commentaire = commentaire[:255]

    cotisations = cotisations_du_bulletin(inscription)
    if cotisations:
        versements = repartir_versement(
            cotisations, montant,
            date_paiement=date_paiement, mode=mode,
            commentaire=commentaire or f"Inscription annuelle {inscription.libelle_annee}",
            user_id=user_id,
        )
        encaisse = round(sum(float(v.montant) for v in versements), 2)
        message = f"{encaisse:.2f} € encaissés et ventilés sur {len(versements)} cotisation(s)."
        if encaisse < montant:
            message += (
                f" {montant - encaisse:.2f} € n'ont pas été affectés : il ne restait plus rien à devoir. "
                "Vérifie le montant saisi."
            )
    else:
        inscription.reglement_montant = round(float(inscription.reglement_montant or 0) + montant, 2)
        encaisse = montant
        message = (
            f"{montant:.2f} € enregistrés sur le bulletin. "
            "Ils seront reportés dans le module Adhésions à la création de la fiche participant."
        )

    etat = resynchroniser_reglement(inscription)
    db.session.commit()
    if etat["statut"] == "complet":
        message += " L'inscription est intégralement réglée."
    elif etat["statut"] == "partiel":
        message += f" Reste dû : {etat['reste']:.2f} €."
    return encaisse, message


def annuler_reglement(inscription: InscriptionAnnuelle) -> None:
    """Repasse le bulletin en « rien réglé ».

    Ne touche PAS aux versements déjà enregistrés dans le module Adhésions :
    un mouvement de caisse s'annule là où il est journalisé, pas d'ici. Ce
    bouton ne sert donc qu'aux encaissements notés sur le bulletin avant
    qu'une fiche participant existe."""
    if cotisations_du_bulletin(inscription):
        raise InscriptionAnnuelleErreur(
            "Les règlements de cette inscription sont enregistrés dans le module "
            "Adhésions & participation : annule le versement depuis la fiche "
            "participant, pour que la caisse et les bilans restent justes."
        )
    inscription.reglement_montant = None
    inscription.reglement_date = None
    resynchroniser_reglement(inscription)
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


def rafraichir_reglements(annee_scolaire: int | None = None) -> int:
    """Recale l'état de règlement des bulletins de l'année (sans rien créer).

    Un versement saisi depuis une fiche participant ne passe pas par ce
    module : ce balayage, joué à l'ouverture de la liste, garantit que la
    colonne « Règlement » y dit la même chose que la fiche."""
    q = InscriptionAnnuelle.query.filter(InscriptionAnnuelle.statut != "annulee")
    if annee_scolaire is not None:
        q = q.filter(InscriptionAnnuelle.annee_scolaire == annee_scolaire)

    recales = 0
    for inscription in q.all():
        avant = (inscription.reglement_statut, inscription.reglement_du, inscription.reglement_montant)
        resynchroniser_reglement(inscription)
        if (inscription.reglement_statut, inscription.reglement_du, inscription.reglement_montant) != avant:
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
        "regles": sum(1 for i in actives if i.reglement_statut == "complet"),
        "partiels": sum(1 for i in actives if i.reglement_statut == "partiel"),
        "non_regles": sum(1 for i in actives if i.reglement_statut not in ("complet", "partiel")),
        "montant_du": round(sum(float(i.reglement_du or 0) for i in actives), 2),
        "montant_regle": round(sum(float(i.reglement_montant or 0) for i in actives), 2),
        "montant_reste": round(sum(i.reglement_reste for i in actives), 2),
        "familiales": sum(1 for i in actives if i.est_familiale),
        "personnes_couvertes": sum(i.nb_personnes for i in actives),
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
    "Type d'inscription", "Personnes couvertes", "Membres du foyer", "N° de foyer",
    "Ateliers choisis", "Autres souhaits (champ libre)",
    "Bénévolat souhaité", "Bénévolat — pour quoi faire",
    "Bénévolat — disponibilités", "Bénévolat — ne sait pas encore",
    "Adhésion (€)", "Participation unitaire (€)", "Participation totale (€)",
    "Total dû (€)", "Montant réglé (€)", "Reste dû (€)", "État du règlement",
    "Mode de règlement", "Date de règlement", "Note sur le règlement",
    "Fiche participant", "N° de fiche",
    "1re participation", "Commentaire", "Saisi par", "Saisi le",
]


def _libelle_membre(membre: InscriptionAnnuelleMembre) -> str:
    """« Léa Martin (12/03/2015, fille) » — tout ce qu'on sait, sans les vides."""
    details = []
    if membre.date_naissance:
        details.append(membre.date_naissance.strftime("%d/%m/%Y"))
    if membre.lien_filiation:
        details.append(membre.lien_filiation)
    suffixe = f" ({', '.join(details)})" if details else ""
    return f"{membre.nom_complet}{suffixe}"


def _ligne_export(inscription: InscriptionAnnuelle, utilisateurs: dict[int, str]) -> list:
    from app.models import MODES_PAIEMENT_LABELS

    cout = calculer_cout(inscription)
    etat = etat_reglement(inscription)

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
        inscription.type_inscription_label,
        inscription.nb_personnes,
        " ; ".join(_libelle_membre(m) for m in inscription.membres or []),
        inscription.foyer_id or "",
        " ; ".join(a.nom for a in sorted(inscription.ateliers or [], key=lambda a: a.nom)),
        inscription.ateliers_libre or "",
        "Oui" if inscription.benevolat_souhaite else "Non",
        inscription.benevolat_mission or "",
        inscription.creneaux_benevolat_libelle if inscription.benevolat_souhaite else "",
        "Oui" if inscription.benevolat_dispo_inconnue else "",
        cout["montant_adhesion"],
        cout["montant_participation_unitaire"],
        cout["montant_participation_total"],
        etat["du"],
        etat["regle"],
        etat["reste"],
        etat["libelle"],
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
        ("dont inscriptions familiales", data["familiales"]),
        ("Personnes couvertes (participation)", data["personnes_couvertes"]),
        ("Sans fiche participant", data["sans_fiche"]),
        ("En attente de 1re participation", data["en_attente"]),
        ("Intégralement réglées", data["regles"]),
        ("Partiellement réglées", data["partiels"]),
        ("Non réglées", data["non_regles"]),
        ("Total dû (€)", data["montant_du"]),
        ("Total encaissé (€)", data["montant_regle"]),
        ("Reste à encaisser (€)", data["montant_reste"]),
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

    foyers = wb.create_sheet("Foyers")
    foyers.append([f"Composition des foyers — {data['libelle_annee']}"])
    foyers.append([])
    foyers.append([
        "Inscription", "Type", "N° de foyer", "Personne", "Rôle", "Lien de filiation",
        "Date de naissance", "Âge", "N° de fiche",
    ])
    for inscription in inscriptions:
        if inscription.statut == "annulee":
            continue
        foyers.append([
            inscription.nom_complet, inscription.type_inscription_label,
            inscription.foyer_id or "", inscription.nom_complet, "Inscrit·e principal·e", "",
            inscription.date_naissance.isoformat() if inscription.date_naissance else "",
            "", inscription.participant_id or "",
        ])
        for membre in inscription.membres or []:
            foyers.append([
                inscription.nom_complet, inscription.type_inscription_label,
                inscription.foyer_id or "", membre.nom_complet, "Membre du foyer",
                membre.lien_filiation or "",
                membre.date_naissance.isoformat() if membre.date_naissance else "",
                membre.age if membre.age is not None else "",
                membre.participant_id or "",
            ])

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
                    "Atelier souhaité", "Année scolaire", "Inscription",
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
