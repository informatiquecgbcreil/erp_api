"""Contraintes de temps : jours fériés et horaires d'ouverture du centre.

À ne pas confondre avec ``app/services/calendrier.py``, qui produit le flux
iCal des feuilles de temps : ici on ne publie rien, on répond seulement à
« le centre est-il ouvert à ce moment-là ? ».


Deux garde-fous simples qui évitent les bêtises fatigantes : proposer une
salle un 1er mai, ou laisser réserver le dimanche à 3 h du matin parce que
personne n'avait dit que le centre était fermé.

Les jours fériés sont CALCULÉS, jamais saisis : ils suivent une règle
stable depuis 1886, autant la coder une fois que redemander la liste
chaque mois de décembre.
"""
from __future__ import annotations

import json
from datetime import date, timedelta

JOURS_SEMAINE_ORDRE = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
JOURS_SEMAINE_COURTS = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"]

MOIS_FR = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]

#: Horaires proposés par défaut à la création d'un site : ouvert du lundi
#: au vendredi, samedi matin, dimanche fermé. À ajuster ensuite.
#: Plage renvoyée quand aucun horaire n'est saisi : on ne contraint rien
#: plutôt que de bloquer à tort une équipe qui n'a pas rempli ce réglage.
OUVERT_EN_PERMANENCE = ["00:00", "24:00"]

HORAIRES_DEFAUT = {
    "lundi": ["09:00", "18:00"],
    "mardi": ["09:00", "18:00"],
    "mercredi": ["09:00", "18:00"],
    "jeudi": ["09:00", "18:00"],
    "vendredi": ["09:00", "18:00"],
    "samedi": ["09:00", "12:30"],
    "dimanche": None,
}


def _paques(annee: int) -> date:
    """Dimanche de Pâques (algorithme de Meeus/Jones/Butcher, grégorien).

    Trois fériés français en dépendent : lundi de Pâques, Ascension et
    lundi de Pentecôte.
    """
    a = annee % 19
    b, c = divmod(annee, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mois, jour = divmod(h + l - 7 * m + 114, 31)
    return date(annee, mois, jour + 1)


def jours_feries(annee: int) -> dict[date, str]:
    """Les onze jours fériés français d'une année, avec leur nom.

    Alsace-Moselle (Vendredi saint, 26 décembre) n'est pas couverte : le
    centre est dans l'Oise.
    """
    paques = _paques(annee)
    feries = {
        date(annee, 1, 1): "Jour de l'an",
        paques + timedelta(days=1): "Lundi de Pâques",
        date(annee, 5, 1): "Fête du Travail",
        date(annee, 5, 8): "Victoire 1945",
        paques + timedelta(days=39): "Ascension",
        paques + timedelta(days=50): "Lundi de Pentecôte",
        date(annee, 7, 14): "Fête nationale",
        date(annee, 8, 15): "Assomption",
        date(annee, 11, 1): "Toussaint",
        date(annee, 11, 11): "Armistice 1918",
        date(annee, 12, 25): "Noël",
    }
    return feries


def jour_ferie(jour: date) -> str | None:
    """Le nom du jour férié, ou ``None`` si c'en est un ordinaire."""
    return jours_feries(jour.year).get(jour)


def feries_entre(debut: date, fin: date) -> dict[date, str]:
    """Les fériés d'une période, bornes comprises (utile sur un planning
    à cheval sur deux années)."""
    resultat: dict[date, str] = {}
    for annee in range(debut.year, fin.year + 1):
        for j, nom in jours_feries(annee).items():
            if debut <= j <= fin:
                resultat[j] = nom
    return resultat


# ---------------------------------------------------------------------------
# Horaires d'ouverture
# ---------------------------------------------------------------------------

def lire_horaires(site) -> dict[str, list[str] | None]:
    """Les horaires d'un site, toujours sous une forme exploitable.

    Un site sans horaires renseignés est considéré comme ouvert en
    permanence : mieux vaut ne rien bloquer que bloquer à tort une équipe
    qui n'a pas encore rempli ce réglage.
    """
    brut = getattr(site, "horaires_json", None)
    if not brut:
        return {}
    try:
        donnees = json.loads(brut)
    except (TypeError, ValueError):
        return {}
    if not isinstance(donnees, dict):
        return {}

    propres: dict[str, list[str] | None] = {}
    for jour in JOURS_SEMAINE_ORDRE:
        plage = donnees.get(jour)
        if isinstance(plage, (list, tuple)) and len(plage) == 2 and plage[0] and plage[1]:
            propres[jour] = [str(plage[0]), str(plage[1])]
        else:
            propres[jour] = None
    return propres


def ecrire_horaires(horaires: dict) -> str:
    """Sérialise des horaires pour la base, en ne gardant que le valide."""
    propres = {}
    for jour in JOURS_SEMAINE_ORDRE:
        plage = horaires.get(jour)
        if isinstance(plage, (list, tuple)) and len(plage) == 2 and plage[0] and plage[1]:
            propres[jour] = [str(plage[0]), str(plage[1])]
        else:
            propres[jour] = None
    return json.dumps(propres, ensure_ascii=False)


def nom_jour(jour: date) -> str:
    return JOURS_SEMAINE_ORDRE[jour.weekday()]


def horaires_du_jour(site, jour: date) -> list[str] | None:
    """La plage d'ouverture ce jour-là, ou ``None`` si le centre est fermé.

    Un jour férié ferme le site, quels que soient les horaires habituels.
    """
    if jour_ferie(jour):
        return None
    horaires = lire_horaires(site)
    if not horaires:
        return OUVERT_EN_PERMANENCE
    return horaires.get(nom_jour(jour))


def hors_ouverture(site, jour: date, minute_debut: int, minute_fin: int) -> str | None:
    """Explique pourquoi ce créneau tombe hors ouverture, ou ``None``.

    Ce n'est volontairement PAS bloquant : on ouvre exceptionnellement un
    samedi soir pour une assemblée générale, et l'application n'a pas à
    l'interdire. Elle doit juste le dire clairement.
    """
    from app.models import minutes_depuis_texte, texte_depuis_minutes

    nom_ferie = jour_ferie(jour)
    if nom_ferie:
        return f"{nom_ferie} : le centre est fermé."

    plage = horaires_du_jour(site, jour)
    if plage is None:
        return f"Le centre est fermé le {nom_jour(jour)}."
    if plage == OUVERT_EN_PERMANENCE:
        return None

    ouverture = minutes_depuis_texte(plage[0])
    fermeture = minutes_depuis_texte(plage[1])
    if ouverture is None or fermeture is None:
        return None
    if minute_debut < ouverture or minute_fin > fermeture:
        return (
            f"Hors des horaires d'ouverture ({texte_depuis_minutes(ouverture)}"
            f"–{texte_depuis_minutes(fermeture)}) : prévoir une ouverture exceptionnelle."
        )
    return None


def libelle_jour(jour: date) -> str:
    """« lundi 15 septembre » — pour les plannings et les messages."""
    return f"{JOURS_SEMAINE_ORDRE[jour.weekday()]} {jour.day} {MOIS_FR[jour.month - 1]}"


def lundi_de(jour: date) -> date:
    return jour - timedelta(days=jour.weekday())


def semaine_de(jour: date) -> list[date]:
    """Les sept jours de la semaine contenant ``jour``, lundi d'abord."""
    debut = lundi_de(jour)
    return [debut + timedelta(days=n) for n in range(7)]
