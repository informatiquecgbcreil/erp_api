"""Géolocalisation partenaire : coordonnées + métadonnées de géocodage.

Ajoute des colonnes NULLABLE à la table ``partenaire`` pour la carte des
partenaires. Migration manuelle DÉFENSIVE (le démarrage exécute aussi
``db.create_all`` : selon l'ordre, les colonnes peuvent déjà exister).

Revision ID: a0b1c2d3e4f5
Revises: 9f0a1b2c3d4e
Create Date: 2026-06-26
"""
import sqlalchemy as sa
from alembic import op

revision = "a0b1c2d3e4f5"
down_revision = "9f0a1b2c3d4e"
branch_labels = None
depends_on = None

TABLE = "partenaire"
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
