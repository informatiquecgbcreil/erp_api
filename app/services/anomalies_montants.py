"""Montants non finis hérités d'une ancienne version (audit C1, suite).

Les nouvelles saisies sont protégées (``app.utils.montants``), mais une base
reprise peut contenir des « NaN » ou des infinis enregistrés avant. Ils ne
sont jamais effacés ni remplacés d'office : on ne peut pas deviner le
montant réel. Cette page les liste ; une personne habilitée indique le
montant réellement constaté (0 si aucune somme n'a existé) avec un motif,
l'ancienne valeur est conservée au journal d'audit, puis les contraintes de
base que la migration avait dû laisser de côté sont posées.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text

from app.extensions import db
from app.utils.montants import parse_montant

BORNE = 1_000_000

#: (table, colonne, libellé) des montants d'argent contrôlés.
COLONNES = [
    ("paiement", "montant", "Versement sur une cotisation"),
    ("encaissement", "montant", "Encaissement"),
    ("don", "montant", "Don"),
    ("caisse_mouvement", "montant", "Mouvement de caisse"),
    ("caisse_mouvement", "ecart", "Écart de comptage"),
    ("cotisation", "montant_du", "Montant dû d'une cotisation"),
    ("tarif_bareme", "montant", "Tarif du barème"),
    ("inscription_annuelle", "reglement_montant", "Somme notée sur un bulletin"),
    ("reservation", "montant_manuel", "Prix imposé d'une location"),
    ("reservation", "acompte_montant", "Acompte d'une location"),
    ("reservation", "caution_montant", "Caution d'une location"),
]

#: Contraintes posées par la migration f3b8d1a6c902 quand les données le permettent.
CONTRAINTES = {
    ("paiement", "montant"): "ck_paiement_montant_fini",
    ("don", "montant"): "ck_don_montant_fini",
    ("caisse_mouvement", "montant"): "ck_caisse_mouvement_montant_fini",
    ("caisse_mouvement", "ecart"): "ck_caisse_mouvement_ecart_fini",
    ("encaissement", "montant"): "ck_encaissement_montant_fini",
}


@dataclass
class Anomalie:
    table: str
    colonne: str
    libelle: str
    ligne_id: int
    valeur: str


def _condition(colonne: str) -> str:
    # NaN est « plus grand que tout » pour PostgreSQL : BETWEEN l'exclut,
    # comme l'infini. SQLite stocke NaN comme NULL (rien à trouver).
    return f"{colonne} IS NOT NULL AND NOT ({colonne} BETWEEN -{BORNE} AND {BORNE})"


def _tables() -> set[str]:
    from sqlalchemy import inspect
    return set(inspect(db.engine).get_table_names())


def lister() -> list[Anomalie]:
    presentes = _tables()
    resultat: list[Anomalie] = []
    for table, colonne, libelle in COLONNES:
        if table not in presentes:
            continue
        lignes = db.session.execute(text(
            f"SELECT id, CAST({colonne} AS VARCHAR) FROM {table} WHERE {_condition(colonne)} ORDER BY id"
        )).fetchall()
        resultat += [Anomalie(table, colonne, libelle, int(i), str(v)) for i, v in lignes]
    return resultat


def compter() -> int:
    presentes = _tables()
    total = 0
    for table, colonne, _ in COLONNES:
        if table in presentes:
            total += int(db.session.execute(text(
                f"SELECT count(*) FROM {table} WHERE {_condition(colonne)}")).scalar() or 0)
    return total


def corriger(table: str, colonne: str, ligne_id: int, montant_constate, motif: str, *, user_id=None) -> None:
    """Remplace un montant non fini par le montant réellement constaté."""
    if (table, colonne) not in {(t, c) for t, c, _ in COLONNES}:
        raise ValueError("Colonne non prise en charge.")
    motif = (motif or "").strip()
    if len(motif) < 3:
        raise ValueError("Le motif est obligatoire : il sera conservé au journal.")
    valeur = parse_montant(montant_constate, negatif=(colonne == "ecart"))
    if valeur is None:
        raise ValueError("Indiquez le montant réellement constaté (0 si aucune somme n'a existé).")
    ancienne = db.session.execute(text(
        f"SELECT CAST({colonne} AS VARCHAR) FROM {table} WHERE id = :i AND {_condition(colonne)}"),
        {"i": ligne_id}).scalar()
    if ancienne is None:
        raise ValueError("Ce montant n'est pas (ou plus) en anomalie.")
    db.session.execute(text(f"UPDATE {table} SET {colonne} = :v WHERE id = :i"), {"v": valeur, "i": ligne_id})
    if table == "paiement" and abs(valeur) > 0.009:
        _encaissement_du_versement(ligne_id)
    db.session.commit()
    from app.services.audit import journaliser
    journaliser("montant.anomalie_corrigee", cible=f"{table} #{ligne_id}",
                details={"colonne": colonne, "ancienne_valeur": ancienne, "montant_constate": valeur,
                         "motif": motif[:255]})
    poser_contraintes()


def _encaissement_du_versement(paiement_id: int) -> None:
    """Un versement corrigé reçoit l'encaissement que la reprise n'avait pas pu créer."""
    from app.models import Encaissement, Paiement
    versement = db.session.get(Paiement, paiement_id)
    db.session.refresh(versement)
    if versement is None or versement.encaissement_id:
        return
    cotisation = versement.cotisation
    encaissement = Encaissement(
        montant=versement.montant, mode=versement.mode or "especes", date_encaissement=versement.date_paiement,
        participant_id=getattr(cotisation, "participant_id", None), foyer_id=getattr(cotisation, "foyer_id", None),
        commentaire=versement.commentaire, source_ancienne=f"paiement:{versement.id}",
        note_qualification="Montant corrigé après anomalie (voir journal d'audit).",
        created_by_user_id=versement.created_by_user_id,
    )
    db.session.add(encaissement)
    db.session.flush()
    versement.encaissement_id = encaissement.id


def poser_contraintes() -> list[str]:
    """Pose les contraintes de montants fini(e)s là où les données le permettent
    désormais (PostgreSQL). Rend les noms posés."""
    if db.engine.dialect.name != "postgresql":
        return []
    posees = []
    presentes = _tables()
    for (table, colonne), nom in CONTRAINTES.items():
        if table not in presentes:
            continue
        existe = db.session.execute(text("SELECT 1 FROM pg_constraint WHERE conname = :n"), {"n": nom}).scalar()
        if existe:
            continue
        reste = db.session.execute(text(f"SELECT count(*) FROM {table} WHERE {_condition(colonne)}")).scalar()
        if reste:
            continue
        db.session.execute(text(
            f"ALTER TABLE {table} ADD CONSTRAINT {nom} CHECK ({colonne} IS NULL OR {colonne} BETWEEN -{BORNE} AND {BORNE})"))
        db.session.commit()
        posees.append(nom)
    return posees
