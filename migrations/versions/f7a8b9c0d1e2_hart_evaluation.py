"""Échelle de Hart : évaluations de la participation des habitants

Revision ID: f7a8b9c0d1e2
Revises: e6f7a8b9c0d1
Create Date: 2026-07-01

Crée ``hart_evaluation`` : positionnement d'un participant sur l'échelle de
Hart (1-8), déclenché par l'émargement (première venue puis toutes les
5 séances, mi-parcours / fin de parcours posés par le professionnel).

Migration DÉFENSIVE : création protégée par inspection préalable.
"""
from alembic import op
import sqlalchemy as sa


revision = "f7a8b9c0d1e2"
down_revision = "e6f7a8b9c0d1"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if insp.has_table("hart_evaluation"):
        return
    op.create_table(
        "hart_evaluation",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("participant_id", sa.Integer(), nullable=False),
        sa.Column("niveau", sa.Integer(), nullable=False),
        sa.Column("type_evaluation", sa.String(length=20), nullable=False, server_default="suivi"),
        sa.Column("date_evaluation", sa.Date(), nullable=False),
        sa.Column("secteur", sa.String(length=80), nullable=True),
        sa.Column("temoignage", sa.Text(), nullable=True),
        sa.Column("remarque_pro", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["participant_id"], ["participant.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_hart_evaluation_participant_id", "hart_evaluation", ["participant_id"])
    op.create_index("ix_hart_evaluation_date_evaluation", "hart_evaluation", ["date_evaluation"])
    op.create_index("ix_hart_evaluation_type_evaluation", "hart_evaluation", ["type_evaluation"])
    op.create_index("ix_hart_evaluation_secteur", "hart_evaluation", ["secteur"])


def downgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if insp.has_table("hart_evaluation"):
        op.drop_table("hart_evaluation")
