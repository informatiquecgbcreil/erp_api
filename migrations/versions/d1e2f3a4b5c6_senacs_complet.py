"""SENACS complet : séances événementielles + emplois/ETP

Revision ID: d1e2f3a4b5c6
Revises: c0d1e2f3a4b5
Create Date: 2026-07-02

- ``session_activite.est_evenement`` : séance événementielle (fête de
  quartier, temps fort…), comptée à part dans le volet événementiel SENACS.
- ``senacs_emploi`` : postes salariés par exercice (fonction, contrat, ETP)
  pour l'onglet Emplois.

Migration DÉFENSIVE : inspections préalables avant chaque opération.
"""
from alembic import op
import sqlalchemy as sa


revision = "d1e2f3a4b5c6"
down_revision = "c0d1e2f3a4b5"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if insp.has_table("session_activite"):
        cols = {c["name"] for c in insp.get_columns("session_activite")}
        if "est_evenement" not in cols:
            op.add_column("session_activite", sa.Column("est_evenement", sa.Boolean(), nullable=False, server_default=sa.false()))
            try:
                op.create_index("ix_session_activite_est_evenement", "session_activite", ["est_evenement"])
            except Exception:
                pass

    if not insp.has_table("senacs_emploi"):
        op.create_table(
            "senacs_emploi",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("annee", sa.Integer(), nullable=False),
            sa.Column("intitule", sa.String(length=200), nullable=False),
            sa.Column("type_contrat", sa.String(length=30), nullable=False, server_default="cdi"),
            sa.Column("etp", sa.Float(), nullable=False, server_default="1"),
            sa.Column("commentaire", sa.String(length=255), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_senacs_emploi_annee", "senacs_emploi", ["annee"])


def downgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if insp.has_table("senacs_emploi"):
        op.drop_table("senacs_emploi")
    if insp.has_table("session_activite"):
        cols = {c["name"] for c in insp.get_columns("session_activite")}
        if "est_evenement" in cols:
            try:
                op.drop_index("ix_session_activite_est_evenement", table_name="session_activite")
            except Exception:
                pass
            op.drop_column("session_activite", "est_evenement")
