"""Géolocalisation participant : coordonnées + métadonnées de géocodage.

Ajoute des colonnes NULLABLE à la table ``participant`` pour la carte des
habitants. L'adresse reste 100 % facultative : NULL = participant non
localisé. Migration manuelle DÉFENSIVE (le démarrage exécute aussi
``db.create_all`` : selon l'ordre, les colonnes peuvent déjà exister).

Revision ID: 9f0a1b2c3d4e
Revises: 8e9f0a1b2c34
Create Date: 2026-06-26
"""
import sqlalchemy as sa
from alembic import op

revision = "9f0a1b2c3d4e"
down_revision = "8e9f0a1b2c34"
branch_labels = None
depends_on = None

TABLE = "participant"
COLONNES = {
    "latitude": sa.Column("latitude", sa.Float(), nullable=True),
    "longitude": sa.Column("longitude", sa.Float(), nullable=True),
    "geocode_precision": sa.Column("geocode_precision", sa.String(length=20), nullable=True),
    "geocode_score": sa.Column("geocode_score", sa.Float(), nullable=True),
    "geocoded_at": sa.Column("geocoded_at", sa.DateTime(), nullable=True),
    "geocode_query": sa.Column("geocode_query", sa.String(length=255), nullable=True),
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
