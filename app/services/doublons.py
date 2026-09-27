"""Détection d'homonymes proches à la création d'une fiche participant.

Une fiche en double fausse toutes les statistiques : ce garde-fou propose
les fiches ressemblantes AVANT de créer, sans jamais bloquer (on peut
toujours forcer la création). Partagé entre le kiosque public et la
création manuelle (/participants/new) pour un comportement identique.
"""
from __future__ import annotations

import unicodedata

from app.extensions import db
from app.models import Participant


# Variantes accentuées par lettre de base, pour élargir le préfiltre SQL :
# « Éric » doit retrouver « Eric », « Ålard » retrouver « Allard », etc.
_ACCENTS = {
    "a": "aàáâãäå",
    "c": "cç",
    "e": "eéèêë",
    "i": "iîïìí",
    "n": "nñ",
    "o": "oôöòóõ",
    "u": "uùûüú",
    "y": "yÿý",
}


_LIGATURES = str.maketrans({"œ": "oe", "Œ": "oe", "æ": "ae", "Æ": "ae", "ß": "ss"})

#: Particules en tête de nom : « De La Fontaine » se compare aussi à
#: « Fontaine », « Da Silva » à « Silva » (audit 5.9).
PARTICULES = {"de", "la", "le", "les", "du", "des", "da", "das", "do", "dos",
              "di", "del", "della", "van", "von", "der", "den", "ten", "d", "l"}


def normaliser_nom(texte: str) -> str:
    """Minuscules, sans accents, ligatures développées, sans caractère non
    alphabétique : « N'Diaye » -> « ndiaye », « Lecœur » -> « lecoeur »."""
    texte = unicodedata.normalize("NFKD", (texte or "").translate(_LIGATURES)).encode("ascii", "ignore").decode("ascii")
    return "".join(c for c in texte.lower() if c.isalpha())


def _mots(texte: str) -> list[str]:
    brut = unicodedata.normalize("NFKD", (texte or "").translate(_LIGATURES)).encode("ascii", "ignore").decode("ascii")
    for separateur in "'\u2019-.,`":
        brut = brut.replace(separateur, " ")
    return [normaliser_nom(m) for m in brut.split() if normaliser_nom(m)]


def coeur_nom(texte: str) -> str:
    """Le nom sans ses particules de tête (« De La Fontaine » -> « fontaine »).
    Une particule seule, ou suivie d'une lettre (« N'Diaye »), est gardée."""
    mots = _mots(texte)
    while len(mots) > 1 and mots[0] in PARTICULES and len(mots[1]) > 1:
        mots = mots[1:]
    return "".join(mots)


def squelette_nom(texte: str) -> str:
    """Forme normalisée avec les lettres doublées réduites : rend identiques
    « Mohammed » et « Mohamed », « Alard » et « Allard »."""
    normalise = normaliser_nom(texte)
    out: list[str] = []
    for c in normalise:
        if not out or out[-1] != c:
            out.append(c)
    return "".join(out)


def _une_faute(a: str, b: str) -> bool:
    """Au plus une lettre ajoutée, retirée ou remplacée (« michut »/« michot »)."""
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) > len(b):
        a, b = b, a
    i = j = ecarts = 0
    while i < len(a) and j < len(b):
        if a[i] == b[j]:
            i += 1; j += 1
            continue
        ecarts += 1
        if ecarts > 1:
            return False
        if len(a) == len(b):
            i += 1
        j += 1
    return ecarts + (len(b) - j) + (len(a) - i) <= 1


def _proches(a: str, b: str) -> bool:
    if a == b:
        return True
    if squelette_nom(a) == squelette_nom(b):
        return True
    if len(a) >= 3 and len(b) >= 3 and (a.startswith(b) or b.startswith(a)):
        return True
    # Faute de frappe sur un nom assez long pour qu'elle ne rapproche pas
    # deux personnes différentes (« Léa »/« Léo » restent distincts).
    return min(len(a), len(b)) >= 5 and _une_faute(a, b)


