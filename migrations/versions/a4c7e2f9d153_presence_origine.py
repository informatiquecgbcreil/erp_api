"""Origine des présences (personnel ou kiosque) : une présence de kiosque
n'ouvre plus les droits de modification d'un secteur sur une fiche.

Les présences déjà posées au kiosque se reconnaissent au nom de leur
signature (« sig_kiosk_… »).
"""
from alembic import op
import sqlalchemy as sa

revision = "a4c7e2f9d153"
down_revision = "f3b8d1a6c902"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("presence_activite") as batch:
        batch.add_column(sa.Column("origine", sa.String(20), nullable=True))
    op.execute(
        "UPDATE presence_activite SET origine = 'kiosque' "
        "WHERE signature_path LIKE '%sig\\_kiosk\\_%' ESCAPE '\\'"
    )


def downgrade():
    with op.batch_alter_table("presence_activite") as batch:
        batch.drop_column("origine")
