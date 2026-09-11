"""Salles lot 2 : salle de référence des ateliers, horaires d'ouverture.

Deux ajouts seulement : les indisponibilités réutilisent la table
``occupation`` du lot 1 (origine « blocage »), et les jours fériés sont
calculés, donc rien à stocker.

Revision ID: cd94ef56ab78
Revises: bc83de45fa67
Create Date: 2026-09-11
"""
import sqlalchemy as sa
from alembic import op

revision = "cd94ef56ab78"
down_revision = "bc83de45fa67"
branch_labels = None
depends_on = None

#: (table, colonne, type, pose une clé étrangère vers espace ?)
AJOUTS = [
    ("atelier_activite", "espace_id", sa.Integer(), True),
    ("site", "horaires_json", sa.Text(), False),
]


def _colonnes(table: str) -> set[str]:
    insp = sa.inspect(op.get_bind())
    if not insp.has_table(table):
        return set()
    return {c["name"] for c in insp.get_columns(table)}


def upgrade():
    insp = sa.inspect(op.get_bind())
    tables = set(insp.get_table_names())
    sqlite = op.get_bind().dialect.name == "sqlite"

    for table, colonne, type_sql, fk in AJOUTS:
        if table not in tables or colonne in _colonnes(table):
            continue
        op.add_column(table, sa.Column(colonne, type_sql, nullable=True))
        if fk:
            op.create_index(f"ix_{table}_{colonne}", table, [colonne])
            # SQLite ne sait pas ajouter une contrainte après coup ; l'ORM y
            # assure déjà la mise à NULL à la suppression d'un espace.
            if not sqlite:
                op.create_foreign_key(
                    f"fk_{table}_{colonne}_espace", table, "espace",
                    [colonne], ["id"], ondelete="SET NULL",
                )


def downgrade():
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    for table, colonne, _type, _fk in AJOUTS:
        if table in tables and colonne in _colonnes(table):
            with op.batch_alter_table(table) as batch_op:
                batch_op.drop_column(colonne)
