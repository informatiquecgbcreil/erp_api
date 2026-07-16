"""participant insertion fields

Revision ID: 20d1e2f3a4b5
Revises: 19c0d1e2f3a4
Create Date: 2026-04-02
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20d1e2f3a4b5"
down_revision = "19c0d1e2f3a4"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("participant", sa.Column("pays_origine", sa.String(length=120), nullable=True))
    op.add_column("participant", sa.Column("titre_sejour_type", sa.String(length=120), nullable=True))
    op.add_column("participant", sa.Column("date_entree_dispositif", sa.Date(), nullable=True))
    op.add_column("participant", sa.Column("date_sortie_dispositif", sa.Date(), nullable=True))
    op.add_column("participant", sa.Column("diplome_obtenu", sa.String(length=180), nullable=True))
    op.add_column("participant", sa.Column("cir_obtenu", sa.Boolean(), nullable=True))


def downgrade():
    op.drop_column("participant", "cir_obtenu")
    op.drop_column("participant", "diplome_obtenu")
    op.drop_column("participant", "date_sortie_dispositif")
    op.drop_column("participant", "date_entree_dispositif")
    op.drop_column("participant", "titre_sejour_type")
    op.drop_column("participant", "pays_origine")
