"""Comparer du texte français dans la base, accents compris.

Bug constaté : **un prénom commençant par une majuscule accentuée était
introuvable, même tapé exactement comme il est enregistré.** Chercher
« Étienne » ne rendait rien. Chercher « amelie » ne trouvait pas « Amélie ».

La cause : la recherche compare ``lower(colonne) LIKE lower(motif)``, mais
les deux ``lower`` ne sont pas le même. Celui de Python connaît l'Unicode
(« É » -> « é »), celui de SQLite ne met en minuscules que l'ASCII et laisse
« É » tel quel. Les deux côtés ne se rencontrent jamais.

On donne donc à SQLite une fonction qui fait exactement ce que fait Python :
minuscules Unicode, puis suppression des signes diacritiques. Le motif étant
normalisé par la MÊME fonction, les deux côtés sont garantis symétriques —
et « amelie », « Amélie », « AMÉLIE » se retrouvent mutuellement.

Coût : la fonction est rappelée pour chaque valeur parcourue. Mesuré sur
20 000 fiches et neuf colonnes, un balayage complet sans résultat passe de
~22 ms à ~220 ms ; dès qu'il y a des résultats, la limite arrête le
parcours et le surcoût disparaît. Une recherche qui ne trouve pas « Étienne »
coûte plus cher que 200 ms.

PostgreSQL n'a pas le problème : son ``lower()`` est unicode. Il y garde le
comportement d'origine — « Étienne » s'y trouve déjà. Seul « amelie » ->
« Amélie » y resterait à faire (extension ``unaccent``), ce qui n'a pas lieu
d'être tant que SQLite est la base par défaut.
"""
from __future__ import annotations

import unicodedata

from sqlalchemy import event
from sqlalchemy.engine import Engine

#: Nom de la fonction côté SQL. Volontairement distinct des fonctions
#: natives : on n'écrase aucun comportement existant de la base.
NOM_FONCTION_SQL = "sans_accent"

_installee = False


def sans_accent(valeur) -> str | None:
    """Minuscules et sans diacritiques. « Étienne » -> « etienne »."""
    if valeur is None:
        return None
    texte = str(valeur).lower()
    return "".join(
        caractere
        for caractere in unicodedata.normalize("NFKD", texte)
        if not unicodedata.combining(caractere)
    )


def installer() -> None:
    """Branche la fonction sur chaque connexion SQLite ouverte.

    Idempotent : la fabrique d'application est appelée plusieurs fois en
    test, et empiler des écouteurs ferait travailler la même fonction
    autant de fois qu'il y a eu d'applications.
    """
    global _installee
    if _installee:
        return
    _installee = True

    @event.listens_for(Engine, "connect")
    def _ajouter_la_fonction(connexion_dbapi, _record):  # pragma: no cover - branché au runtime
        creer = getattr(connexion_dbapi, "create_function", None)
        if creer is None:
            return  # pas SQLite : rien à ajouter
        try:
            # deterministic=True autorise SQLite à mettre le résultat en
            # cache ; refusé par les vieilles versions, d'où le repli.
            creer(NOM_FONCTION_SQL, 1, sans_accent, deterministic=True)
        except (TypeError, ValueError):
            try:
                creer(NOM_FONCTION_SQL, 1, sans_accent)
            except Exception:  # noqa: BLE001 — une recherche dégradée vaut mieux qu'une base morte
                pass
        except Exception:  # noqa: BLE001
            pass
