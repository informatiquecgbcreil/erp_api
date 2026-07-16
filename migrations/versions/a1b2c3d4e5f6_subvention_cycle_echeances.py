"""Subventions : financeur, statut de cycle et échéances

Revision ID: a1b2c3d4e5f6
Revises: f4a5b6c7d8e9
Create Date: 2026-06-30

Ajoute au dossier de subvention :
- ``financeur`` et ``reference`` (organisme + n° de dossier),
- ``statut_cycle`` (sollicitée / accordée / versée / soldée / refusée),
- quatre échéances clés (dépôt, décision, versement attendu, bilan à rendre).

Migration DÉFENSIVE : chaque ``add_column`` est protégé par une inspection des
colonnes existantes, afin de pouvoir s'exécuter sans erreur sur une base déjà
partiellement à jour (création via ``db.create_all`` au démarrage).
"""
from alembic import op
import sqlalchemy as sa


revision = "a1b2c3d4e5f6"
down_revision = "f4a5b6c7d8e9"
branch_labels = None
depends_on = None


COLUMNS = [
    ("financeur", sa.Column("financeur", sa.String(length=200), nullable=True)),
    ("reference", sa.Column("reference", sa.String(length=120), nullable=True)),
    ("statut_cycle", sa.Column("statut_cycle", sa.String(length=30), nullable=False, server_default="sollicitee")),
    ("date_depot", sa.Column("date_depot", sa.Date(), nullable=True)),
    ("date_decision", sa.Column("date_decision", sa.Date(), nullable=True)),
    ("date_versement_prevu", sa.Column("date_versement_prevu", sa.Date(), nullable=True)),
    ("date_bilan_prevu", sa.Column("date_bilan_prevu", sa.Date(), nullable=True)),
]


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if not insp.has_table("subvention"):
        return
    existing = {c["name"] for c in insp.get_columns("subvention")}
    for name, column in COLUMNS:
        if name not in existing:
            op.add_column("subvention", column)


def downgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if not insp.has_table("subvention"):
        return
    existing = {c["name"] for c in insp.get_columns("subvention")}
    for name, _ in reversed(COLUMNS):
        if name in existing:
            op.drop_column("subvention", name)
