"""insertion titre sejour dates and scope tweaks

Revision ID: 23a4b5c6d7e8
Revises: 22f3a4b5c6d7
Create Date: 2026-04-03 14:10:00
"""

from alembic import op
import sqlalchemy as sa

revision = "23a4b5c6d7e8"
down_revision = "22f3a4b5c6d7"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("participant_insertion_parcours") as batch_op:
        batch_op.add_column(sa.Column("date_debut_titre_sejour", sa.Date(), nullable=True))
        batch_op.add_column(sa.Column("date_expiration_titre_sejour", sa.Date(), nullable=True))


def downgrade():
    with op.batch_alter_table("participant_insertion_parcours") as batch_op:
        batch_op.drop_column("date_expiration_titre_sejour")
        batch_op.drop_column("date_debut_titre_sejour")
