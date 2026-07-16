"""Quartier : coordonnées (latitude/longitude) + drapeau de placement manuel.

Permet de positionner la bulle d'un quartier sur la carte des habitants à
l'emplacement du quartier (et non à la moyenne des adresses), pour les
structures qui ne saisissent que ville + quartier. Migration défensive.

Revision ID: c1d2e3f4a5b6
Revises: a0b1c2d3e4f5
Create Date: 2026-06-26
"""
import sqlalchemy as sa
from alembic import op

revision = "c1d2e3f4a5b6"
down_revision = "a0b1c2d3e4f5"
branch_labels = None
depends_on = None

TABLE = "quartier"
COLONNES = {
    "latitude": sa.Column("latitude", sa.Float(), nullable=True),
    "longitude": sa.Column("longitude", sa.Float(), nullable=True),
    "geo_manuel": sa.Column("geo_manuel", sa.Boolean(), nullable=False, server_default=sa.false()),
}


def _colonnes_existantes() -> set[str]:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if not insp.has_table(TABLE):
        return set()
    return {c["name"] for c in insp.get_columns(TABLE)}


def upgrade():
    existantes = _colonnes_existantes()
    for nom, colonne in COLONNES.items():
        if nom not in existantes:
            op.add_column(TABLE, colonne)


def downgrade():
    existantes = _colonnes_existantes()
    with op.batch_alter_table(TABLE) as batch_op:
        for nom in COLONNES:
            if nom in existantes:
                batch_op.drop_column(nom)
