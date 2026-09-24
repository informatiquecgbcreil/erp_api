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


def _like_prefixes(nom: str) -> list[str]:
    """Motifs LIKE couvrant les variantes accentuées et de casse des 2
    premières lettres, pour ne pas rater un homonyme à cause d'un accent.

    La précision reste assurée en aval par ``_proches`` (comparaison sur les
    formes normalisées) : ici on cherche juste à ne pas perdre de candidat."""
    norm = normaliser_nom(nom)
    if not norm:
        brut = (nom or "").strip()
        return [f"{brut[:2]}%"] if brut else ["%"]
    premiers = _ACCENTS.get(norm[0], norm[0])
    seconde = norm[1] if len(norm) > 1 else ""
    motifs: set[str] = set()
    for lettre in premiers:
        for variante in {lettre, lettre.upper()}:
            motifs.add(f"{variante}{seconde}%")
            if seconde:
                motifs.add(f"{variante}{seconde.upper()}%")
    motifs.add(f"{(nom or '').strip()[:2]}%")
    return sorted(m for m in motifs if m)


def normaliser_nom(texte: str) -> str:
    """Minuscules, sans accents ni caractères non alphabétiques."""
    texte = unicodedata.normalize("NFKD", (texte or "")).encode("ascii", "ignore").decode("ascii")
    return "".join(c for c in texte.lower() if c.isalpha())


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


def candidats_doublons(nom: str, prenom: str, *, exclure_id: int | None = None) -> list[Participant]:
    """Personnes proches d'un nom/prénom saisi (anti-doublons).

    Deux champs sont « proches » si, après normalisation (casse/accents) :
    identiques, mêmes squelettes (lettres doublées réduites), ou l'un préfixe
    de l'autre (>= 3 lettres). Match global si le nom ET le prénom sont
    proches, avec au moins l'un des deux strictement identique.
    """
    n, p = normaliser_nom(nom), normaliser_nom(prenom)
    if not n or not p:
        return []

    prefixes = _like_prefixes(nom)
    prefiltre = db.or_(*[Participant.nom.like(motif) for motif in prefixes])
    candidats: list[Participant] = []
    for cand in Participant.query.filter(prefiltre).limit(400).all():
        if exclure_id is not None and cand.id == exclure_id:
            continue
        cn, cp = normaliser_nom(cand.nom), normaliser_nom(cand.prenom)
        if not cn or not cp:
            continue
        if (cn == n or cp == p) and _proches(cn, n) and _proches(cp, p):
            candidats.append(cand)
        if len(candidats) >= 5:
            break
    return candidats
