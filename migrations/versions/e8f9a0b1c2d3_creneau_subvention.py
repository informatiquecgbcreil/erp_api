"""Créneaux d'agenda rattachables à une subvention (feuille de temps financeur)

Revision ID: e8f9a0b1c2d3
Revises: d7e8f9a0b1c2
Create Date: 2026-07-06

``agenda_creneau.subvention_id`` : un créneau (réunion projet, préparation
dédiée…) peut compter dans la feuille de temps d'un financeur.

Migration DÉFENSIVE : inspections préalables avant chaque opération.
"""
from alembic import op
import sqlalchemy as sa


revision = "e8f9a0b1c2d3"
down_revision = "d7e8f9a0b1c2"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if insp.has_table("agenda_creneau"):
        cols = {c["name"] for c in insp.get_columns("agenda_creneau")}
        if "subvention_id" not in cols:
            op.add_column("agenda_creneau", sa.Column("subvention_id", sa.Integer(), nullable=True))
            op.create_index("ix_agenda_creneau_subvention_id", "agenda_creneau", ["subvention_id"])
            if bind.dialect.name != "sqlite":
                op.create_foreign_key(
                    "fk_agenda_creneau_subvention", "agenda_creneau", "subvention",
                    ["subvention_id"], ["id"], ondelete="SET NULL",
                )


def downgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if insp.has_table("agenda_creneau"):
        cols = {c["name"] for c in insp.get_columns("agenda_creneau")}
        if "subvention_id" in cols:
            if bind.dialect.name != "sqlite":
                try:
                    op.drop_constraint("fk_agenda_creneau_subvention", "agenda_creneau", type_="foreignkey")
                except Exception:
                    pass
            op.drop_index("ix_agenda_creneau_subvention_id", table_name="agenda_creneau")
            op.drop_column("agenda_creneau", "subvention_id")
