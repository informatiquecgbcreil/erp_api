"""Une ville, une seule écriture.

Le champ « ville » est saisi librement sur onze écrans (fiche participant,
émargement, kiosque, bulletin d'inscription, orientations, preneurs de
salle…) et proposé en liste déroulante sur deux — les FILTRES, alimentés
par ce qui a déjà été tapé. Résultat : « Nogent sur Oise »,
« Nogent-sur-Oise », « NOGENT SUR OISE » et « Nogent » vivaient côte à
côte, comptaient pour quatre villes différentes dans les bilans, et
faisaient quatre entrées distinctes dans le filtre.

Effet de bord silencieux et coûteux : la liste des quartiers est filtrée
par ÉGALITÉ DE CHAÎNE avec la ville saisie (voir le script de
layout.html). Taper « Nogent sur Oise » quand les quartiers sont
enregistrés sous « Nogent-sur-Oise » vide la liste des quartiers, sans le
moindre message. La personne à l'accueil en conclut qu'il n'y a pas de
quartier pour cette ville, et laisse le champ vide.

## Deux problèmes, deux traitements

1. **Les variantes d'écriture** (majuscules, tirets, accents, « St » pour
   « Saint », un code postal collé) désignent PROUVABLEMENT la même
   commune. On les ramène à une forme unique, automatiquement, à la
   saisie.

2. **Les troncatures** (« Nogent » pour « Nogent-sur-Oise ») demandent une
   connaissance locale que le code n'a pas : « Villers » peut être
   Villers-Saint-Paul comme Villers-sous-Saint-Leu, deux communes voisines.
   On ne devine pas. Elles se règlent à la main, par la fusion — un écran
   qui montre les villes réellement présentes, leur nombre de fiches, et
   permet d'en rabattre une sur une autre.

La forme retenue est celle de l'état civil : article de tête séparé, reste
lié par des traits d'union, mots de liaison en minuscules.
« la chapelle en serval » -> « La Chapelle-en-Serval ».
"""
from __future__ import annotations

import re
import unicodedata

#: Mots qui restent en minuscules à l'intérieur d'un nom de commune.
LIAISONS = {
    "sur", "sous", "lès", "les", "le", "la", "de", "du", "des", "d", "en",
    "et", "au", "aux", "l", "sainte", "saint",
}
#: …sauf « saint » / « sainte », qui prennent toujours la majuscule.
MAJUSCULE_TOUJOURS = {"saint", "sainte"}

#: Articles de tête, qui restent détachés : « Le Plessis-Belleville ».
ARTICLES_DE_TETE = {"le", "la", "les", "l"}

#: Abréviations courantes des feuilles de présence et des imports.
ABREVIATIONS = {
    "st": "saint",
    "ste": "sainte",
    "sts": "saints",
}

#: Code postal ou département collé au nom : « Creil (60) », « 60100 Creil ».
_PARENTHESES = re.compile(r"\((?:\d{2,5})\)")
_CODE_POSTAL = re.compile(r"\b\d{5}\b")


def _sans_accent(texte: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", texte) if not unicodedata.combining(c)
    )


def cle(valeur) -> str:
    """Clé de comparaison : deux écritures d'une même commune la partagent.

    Sans accent, sans ponctuation, sans majuscules, sans code postal — ce
    par quoi on reconnaît que « NOGENT-SUR-OISE » et « nogent sur oise »
    sont la même chose. Ne sert JAMAIS à l'affichage.
    """
    texte = (str(valeur or "")).strip().lower()
    if not texte:
        return ""
    texte = _PARENTHESES.sub(" ", texte)
    texte = _CODE_POSTAL.sub(" ", texte)
    texte = _sans_accent(texte)
    mots = [ABREVIATIONS.get(mot, mot) for mot in re.split(r"[^a-z0-9]+", texte) if mot]
    return " ".join(mots)


def normaliser(valeur) -> str | None:
    """La forme d'état civil : « nogent sur oise » -> « Nogent-sur-Oise ».

    Ne devine JAMAIS un nom tronqué : « Nogent » reste « Nogent ». C'est le
    rôle de la fusion, qui demande l'avis de quelqu'un qui connaît le
    territoire.
    """
    texte = (str(valeur or "")).strip()
    if not texte:
        return None
    texte = _PARENTHESES.sub(" ", texte)
    texte = _CODE_POSTAL.sub(" ", texte)

    # Découpage : l'espace et le trait d'union séparent, l'apostrophe non
    # (« d'Esserent » est un seul élément).
    texte = texte.replace("\u2019", "'")
    morceaux = [m for m in re.split(r"[\s\-]+", texte.lower()) if m]
    if not morceaux:
        return None

    elements: list[str] = []
    for morceau in morceaux:
        parts = [ABREVIATIONS.get(m, m) for m in morceau.split("'") if m]
        if parts:
            elements.append("'".join(parts))

    # Élision perdue en route : les feuilles de présence et les imports
    # écrivent souvent « saint leu d esserent ». Un « d » ou un « l » tout
    # seul se recolle au mot suivant, sinon on produirait un improbable
    # « Saint-Leu-d-Esserent ».
    recollees: list[str] = []
    for element in elements:
        if recollees and recollees[-1] in ("d", "l"):
            recollees[-1] = f"{recollees[-1]}'{element}"
        else:
            recollees.append(element)
    elements = recollees

    def _capitaliser(mot: str, *, premier: bool) -> str:
        if "'" in mot:
            avant, _, apres = mot.partition("'")
            # En tête de nom, l'élision porte la majuscule : « L'Isle-Adam »,
            # et non « l'Isle-Adam ». À l'intérieur elle reste minuscule :
            # « Saint-Leu-d'Esserent ».
            tete = avant.capitalize() if premier else avant.lower()
            return f"{tete}'{apres.capitalize()}"
        if not premier and mot in LIAISONS and mot not in MAJUSCULE_TOUJOURS:
            return mot
        return mot.capitalize()

    rendus = [_capitaliser(mot, premier=(i == 0)) for i, mot in enumerate(elements)]

    # Article de tête détaché : « Le Plessis-Belleville », « Les Ageux ».
    if len(rendus) > 1 and elements[0] in ARTICLES_DE_TETE:
        return f"{rendus[0]} " + "-".join(rendus[1:])
    return "-".join(rendus)


def memes_villes(a, b) -> bool:
    """Deux écritures désignent-elles la même commune ?"""
    cle_a, cle_b = cle(a), cle(b)
    return bool(cle_a) and cle_a == cle_b
