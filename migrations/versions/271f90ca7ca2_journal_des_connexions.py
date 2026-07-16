"""Journal des connexions (anti force brute + audit).

Migration écrite à la main : l'autogénération est inutilisable sur ce
projet (dérives historiques entre modèles et base). Elle est défensive
car bootstrap_rbac() exécute db.create_all() au démarrage, qui peut
avoir déjà créé la table sur certaines installations.

Revision ID: 271f90ca7ca2
Revises: 40f1a2b3c4d5
Create Date: 2026-06-12
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "271f90ca7ca2"
down_revision = "40f1a2b3c4d5"
branch_labels = None
depends_on = None

TABLE = "journal_connexion"


def _table_existe() -> bool:
    bind = op.get_bind()
    return sa.inspect(bind).has_table(TABLE)


def upgrade():
    if _table_existe():
        return
    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(length=180), nullable=False),
        sa.Column("adresse_ip", sa.String(length=45), nullable=True),
        sa.Column("succes", sa.Boolean(), nullable=False),
        sa.Column("cree_le", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_journal_connexion_email", TABLE, ["email"])
    op.create_index("ix_journal_connexion_cree_le", TABLE, ["cree_le"])


def downgrade():
    if _table_existe():
        op.drop_table(TABLE)
