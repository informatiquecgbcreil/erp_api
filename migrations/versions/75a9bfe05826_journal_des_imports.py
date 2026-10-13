"""Journal des imports Excel, pour pouvoir annuler le dernier.

Chaque import réel (présences, annuaire) retient la sauvegarde faite juste
avant, ce qu'il a créé et ce qu'il a modifié. Table neuve : rien à reprendre.
Rejouable.
"""
import sqlalchemy as sa
from alembic import op

revision = "75a9bfe05826"
down_revision = "ea3a8eed9c2f"
branch_labels = None
depends_on = None


def upgrade():
    if sa.inspect(op.get_bind()).has_table("import_lot"):
        return
    op.create_table(
        "import_lot",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("nature", sa.String(length=40), nullable=False),
        sa.Column("fichier", sa.String(length=255), nullable=True),
        sa.Column("cree_le", sa.DateTime(), nullable=False),
        sa.Column("cree_par_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
        sa.Column("sauvegarde", sa.String(length=255), nullable=True),
        sa.Column("elements_json", sa.Text(), nullable=False),
        sa.Column("annule_le", sa.DateTime(), nullable=True),
        sa.Column("annule_par_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
        sa.Column("annulation_json", sa.Text(), nullable=True),
    )
    op.create_index("ix_import_lot_nature", "import_lot", ["nature"])


def downgrade():
    if sa.inspect(op.get_bind()).has_table("import_lot"):
        op.drop_index("ix_import_lot_nature", table_name="import_lot")
        op.drop_table("import_lot")
