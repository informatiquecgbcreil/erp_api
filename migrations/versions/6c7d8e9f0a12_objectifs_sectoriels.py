"""Objectifs sectoriels configurables + rattachement des actions.

Migration manuelle défensive (db.create_all au démarrage peut avoir déjà
créé les tables).

Revision ID: 6c7d8e9f0a12
Revises: 5b6c7d8e9f01
Create Date: 2026-06-14
"""
import sqlalchemy as sa
from alembic import op

revision = "6c7d8e9f0a12"
down_revision = "5b6c7d8e9f01"
branch_labels = None
depends_on = None


def _a_table(nom: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(nom)


def upgrade():
    if not _a_table("objectif_sectoriel"):
        op.create_table(
            "objectif_sectoriel",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("secteur", sa.String(length=80), nullable=False),
            sa.Column("code", sa.String(length=20), nullable=False),
            sa.Column("libelle", sa.String(length=255), nullable=False),
            sa.Column("axe", sa.String(length=160), nullable=True),
            sa.Column("ordre", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("actif", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        )
        op.create_index("ix_objectif_sectoriel_secteur", "objectif_sectoriel", ["secteur"])

    if not _a_table("action_objectif_sectoriel"):
        op.create_table(
            "action_objectif_sectoriel",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("action_id", sa.Integer(),
                      sa.ForeignKey("projet_action.id", ondelete="CASCADE"), nullable=False),
            sa.Column("objectif_id", sa.Integer(),
                      sa.ForeignKey("objectif_sectoriel.id", ondelete="CASCADE"), nullable=False),
            sa.Column("contribution", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.UniqueConstraint("action_id", "objectif_id", name="uq_action_objectif"),
        )
        op.create_index("ix_action_objectif_sectoriel_action_id", "action_objectif_sectoriel", ["action_id"])
        op.create_index("ix_action_objectif_sectoriel_objectif_id", "action_objectif_sectoriel", ["objectif_id"])


def downgrade():
    for nom in ("action_objectif_sectoriel", "objectif_sectoriel"):
        if _a_table(nom):
            op.drop_table(nom)
