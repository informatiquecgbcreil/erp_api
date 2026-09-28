"""Dons & reçus fiscaux — numérotation et montant en toutes lettres.

Le reçu fiscal (modèle CERFA 11580) exige un numéro d'ordre unique et le
montant en chiffres ET en toutes lettres. La numérotation est annuelle et
séquentielle (2026-0001, 2026-0002, …), un reçu annulé conserve son numéro.
"""
from __future__ import annotations

from app.extensions import db
from app.models import Don


def prochain_numero(annee: int) -> str:
    """Numéro d'ordre suivant pour l'année (séquence continue, annulés inclus)."""
    from app.services.financial_sequence import next_number
    existing = db.session.query(Don.numero).filter(Don.annee == annee).all()
    number = next_number(f"don:{annee}", [r[0] for r in existing])
    return f"{annee}-{number:04d}"


_UNITES = ["zéro", "un", "deux", "trois", "quatre", "cinq", "six", "sept", "huit", "neuf",
           "dix", "onze", "douze", "treize", "quatorze", "quinze", "seize",
           "dix-sept", "dix-huit", "dix-neuf"]
_DIZAINES = {20: "vingt", 30: "trente", 40: "quarante", 50: "cinquante", 60: "soixante"}


def _moins_de_cent(n: int, final: bool = True) -> str:
    """``final`` : le nombre termine l'expression. « Vingt » et « cent »
    multipliés ne prennent le pluriel qu'en fin de nombre : quatre-vingts,
    mais quatre-vingt mille (mille est invariable et n'est pas un nom)."""
    if n < 20:
        return _UNITES[n]
    if n < 70:
        d, u = divmod(n, 10)
        base = _DIZAINES[d * 10]
        if u == 0:
            return base
        if u == 1:
            return f"{base} et un"
        return f"{base}-{_UNITES[u]}"
    if n < 80:   # 70-79 : soixante-dix…
        reste = n - 60
        if reste == 11:
            return "soixante et onze"
        return f"soixante-{_UNITES[reste]}"
    # 80-99 : quatre-vingt…
    reste = n - 80
    if reste == 0:
        return "quatre-vingts" if final else "quatre-vingt"
    return f"quatre-vingt-{_UNITES[reste]}"


def _moins_de_mille(n: int, final: bool = True) -> str:
    if n < 100:
        return _moins_de_cent(n, final)
    c, reste = divmod(n, 100)
    if c == 1:
        prefixe = "cent"
    else:
        prefixe = f"{_UNITES[c]} cent" + ("s" if reste == 0 and final else "")
    if reste == 0:
        return prefixe
    return f"{prefixe} {_moins_de_cent(reste, final)}"


def _entier_en_lettres(n: int) -> str:
    if n == 0:
        return "zéro"
    if n >= 1_000_000:
        millions, reste = divmod(n, 1_000_000)
        libelle = "million" if millions == 1 else "millions"
        suite = f" {_entier_en_lettres(reste)}" if reste else ""
        # « Million » est un nom : deux cents millions, quatre-vingts millions.
        return f"{_entier_en_lettres(millions)} {libelle}{suite}"
    if n >= 1000:
        milliers, reste = divmod(n, 1000)
        # « Mille » est invariable et fige ce qui précède : deux cent mille.
        prefixe = "mille" if milliers == 1 else f"{_moins_de_mille(milliers, final=False)} mille"
        if reste == 0:
            return prefixe
        return f"{prefixe} {_moins_de_mille(reste)}"
    return _moins_de_mille(n)


def montant_en_lettres(montant: float) -> str:
    """« 1 234,56 » -> « mille deux cent trente-quatre euros et cinquante-six centimes »."""
    montant = round(float(montant or 0), 2)
    euros = int(montant)
    centimes = int(round((montant - euros) * 100))
    libelle_euros = "euro" if euros in (0, 1) else "euros"
    if euros >= 1_000_000 and euros % 1_000_000 == 0:
        libelle_euros = "d'euros"  # un million d'euros, deux millions d'euros
    texte = f"{_entier_en_lettres(euros)} {libelle_euros}"
    if centimes:
        libelle_cts = "centime" if centimes == 1 else "centimes"
        texte += f" et {_entier_en_lettres(centimes)} {libelle_cts}"
    return texte
