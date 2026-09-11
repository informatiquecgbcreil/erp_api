"""Mises à disposition : récurrences, options qui expirent, contrôles.

Une réservation ne stocke pas ses dates : chacune devient une
``Occupation`` rattachée. La location passe donc par le moteur de conflits
du lot 1 sans une ligne de plus — une réunion d'équipe protège la salle
d'une location exactement comme l'inverse.
"""
from __future__ import annotations

import json
from datetime import date as Date
from datetime import timedelta

from app.extensions import db
from app.models import (
    STATUTS_RESERVATION_BLOQUANTS,
    Espace,
    Occupation,
    Reservation,
    minutes_depuis_texte,
)
from app.services.salles import SalleErreur, conflits, message_conflit, normaliser_plage
from app.services.tarifs_salles import calculer_pour_reservation

#: Garde-fou de saisie : au-delà, c'est une erreur de date, pas une
#: location. Une association qui vient toutes les semaines pendant deux ans
#: se saisit en deux fois.
MAX_OCCURRENCES = 400


def reference_unique(jour: Date | None = None) -> str:
    """Référence lisible et parlante au téléphone : « LOC-2026-0007 »."""
    annee = (jour or Date.today()).year
    prefixe = f"LOC-{annee}-"
    derniere = (
        Reservation.query
        .filter(Reservation.reference.like(prefixe + "%"))
        .order_by(Reservation.reference.desc())
        .first()
    )
    numero = 0
    if derniere is not None:
        try:
            numero = int(derniere.reference.rsplit("-", 1)[1])
        except (IndexError, ValueError):
            numero = 0
    return f"{prefixe}{numero + 1:04d}"


# ---------------------------------------------------------------------------
# Récurrence
# ---------------------------------------------------------------------------

def dates_recurrentes(
    debut: Date,
    fin: Date,
    jours_semaine: set[int] | None = None,
    *,
    sauter_feries: bool = True,
    exclusions: set[Date] | None = None,
) -> list[Date]:
    """« Tous les mardis de septembre à juin », fériés exclus.

    Les occurrences sont matérialisées une par une, comme les créneaux
    d'agenda existants : chacune reste annulable ou déplaçable
    individuellement, ce qu'une règle de répétition abstraite ne permet
    pas sans complexité inutile.
    """
    from app.services.temps_ouverture import jour_ferie

    if fin < debut:
        debut, fin = fin, debut
    exclusions = exclusions or set()
    retenues, jour, garde = [], debut, 0

    while jour <= fin and len(retenues) < MAX_OCCURRENCES:
        garde += 1
        if garde > 5000:  # ceinture : une boucle infinie n'est jamais loin
            break
        if (not jours_semaine or jour.weekday() in jours_semaine) and jour not in exclusions:
            if not (sauter_feries and jour_ferie(jour)):
                retenues.append(jour)
        jour += timedelta(days=1)
    return retenues


# ---------------------------------------------------------------------------
# Occupations et prix
# ---------------------------------------------------------------------------

def appliquer_dates(reservation: Reservation, dates: list[Date], debut, fin) -> int:
    """Remplace les dates d'une réservation par celles-ci.

    On supprime puis on recrée : une réservation compte quelques dizaines
    d'occurrences, et un remplacement franc évite toute la mécanique de
    réconciliation fine pour un gain nul.
    """
    m_debut, m_fin = normaliser_plage(debut, fin)

    for ancienne in list(reservation.occupations or []):
        db.session.delete(ancienne)
    reservation.occupations = []

    for jour in dates[:MAX_OCCURRENCES]:
        db.session.add(Occupation(
            reservation=reservation,
            espace_id=reservation.espace_id,
            date_jour=jour,
            minute_debut=m_debut,
            minute_fin=m_fin,
            origine="location",
            statut="annule" if reservation.statut == "annulee" else "confirme",
            titre=reservation.titre[:200],
            effectif_prevu=reservation.effectif,
        ))
    return len(dates[:MAX_OCCURRENCES])


