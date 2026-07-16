"""Droit à l'image sur le participant (consentement RGPD dématérialisé).

Migration écrite à la main (l'autogénération est inutilisable sur ce
projet) et défensive : n'ajoute chaque colonne que si elle n'existe pas
déjà.

Revision ID: 382fa1db8db3
Revises: 271f90ca7ca2
Create Date: 2026-06-12
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "382fa1db8db3"
down_revision = "271f90ca7ca2"
branch_labels = None
depends_on = None

TABLE = "participant"
COLONNES = {
    "droit_image_statut": sa.Column(
        "droit_image_statut", sa.String(length=20), nullable=False,
        server_default="non_renseigne",
    ),
    "droit_image_date": sa.Column("droit_image_date", sa.Date(), nullable=True),
    "droit_image_recueilli_par": sa.Column(
        "droit_image_recueilli_par", sa.String(length=180), nullable=True
    ),
}


def _colonnes_existantes() -> set[str]:
    bind = op.get_bind()
    return {c["name"] for c in sa.inspect(bind).get_columns(TABLE)}


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
