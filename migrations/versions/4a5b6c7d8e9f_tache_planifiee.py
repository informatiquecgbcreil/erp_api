"""Table tache_planifiee (tâches automatiques internes, ex: purge RGPD).

Migration manuelle défensive (db.create_all au démarrage peut avoir déjà
créé la table sur certaines installations).

Revision ID: 4a5b6c7d8e9f
Revises: 382fa1db8db3
Create Date: 2026-06-12
"""
import sqlalchemy as sa
from alembic import op

revision = "4a5b6c7d8e9f"
down_revision = "382fa1db8db3"
branch_labels = None
depends_on = None

TABLE = "tache_planifiee"


def _table_existe() -> bool:
    return sa.inspect(op.get_bind()).has_table(TABLE)


def upgrade():
    if _table_existe():
        return
    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("nom", sa.String(length=80), nullable=False, unique=True),
        sa.Column("derniere_execution", sa.DateTime(), nullable=True),
    )


def downgrade():
    if _table_existe():
        op.drop_table(TABLE)
