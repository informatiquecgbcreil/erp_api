"""Indicateurs de projet : table d'historique des valeurs

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-06-30

Crée ``projet_indicateur_valeur`` pour tracer l'évolution des valeurs saisies
(qui / quand / combien) sur les indicateurs de projet.

Migration DÉFENSIVE : la création de table est protégée par une inspection
préalable, afin de cohabiter sans erreur avec ``db.create_all`` au démarrage.
"""
from alembic import op
import sqlalchemy as sa


revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if insp.has_table("projet_indicateur_valeur"):
        return
    op.create_table(
        "projet_indicateur_valeur",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("indicateur_id", sa.Integer(), nullable=False),
        sa.Column("date_releve", sa.Date(), nullable=False),
        sa.Column("valeur", sa.Float(), nullable=True),
        sa.Column("source", sa.String(length=30), nullable=False, server_default="manual"),
        sa.Column("commentaire", sa.Text(), nullable=True),
        sa.Column("saisie_par_user_id", sa.Integer(), nullable=True),
        sa.Column("saisie_le", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["indicateur_id"], ["projet_indicateur.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["saisie_par_user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_projet_indicateur_valeur_indicateur_id", "projet_indicateur_valeur", ["indicateur_id"])
    op.create_index("ix_projet_indicateur_valeur_saisie_le", "projet_indicateur_valeur", ["saisie_le"])


def downgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if not insp.has_table("projet_indicateur_valeur"):
        return
    op.drop_table("projet_indicateur_valeur")
