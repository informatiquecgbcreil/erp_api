"""User : drapeau actif (désactivation d'un compte sans suppression).

Un compte désactivé ne peut plus se connecter mais conserve son historique.
Migration défensive (le démarrage exécute aussi db.create_all).

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
Create Date: 2026-06-30
"""
import sqlalchemy as sa
from alembic import op

revision = "d2e3f4a5b6c7"
down_revision = "c1d2e3f4a5b6"
branch_labels = None
depends_on = None

TABLE = "user"
COLONNES = {
    "actif": sa.Column("actif", sa.Boolean(), nullable=False, server_default=sa.true()),
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
