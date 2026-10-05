"""Calcul des frais kilométriques au barème.

Le barème officiel donne un montant ANNUEL selon la distance totale parcourue
dans l'année avec le véhicule : ``d × taux + forfait``, par tranches
(0–5 000 km, 5 001–20 000 km, au-delà). Un trajet est donc remboursé de la
différence entre le montant annuel APRÈS lui et le montant annuel AVANT lui :

    montant(trajet) = F(cumul + km) − F(cumul)

La somme des trajets de l'année redonne exactement F(total annuel), tranches
et forfaits compris (le barème est continu aux changements de tranche). Pour
les trajets courants de la première tranche, cela revient à ``km × taux``.

L'ancienne application « Récup » choisissait la tranche d'après la distance
du SEUL trajet : correct tant que le cumul annuel reste sous 5 000 km, faux
au-delà. Montants en centimes, taux en millièmes d'euro : aucun flottant.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from app.extensions import db

TYPES_VEHICULE = {
    "voiture": "Voiture",
    "moto": "Moto (plus de 50 cm³)",
    "cyclo": "Cyclomoteur (50 cm³ au plus)",
    "utilitaire": "Utilitaire",
    "autre": "Autre",
}

TRANCHES_USUELLES = {
    "0_5000": (0, 5000),
    "5001_20000": (5001, 20000),
    "20001_plus": (20001, None),
}


class BaremeIntrouvable(ValueError):
    pass


@dataclass
class Calcul:
    montant_centimes: int
    annee_bareme: int
    bareme_anterieur: bool       # barème d'une année précédente, faute de mieux
    cumul_avant: int
    ligne: object                # BaremeKilometrique appliqué au cumul final


def libelle_vehicule(code: str | None) -> str:
    return TYPES_VEHICULE.get(code or "", code or "—")


def _lignes(annee: int, type_vehicule: str, puissance: int):
    from app.models import BaremeKilometrique
    return (BaremeKilometrique.query
            .filter_by(annee=annee, type_vehicule=type_vehicule, puissance_fiscale=puissance)
            .order_by(BaremeKilometrique.km_de.asc()).all())


def annee_bareme(annee: int, type_vehicule: str, puissance: int) -> int | None:
    """Année du barème applicable : celle du trajet, sinon la plus récente avant.

    Le barème n'est pas revalorisé chaque année ; tant que le nouveau n'est
    pas saisi, le précédent s'applique (et la note l'indique)."""
    from app.models import BaremeKilometrique
    valeur = (db.session.query(db.func.max(BaremeKilometrique.annee))
              .filter(BaremeKilometrique.annee <= annee,
                      BaremeKilometrique.type_vehicule == type_vehicule,
                      BaremeKilometrique.puissance_fiscale == puissance)
              .scalar())
    return int(valeur) if valeur is not None else None


def _ligne_pour(lignes, distance: int):
    for ligne in lignes:
        haut = ligne.km_a if ligne.km_a is not None else 10 ** 9
        if ligne.km_de <= distance <= haut:
            return ligne
    return None


def montant_annuel(lignes, distance: int, electrique: bool) -> tuple[int, object]:
    """F(distance) en centimes, arrondi au centime, et la ligne utilisée."""
    if distance <= 0:
        return 0, (lignes[0] if lignes else None)
    ligne = _ligne_pour(lignes, distance)
    if ligne is None:
        raise BaremeIntrouvable(f"Aucune tranche du barème ne couvre {distance} km.")
    # taux en millièmes d'euro/km → centimes : × distance / 10
    centimes = Decimal(ligne.taux_millieme) * distance / 10 + ligne.forfait_centimes
    if electrique and ligne.bonus_electrique_pct:
        centimes = centimes * (100 + ligne.bonus_electrique_pct) / 100
    return int(centimes.quantize(Decimal("1"), rounding=ROUND_HALF_UP)), ligne


def cumul_annuel(salarie_id: int, annee: int, type_vehicule: str, puissance: int,
                 exclure_id: int | None = None) -> int:
    from app.models import FraisKilometrique
    q = db.session.query(db.func.coalesce(db.func.sum(FraisKilometrique.distance_km), 0)).filter(
        FraisKilometrique.salarie_id == salarie_id,
        FraisKilometrique.annee == annee,
        FraisKilometrique.type_vehicule == type_vehicule,
        FraisKilometrique.puissance_fiscale == puissance,
    )
    if exclure_id is not None:
        q = q.filter(FraisKilometrique.id != exclure_id)
    return int(q.scalar() or 0)


def calculer(salarie_id: int, annee: int, type_vehicule: str, puissance: int,
             distance: int, electrique: bool) -> Calcul:
    if distance <= 0:
        raise ValueError("La distance doit être d'au moins 1 km.")
    an = annee_bareme(annee, type_vehicule, puissance)
    if an is None:
        raise BaremeIntrouvable(
            f"Aucun barème pour « {libelle_vehicule(type_vehicule)} {puissance} CV » en {annee} "
            "ni avant. Demande à la comptabilité de saisir le barème.")
    lignes = _lignes(an, type_vehicule, puissance)
    cumul = cumul_annuel(salarie_id, annee, type_vehicule, puissance)
    avant, _ = montant_annuel(lignes, cumul, electrique)
    apres, ligne = montant_annuel(lignes, cumul + distance, electrique)
    return Calcul(montant_centimes=apres - avant, annee_bareme=an, bareme_anterieur=an != annee,
                  cumul_avant=cumul, ligne=ligne)


def combinaisons_disponibles() -> dict[str, list[int]]:
    """Types de véhicule → puissances présentes dans les barèmes (pour les listes)."""
    from app.models import BaremeKilometrique
    res: dict[str, set[int]] = {}
    for type_vehicule, puissance in db.session.query(
            BaremeKilometrique.type_vehicule, BaremeKilometrique.puissance_fiscale).distinct().all():
        res.setdefault(type_vehicule, set()).add(int(puissance))
    return {k: sorted(v) for k, v in sorted(res.items())}


def parse_taux_millieme(valeur: str | None) -> int:
    """« 0,529 » €/km → 529."""
    from decimal import InvalidOperation
    brut = (valeur or "").strip().replace(",", ".").replace("€", "").replace(" ", "")
    if not brut:
        raise ValueError("Taux €/km manquant.")
    try:
        return int((Decimal(brut) * 1000).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except InvalidOperation:
        raise ValueError("Taux €/km illisible.") from None


def format_taux(millieme: int | None) -> str:
    return f"{Decimal(int(millieme or 0)) / 1000:.3f}".replace(".", ",")
