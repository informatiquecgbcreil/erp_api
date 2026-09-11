"""Calcul du prix d'une mise à disposition.

Une règle gouverne tout : **le calcul retient toujours le tarif le plus
avantageux pour le preneur**, et il dit pourquoi.

Quatre heures d'affilée dans une salle facturée 12 €/h et 35 € la
demi-journée, ça fait 35 € — pas 48 €. Personne n'a à y penser, personne
n'a à le réclamer, et la ligne « forfait demi-journée retenu à la place de
4 h, économie 13 € » s'imprime sur le contrat. C'est ce qui évite les
discussions au comptoir, et surtout le sentiment, chez le preneur, qu'on
lui a pris ce qu'il n'a pas su négocier.

Le détail produit ici est figé dans la réservation à la confirmation :
six mois plus tard, on sait encore expliquer le prix.
"""
from __future__ import annotations

import math
from datetime import date as Date

from app.extensions import db
from app.models import (
    UNITES_PERIODE_JOURS,
    UNITES_TARIF,
    UNITES_TARIF_LABELS,
    UNITES_TARIF_MINUTES,
    CategoriePreneur,
    MajorationSalle,
    TarifSalle,
    texte_depuis_minutes,
)


def _arrondi(valeur) -> float:
    return round(float(valeur or 0.0), 2)


# ---------------------------------------------------------------------------
# Lecture de la grille
# ---------------------------------------------------------------------------

def tarif_en_vigueur(espace_id: int, categorie_id: int, unite: str, jour: Date) -> TarifSalle | None:
    """Le tarif applicable à une date : le plus récent sans la dépasser.

    Même principe que le barème des adhésions : on peut préparer la grille
    de l'an prochain sans toucher aux contrats de cette année.
    """
    return (
        TarifSalle.query
        .filter(
            TarifSalle.espace_id == espace_id,
            TarifSalle.categorie_id == categorie_id,
            TarifSalle.unite == unite,
            TarifSalle.date_debut <= jour,
        )
        .order_by(TarifSalle.date_debut.desc(), TarifSalle.id.desc())
        .first()
    )


def grille_en_vigueur(espace_id: int, categorie_id: int, jour: Date) -> dict[str, float]:
    """Toutes les unités tarifées pour ce couple, à cette date."""
    grille = {}
    for unite in UNITES_TARIF:
        tarif = tarif_en_vigueur(espace_id, categorie_id, unite, jour)
        if tarif is not None:
            grille[unite] = float(tarif.montant)
    return grille


# ---------------------------------------------------------------------------
# Prix d'une occurrence
# ---------------------------------------------------------------------------

def _candidats_occurrence(grille: dict[str, float], minutes: int) -> list[dict]:
    """Les façons possibles de facturer UNE occurrence, avec leur prix.

    Les heures se comptent par heure commencée : c'est l'usage, et c'est
    le seul arrondi qu'on sait expliquer sans schéma. Les forfaits
    demi-journée et journée se multiplient quand la durée les dépasse, pour
    qu'une occupation de 10 h ne coûte pas le prix d'une demi-journée.
    """
    candidats = []
    for unite, couverture in UNITES_TARIF_MINUTES.items():
        if unite not in grille:
            continue
        nombre = max(1, math.ceil(minutes / couverture))
        candidats.append({
            "unite": unite,
            "nombre": nombre,
            "montant_unitaire": grille[unite],
            "total": _arrondi(grille[unite] * nombre),
        })
    return candidats


def prix_occurrence(grille: dict[str, float], minutes: int) -> dict | None:
    """Le meilleur prix pour une occurrence, et ce qu'il remplace.

    Retourne ``None`` quand aucune unité n'est tarifée : c'est un trou
    dans la grille, pas une gratuité, et l'appelant doit le signaler.
    """
    candidats = _candidats_occurrence(grille, minutes)
    if not candidats:
        return None

    meilleur = min(candidats, key=lambda c: (c["total"], c["nombre"]))

    # L'économie se mesure contre la facturation À L'HEURE, pas contre le
    # forfait le plus cher : personne n'aurait facturé une journée entière
    # pour quatre heures, annoncer une économie de ce côté serait malhonnête.
    # Quand l'heure n'est pas tarifée, on prend l'unité la plus fine
    # disponible, qui joue le même rôle de référence.
    reference = next((c for c in candidats if c["unite"] == "heure"), None)
    if reference is None:
        reference = max(candidats, key=lambda c: c["nombre"])
    economie = _arrondi(reference["total"] - meilleur["total"])

    return {
        **meilleur,
        "reference": reference,
        "economie": economie if economie > 0 else 0.0,
    }


