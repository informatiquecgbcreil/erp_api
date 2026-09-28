"""Réservation atomique d'un numéro dans la transaction du document.

Garde-fou contre la restauration d'une sauvegarde (audit, mineur caisse) :
les reçus fiscaux, factures et avoirs émis APRÈS cette sauvegarde existent
sur papier. Les renuméroter serait bien pire qu'un trou. Chaque numéro
attribué est donc aussi noté dans un registre hors base et hors des
sauvegardes (dossier ``runtime`` des données) ; la numérotation repart
toujours au-delà du plus grand numéro jamais émis, base restaurée ou non.
Un numéro réservé puis abandonné (transaction annulée) laisse un trou :
c'est voulu.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from sqlalchemy import case

from app.extensions import db
from app.models import FinancialSequence


def _registre() -> Path | None:
    """Registre des numéros émis, dans le dossier des données de
    l'installation (APP_DATA_DIR, défini par l'installateur Windows), jamais
    dans instance/ ni uploads/ que les sauvegardes restaurent."""
    racine = os.environ.get("APP_DATA_DIR")
    return Path(racine) / "runtime" / "numeros-emis.json" if racine else None


def _lire() -> dict:
    chemin = _registre()
    try:
        return json.loads(chemin.read_text(encoding="utf-8")) if chemin and chemin.exists() else {}
    except (OSError, ValueError):
        return {}


def _noter(namespace: str, valeur: int) -> None:
    chemin = _registre()
    if chemin is None:
        return
    try:
        chemin.parent.mkdir(parents=True, exist_ok=True)
        marques = _lire()
        if int(marques.get(namespace, 0)) >= valeur:
            return
        marques[namespace] = valeur
        temporaire = chemin.with_suffix(".tmp")
        temporaire.write_text(json.dumps(marques, sort_keys=True), encoding="utf-8")
        temporaire.replace(chemin)
    except OSError:
        pass  # Le compteur en base reste la protection principale.


def plus_haut_emis(namespace: str) -> int:
    try:
        return int(_lire().get(namespace, 0))
    except (TypeError, ValueError):
        return 0


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
    _noter(namespace, numero)
    return numero
