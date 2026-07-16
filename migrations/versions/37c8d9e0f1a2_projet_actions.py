"""projet actions

Revision ID: 37c8d9e0f1a2
Revises: 36b7c8d9e0f1
Create Date: 2026-06-01

Ajoute les fiches actions detaillees rattachees aux projets.
"""
from alembic import op
import sqlalchemy as sa

revision = "37c8d9e0f1a2"
down_revision = "36b7c8d9e0f1"
branch_labels = None
depends_on = None


def _bind():
    return op.get_bind()


def _insp():
    return sa.inspect(_bind())


def _has_table(name: str) -> bool:
    return _insp().has_table(name)


def _has_index(table: str, name: str) -> bool:
    if not _has_table(table):
        return False
    return any(idx.get("name") == name for idx in _insp().get_indexes(table))


def upgrade():
    if not _has_table("projet_action"):
        op.create_table(
            "projet_action",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("projet_id", sa.Integer(), sa.ForeignKey("projet.id", ondelete="CASCADE"), nullable=False),
            sa.Column("titre", sa.String(length=200), nullable=False),
            sa.Column("categorie", sa.String(length=50), nullable=False, server_default="atelier"),
            sa.Column("statut", sa.String(length=30), nullable=False, server_default="prevue"),
            sa.Column("referent", sa.String(length=160), nullable=True),
            sa.Column("date_debut", sa.Date(), nullable=True),
            sa.Column("date_fin", sa.Date(), nullable=True),
            sa.Column("lieu", sa.String(length=200), nullable=True),
            sa.Column("public_vise", sa.Text(), nullable=True),
            sa.Column("territoire", sa.Text(), nullable=True),
            sa.Column("objectifs", sa.Text(), nullable=True),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("partenaires_text", sa.Text(), nullable=True),
            sa.Column("bilan_qualitatif", sa.Text(), nullable=True),
            sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )

    if not _has_table("projet_action_atelier"):
        op.create_table(
            "projet_action_atelier",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("action_id", sa.Integer(), sa.ForeignKey("projet_action.id", ondelete="CASCADE"), nullable=False),
            sa.Column("atelier_id", sa.Integer(), sa.ForeignKey("atelier_activite.id", ondelete="CASCADE"), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("action_id", "atelier_id", name="uq_projet_action_atelier"),
        )

    for table, indexes in {
        "projet_action": (
            ("ix_projet_action_projet_id", ["projet_id"]),
            ("ix_projet_action_categorie", ["categorie"]),
            ("ix_projet_action_statut", ["statut"]),
            ("ix_projet_action_date_debut", ["date_debut"]),
            ("ix_projet_action_date_fin", ["date_fin"]),
            ("ix_projet_action_created_at", ["created_at"]),
        ),
        "projet_action_atelier": (
            ("ix_projet_action_atelier_action_id", ["action_id"]),
            ("ix_projet_action_atelier_atelier_id", ["atelier_id"]),
        ),
    }.items():
        if not _has_table(table):
            continue
        for name, cols in indexes:
            if not _has_index(table, name):
                op.create_index(name, table, cols)


def downgrade():
    if _has_table("projet_action_atelier"):
        op.drop_table("projet_action_atelier")
    if _has_table("projet_action"):
        op.drop_table("projet_action")
