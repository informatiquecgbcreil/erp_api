"""Adhésions, participation, foyers : logique métier partagée.

Règles :
- l'année scolaire court de septembre à août ; elle est identifiée par
  son année de rentrée (2026 -> libellé « 2026-2027 ») ;
- le tarif « en vigueur » à une date donnée est la ligne de barème la plus
  récente (par date de début) qui ne dépasse pas cette date — permet de
  faire évoluer un prix en cours d'année (ex. participation moins chère
  en fin d'année scolaire) ;
- le montant dû d'une cotisation est figé à sa création (photo du tarif à
  la date de référence) : un changement de barème plus tard ne modifie
  jamais rétroactivement une dette déjà enregistrée.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from app.extensions import db
from app.models import TYPES_TARIF_LABELS, Cotisation, Foyer, Participant, TarifBareme


def annee_scolaire_de(d: date) -> int:
    """Année de rentrée de la date donnée (septembre à août)."""
    return d.year if d.month >= 9 else d.year - 1


def annee_scolaire_courante() -> int:
    return annee_scolaire_de(date.today())


def libelle_annee_scolaire(annee: int) -> str:
    return f"{annee}-{annee + 1}"


def annees_scolaires_disponibles() -> list[int]:
    """Les années scolaires pour lesquelles il existe déjà des données,
    plus l'année courante — pour peupler les sélecteurs."""
    annees = {annee_scolaire_courante()}
    for (a,) in db.session.query(TarifBareme.annee_scolaire).distinct():
        annees.add(a)
    for (a,) in db.session.query(Cotisation.annee_scolaire).distinct():
        annees.add(a)
    return sorted(annees, reverse=True)


def tarif_en_vigueur(annee_scolaire: int, type_tarif: str, a_la_date: date | None = None) -> Optional[TarifBareme]:
    """La ligne de barème applicable à la date donnée (par défaut aujourd'hui)."""
    a_la_date = a_la_date or date.today()
    return (
        TarifBareme.query
        .filter(
            TarifBareme.annee_scolaire == annee_scolaire,
            TarifBareme.type_tarif == type_tarif,
            TarifBareme.date_debut <= a_la_date,
        )
        .order_by(TarifBareme.date_debut.desc())
        .first()
    )


def bareme_annee(annee_scolaire: int) -> dict[str, list[TarifBareme]]:
    """Toutes les lignes de barème de l'année, groupées par type, triées
    par date de début (la plus récente d'abord)."""
    lignes = (
        TarifBareme.query
        .filter(TarifBareme.annee_scolaire == annee_scolaire)
        .order_by(TarifBareme.date_debut.desc())
        .all()
    )
    groupes: dict[str, list[TarifBareme]] = {"adhesion_individuelle": [], "adhesion_familiale": [], "participation": []}
    for ligne in lignes:
        groupes.setdefault(ligne.type_tarif, []).append(ligne)
    return groupes


def cotisation_existante(*, annee_scolaire: int, type_cotisation: str,
                         participant_id: int | None = None, foyer_id: int | None = None) -> Cotisation | None:
    q = Cotisation.query.filter(
        Cotisation.annee_scolaire == annee_scolaire,
        Cotisation.type_cotisation == type_cotisation,
    )
    if participant_id is not None:
        q = q.filter(Cotisation.participant_id == participant_id)
    if foyer_id is not None:
        q = q.filter(Cotisation.foyer_id == foyer_id)
    return q.first()


def cotisations_du_participant(participant: Participant, *, inclure_foyer: bool = True) -> list[Cotisation]:
    """Cotisations propres au participant + (si demandé) celles de son
    foyer (adhésion familiale), triées année décroissante puis type."""
    items = list(Cotisation.query.filter(Cotisation.participant_id == participant.id).all())
    if inclure_foyer and participant.foyer_id:
        items += list(Cotisation.query.filter(Cotisation.foyer_id == participant.foyer_id).all())
    ordre_type = {"adhesion_individuelle": 0, "adhesion_familiale": 0, "participation": 1}
    items.sort(key=lambda c: (-c.annee_scolaire, ordre_type.get(c.type_cotisation, 9)))
    return items


def foyer_membres_autres(participant: Participant) -> list[Participant]:
    """Les autres membres du foyer du participant (liste vide si aucun foyer)."""
    if not participant.foyer_id:
        return []
    return [
        m for m in Participant.query.filter(Participant.foyer_id == participant.foyer_id).all()
        if m.id != participant.id
    ]


