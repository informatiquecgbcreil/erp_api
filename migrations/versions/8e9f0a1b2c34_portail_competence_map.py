"""Correspondance exercices portail -> compétences ERP.

Migration manuelle défensive (db.create_all au démarrage peut avoir déjà
créé la table ; gardes sa.inspect).

Revision ID: 8e9f0a1b2c34
Revises: 7d8e9f0a1b23
Create Date: 2026-06-24
"""
import sqlalchemy as sa
from alembic import op

revision = "8e9f0a1b2c34"
down_revision = "7d8e9f0a1b23"
branch_labels = None
depends_on = None


def _a_table(nom: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(nom)


def upgrade():
    if not _a_table("portail_competence_map"):
        op.create_table(
            "portail_competence_map",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("activity", sa.String(length=255), nullable=False),
            sa.Column("competence_id", sa.Integer(),
                      sa.ForeignKey("competence.id", ondelete="CASCADE"), nullable=False),
            sa.Column("seuil_pct", sa.Integer(), nullable=False, server_default="80"),
            sa.Column("actif", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.UniqueConstraint("activity", "competence_id", name="uq_portail_competence_map"),
        )
        op.create_index("ix_portail_competence_map_activity", "portail_competence_map", ["activity"])
        op.create_index("ix_portail_competence_map_competence_id", "portail_competence_map", ["competence_id"])


def downgrade():
    if _a_table("portail_competence_map"):
        op.drop_table("portail_competence_map")
