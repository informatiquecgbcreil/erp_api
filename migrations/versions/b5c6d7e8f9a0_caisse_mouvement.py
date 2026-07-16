"""Caisse : journal des mouvements (fond, dépôts, comptages, ajustements)

Revision ID: b5c6d7e8f9a0
Revises: a4b5c6d7e8f9
Create Date: 2026-07-03

Les encaissements (règlements espèces/chèque, dons en numéraire) sont lus
depuis leurs tables d'origine : cette table ne stocke que les mouvements
propres à la caisse.

Migration DÉFENSIVE : inspections préalables avant chaque opération.
"""
from alembic import op
import sqlalchemy as sa


revision = "b5c6d7e8f9a0"
down_revision = "a4b5c6d7e8f9"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if not insp.has_table("caisse_mouvement"):
        op.create_table(
            "caisse_mouvement",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("type_mouvement", sa.String(length=20), nullable=False),
            sa.Column("canal", sa.String(length=10), nullable=False, server_default="especes"),
            sa.Column("montant", sa.Float(), nullable=False, server_default="0"),
            sa.Column("ecart", sa.Float(), nullable=True),
            sa.Column("nb_cheques", sa.Integer(), nullable=True),
            sa.Column("date_mouvement", sa.Date(), nullable=False),
            sa.Column("commentaire", sa.String(length=255), nullable=True),
            sa.Column("created_by_user_id", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["created_by_user_id"], ["user.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_caisse_mouvement_type_mouvement", "caisse_mouvement", ["type_mouvement"])
        op.create_index("ix_caisse_mouvement_canal", "caisse_mouvement", ["canal"])
        op.create_index("ix_caisse_mouvement_date_mouvement", "caisse_mouvement", ["date_mouvement"])


def downgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if insp.has_table("caisse_mouvement"):
        op.drop_table("caisse_mouvement")
