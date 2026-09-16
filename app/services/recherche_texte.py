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

## Et PostgreSQL ?

Il ne tombe pas dans le même piège — son ``lower()`` connaît l'Unicode —,
mais il ne résout que la moitié du problème : « Étienne » s'y trouve
lui-même, tandis que « amelie » n'y trouve toujours pas « Amélie ». Or
c'est le cas le plus fréquent à l'accueil : on tape vite, sans accents.

PostgreSQL sait le faire avec son extension ``unaccent``. On la demande
au démarrage. ``CREATE EXTENSION`` réclame des droits que le compte
applicatif n'a pas forcément : si c'est refusé, la recherche retombe sur
``ILIKE`` — un peu moins tolérante, mais intacte. Une extension manquante
ne doit jamais empêcher l'application de démarrer.

L'ordre des opérations compte : ``lower(unaccent(colonne))`` et non
l'inverse. ``unaccent`` ramène d'abord « É » à « E » — de l'ASCII pur —,
que ``lower()`` sait alors traiter quelle que soit la locale du serveur.
Dans l'autre sens, une base créée en locale ``C`` laisserait « É » intact
et la comparaison échouerait de nouveau, exactement comme sur SQLite.
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


# ---------------------------------------------------------------------------
# PostgreSQL : l'extension unaccent
# ---------------------------------------------------------------------------

#: Résultat du sondage, par moteur (une URL = une base). Le sondage ouvre
#: une connexion : le refaire à chaque frappe dans la barre de recherche
#: coûterait plus cher que la recherche elle-même.
_UNACCENT: dict[str, bool] = {}

#: Mot témoin du sondage : il porte une majuscule accentuée (le cas qui
#: cassait) et sa réponse attendue ne laisse aucune place au doute.
_TEMOIN = "Étienne"
_TEMOIN_ATTENDU = "etienne"


def _unaccent_repond(connexion) -> bool:
    """La fonction est-elle là, et donne-t-elle le bon résultat ?

    On l'interroge plutôt que de lire ``pg_extension`` : une extension
    installée dans un schéma absent du ``search_path`` est présente au
    catalogue et introuvable à l'exécution. Seule la réponse fait foi.
    """
    from sqlalchemy import text

    try:
        valeur = connexion.execute(
            text("SELECT lower(unaccent(:mot))"), {"mot": _TEMOIN}
        ).scalar()
    except Exception:  # noqa: BLE001 — extension absente, droits, schéma…
        return False
    return valeur == _TEMOIN_ATTENDU


def _sonder(moteur) -> bool:
    try:
        with moteur.connect() as connexion:
            if _unaccent_repond(connexion):
                return True
    except Exception:  # noqa: BLE001
        return False

    # Absente : on tente de l'installer. Connexion neuve pour l'essai
    # suivant — après une erreur SQL, PostgreSQL refuse tout le reste de
    # la transaction, et le sondage répondrait « non » à tort.
    from sqlalchemy import text

    try:
        with moteur.connect() as connexion:
            connexion.execute(text("CREATE EXTENSION IF NOT EXISTS unaccent"))
            connexion.commit()
    except Exception:  # noqa: BLE001 — droits insuffisants, le plus souvent
        return False

    try:
        with moteur.connect() as connexion:
            return _unaccent_repond(connexion)
    except Exception:  # noqa: BLE001
        return False


def unaccent_disponible(moteur) -> bool:
    """Peut-on comparer sans accents DANS PostgreSQL, sur ce moteur ?

    Faux pour tout autre dialecte : SQLite a sa propre fonction (voir
    ``installer``), et d'une base inconnue on ne suppose rien.
    """
    try:
        if (moteur.dialect.name or "").lower() != "postgresql":
            return False
        cle = str(moteur.url)
    except Exception:  # noqa: BLE001
        return False

    if cle not in _UNACCENT:
        _UNACCENT[cle] = _sonder(moteur)
    return _UNACCENT[cle]


def oublier_le_sondage() -> None:
    """Vide le cache du sondage (bases de test, changement de moteur)."""
    _UNACCENT.clear()