def rapprocher_foyer(a: Participant, b: Participant) -> tuple[bool, str]:
    """Rattache b au foyer de a (ou l'inverse), en créant un foyer si besoin.

    Refuse si les deux appartiennent déjà à des foyers DIFFÉRENTS non vides
    (fusion non gérée automatiquement, pour ne pas mélanger silencieusement
    des adhésions familiales déjà réglées de deux familles distinctes)."""
    if a.id == b.id:
        return False, "Impossible de rapprocher une personne avec elle-même."
    if a.foyer_id and b.foyer_id and a.foyer_id != b.foyer_id:
        return False, (
            "Ces deux personnes appartiennent déjà à des foyers différents. "
            "Retire d'abord l'une d'elles de son foyer actuel avant de les rapprocher."
        )
    foyer = None
    if a.foyer_id:
        foyer = a.foyer
    elif b.foyer_id:
        foyer = b.foyer
    if foyer is None:
        foyer = Foyer(nom=f"Famille {a.nom}")
        db.session.add(foyer)
        db.session.flush()
    a.foyer_id = foyer.id
    b.foyer_id = foyer.id
    db.session.commit()
    return True, f"{b.nom} {b.prenom} est maintenant rapproché·e du foyer de {a.nom} {a.prenom}."


def regrouper_en_foyer(membres: list[Participant]) -> tuple[bool, str]:
    """Regroupe plusieurs personnes dans un même foyer (famille).

    Même règle de prudence que ``rapprocher_foyer`` : si la sélection couvre
    déjà DEUX foyers différents, on refuse (fusionner deux familles peut
    mélanger des adhésions familiales déjà réglées — l'agent doit d'abord
    détacher les personnes concernées). Un seul foyer existant : tout le
    monde le rejoint. Aucun foyer : on en crée un.
    """
    membres = [m for m in membres if m is not None]
    if len(membres) < 2:
        return False, "Sélectionne au moins deux personnes pour former une famille."

    foyers_existants = {m.foyer_id for m in membres if m.foyer_id}
    if len(foyers_existants) > 1:
        return False, (
            "La sélection contient des personnes de plusieurs familles différentes. "
            "Détache d'abord celles qui changent de famille (depuis leur fiche, "
            "partie Adhésion), puis recommence."
        )

    if foyers_existants:
        foyer = db.session.get(Foyer, foyers_existants.pop())
    else:
        foyer = Foyer(nom=f"Famille {membres[0].nom}")
        db.session.add(foyer)
        db.session.flush()

    nouveaux = 0
    for m in membres:
        if m.foyer_id != foyer.id:
            m.foyer_id = foyer.id
            nouveaux += 1
    db.session.commit()

    noms = ", ".join(f"{m.prenom} {m.nom}" for m in membres[:4])
    if len(membres) > 4:
        noms += f" (+{len(membres) - 4})"
    return True, f"Famille « {foyer.nom or 'sans nom'} » : {len(membres)} membres ({noms})."


def detacher_du_foyer(participant: Participant) -> None:
    """Retire le participant de son foyer (ne supprime pas les autres membres)."""
    foyer_id = participant.foyer_id
    participant.foyer_id = None
    db.session.commit()
    if foyer_id is not None:
        autres = Participant.query.filter(Participant.foyer_id == foyer_id).count()
        if autres == 0:
            foyer = db.session.get(Foyer, foyer_id)
            if foyer is not None and not foyer.cotisations:
                db.session.delete(foyer)
                db.session.commit()


# ---------------------------------------------------------------------------
# Coût d'une inscription : adhésion + participation par personne
# ---------------------------------------------------------------------------

