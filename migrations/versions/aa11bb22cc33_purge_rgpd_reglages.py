"""Réglages de la purge RGPD dans les paramètres d'instance

Revision ID: aa11bb22cc33
Revises: f9a0b1c2d3e4
Create Date: 2026-07-09

- ``instance_settings.purge_rgpd_annees`` : délai d'inactivité (en années)
  avant anonymisation automatique d'un participant — prioritaire sur la
  variable d'environnement PURGE_INACTIFS_ANNEES.
- ``instance_settings.purge_rgpd_auto`` : active/désactive la purge
  quotidienne automatique — prioritaire sur PURGE_INACTIFS_AUTO.

Migration DÉFENSIVE : inspections préalables avant chaque opération.
"""
from alembic import op
import sqlalchemy as sa


revision = "aa11bb22cc33"
down_revision = "f9a0b1c2d3e4"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if insp.has_table("instance_settings"):
        cols = {c["name"] for c in insp.get_columns("instance_settings")}
        if "purge_rgpd_annees" not in cols:
            op.add_column("instance_settings", sa.Column("purge_rgpd_annees", sa.Integer(), nullable=True))
        if "purge_rgpd_auto" not in cols:
            op.add_column("instance_settings", sa.Column("purge_rgpd_auto", sa.Boolean(), nullable=True))


def downgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if insp.has_table("instance_settings"):
        cols = {c["name"] for c in insp.get_columns("instance_settings")}
        if "purge_rgpd_auto" in cols:
            op.drop_column("instance_settings", "purge_rgpd_auto")
        if "purge_rgpd_annees" in cols:
            op.drop_column("instance_settings", "purge_rgpd_annees")
