"""Dons et reçus fiscaux (CERFA 11580)

Revision ID: a8b9c0d1e2f3
Revises: f7a8b9c0d1e2
Create Date: 2026-07-01

Crée ``don`` : registre numéroté des dons, avec les mentions nécessaires au
reçu fiscal (donateur, montant, forme, mode de versement, organisme figé).

Migration DÉFENSIVE : création protégée par inspection préalable.
"""
from alembic import op
import sqlalchemy as sa


revision = "a8b9c0d1e2f3"
down_revision = "f7a8b9c0d1e2"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if insp.has_table("don"):
        return
    op.create_table(
        "don",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("numero", sa.String(length=20), nullable=False),
        sa.Column("annee", sa.Integer(), nullable=False),
        sa.Column("type_donateur", sa.String(length=20), nullable=False, server_default="particulier"),
        sa.Column("donateur_civilite", sa.String(length=20), nullable=True),
        sa.Column("donateur_nom", sa.String(length=160), nullable=False),
        sa.Column("donateur_prenom", sa.String(length=120), nullable=True),
        sa.Column("donateur_adresse", sa.String(length=255), nullable=True),
        sa.Column("donateur_cp", sa.String(length=10), nullable=True),
        sa.Column("donateur_ville", sa.String(length=120), nullable=True),
        sa.Column("donateur_email", sa.String(length=180), nullable=True),
        sa.Column("montant", sa.Float(), nullable=False, server_default="0"),
        sa.Column("date_don", sa.Date(), nullable=False),
        sa.Column("forme_don", sa.String(length=20), nullable=False, server_default="numeraire"),
        sa.Column("mode_versement", sa.String(length=20), nullable=False, server_default="virement"),
        sa.Column("nature_description", sa.Text(), nullable=True),
        sa.Column("organisme_nom", sa.String(length=200), nullable=True),
        sa.Column("organisme_adresse", sa.String(length=255), nullable=True),
        sa.Column("est_annule", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("annulation_motif", sa.String(length=255), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("numero", name="uq_don_numero"),
    )
    op.create_index("ix_don_annee", "don", ["annee"])


def downgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if insp.has_table("don"):
        op.drop_table("don")