def cout_inscription(
    annee_scolaire: int,
    *,
    familiale: bool,
    nb_personnes: int,
    a_la_date: date | None = None,
) -> dict:
    """Ce que coûte une inscription pour l'année scolaire.

    Deux étages, tels que la structure les facture :

    - **l'adhésion**, une fois : au tarif ``adhesion_individuelle`` pour une
      personne seule, ``adhesion_familiale`` pour un foyer ;
    - **la participation**, une fois PAR PERSONNE du foyer.

    Exemple avec un barème à 7 € / 10 € / 30 € : une personne seule paye
    7 + 30 = 37 € ; une mère et son enfant payent 10 + 30 × 2 = 70 €.

    Les trois montants sont lus dans le barème **à la date de référence** :
    c'est là que se joue la proratisation en cours d'année. Une ligne de
    barème qui démarre au 1er janvier remplace celle de septembre pour toute
    inscription postérieure, et chaque type se prorate indépendamment (on
    peut baisser la participation sans toucher à l'adhésion).

    Un tarif absent du barème ne bloque rien : le montant vaut 0 € et le type
    concerné est signalé dans ``manquants``, à l'appelant de le dire.
    """
    a_la_date = a_la_date or date.today()
    nb_personnes = max(1, int(nb_personnes or 1))
    type_adhesion = "adhesion_familiale" if familiale else "adhesion_individuelle"

    tarif_adhesion = tarif_en_vigueur(annee_scolaire, type_adhesion, a_la_date)
    tarif_participation = tarif_en_vigueur(annee_scolaire, "participation", a_la_date)

    montant_adhesion = round(float(tarif_adhesion.montant), 2) if tarif_adhesion else 0.0
    montant_participation = round(float(tarif_participation.montant), 2) if tarif_participation else 0.0

    manquants = []
    if tarif_adhesion is None:
        manquants.append(type_adhesion)
    if tarif_participation is None:
        manquants.append("participation")

    lignes = [{
        "type_tarif": type_adhesion,
        "libelle": TYPES_TARIF_LABELS.get(type_adhesion, type_adhesion),
        "quantite": 1,
        "prix_unitaire": montant_adhesion,
        "total": montant_adhesion,
        "depuis": tarif_adhesion.date_debut if tarif_adhesion else None,
    }, {
        "type_tarif": "participation",
        "libelle": TYPES_TARIF_LABELS.get("participation", "Participation"),
        "quantite": nb_personnes,
        "prix_unitaire": montant_participation,
        "total": round(montant_participation * nb_personnes, 2),
        "depuis": tarif_participation.date_debut if tarif_participation else None,
    }]

    return {
        "annee_scolaire": annee_scolaire,
        "libelle_annee": libelle_annee_scolaire(annee_scolaire),
        "date_reference": a_la_date,
        "familiale": bool(familiale),
        "type_adhesion": type_adhesion,
        "nb_personnes": nb_personnes,
        "montant_adhesion": montant_adhesion,
        "montant_participation_unitaire": montant_participation,
        "montant_participation_total": round(montant_participation * nb_personnes, 2),
        "lignes": lignes,
        "total": round(montant_adhesion + montant_participation * nb_personnes, 2),
        "manquants": manquants,
    }


# ---------------------------------------------------------------------------
# Où en est une personne de son règlement ?
# ---------------------------------------------------------------------------

#: Les trois états visibles à l'accueil et à l'émargement, plus le cas « rien
#: n'est dû » (aucune cotisation enregistrée pour l'année).
ETATS_REGLEMENT_LABELS = {
    "complet": "À jour",
    "partiel": "Partiellement réglé",
    "rien": "Non réglé",
    "aucune": "Aucune cotisation",
}
ETATS_REGLEMENT_TONS = {
    "complet": "ok",
    "partiel": "warn",
    "rien": "danger",
    "aucune": "neutre",
}


def etat_reglement_participant(participant, annee_scolaire: int | None = None) -> dict:
    """Où en est cette personne de son règlement pour l'année scolaire.

    Additionne TOUT ce qu'elle doit pour l'année : ses cotisations propres
    (adhésion individuelle, participation) et celles de son foyer (adhésion
    familiale, qui la couvre). Le résultat sert d'affichage partagé — fiche
    participant, émargement, listes — pour que la même personne ne soit
    jamais « à jour » ici et « impayée » là.

    ``statut`` vaut ``complet`` (tout réglé), ``partiel`` (au moins un euro
    versé, reste dû), ``rien`` (une somme est due, aucun versement) ou
    ``aucune`` (aucune cotisation enregistrée : rien à réclamer, et surtout
    pas un impayé).
    """
    annee_scolaire = annee_scolaire if annee_scolaire is not None else annee_scolaire_courante()
    cotisations = [
        c for c in cotisations_du_participant(participant)
        if c.annee_scolaire == annee_scolaire
    ]

    du = round(sum(float(c.montant_du or 0) for c in cotisations), 2)
    regle = round(sum(c.montant_regle for c in cotisations), 2)
    reste = round(max(0.0, du - regle), 2)

    if not cotisations:
        statut = "aucune"
    elif reste <= 0.009:
        statut = "complet"
    elif regle > 0.009:
        statut = "partiel"
    else:
        statut = "rien"

    return {
        "annee_scolaire": annee_scolaire,
        "libelle_annee": libelle_annee_scolaire(annee_scolaire),
        "statut": statut,
        "libelle": ETATS_REGLEMENT_LABELS[statut],
        "ton": ETATS_REGLEMENT_TONS[statut],
        "du": du,
        "regle": regle,
        "reste": reste,
        "cotisations": cotisations,
    }


