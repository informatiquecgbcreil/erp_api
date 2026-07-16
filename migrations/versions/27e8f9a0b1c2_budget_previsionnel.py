"""budget previsionnel global et extraction appel projet

Revision ID: 27e8f9a0b1c2
Revises: 26d7e8f9a0b1
Create Date: 2026-04-25
"""
from alembic import op
import sqlalchemy as sa

revision = "27e8f9a0b1c2"
down_revision = "26d7e8f9a0b1"
branch_labels = None
depends_on = None


def _bind():
    return op.get_bind()


def _inspector():
    return sa.inspect(_bind())


def _has_table(name: str) -> bool:
    return _inspector().has_table(name)


def upgrade():
    if not _has_table("budget_previsionnel"):
        op.create_table(
            "budget_previsionnel",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("nom", sa.String(length=200), nullable=False),
            sa.Column("annee", sa.Integer(), nullable=False),
            sa.Column("secteur", sa.String(length=80), nullable=False),
            sa.Column("statut", sa.String(length=30), nullable=False, server_default="brouillon"),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_budget_previsionnel_annee", "budget_previsionnel", ["annee"])
        op.create_index("ix_budget_previsionnel_secteur", "budget_previsionnel", ["secteur"])

    if not _has_table("budget_previsionnel_ligne"):
        op.create_table(
            "budget_previsionnel_ligne",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("budget_id", sa.Integer(), sa.ForeignKey("budget_previsionnel.id"), nullable=False),
            sa.Column("projet_id", sa.Integer(), sa.ForeignKey("projet.id"), nullable=True),
            sa.Column("nature", sa.String(length=10), nullable=False, server_default="charge"),
            sa.Column("compte", sa.String(length=20), nullable=False, server_default="60"),
            sa.Column("libelle", sa.String(length=255), nullable=False),
            sa.Column("montant", sa.Float(), nullable=True, server_default="0"),
            sa.Column("commentaire", sa.Text(), nullable=True),
            sa.Column("ordre", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_budget_previsionnel_ligne_budget_id", "budget_previsionnel_ligne", ["budget_id"])
        op.create_index("ix_budget_previsionnel_ligne_projet_id", "budget_previsionnel_ligne", ["projet_id"])

    if not _has_table("appel_projet_budget"):
        op.create_table(
            "appel_projet_budget",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("budget_id", sa.Integer(), sa.ForeignKey("budget_previsionnel.id"), nullable=False),
            sa.Column("subvention_id", sa.Integer(), sa.ForeignKey("subvention.id"), nullable=True),
            sa.Column("projet_id", sa.Integer(), sa.ForeignKey("projet.id"), nullable=True),
            sa.Column("nom", sa.String(length=200), nullable=False),
            sa.Column("financeur", sa.String(length=200), nullable=True),
            sa.Column("statut", sa.String(length=30), nullable=False, server_default="preparation"),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_appel_projet_budget_budget_id", "appel_projet_budget", ["budget_id"])
        op.create_index("ix_appel_projet_budget_subvention_id", "appel_projet_budget", ["subvention_id"])
        op.create_index("ix_appel_projet_budget_projet_id", "appel_projet_budget", ["projet_id"])

    if not _has_table("appel_projet_budget_ligne"):
        op.create_table(
            "appel_projet_budget_ligne",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("appel_id", sa.Integer(), sa.ForeignKey("appel_projet_budget.id"), nullable=False),
            sa.Column("budget_ligne_id", sa.Integer(), sa.ForeignKey("budget_previsionnel_ligne.id"), nullable=False),
            sa.Column("montant_retenu", sa.Float(), nullable=True, server_default="0"),
            sa.Column("pourcentage_retenu", sa.Float(), nullable=True),
            sa.Column("commentaire", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_appel_projet_budget_ligne_appel_id", "appel_projet_budget_ligne", ["appel_id"])
        op.create_index("ix_appel_projet_budget_ligne_budget_ligne_id", "appel_projet_budget_ligne", ["budget_ligne_id"])


def downgrade():
    for table in (
        "appel_projet_budget_ligne",
        "appel_projet_budget",
        "budget_previsionnel_ligne",
        "budget_previsionnel",
    ):
        if _has_table(table):
            op.drop_table(table)
