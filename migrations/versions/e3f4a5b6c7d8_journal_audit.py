"""Journal d'audit des actions sensibles (table journal_audit).

Migration défensive : ne crée la table que si elle est absente
(le démarrage exécute aussi db.create_all).

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
Create Date: 2026-06-30
"""
import sqlalchemy as sa
from alembic import op

revision = "e3f4a5b6c7d8"
down_revision = "d2e3f4a5b6c7"
branch_labels = None
depends_on = None

TABLE = "journal_audit"


def upgrade():
    bind = op.get_bind()
    if sa.inspect(bind).has_table(TABLE):
        return
    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, index=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
        sa.Column("user_email", sa.String(length=180), nullable=True),
        sa.Column("action", sa.String(length=60), nullable=False, index=True),
        sa.Column("cible", sa.String(length=255), nullable=True),
        sa.Column("details", sa.Text(), nullable=True),
    )


def downgrade():
    bind = op.get_bind()
    if sa.inspect(bind).has_table(TABLE):
        op.drop_table(TABLE)
