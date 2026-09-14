"""Le genre : une valeur enregistrée, un libellé qui suit l'âge.

## Le problème constaté

Trois écrans, trois vocabulaires de saisie — « Femme / Homme / Autre /
Préférez ne pas répondre » sur le tableau de bord d'impact, un champ libre
« F / H / … » sur la fiche d'émargement, une liste ailleurs — et QUATRE
fonctions de classement différentes en aval. Le même mot ne donnait pas le
même comptage selon l'écran :

    saisi       tableau de bord   indicateurs   stats impact            SENACS
    Fille       Non renseigné     femme         Femmes                  Fille
    Garçon      Non renseigné     inconnu       Autre / non renseigné   Garçon
    Préférez…   Non renseigné     inconnu       Autre / non renseigné   Préférez…

Un « Garçon » n'était compté comme garçon nulle part. Et « Préférez ne pas
répondre » — une valeur que l'application propose elle-même — était classée
de trois façons différentes.

## Le principe retenu

On enregistre **le genre**, pas le mot qu'on affiche. Le mot se calcule à
la lecture, à partir de l'âge à la date qui compte :

- moins de 18 ans révolus -> fille / garçon
- 18 ans et plus, ou âge inconnu -> femme / homme

Conséquence directe : personne n'a jamais à repasser sur une fiche le jour
des 18 ans. Un bilan de l'année dernière garde ses filles et ses garçons de
l'année dernière, parce que l'âge est calculé à la date de référence du
bilan — et non à aujourd'hui.

La date de référence :
- un bilan annuel -> le 31 décembre de l'année (convention SENACS, la même
  que ``Participant.age_au``) ;
- une feuille d'appel -> le jour de la séance ;
- une fiche -> aujourd'hui.

## Compatibilité

``normaliser`` accepte tout ce qui a pu être saisi depuis le début : F, H,
M, Femme, FEMME, Féminin, Fille, Garçon, Homme, Masculin, Autre, non
binaire, Préférez ne pas répondre… Les lectures passant toutes par ici, une
ligne oubliée par la migration continue d'être comptée correctement.
"""
from __future__ import annotations

import unicodedata
from datetime import date

#: Codes enregistrés en base. Volontairement courts et neutres : ce sont
#: des marqueurs, pas des libellés — le libellé dépend de l'âge.
FEMININ = "F"
MASCULIN = "H"
AUTRE = "A"
SANS_REPONSE = "N"

CODES = (FEMININ, MASCULIN, AUTRE, SANS_REPONSE)

#: Âge à partir duquel on dit femme / homme plutôt que fille / garçon.
MAJORITE = 18

NON_RENSEIGNE = "Non renseigné"
NON_RENSEIGNE_PLURIEL = "Non renseigné"

_LIBELLES = {
    FEMININ: ("Fille", "Femme", "Filles", "Femmes"),
    MASCULIN: ("Garçon", "Homme", "Garçons", "Hommes"),
    AUTRE: ("Autre", "Autre", "Autres", "Autres"),
    SANS_REPONSE: ("Non précisé", "Non précisé", "Non précisé", "Non précisé"),
}

#: Ce qui a pu être tapé, et ce que ça veut dire. Les clés sont
#: normalisées (minuscules, sans accents, sans ponctuation).
_SYNONYMES = {
    FEMININ: {
        "f", "femme", "femmes", "feminin", "feminine", "fem", "fille", "filles",
        "female", "woman", "women", "mme", "madame", "mlle", "mademoiselle", "2",
    },
    MASCULIN: {
        "h", "m", "homme", "hommes", "masculin", "masculine", "masc",
        "garcon", "garcons", "male", "man", "men", "mr", "monsieur", "1",
    },
    AUTRE: {
        "a", "autre", "autres", "non binaire", "nonbinaire", "nb", "x",
        "indetermine", "intersexe", "other",
    },
    SANS_REPONSE: {
        "n", "nsp", "ne se prononce pas", "ne souhaite pas repondre",
        "prefere ne pas repondre", "preferez ne pas repondre",
        "prefere ne pas le dire", "sans reponse", "refus", "non precise",
    },
}

