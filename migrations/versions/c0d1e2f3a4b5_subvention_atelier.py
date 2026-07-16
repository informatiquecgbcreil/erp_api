"""Lien subvention <-> ateliers financés (justificatif financeur)

Revision ID: c0d1e2f3a4b5
Revises: b9c0d1e2f3a4
Create Date: 2026-07-02

Crée ``subvention_atelier`` : quels ateliers une subvention finance, avec la
part affectée (%) et une justification libre. Sert au justificatif financeur
(séances, heures, participants, coût unitaire par atelier financé).

Migration DÉFENSIVE : création protégée par inspection préalable.
"""
from alembic import op
import sqlalchemy as sa


revision = "c0d1e2f3a4b5"
down_revision = "b9c0d1e2f3a4"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if insp.has_table("subvention_atelier"):
        return
    op.create_table(
        "subvention_atelier",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("subvention_id", sa.Integer(), nullable=False),
        sa.Column("atelier_id", sa.Integer(), nullable=False),
        sa.Column("poids_pct", sa.Float(), nullable=False, server_default="100"),
        sa.Column("justification", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["subvention_id"], ["subvention.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["atelier_id"], ["atelier_activite.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("subvention_id", "atelier_id", name="uq_subvention_atelier"),
    )
    op.create_index("ix_subvention_atelier_subvention_id", "subvention_atelier", ["subvention_id"])
    op.create_index("ix_subvention_atelier_atelier_id", "subvention_atelier", ["atelier_id"])


def downgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if insp.has_table("subvention_atelier"):
        op.drop_table("subvention_atelier")
