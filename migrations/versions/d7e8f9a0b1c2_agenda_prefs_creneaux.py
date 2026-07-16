"""Agenda personnalisable : préférences du flux + créneaux hors ateliers

Revision ID: d7e8f9a0b1c2
Revises: c6d7e8f9a0b1
Create Date: 2026-07-06

- ``agenda_preference`` : réglages personnels du flux iCal (titre,
  description, fenêtre, préparation auto…) en JSON.
- ``agenda_creneau`` : temps de travail hors ateliers (réunions,
  préparation, formation…) qui complètent le flux — l'agenda extrait
  par les financeurs reflète alors tout le temps effectif.

Migration DÉFENSIVE : inspections préalables avant chaque opération.
"""
from alembic import op
import sqlalchemy as sa


revision = "d7e8f9a0b1c2"
down_revision = "c6d7e8f9a0b1"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if not insp.has_table("agenda_preference"):
        op.create_table(
            "agenda_preference",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("options_json", sa.Text(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id", name="uq_agenda_preference_user"),
        )
        op.create_index("ix_agenda_preference_user_id", "agenda_preference", ["user_id"])

    if not insp.has_table("agenda_creneau"):
        op.create_table(
            "agenda_creneau",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("type_creneau", sa.String(length=20), nullable=False, server_default="reunion"),
            sa.Column("titre", sa.String(length=200), nullable=False),
            sa.Column("date_creneau", sa.Date(), nullable=False),
            sa.Column("heure_debut", sa.String(length=10), nullable=True),
            sa.Column("heure_fin", sa.String(length=10), nullable=True),
            sa.Column("description", sa.String(length=500), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_agenda_creneau_user_id", "agenda_creneau", ["user_id"])
        op.create_index("ix_agenda_creneau_date_creneau", "agenda_creneau", ["date_creneau"])


def downgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if insp.has_table("agenda_creneau"):
        op.drop_table("agenda_creneau")
    if insp.has_table("agenda_preference"):
        op.drop_table("agenda_preference")
