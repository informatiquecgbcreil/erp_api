"""Import de partenaires depuis un fichier CSV.

Reprend tous les champs d'identification de la fiche partenaire. Tolérant :
- encodage UTF-8 (avec ou sans BOM Excel), cp1252, latin-1 ;
- séparateur de colonnes « ; » (Excel FR) ou « , » ;
- en-têtes insensibles à la casse/accents ;
- valeurs multiples (secteurs, compétences) séparées par « | ».

Upsert par nom (insensible à la casse) : une 2e import de la même liste met à
jour les fiches au lieu de les dupliquer. Les colonnes laissées vides ne
écrasent pas une valeur existante.
"""

from __future__ import annotations

import csv
import io
import json
import unicodedata

from app.extensions import db
from app.models import Partenaire, PartenaireSecteur

# En-tête normalisé -> attribut texte du modèle.
CHAMPS_TEXTE = {
    "nom": "nom",
    "contact_nom": "contact_nom",
    "contact_prenom": "contact_prenom",
    "adresse": "adresse",
    "email_contact": "email_contact",
    "email_general": "email_general",
    "tel_contact": "tel_contact",
    "tel_general": "tel_general",
    "territoire_couvert": "territoire_couvert",
    "modalites_orientation": "modalites_orientation",
    "description": "description",
}

NIVEAUX = {"principal", "relais", "recours"}

# Colonnes attendues (ordre du modèle d'export).
COLONNES = [
    "nom", "contact_nom", "contact_prenom", "adresse",
    "email_contact", "email_general", "tel_contact", "tel_general",
    "secteurs", "competences", "territoire_couvert",
    "modalites_orientation", "niveau_orientation", "description",
]


def _normaliser(s: str | None) -> str:
    s = (s or "").strip().lower()
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    return s.replace(" ", "_").replace("-", "_").replace("'", "")


def _decoder(donnees: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return donnees.decode(enc)
        except UnicodeDecodeError:
            continue
    return donnees.decode("utf-8", "replace")


def _delimiteur(texte: str) -> str:
    premiere = next((l for l in texte.splitlines() if l.strip()), "")
    return ";" if premiere.count(";") >= premiere.count(",") else ","


def _liste(valeur: str) -> list[str]:
    return list(dict.fromkeys(c.strip() for c in (valeur or "").split("|") if c.strip()))


def _resoudre_competences(valeurs: list[str]) -> list[str]:
    """Garde les codes de domaine valides ; tente de mapper un libellé -> code."""
    try:
        from app.partenaires.routes import ORIENTATION_DOMAINES
    except Exception:  # pragma: no cover
        ORIENTATION_DOMAINES = {}
    codes = set(ORIENTATION_DOMAINES.keys())
    label_to_code = {_normaliser(lbl): code for code, lbl in ORIENTATION_DOMAINES.items()}
    out = []
    for v in valeurs:
        n = _normaliser(v)
        if n in codes:
            out.append(n)
        elif n in label_to_code:
            out.append(label_to_code[n])
    return list(dict.fromkeys(out))


def importer_csv(donnees: bytes) -> dict:
    texte = _decoder(donnees)
    if not texte.strip():
        return {"crees": 0, "maj": 0, "ignores": 0, "total": 0, "erreurs": ["Fichier vide."]}

    delim = _delimiteur(texte)
    lecteur = csv.DictReader(io.StringIO(texte), delimiter=delim)
    if not lecteur.fieldnames:
        return {"crees": 0, "maj": 0, "ignores": 0, "total": 0, "erreurs": ["En-tête introuvable."]}

    entetes = {_normaliser(h): h for h in lecteur.fieldnames if h}
    if "nom" not in entetes:
        return {"crees": 0, "maj": 0, "ignores": 0, "total": 0,
                "erreurs": ["Colonne « nom » obligatoire absente de l'en-tête."]}

    crees = maj = ignores = total = 0
    erreurs: list[str] = []

    for i, row in enumerate(lecteur, start=2):  # ligne 1 = en-tête
        if not any((v or "").strip() for v in row.values()):
            continue  # ligne entièrement vide

        def val(champ: str) -> str:
            cle = entetes.get(champ)
            return (row.get(cle) or "").strip() if cle else ""

        nom = val("nom")
        total += 1
        if not nom:
            ignores += 1
            erreurs.append(f"Ligne {i} : nom manquant, ligne ignorée.")
            continue

        existant = Partenaire.query.filter(db.func.lower(Partenaire.nom) == nom.lower()).first()
        p = existant or Partenaire(nom=nom)

        for champ_norm, attr in CHAMPS_TEXTE.items():
            v = val(champ_norm)
            if v:
                setattr(p, attr, v)

        niveau = _normaliser(val("niveau_orientation"))
        if niveau in NIVEAUX:
            p.niveau_orientation = niveau
        elif not existant:
            p.niveau_orientation = "relais"

        comps = _resoudre_competences(_liste(val("competences")))
        if comps:
            p.competences_orientation_json = json.dumps(comps, ensure_ascii=False)

        if existant:
            maj += 1
        else:
            db.session.add(p)
            crees += 1
        db.session.flush()

        secteurs = _liste(val("secteurs"))
        if secteurs:
            PartenaireSecteur.query.filter_by(partenaire_id=p.id).delete()
            for s in secteurs:
                db.session.add(PartenaireSecteur(partenaire_id=p.id, secteur=s))

    db.session.commit()
    return {"crees": crees, "maj": maj, "ignores": ignores, "total": total, "erreurs": erreurs}