def recalculer(reservation: Reservation) -> dict:
    """Recalcule le prix et range le détail dans la réservation.

    Le détail est réécrit à chaque passage : il n'a de sens qu'en regard
    des dates et prestations du moment. Ce qui protège un contrat signé,
    ce n'est pas de figer ce champ, c'est l'historisation de la grille —
    un tarif modifié en janvier ne change pas le prix d'une réservation
    calculée en novembre, puisque le calcul lit le tarif en vigueur à la
    date de la mise à disposition.
    """
    resultat = calculer_pour_reservation(reservation)
    reservation.montant_calcule = resultat["total"]
    reservation.detail_json = json.dumps(resultat["lignes"], ensure_ascii=False)
    return resultat


def synchroniser_statut_occupations(reservation: Reservation) -> None:
    """Une réservation annulée libère ses salles, et les reprend si on la
    réactive."""
    voulu = "confirme" if reservation.statut in STATUTS_RESERVATION_BLOQUANTS else "annule"
    for occ in reservation.occupations or []:
        if occ.statut != voulu:
            occ.statut = voulu
        if occ.titre != reservation.titre[:200]:
            occ.titre = reservation.titre[:200]


# ---------------------------------------------------------------------------
# Options qui expirent
# ---------------------------------------------------------------------------

def purger_options_expirees(aujourdhui: Date | None = None) -> int:
    """Libère les options qu'on a posées au téléphone et que personne n'a
    confirmées.

    Sans ça, le planning se remplit de fantômes : des salles qui semblent
    prises depuis des mois pour des gens qui n'ont jamais rappelé. La purge
    n'efface rien — elle annule, ce qui garde la trace de la demande.
    """
    aujourdhui = aujourdhui or Date.today()
    expirees = (
        Reservation.query
        .filter(Reservation.statut == "option")
        .filter(Reservation.option_expire_le.isnot(None))
        .filter(Reservation.option_expire_le < aujourdhui)
        .all()
    )
    for reservation in expirees:
        reservation.statut = "annulee"
        reservation.notes = ((reservation.notes or "") + (
            f"\n[Option expirée le {reservation.option_expire_le.strftime('%d/%m/%Y')}, "
            "libérée automatiquement.]"
        )).strip()
        synchroniser_statut_occupations(reservation)
    if expirees:
        db.session.commit()
    return len(expirees)


# ---------------------------------------------------------------------------
# Contrôles avant de s'engager
# ---------------------------------------------------------------------------

