"""File d'effacement des fichiers après commit, reprise après erreur disque."""
from alembic import op
import sqlalchemy as sa

revision = "cd91ef23ab45"
down_revision = "e1a7c3f9b2d4"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("financial_sequence",
                    sa.Column("namespace", sa.String(60), primary_key=True),
                    sa.Column("value", sa.Integer(), nullable=False))
    op.create_table("pending_file_deletion",
                    sa.Column("id", sa.Integer(), primary_key=True),
                    sa.Column("file_path", sa.Text(), nullable=False),
                    sa.Column("created_at", sa.DateTime(), nullable=False))


def downgrade():
    op.drop_table("pending_file_deletion")
    op.drop_table("financial_sequence")
