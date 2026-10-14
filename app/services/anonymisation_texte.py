"""Retirer les données personnelles d'un texte de diagnostic.

Un rapport d'erreur doit pouvoir être collé dans une IA de programmation ou
envoyé à quelqu'un hors de la structure. Or les journaux d'erreurs peuvent
contenir des noms (un message qui cite une fiche), des e-mails (les comptes
des salariés), des téléphones, des adresses, des jetons de signature.

Deux filets, du plus sûr au plus large :

1. **Les identités connues de la base** : noms et prénoms des participants,
   inscrits, membres de foyer, salariés, comptes, contacts et loueurs de
   salle. Chaque mot (3 lettres et plus, sans accent ni casse) est remplacé
   par ``[NOM]``.
2. **Les formes reconnaissables** : e-mail, téléphone, IBAN, adresse postale
   (« 12 rue … »), longue suite de chiffres (n° de sécurité sociale, SIRET),
   jeton (signature à distance, flux agenda), valeur d'un paramètre d'URL,
   nom du compte Windows dans un chemin.

Le premier filet laisse passer un nom absent de la base ; le second, un nom
sans forme reconnaissable. Ensemble, ils couvrent ce que l'application écrit
elle-même dans ses journaux. Le texte du **code source** cité par une trace
n'est pas touché : il est public, et c'est ce dont l'IA a besoin.
"""
from __future__ import annotations

import re

from app.services.recherche_texte import sans_accent

NOM = "[NOM]"

#: Mots techniques qui peuvent figurer dans un message d'erreur et qu'on
#: ne masque pas même si une fiche porte ce nom (« Martin » oui, « Error » non).
MOTS_TECHNIQUES = {
    "exception", "error", "erreur", "warning", "critical", "traceback", "file", "line",
    "most", "recent", "call", "last", "none", "true", "false", "self", "return", "get",
    "post", "put", "delete", "head", "app", "the", "and", "for", "not", "with", "from",
    "key", "value", "type", "name", "index", "base", "page", "route", "admin", "test",
    "les", "des", "une", "par", "sur", "pour", "dans", "avec", "pas", "est", "aux",
}

_MOT = re.compile(r"[^\W\d_]{3,}", re.UNICODE)

_FORMES = [
    # Adresse électronique.
    (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "[EMAIL]"),
    # IBAN (avant les numéros longs, qu'il contient).
    (re.compile(r"\b[A-Z]{2}\d{2}(?:\s?[\dA-Z]{4}){3,7}(?:\s?[\dA-Z]{1,4})?\b"), "[IBAN]"),
    # Téléphone français, avec ou sans séparateurs.
    (re.compile(r"(?<!\d)(?:\+33\s?|0)[1-9](?:[\s.-]?\d{2}){4}(?!\d)"), "[TÉLÉPHONE]"),
    # Adresse postale : numéro puis type de voie, jusqu'à la virgule ou la fin.
    (re.compile(
        r"\b\d{1,4}\s?(?:bis|ter)?,?\s+(?:rue|avenue|av\.?|boulevard|bd|allée|allee|chemin|impasse|"
        r"place|route|square|cours|quai|résidence|residence|cité|cite|sentier|passage|voie)\b[^,\n\"'\]]*",
        re.IGNORECASE), "[ADRESSE]"),
    # Longue suite de chiffres : sécurité sociale, SIRET, numéro collé.
    (re.compile(r"(?<!\d)\d{9,}(?!\d)"), "[NUMÉRO]"),
    # Jeton : signature à distance, flux agenda (au moins un chiffre).
    (re.compile(r"\b(?=[A-Za-z_-]*\d)[A-Za-z0-9_-]{24,}\b"), "[JETON]"),
    # Valeur d'un paramètre d'URL (?q=Dupont, &email=…).
    (re.compile(r"([?&][\w.-]+=)[^&\s\]\"']+"), r"\1…"),
    # Compte Windows ou Linux dans un chemin.
    (re.compile(r"(?i)([A-Z]:\\Users\\)[^\\\"']+"), r"\1[UTILISATEUR]"),
    (re.compile(r"(/home/)[^/\s\"']+"), r"\1[UTILISATEUR]"),
]


def _mots_de(valeur) -> set[str]:
    return {m for m in (sans_accent(x) for x in _MOT.findall(str(valeur or ""))) if m}


def identites_connues() -> set[str]:
    """Les mots des noms et prénoms enregistrés, sans accent ni casse.

    Lu une fois par rapport : quelques milliers de fiches, quelques
    millisecondes.
    """
    from app.extensions import db
    from app.models import (
        Foyer,
        InscriptionAnnuelle,
        InscriptionAnnuelleMembre,
        Participant,
        Partenaire,
        Preneur,
        Salarie,
        User,
    )

    colonnes = [
        Participant.nom, Participant.prenom,
        InscriptionAnnuelle.nom, InscriptionAnnuelle.prenom,
        InscriptionAnnuelleMembre.nom, InscriptionAnnuelleMembre.prenom,
        Foyer.nom, Salarie.nom, Salarie.prenom, User.nom,
        Partenaire.contact_nom, Preneur.nom, Preneur.contact_nom,
    ]
    mots: set[str] = set()
    for colonne in colonnes:
        for (valeur,) in db.session.query(colonne).filter(colonne.isnot(None)).distinct():
            mots |= _mots_de(valeur)
    return mots - MOTS_TECHNIQUES


def anonymiser(texte: str | None, identites: set[str] | frozenset = frozenset()) -> str:
    """Le texte sans données personnelles (formes reconnues, puis identités)."""
    sortie = str(texte or "")
    for motif, remplacement in _FORMES:
        sortie = motif.sub(remplacement, sortie)
    if identites:
        sortie = _MOT.sub(lambda m: NOM if sans_accent(m.group(0)) in identites else m.group(0), sortie)
    return sortie
