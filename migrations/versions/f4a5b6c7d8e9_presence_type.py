"""PresenceActivite : statut de présence (présent / retard / absent excusé).

Migration défensive (le démarrage exécute aussi db.create_all).

Revision ID: f4a5b6c7d8e9
Revises: e3f4a5b6c7d8
Create Date: 2026-06-30
"""
import sqlalchemy as sa
from alembic import op

revision = "f4a5b6c7d8e9"
down_revision = "e3f4a5b6c7d8"
branch_labels = None
depends_on = None

TABLE = "presence_activite"
COLONNES = {
    "presence_type": sa.Column("presence_type", sa.String(length=20), nullable=False, server_default="present"),
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
