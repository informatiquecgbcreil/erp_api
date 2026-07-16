"""Projets : archivage (soft-delete) et jalons / échéancier

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
Create Date: 2026-06-30

- Ajoute ``is_archive`` / ``archived_at`` au projet (soft-delete).
- Crée ``projet_jalon`` (échéancier de pilotage).

Migration DÉFENSIVE : inspections préalables avant chaque opération.
"""
from alembic import op
import sqlalchemy as sa


revision = "d5e6f7a8b9c0"
down_revision = "c4d5e6f7a8b9"
branch_labels = None
depends_on = None


PROJET_COLUMNS = [
    ("is_archive", sa.Column("is_archive", sa.Boolean(), nullable=False, server_default=sa.false())),
    ("archived_at", sa.Column("archived_at", sa.DateTime(), nullable=True)),
]


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if insp.has_table("projet"):
        existing = {c["name"] for c in insp.get_columns("projet")}
        for name, column in PROJET_COLUMNS:
            if name not in existing:
                op.add_column("projet", column)
        if "is_archive" not in existing:
            try:
                op.create_index("ix_projet_is_archive", "projet", ["is_archive"])
            except Exception:
                pass

    if not insp.has_table("projet_jalon"):
        op.create_table(
            "projet_jalon",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("projet_id", sa.Integer(), nullable=False),
            sa.Column("libelle", sa.String(length=200), nullable=False),
            sa.Column("date_echeance", sa.Date(), nullable=True),
            sa.Column("statut", sa.String(length=20), nullable=False, server_default="a_faire"),
            sa.Column("ordre", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["projet_id"], ["projet.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_projet_jalon_projet_id", "projet_jalon", ["projet_id"])
        op.create_index("ix_projet_jalon_date_echeance", "projet_jalon", ["date_echeance"])


def downgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if insp.has_table("projet_jalon"):
        op.drop_table("projet_jalon")

    if insp.has_table("projet"):
        existing = {c["name"] for c in insp.get_columns("projet")}
        try:
            op.drop_index("ix_projet_is_archive", table_name="projet")
        except Exception:
            pass
        for name, _ in reversed(PROJET_COLUMNS):
            if name in existing:
                op.drop_column("projet", name)
