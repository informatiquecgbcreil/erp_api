"""Destinations des copies hors serveur réglables dans l'administration (audit 6.5)."""
import sqlalchemy as sa
from alembic import op

revision = "f5a8b3d0c279"
down_revision = "e4f7a2c9b168"
branch_labels = None
depends_on = None


def upgrade():
    colonnes = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("instance_settings")}
    if "sauvegarde_hors_serveur" not in colonnes:
        with op.batch_alter_table("instance_settings") as batch:
            batch.add_column(sa.Column("sauvegarde_hors_serveur", sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table("instance_settings") as batch:
        batch.drop_column("sauvegarde_hors_serveur")
