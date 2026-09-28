"""Réservation atomique d'un numéro dans la transaction du document.

Garde-fou contre la restauration d'une sauvegarde (audit, mineur caisse) :
les reçus fiscaux, factures et avoirs émis APRÈS cette sauvegarde existent
sur papier. Les renuméroter serait bien pire qu'un trou. Chaque numéro
attribué est donc aussi noté dans un registre hors base et hors des
sauvegardes ordinaires (dossier ``runtime`` des données) ; la numérotation
repart toujours au-delà du plus grand numéro jamais émis, base restaurée ou
non.

Politique : un numéro réservé puis abandonné (transaction annulée) reste
consommé et laisse un trou — c'est voulu. Le registre est donc écrit AVANT
la validation SQL, et durablement : si cette écriture échoue, le document
n'est pas émis (``RegistreErreur``), plutôt que d'émettre un numéro que le
registre ne protégerait plus.

Le registre est un objet JSON ``{"espace": plus grand numéro}`` (ex.
``"don:2026": 42``, ``"facture:2026": 17``, ``"avoir:2026": 3``) — même
format qu'avant, lisible par une version précédente en cas de retour
arrière. La mise à jour se fait sous verrou (``registre_externe``) et ne fait
jamais diminuer un maximum.

Registre illisible (fichier endommagé) : il est mis de côté, jamais
supprimé, et reconstitué depuis sa copie précédente et les compteurs de la
base ; l'incident est journalisé et affiché dans Contrôle. Registre
inaccessible (droits) : l'émission est refusée avec un message clair.
"""
from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import case

from app.extensions import db
from app.models import FinancialSequence
from app.services import registre_externe as fichier_registre
from app.services.registre_externe import RegistreEndommage, RegistreErreur

NOM_FICHIER = "numeros-emis.json"

journal = logging.getLogger(__name__)


def _registre() -> Path | None:
    """Registre des numéros émis, dans le dossier des données de
    l'installation (APP_DATA_DIR, défini par l'installateur Windows), jamais
    dans instance/ ni uploads/ que les sauvegardes restaurent."""
    dossier = fichier_registre.dossier_runtime()
    return dossier / NOM_FICHIER if dossier else None


def _valider(donnees: dict, nom: str) -> dict[str, int]:
    propres: dict[str, int] = {}
    for espace, valeur in donnees.items():
        if not isinstance(espace, str) or isinstance(valeur, bool) or not isinstance(valeur, int) or valeur < 0:
            raise RegistreEndommage("Le registre des numéros émis est endommagé.",
                                    f"entrée inattendue dans {nom}")
        propres[espace] = valeur
    return propres


def fusionner(*registres: dict) -> dict[str, int]:
    """Plus grand numéro par espace : jamais de retour en arrière."""
    resultat: dict[str, int] = {}
    for registre in registres:
        for espace, valeur in (registre or {}).items():
            if int(valeur) > resultat.get(espace, 0):
                resultat[espace] = int(valeur)
    return resultat


def _compteurs_base() -> dict[str, int]:
    try:
        return {ligne.namespace: int(ligne.value or 0) for ligne in FinancialSequence.query.all()}
    except Exception:  # noqa: BLE001 — table absente (base pas encore migrée)
        return {}


def _lire_ou_reconstituer(chemin: Path) -> dict[str, int]:
    """Lecture sous verrou. Fichier endommagé : mis de côté et reconstitué
    (copie précédente + compteurs en base), incident signalé."""
    try:
        return _valider(fichier_registre.lire(chemin) or {}, chemin.name)
    except RegistreEndommage as exc:
        # (Inaccessible — droits, disque — n'est PAS rattrapé : rien inventé.)
        precedent = chemin.with_name(chemin.name + ".prec")
        try:
            copie = _valider(fichier_registre.lire(precedent) or {}, precedent.name)
        except RegistreEndommage:
            copie = {}
        mis_de_cote = fichier_registre.mettre_de_cote(chemin)
        reconstitue = fusionner(copie, _compteurs_base())
        fichier_registre.ecrire(chemin, reconstitue)
        journal.error("Registre des numéros émis endommagé (%s) : mis de côté sous %s, reconstitué depuis "
                      "la copie précédente et les compteurs de la base.", exc.detail,
                      mis_de_cote.name if mis_de_cote else "?")
        return reconstitue



def lire_registre() -> dict[str, int]:
    """Tous les maxima connus (sous verrou). Registre non tenu : {}."""
    chemin = _registre()
    if chemin is None:
        return {}
    with fichier_registre.verrou(chemin):
        fichier_registre.nettoyer_temporaires(chemin)
        return _lire_ou_reconstituer(chemin)


def noter_maxima(maxima: dict[str, int]) -> dict[str, int]:
    """Fusionne des maxima dans le registre (max par espace), sous verrou.
    Sert à chaque émission, à la restauration d'un lot et au transfert."""
    chemin = _registre()
    if chemin is None:
        return {}
    with fichier_registre.verrou(chemin):
        fichier_registre.nettoyer_temporaires(chemin)
        actuel = _lire_ou_reconstituer(chemin)
        nouveau = fusionner(actuel, _valider(dict(maxima), "maxima fournis"))
        if nouveau != actuel or not chemin.exists():
            fichier_registre.ecrire(chemin, nouveau)
        return nouveau


def plus_haut_emis(namespace: str) -> int:
    return int(lire_registre().get(namespace, 0))


def next_number(namespace, existing_numbers):
    maximum = plus_haut_emis(namespace)
    for value in existing_numbers:
        try:
            maximum = max(maximum, int(str(value).rsplit("-", 1)[1]))
        except (ValueError, IndexError):
            continue
    if db.engine.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    table = FinancialSequence.__table__
    statement = insert(table).values(namespace=namespace, value=maximum + 1)
    statement = statement.on_conflict_do_update(index_elements=[table.c.namespace], set_={
        "value": case((table.c.value < maximum, maximum + 1), else_=table.c.value + 1)
    }).returning(table.c.value)
    numero = db.session.execute(statement).scalar_one()
    # Avant la validation SQL : un numéro abandonné reste consommé, mais un
    # numéro émis n'échappe jamais au registre. Échec = document non émis.
    noter_maxima({namespace: numero})
    return numero


__all__ = ["RegistreErreur", "fusionner", "lire_registre", "next_number", "noter_maxima", "plus_haut_emis"]
