"""Factures de salles figées à l'émission et avoirs numérotés (audit 2.1) ;
annulation des dons en caisse sans réécriture rétroactive.

Les factures déjà émises n'ont pas d'instantané : il est reconstitué à leur
première réimpression, avec une mention « duplicata reconstitué ».
"""
from alembic import op
import sqlalchemy as sa

revision = "c7e9f4b2a375"
down_revision = "b5d8e3a1f264"
branch_labels = None
depends_on = None


def upgrade():
    colonnes = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("reservation")}
    with op.batch_alter_table("reservation") as batch:
        if "facture_snapshot_json" not in colonnes:
            batch.add_column(sa.Column("facture_snapshot_json", sa.Text(), nullable=True))
        if "avoir_numero" not in colonnes:
            batch.add_column(sa.Column("avoir_numero", sa.String(30), nullable=True))
            batch.create_unique_constraint("uq_reservation_avoir_numero", ["avoir_numero"])
            batch.create_index("ix_reservation_avoir_numero", ["avoir_numero"])
        if "avoir_emis_le" not in colonnes:
            batch.add_column(sa.Column("avoir_emis_le", sa.Date(), nullable=True))
        if "avoir_snapshot_json" not in colonnes:
            batch.add_column(sa.Column("avoir_snapshot_json", sa.Text(), nullable=True))
    if "annulation_mouvement_id" not in {c["name"] for c in sa.inspect(op.get_bind()).get_columns("don")}:
        with op.batch_alter_table("don") as batch:
            batch.add_column(sa.Column("annulation_mouvement_id", sa.Integer(), nullable=True))
            batch.create_foreign_key("fk_don_annulation_mouvement", "caisse_mouvement",
                                     ["annulation_mouvement_id"], ["id"], ondelete="SET NULL")


def downgrade():
    with op.batch_alter_table("don") as batch:
        batch.drop_constraint("fk_don_annulation_mouvement", type_="foreignkey")
        batch.drop_column("annulation_mouvement_id")
    with op.batch_alter_table("reservation") as batch:
        batch.drop_index("ix_reservation_avoir_numero")
        batch.drop_constraint("uq_reservation_avoir_numero", type_="unique")
        for colonne in ("avoir_snapshot_json", "avoir_emis_le", "avoir_numero", "facture_snapshot_json"):
            batch.drop_column(colonne)