def controles(reservation: Reservation) -> list[dict]:
    """Ce qui doit être vu AVANT de confirmer et d'éditer un contrat.

    Deux niveaux : ``bloquant`` (on ne devrait pas signer en l'état) et
    ``alerte`` (à vérifier). Rien n'est interdit techniquement — la
    décision reste humaine — mais plus personne ne peut dire qu'il ne
    savait pas.
    """
    resultats: list[dict] = []
    espace = reservation.espace
    preneur = reservation.preneur
    occurrences = sorted(reservation.occupations or [], key=lambda o: (o.date_jour, o.minute_debut))

    if not occurrences:
        resultats.append({"niveau": "bloquant", "message": "Aucune date retenue."})
        return resultats

    premiere = occurrences[0].date_jour
    derniere = occurrences[-1].date_jour

    # 1. Le droit de mettre à disposition, qui vient de la convention.
    if espace is not None and espace.site is not None:
        alerte = espace.site.alerte_sous_location
        if alerte:
            niveau = "bloquant" if espace.site.regime_sous_location == "interdite" else "alerte"
            resultats.append({"niveau": niveau, "message": alerte})

    # 2. L'assurance du preneur : pas d'attestation valide, pas de clés.
    if preneur is not None:
        if not preneur.assurance_rc_fin:
            resultats.append({
                "niveau": "bloquant",
                "message": "Aucune attestation de responsabilité civile enregistrée pour ce preneur.",
            })
        elif not preneur.assurance_ok(derniere):
            resultats.append({
                "niveau": "bloquant",
                "message": (
                    f"L'attestation d'assurance expire le "
                    f"{preneur.assurance_rc_fin.strftime('%d/%m/%Y')}, avant la fin de la mise à "
                    f"disposition ({derniere.strftime('%d/%m/%Y')})."
                ),
            })

    # 3. La salle est-elle louable, et assez grande ?
    if espace is not None:
        if not espace.louable:
            resultats.append({
                "niveau": "alerte",
                "message": f"« {espace.nom} » n'est pas marquée comme mobilisable par un tiers.",
            })
        if espace.habilitation_requise:
            resultats.append({"niveau": "alerte", "message": espace.habilitation_requise})
        effectif = reservation.effectif
        if effectif and espace.capacite_reglementaire and effectif > espace.capacite_reglementaire:
            resultats.append({
                "niveau": "bloquant",
                "message": (
                    f"Effectif annoncé ({effectif}) supérieur à la capacité réglementaire "
                    f"de la salle ({espace.capacite_reglementaire}) : interdit."
                ),
            })
        elif effectif and espace.capacite_affichee and effectif > espace.capacite_affichee:
            resultats.append({
                "niveau": "alerte",
                "message": f"Effectif annoncé ({effectif}) au-dessus de la capacité d'usage "
                           f"({espace.capacite_affichee}).",
            })

    # 4. Chevauchements avec autre chose que cette réservation.
    if espace is not None:
        propres = {o.id for o in occurrences}
        genes = []
        for occ in occurrences:
            try:
                trouves = [
                    c for c in conflits(espace, occ.date_jour, occ.heure_debut, occ.heure_fin)
                    if c.id not in propres
                ]
            except SalleErreur:
                continue
            if trouves:
                genes.append(message_conflit(trouves[0], espace))
        if genes:
            resultats.append({
                "niveau": "bloquant",
                "message": f"{len(genes)} date(s) en conflit. Première : {genes[0]}",
            })

    # 5. Hors des horaires d'ouverture : ouverture exceptionnelle à prévoir.
    if espace is not None and espace.site is not None:
        from app.services.temps_ouverture import hors_ouverture

        hors = [
            o for o in occurrences
            if hors_ouverture(espace.site, o.date_jour, o.minute_debut, o.minute_fin)
        ]
        if hors:
            resultats.append({
                "niveau": "alerte",
                "message": (
                    f"{len(hors)} date(s) hors des horaires d'ouverture : "
                    "une ouverture exceptionnelle est à organiser."
                ),
            })

    # 6. Cohérence de la gratuité et du prix imposé.
    if reservation.gratuite and not (reservation.motif_gratuite or "").strip():
        resultats.append({
            "niveau": "bloquant",
            "message": "Gratuité accordée sans motif : indispensable pour la justifier en contrôle.",
        })
    if reservation.montant_manuel is not None and not (reservation.motif_montant_manuel or "").strip():
        resultats.append({
            "niveau": "bloquant",
            "message": "Prix saisi à la main sans motif : la raison de l'écart doit être tracée.",
        })

    # 7. Une option sans date d'expiration ne se libérera jamais seule.
    if reservation.statut == "option" and not reservation.option_expire_le:
        resultats.append({
            "niveau": "alerte",
            "message": "Option sans date limite : elle ne se libérera pas toute seule.",
        })
    if premiere < Date.today() and reservation.statut == "option":
        resultats.append({
            "niveau": "alerte",
            "message": "Cette option porte sur une date déjà passée.",
        })

    return resultats


def bloquants(reservation: Reservation) -> list[dict]:
    return [c for c in controles(reservation) if c["niveau"] == "bloquant"]