def _formes(texte: str) -> set[str]:
    return {f for f in (normaliser_nom(texte), coeur_nom(texte)) if f}


def _champ_identique(formes_a: set[str], formes_b: set[str]) -> bool:
    return bool(formes_a & formes_b)


def _champ_proche(formes_a: set[str], formes_b: set[str]) -> bool:
    return any(_proches(a, b) for a in formes_a for b in formes_b)


def _ressemble(n: set[str], p: set[str], cn: set[str], cp: set[str]) -> bool:
    """Nom ET prénom proches, dont l'un strictement identique."""
    return ((_champ_identique(cn, n) or _champ_identique(cp, p))
            and _champ_proche(cn, n) and _champ_proche(cp, p))


def _prefiltre(nom: str, prenom: str):
    """Présélection SQL large (la précision vient de ``_ressemble``) :
    - initiales du nom et du prénom, au début d'un mot (particules) et dans
      les deux sens (nom et prénom inversés) ;
    - ou l'un des deux champs identique, l'autre partageant quelques lettres
      au-delà des deux premières (faute sur l'initiale)."""
    from app.services.recherche_texte import _normaliseur, normaliser_saisie
    colonne, sans_accents = _normaliseur()
    col_nom, col_prenom = colonne(Participant.nom), colonne(Participant.prenom)

    def initiales(lettre):
        variantes = _ACCENTS.get(lettre, lettre) if not sans_accents else lettre
        return {v for v in variantes}

    def commence_mot(col, lettre):
        motifs = []
        for v in initiales(lettre):
            motifs += [col.like(f"{v}%"), col.like(f"% {v}%"), col.like(f"%-{v}%")]
        return db.or_(*motifs)

    n0, p0 = coeur_nom(nom)[:1], normaliser_nom(prenom)[:1]
    conditions = [
        db.and_(commence_mot(col_nom, n0), commence_mot(col_prenom, p0)),
        db.and_(commence_mot(col_nom, p0), commence_mot(col_prenom, n0)),
    ]
    saisie_nom, saisie_prenom = normaliser_saisie(nom.strip(), sans_accents), normaliser_saisie(prenom.strip(), sans_accents)
    milieu_nom, milieu_prenom = coeur_nom(nom)[2:5], normaliser_nom(prenom)[2:5]
    if len(milieu_nom) >= 2:
        conditions.append(db.and_(col_prenom == saisie_prenom, col_nom.like(f"%{milieu_nom}%")))
    if len(milieu_prenom) >= 2:
        conditions.append(db.and_(col_nom == saisie_nom, col_prenom.like(f"%{milieu_prenom}%")))
    return db.or_(*conditions)


def candidats_doublons(nom: str, prenom: str, *, exclure_id: int | None = None) -> list[Participant]:
    """Personnes proches d'un nom/prénom saisi (anti-doublons).

    Deux champs sont « proches » si, après normalisation (casse, accents,
    apostrophes, ligatures, particules de tête) : identiques, mêmes
    squelettes (lettres doublées réduites), l'un préfixe de l'autre
    (>= 3 lettres), ou une faute de frappe sur un nom d'au moins 5 lettres.
    Match global si le nom ET le prénom sont proches, avec au moins l'un des
    deux strictement identique, dans un sens ou l'autre (nom et prénom
    inversés). Les fiches les plus récentes passent en premier.
    """
    n, p = _formes(nom), _formes(prenom)
    if not n or not p:
        return []

    candidats: list[Participant] = []
    requete = (Participant.query.filter(_prefiltre(nom, prenom))
               .filter(~Participant.nom.like("ANONYME%"))
               .order_by(Participant.id.desc()).limit(400))
    for cand in requete.all():
        if exclure_id is not None and cand.id == exclure_id:
            continue
        cn, cp = _formes(cand.nom), _formes(cand.prenom)
        if not cn or not cp:
            continue
        if _ressemble(n, p, cn, cp) or _ressemble(n, p, cp, cn):
            candidats.append(cand)
        if len(candidats) >= 5:
            break
    return candidats
