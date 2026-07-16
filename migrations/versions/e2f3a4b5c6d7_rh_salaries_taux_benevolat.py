"""RH salariés (réservé direction) + taux horaire de valorisation éditable

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2026-07-02

- ``instance_settings.benevolat_taux_horaire`` : taux horaire de valorisation
  du bénévolat modifiable depuis l'application (prioritaire sur la config).
- ``salarie`` : module RH minimal — salariés, poste, secteur, contrat, ETP,
  salaire chargé, dates d'entrée/sortie, référence externe pour import.

Migration DÉFENSIVE : inspections préalables avant chaque opération.
"""
from alembic import op
import sqlalchemy as sa


revision = "e2f3a4b5c6d7"
down_revision = "d1e2f3a4b5c6"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if insp.has_table("instance_settings"):
        cols = {c["name"] for c in insp.get_columns("instance_settings")}
        if "benevolat_taux_horaire" not in cols:
            op.add_column("instance_settings", sa.Column("benevolat_taux_horaire", sa.Float(), nullable=True))

    if not insp.has_table("salarie"):
        op.create_table(
            "salarie",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("nom", sa.String(length=160), nullable=False),
            sa.Column("prenom", sa.String(length=120), nullable=True),
            sa.Column("poste", sa.String(length=200), nullable=True),
            sa.Column("secteur", sa.String(length=80), nullable=True),
            sa.Column("type_contrat", sa.String(length=30), nullable=False, server_default="cdi"),
            sa.Column("etp", sa.Float(), nullable=False, server_default="1"),
            sa.Column("salaire_brut_charge", sa.Float(), nullable=True),
            sa.Column("date_entree", sa.Date(), nullable=True),
            sa.Column("date_sortie", sa.Date(), nullable=True),
            sa.Column("commentaire", sa.Text(), nullable=True),
            sa.Column("source_ref", sa.String(length=120), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_salarie_secteur", "salarie", ["secteur"])
        op.create_index("ix_salarie_date_sortie", "salarie", ["date_sortie"])
        op.create_index("ix_salarie_source_ref", "salarie", ["source_ref"])


def downgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if insp.has_table("salarie"):
        op.drop_table("salarie")
    if insp.has_table("instance_settings"):
        cols = {c["name"] for c in insp.get_columns("instance_settings")}
        if "benevolat_taux_horaire" in cols:
            op.drop_column("instance_settings", "benevolat_taux_horaire")