_INDEX = {valeur: code for code, valeurs in _SYNONYMES.items() for valeur in valeurs}


def _aplatir(valeur) -> str:
    """Minuscules, sans accents, ponctuation ramenée à des espaces simples."""
    if valeur is None:
        return ""
    texte = str(valeur).strip().lower()
    texte = "".join(
        c for c in unicodedata.normalize("NFKD", texte) if not unicodedata.combining(c)
    )
    texte = "".join(c if c.isalnum() else " " for c in texte)
    return " ".join(texte.split())


def normaliser(brut) -> str | None:
    """Ramène n'importe quelle saisie à un code, ou None si on ne sait pas.

    Renvoyer None plutôt que de deviner est volontaire : compter quelqu'un
    dans la mauvaise colonne est pire que de l'afficher « non renseigné »,
    parce que l'erreur devient invisible.
    """
    aplati = _aplatir(brut)
    if not aplati:
        return None
    code = _INDEX.get(aplati)
    if code:
        return code
    # Repli sur le premier mot : « femme (mere de famille) », « homme 45 ans ».
    # Seulement s'il fait au moins trois lettres : les codes d'une seule
    # lettre (f, h, m, a, n) ne valent que seuls. Sinon « n'importe quoi »
    # serait lu « n », donc « ne se prononce pas » — une saisie illisible
    # deviendrait une réponse.
    premier = aplati.split(" ", 1)[0]
    if len(premier) < 3:
        return None
    return _INDEX.get(premier)


def est_mineur(age: int | None) -> bool:
    """Vrai seulement si l'âge est connu ET inférieur à la majorité.

    Un âge inconnu n'est pas un enfant : on retombe sur femme / homme, comme
    l'application le faisait déjà.
    """
    return age is not None and age < MAJORITE


def libelle(brut, age: int | None = None, *, pluriel: bool = False) -> str:
    """Le mot à afficher, selon le genre ET l'âge."""
    code = normaliser(brut)
    if code is None:
        return NON_RENSEIGNE_PLURIEL if pluriel else NON_RENSEIGNE
    mineur, majeur, mineurs, majeurs = _LIBELLES[code]
    if pluriel:
        return mineurs if est_mineur(age) else majeurs
    return mineur if est_mineur(age) else majeur


def age_de(participant, reference: date | None = None) -> int | None:
    """L'âge du participant à la date de référence, si on peut le savoir."""
    calcul = getattr(participant, "age_au", None)
    if callable(calcul):
        try:
            return calcul(reference)
        except Exception:  # noqa: BLE001 — un âge illisible ne casse pas un bilan
            return None
    return getattr(participant, "age", None)


def libelle_participant(participant, reference: date | None = None, *, pluriel: bool = False) -> str:
    """« Fille » ou « Femme » pour cette personne, à cette date-là.

    ``reference`` est la date qui compte : le 31 décembre pour un bilan
    annuel, le jour de la séance pour une feuille d'appel, aujourd'hui pour
    une fiche. Un bilan tiré deux ans plus tard doit redonner les mêmes
    chiffres — d'où le refus systématique d'un âge calculé « maintenant ».
    """
    return libelle(
        getattr(participant, "genre", None),
        age_de(participant, reference),
        pluriel=pluriel,
    )


#: Ordre d'affichage stable des colonnes d'un tableau de répartition.
#: Figé ici pour que tous les écrans et tous les exports présentent les
#: mêmes colonnes dans le même ordre, y compris quand un bucket est vide.
def ordre_pluriels(mineurs: bool = True) -> list[str]:
    colonnes = ["Femmes", "Hommes"]
    if mineurs:
        colonnes = ["Filles", "Garçons"] + colonnes
    return colonnes + ["Autres", "Non précisé", NON_RENSEIGNE_PLURIEL]


