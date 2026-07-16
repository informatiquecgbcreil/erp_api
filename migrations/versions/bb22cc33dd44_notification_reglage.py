"""Notifications automatiques réglables (digest e-mail)

Revision ID: bb22cc33dd44
Revises: aa11bb22cc33
Create Date: 2026-07-13

Table ``notification_reglage`` : un réglage par type de notification
(échéances financeurs, impayés, sauvegarde en retard, rappels en retard) —
actif/inactif, destinataires, fréquence, seuil. Rien n'est actif par défaut.

Migration DÉFENSIVE et purement ADDITIVE : aucune table ni colonne existante
n'est modifiée.
"""
from alembic import op
import sqlalchemy as sa


revision = "bb22cc33dd44"
down_revision = "aa11bb22cc33"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if not insp.has_table("notification_reglage"):
        op.create_table(
            "notification_reglage",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("code", sa.String(length=60), nullable=False),
            sa.Column("actif", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("destinataires", sa.Text(), nullable=True),
            sa.Column("frequence", sa.String(length=20), nullable=False, server_default="quotidien"),
            sa.Column("seuil_jours", sa.Integer(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint("code", name="uq_notification_reglage_code"),
        )
        op.create_index(
            "ix_notification_reglage_code", "notification_reglage", ["code"], unique=False
        )


def downgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if insp.has_table("notification_reglage"):
        op.drop_table("notification_reglage")
