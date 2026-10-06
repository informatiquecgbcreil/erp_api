"""Espace salarié : l'assistant·e de direction peut refuser une demande.

Ajoute ``demande_recuperation.refusee_par_relais`` (refus au premier niveau,
sans passage en direction). Rejouable.
"""
import sqlalchemy as sa
from alembic import op

revision = "d5b7c9e1f3a4"
down_revision = "c4a6b8d0e2f3"
branch_labels = None
depends_on = None


def upgrade():
    insp = sa.inspect(op.get_bind())
    if insp.has_table("demande_recuperation") and \
            "refusee_par_relais" not in {c["name"] for c in insp.get_columns("demande_recuperation")}:
        with op.batch_alter_table("demande_recuperation") as batch:
            batch.add_column(sa.Column("refusee_par_relais", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    insp = sa.inspect(op.get_bind())
    if insp.has_table("demande_recuperation") and \
            "refusee_par_relais" in {c["name"] for c in insp.get_columns("demande_recuperation")}:
        with op.batch_alter_table("demande_recuperation") as batch:
            batch.drop_column("refusee_par_relais")
