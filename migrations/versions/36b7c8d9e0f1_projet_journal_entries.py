"""projet journal entries

Revision ID: 36b7c8d9e0f1
Revises: 35a6b7c8d9e0
Create Date: 2026-06-01

Ajoute un journal de bord simple par projet.
"""
from alembic import op
import sqlalchemy as sa

revision = "36b7c8d9e0f1"
down_revision = "35a6b7c8d9e0"
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
    if not _has_table("projet_journal_entry"):
        op.create_table(
            "projet_journal_entry",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("projet_id", sa.Integer(), sa.ForeignKey("projet.id", ondelete="CASCADE"), nullable=False),
            sa.Column("entry_date", sa.Date(), nullable=False),
            sa.Column("categorie", sa.String(length=40), nullable=False, server_default="fait_marquant"),
            sa.Column("titre", sa.String(length=180), nullable=True),
            sa.Column("contenu", sa.Text(), nullable=False),
            sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )

    for name, cols in (
        ("ix_projet_journal_entry_projet_id", ["projet_id"]),
        ("ix_projet_journal_entry_entry_date", ["entry_date"]),
        ("ix_projet_journal_entry_categorie", ["categorie"]),
        ("ix_projet_journal_entry_created_at", ["created_at"]),
    ):
        if _has_table("projet_journal_entry") and not _has_index("projet_journal_entry", name):
            op.create_index(name, "projet_journal_entry", cols)


def downgrade():
    if _has_table("projet_journal_entry"):
        op.drop_table("projet_journal_entry")
