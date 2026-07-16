"""Inscriptions préalables & listes d'attente

Revision ID: cc33dd44ee55
Revises: bb22cc33dd44
Create Date: 2026-07-13

Table ``inscription_activite`` : inscription à un atelier (période) ou à une
séance précise, avec statut inscrit / liste d'attente / annulée. La jauge
s'appuie sur les capacités existantes (séance, sinon atelier).

Migration DÉFENSIVE et purement ADDITIVE : aucune table ni colonne existante
n'est modifiée.
"""
from alembic import op
import sqlalchemy as sa


revision = "cc33dd44ee55"
down_revision = "bb22cc33dd44"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if not insp.has_table("inscription_activite"):
        op.create_table(
            "inscription_activite",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("atelier_id", sa.Integer(), sa.ForeignKey("atelier_activite.id", ondelete="CASCADE"), nullable=False),
            sa.Column("session_id", sa.Integer(), sa.ForeignKey("session_activite.id", ondelete="CASCADE"), nullable=True),
            sa.Column("participant_id", sa.Integer(), sa.ForeignKey("participant.id", ondelete="CASCADE"), nullable=False),
            sa.Column("statut", sa.String(length=20), nullable=False, server_default="inscrit"),
            sa.Column("commentaire", sa.String(length=255), nullable=True),
            sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
        op.create_index("ix_inscription_activite_atelier_id", "inscription_activite", ["atelier_id"])
        op.create_index("ix_inscription_activite_session_id", "inscription_activite", ["session_id"])
        op.create_index("ix_inscription_activite_participant_id", "inscription_activite", ["participant_id"])
        op.create_index("ix_inscription_activite_statut", "inscription_activite", ["statut"])
        op.create_index("ix_inscription_activite_cible", "inscription_activite", ["atelier_id", "session_id", "statut"])


def downgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if insp.has_table("inscription_activite"):
        op.drop_table("inscription_activite")