def repartition(participants, reference: date | None = None, *, mineurs: bool = True) -> dict[str, int]:
    """Compte les personnes par libellé, colonnes vides comprises.

    Les colonnes vides sont conservées : un tableau où « Garçons » disparaît
    les années sans garçon se lit mal d'une année sur l'autre, et laisse
    croire à un oubli de saisie.
    """
    comptes = {colonne: 0 for colonne in ordre_pluriels(mineurs)}
    for participant in participants or []:
        mot = libelle_participant(participant, reference, pluriel=True)
        if not mineurs and mot in ("Filles", "Garçons"):
            mot = "Femmes" if mot == "Filles" else "Hommes"
        comptes[mot] = comptes.get(mot, 0) + 1
    return comptes


def choix(age: int | None = None) -> list[tuple[str, str]]:
    """Les options d'une liste déroulante, libellées selon l'âge connu.

    Sur la fiche d'un enfant de 8 ans, on choisit entre « Fille » et
    « Garçon » ; sur celle d'un adulte, entre « Femme » et « Homme ». C'est
    la même donnée enregistrée — seul le mot proposé change, pour que la
    personne à l'accueil ne se demande pas si elle coche la bonne case.
    """
    return [(code, libelle(code, age)) for code in CODES]


#: Identifiants stables pour les filtres d'URL : sans accent ni espace, ils
#: traversent un lien, un signet et un export sans se déformer. Les libellés
#: affichés, eux, peuvent changer ; ces clés, non.
GROUPES = {
    "filles": "Filles",
    "garcons": "Garçons",
    "femmes": "Femmes",
    "hommes": "Hommes",
    "autres": "Autres",
    "non_precise": "Non précisé",
    "inconnu": NON_RENSEIGNE_PLURIEL,
}

_GROUPE_PAR_LIBELLE = {valeur: cle for cle, valeur in GROUPES.items()}


def groupe(brut, age: int | None = None) -> str:
    """La clé de filtre correspondant à ce genre et cet âge."""
    return _GROUPE_PAR_LIBELLE.get(libelle(brut, age, pluriel=True), "inconnu")


def groupe_participant(participant, reference: date | None = None) -> str:
    return groupe(getattr(participant, "genre", None), age_de(participant, reference))


def libelle_de_groupe(cle: str) -> str:
    return GROUPES.get((cle or "").strip().lower(), NON_RENSEIGNE_PLURIEL)


def normaliser_colonne(bind, table: str, colonne: str = "genre") -> dict[str, int]:
    """Ramène une colonne de genre déjà remplie sur les codes du référentiel.

    Utilisée par la migration, et testable sans elle. Retourne le compte des
    lignes touchées par valeur d'origine, pour que l'opération se raconte.

    Deux précautions :
    - on lit d'abord le répertoire des valeurs distinctes, puis on écrit une
      fois par valeur : peu de valeurs, beaucoup de lignes ;
    - une saisie illisible est VIDÉE plutôt que laissée telle quelle. Un mot
      inconnu serait rangé différemment par chaque écran ; une case vide,
      elle, se voit et se corrige.
    """
    import sqlalchemy as sa

    inspecteur = sa.inspect(bind)
    if table not in set(inspecteur.get_table_names()):
        return {}
    if colonne not in {c["name"] for c in inspecteur.get_columns(table)}:
        return {}

    distinctes = [
        ligne[0]
        for ligne in bind.execute(
            sa.text(f"SELECT DISTINCT {colonne} FROM {table} WHERE {colonne} IS NOT NULL")
        )
    ]

    touchees: dict[str, int] = {}
    for ancienne in distinctes:
        code = normaliser(ancienne)
        if code == ancienne:
            continue
        if code is None:
            resultat = bind.execute(
                sa.text(f"UPDATE {table} SET {colonne} = NULL WHERE {colonne} = :ancienne"),
                {"ancienne": ancienne},
            )
        else:
            resultat = bind.execute(
                sa.text(f"UPDATE {table} SET {colonne} = :code WHERE {colonne} = :ancienne"),
                {"code": code, "ancienne": ancienne},
            )
        touchees[ancienne] = resultat.rowcount or 0
    return touchees