# ---------------------------------------------------------------------------
# Majorations
# ---------------------------------------------------------------------------

def majorations_applicables(jour: Date, minute_debut: int, minute_fin: int) -> list[MajorationSalle]:
    """Les suppléments déclenchés par ce créneau précis."""
    from app.services.temps_ouverture import jour_ferie

    actives = MajorationSalle.query.filter(MajorationSalle.actif.is_(True)).all()
    retenues = []
    for majoration in actives:
        condition = majoration.condition
        if condition == "ferie" and jour_ferie(jour):
            retenues.append(majoration)
        elif condition == "samedi" and jour.weekday() == 5:
            retenues.append(majoration)
        elif condition == "dimanche" and jour.weekday() == 6:
            retenues.append(majoration)
        elif condition == "soiree":
            seuil = majoration.seuil_minute if majoration.seuil_minute is not None else 20 * 60
            # Déclenchée dès que l'occupation MORD sur la soirée, pas
            # seulement si elle commence après : une salle rendue à 23 h
            # mobilise quelqu'un pour fermer, quelle que soit l'heure
            # d'arrivée.
            if minute_fin > seuil:
                retenues.append(majoration)
    return retenues


def _appliquer_majorations(base: float, majorations) -> tuple[float, list[dict]]:
    total, lignes = 0.0, []
    for majoration in majorations:
        if majoration.pourcentage:
            montant = _arrondi(base * float(majoration.pourcentage) / 100.0)
        elif majoration.montant_fixe:
            montant = _arrondi(majoration.montant_fixe)
        else:
            continue
        total += montant
        lignes.append({
            "type": "majoration",
            "libelle": majoration.libelle_complet,
            "condition": majoration.condition_label,
            "montant": montant,
        })
    return _arrondi(total), lignes


# ---------------------------------------------------------------------------
# Calcul complet
# ---------------------------------------------------------------------------

