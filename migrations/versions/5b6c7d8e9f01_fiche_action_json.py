"""Champ fiche_json sur projet_action (fiche action format imposé).

Migration manuelle défensive (db.create_all au démarrage peut avoir déjà
créé la colonne sur certaines installations).

Revision ID: 5b6c7d8e9f01
Revises: 4a5b6c7d8e9f
Create Date: 2026-06-14
"""
import sqlalchemy as sa
from alembic import op

revision = "5b6c7d8e9f01"
down_revision = "4a5b6c7d8e9f"
branch_labels = None
depends_on = None

TABLE = "projet_action"
COL = "fiche_json"


def _colonnes() -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(TABLE)}


def upgrade():
    if COL not in _colonnes():
        op.add_column(TABLE, sa.Column(COL, sa.Text(), nullable=True))


def downgrade():
    if COL in _colonnes():
        with op.batch_alter_table(TABLE) as batch_op:
            batch_op.drop_column(COL)