def etats_reglement_par_participant(participant_ids, annee_scolaire: int | None = None) -> dict[int, dict]:
    """Même chose pour une liste de personnes, en un seul aller-retour.

    Pensé pour l'émargement : afficher l'état de vingt présents ne doit pas
    coûter vingt fois trois requêtes."""
    from app.models import Cotisation, Participant

    annee_scolaire = annee_scolaire if annee_scolaire is not None else annee_scolaire_courante()
    ids = [int(i) for i in participant_ids if i]
    if not ids:
        return {}

    participants = Participant.query.filter(Participant.id.in_(ids)).all()
    foyer_ids = {p.foyer_id for p in participants if p.foyer_id}

    conditions = [Cotisation.participant_id.in_(ids)]
    if foyer_ids:
        conditions.append(Cotisation.foyer_id.in_(list(foyer_ids)))
    lignes = (
        Cotisation.query
        .filter(Cotisation.annee_scolaire == annee_scolaire)
        .filter(db.or_(*conditions))
        .all()
    )

    par_participant: dict[int, list] = {i: [] for i in ids}
    par_foyer: dict[int, list] = {}
    for c in lignes:
        if c.participant_id and c.participant_id in par_participant:
            par_participant[c.participant_id].append(c)
        elif c.foyer_id:
            par_foyer.setdefault(c.foyer_id, []).append(c)

    resultats: dict[int, dict] = {}
    for p in participants:
        cotisations = list(par_participant.get(p.id, []))
        if p.foyer_id:
            cotisations += par_foyer.get(p.foyer_id, [])
        du = round(sum(float(c.montant_du or 0) for c in cotisations), 2)
        regle = round(sum(c.montant_regle for c in cotisations), 2)
        reste = round(max(0.0, du - regle), 2)
        if not cotisations:
            statut = "aucune"
        elif reste <= 0.009:
            statut = "complet"
        elif regle > 0.009:
            statut = "partiel"
        else:
            statut = "rien"
        resultats[p.id] = {
            "annee_scolaire": annee_scolaire,
            "libelle_annee": libelle_annee_scolaire(annee_scolaire),
            "statut": statut,
            "libelle": ETATS_REGLEMENT_LABELS[statut],
            "ton": ETATS_REGLEMENT_TONS[statut],
            "du": du,
            "regle": regle,
            "reste": reste,
            "cotisations": cotisations,
        }
    return resultats


def repartir_versement(
    cotisations: list[Cotisation],
    montant: float,
    *,
    date_paiement: date | None = None,
    mode: str = "especes",
    commentaire: str | None = None,
    user_id: int | None = None,
) -> list:
    """Ventile une somme encaissée sur plusieurs cotisations impayées.

    À l'accueil, la personne tend un billet pour « son inscription » — pas
    une enveloppe par ligne comptable. On solde donc dans l'ordre reçu
    (l'appelant range l'adhésion en premier), sans jamais dépasser le reste
    dû d'une ligne. Un éventuel surplus est ignoré : il n'existe pas de
    trop-perçu qui traînerait sans obligation en face.

    Retourne les versements créés (non commités : l'appelant maîtrise sa
    transaction).
    """
    from app.models import Paiement

    restant = round(float(montant or 0), 2)
    if restant <= 0:
        return []

    date_paiement = date_paiement or date.today()
    versements = []
    for cotisation in cotisations:
        if restant <= 0.009:
            break
        a_payer = min(restant, cotisation.reste_du)
        if a_payer <= 0.009:
            continue
        versement = Paiement(
            montant=round(a_payer, 2),
            date_paiement=date_paiement,
            mode=mode,
            commentaire=commentaire,
            created_by_user_id=user_id,
        )
        # On passe par la relation et non par la clé étrangère : sinon la
        # collection ``cotisation.paiements`` déjà chargée ignore le versement,
        # et ``montant_regle`` continue de répondre l'ancien total tant que la
        # session n'a pas été rafraîchie — de quoi ventiler deux fois de suite
        # sur la même ligne.
        cotisation.paiements.append(versement)
        db.session.add(versement)
        versements.append(versement)
        restant = round(restant - a_payer, 2)
    db.session.flush()
    return versements
