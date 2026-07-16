"""suivi rappels

Revision ID: 40f1a2b3c4d5
Revises: 39e0f1a2b3c4
Create Date: 2026-06-08

Ajoute les rappels manuels du centre de suivi.
"""
from alembic import op
import sqlalchemy as sa

revision = "40f1a2b3c4d5"
down_revision = "39e0f1a2b3c4"
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
    if not _has_table("suivi_rappel"):
        op.create_table(
            "suivi_rappel",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("titre", sa.String(length=200), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("categorie", sa.String(length=40), nullable=False, server_default="general"),
            sa.Column("priorite", sa.String(length=20), nullable=False, server_default="warn"),
            sa.Column("statut", sa.String(length=20), nullable=False, server_default="ouvert"),
            sa.Column("secteur", sa.String(length=80), nullable=True),
            sa.Column("echeance", sa.Date(), nullable=True),
            sa.Column("lien_url", sa.String(length=500), nullable=True),
            sa.Column("is_private", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
            sa.Column("done_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )

    for name, cols in (
        ("ix_suivi_rappel_categorie", ["categorie"]),
        ("ix_suivi_rappel_priorite", ["priorite"]),
        ("ix_suivi_rappel_statut", ["statut"]),
        ("ix_suivi_rappel_secteur", ["secteur"]),
        ("ix_suivi_rappel_echeance", ["echeance"]),
        ("ix_suivi_rappel_is_private", ["is_private"]),
        ("ix_suivi_rappel_created_by_user_id", ["created_by_user_id"]),
        ("ix_suivi_rappel_created_at", ["created_at"]),
    ):
        if not _has_index("suivi_rappel", name):
            op.create_index(name, "suivi_rappel", cols)


def downgrade():
    if _has_table("suivi_rappel"):
        op.drop_table("suivi_rappel")
