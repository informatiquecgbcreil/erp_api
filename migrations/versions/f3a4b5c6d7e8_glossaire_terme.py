"""Glossaire éditable : personnalisations locales des termes

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
Create Date: 2026-07-02

Table ``glossaire_terme`` : ajouts, modifications et masquages locaux du
glossaire de base (qui vit dans le code et suit les mises à jour).

Migration DÉFENSIVE : inspections préalables avant chaque opération.
"""
from alembic import op
import sqlalchemy as sa


revision = "f3a4b5c6d7e8"
down_revision = "e2f3a4b5c6d7"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if not insp.has_table("glossaire_terme"):
        op.create_table(
            "glossaire_terme",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("terme", sa.String(length=180), nullable=False),
            sa.Column("definition", sa.Text(), nullable=False, server_default=""),
            sa.Column("categorie", sa.String(length=120), nullable=True),
            sa.Column("dans_app", sa.Text(), nullable=True),
            sa.Column("masque", sa.Boolean(), nullable=False, server_default=sa.text("0") if bind.dialect.name == "sqlite" else sa.text("false")),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("terme", name="uq_glossaire_terme_terme"),
        )
        op.create_index("ix_glossaire_terme_terme", "glossaire_terme", ["terme"])
        op.create_index("ix_glossaire_terme_masque", "glossaire_terme", ["masque"])


def downgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if insp.has_table("glossaire_terme"):
        op.drop_table("glossaire_terme")