def calculer(
    espace_id: int,
    categorie_id: int | None,
    occurrences: list[tuple[Date, int, int]],
    *,
    prestations: list[dict] | None = None,
    date_reference: Date | None = None,
) -> dict:
    """Le prix d'une mise à disposition, avec son détail ligne par ligne.

    ``occurrences`` : liste de ``(jour, minute_debut, minute_fin)``.
    ``prestations`` : dicts ``{libelle, montant_unitaire, unite, quantite}``.

    Retourne ``{"total", "lignes", "avertissements", "economie"}``. Les
    lignes sont directement affichables et imprimables : c'est le même
    détail qui part sur le contrat.
    """
    occurrences = sorted(occurrences or [])
    if not occurrences:
        return {"total": 0.0, "lignes": [], "avertissements": ["Aucune date retenue."], "economie": 0.0}

    jour_reference = date_reference or occurrences[0][0]
    lignes: list[dict] = []
    avertissements: list[str] = []

    if not categorie_id:
        return {
            "total": 0.0, "lignes": [], "economie": 0.0,
            "avertissements": ["Aucune catégorie de preneur : impossible d'appliquer un tarif."],
        }

    grille = grille_en_vigueur(espace_id, categorie_id, jour_reference)
    if not grille:
        categorie = db.session.get(CategoriePreneur, categorie_id)
        nom = categorie.libelle if categorie else "cette catégorie"
        avertissements.append(
            f"Aucun tarif n'est renseigné pour cette salle et {nom} : "
            "le montant reste à zéro tant que la grille n'est pas remplie."
        )

    # --- Occurrence par occurrence ---------------------------------------
    total_occurrences, total_economie = 0.0, 0.0
    for jour, minute_debut, minute_fin in occurrences:
        minutes = max(0, minute_fin - minute_debut)
        plage = f"{texte_depuis_minutes(minute_debut)}–{texte_depuis_minutes(minute_fin)}"
        meilleur = prix_occurrence(grille, minutes) if grille else None

        if meilleur is None:
            lignes.append({
                "type": "occurrence", "jour": jour.isoformat(),
                "libelle": f"{jour.strftime('%d/%m/%Y')} · {plage}",
                "detail": "Aucun tarif applicable", "montant": 0.0,
            })
            continue

        remplace = ""
        if meilleur["economie"] > 0:
            reference = meilleur["reference"]
            remplace = (
                f" — forfait {UNITES_TARIF_LABELS[meilleur['unite']].lower()} retenu "
                f"au lieu de {reference['nombre']} × {UNITES_TARIF_LABELS[reference['unite']].lower()} "
                f"({reference['total']:.2f} €), économie {meilleur['economie']:.2f} €"
            )
            total_economie += meilleur["economie"]

        base = meilleur["total"]
        lignes.append({
            "type": "occurrence", "jour": jour.isoformat(),
            "libelle": f"{jour.strftime('%d/%m/%Y')} · {plage}",
            "detail": (
                f"{meilleur['nombre']} × {UNITES_TARIF_LABELS[meilleur['unite']].lower()} "
                f"à {meilleur['montant_unitaire']:.2f} €{remplace}"
            ),
            "montant": base,
        })
        total_occurrences += base

        supplement, lignes_majoration = _appliquer_majorations(
            base, majorations_applicables(jour, minute_debut, minute_fin)
        )
        for ligne in lignes_majoration:
            ligne["jour"] = jour.isoformat()
            ligne["libelle"] = f"{jour.strftime('%d/%m')} · {ligne['libelle']}"
        lignes.extend(lignes_majoration)
        total_occurrences += supplement

    total_occurrences = _arrondi(total_occurrences)

    # --- Forfait de période -----------------------------------------------
    # Une semaine de location n'est pas sept journées : si un forfait
    # couvre l'étendue réservée et coûte moins cher, il remplace tout.
    etendue = (occurrences[-1][0] - occurrences[0][0]).days + 1
    for unite, jours_couverts in sorted(UNITES_PERIODE_JOURS.items(), key=lambda kv: kv[1]):
        if unite not in grille or etendue > jours_couverts:
            continue
        forfait = _arrondi(grille[unite])
        if forfait < total_occurrences:
            economie = _arrondi(total_occurrences - forfait)
            lignes = [{
                "type": "forfait",
                "libelle": f"Forfait {UNITES_TARIF_LABELS[unite].lower()}",
                "detail": (
                    f"{len(occurrences)} date(s) sur {etendue} jour(s) — "
                    f"remplace le détail à {total_occurrences:.2f} €, économie {economie:.2f} €"
                ),
                "montant": forfait,
            }]
            total_occurrences, total_economie = forfait, economie
            break

    # --- Prestations annexes ----------------------------------------------
    total_prestations = 0.0
    for prestation in prestations or []:
        quantite = float(prestation.get("quantite") or 0)
        unitaire = float(prestation.get("montant_unitaire") or 0)
        if quantite <= 0:
            continue
        montant = _arrondi(unitaire * quantite)
        total_prestations += montant
        lignes.append({
            "type": "prestation",
            "libelle": prestation.get("libelle") or "Prestation",
            "detail": f"{quantite:g} × {unitaire:.2f} €",
            "montant": montant,
        })

    total = _arrondi(total_occurrences + total_prestations)
    return {
        "total": total,
        "lignes": lignes,
        "avertissements": avertissements,
        "economie": _arrondi(total_economie),
    }


def calculer_pour_reservation(reservation, occurrences=None) -> dict:
    """Calcule le prix d'une réservation à partir de ses occupations."""
    if occurrences is None:
        occurrences = [
            (o.date_jour, o.minute_debut, o.minute_fin)
            for o in (reservation.occupations or [])
        ]
    prestations = [
        {
            "libelle": p.libelle, "montant_unitaire": p.montant_unitaire,
            "unite": p.unite, "quantite": p.quantite,
        }
        for p in (reservation.prestations or [])
    ]
    return calculer(
        reservation.espace_id, reservation.categorie_id, occurrences,
        prestations=prestations,
        date_reference=occurrences[0][0] if occurrences else None,
    )
