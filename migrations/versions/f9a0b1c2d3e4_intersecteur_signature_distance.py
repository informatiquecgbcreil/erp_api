"""Ateliers intersecteur + signature de présence à distance

Revision ID: f9a0b1c2d3e4
Revises: e8f9a0b1c2d3
Create Date: 2026-07-06

- ``atelier_activite.est_intersecteur`` : atelier visible et utilisable
  par tous les secteurs ; son champ ``secteur`` devient le secteur
  d'imputation des statistiques (choisi à la création).
- ``presence_activite.signature_token`` : jeton personnel à usage unique
  pour signer sa présence à distance (lien /signer/<jeton>).

Migration DÉFENSIVE : inspections préalables avant chaque opération.
"""
from alembic import op
import sqlalchemy as sa


revision = "f9a0b1c2d3e4"
down_revision = "e8f9a0b1c2d3"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if insp.has_table("atelier_activite"):
        cols = {c["name"] for c in insp.get_columns("atelier_activite")}
        if "est_intersecteur" not in cols:
            faux = sa.text("0") if bind.dialect.name == "sqlite" else sa.text("false")
            op.add_column("atelier_activite", sa.Column("est_intersecteur", sa.Boolean(), nullable=False, server_default=faux))
            op.create_index("ix_atelier_activite_est_intersecteur", "atelier_activite", ["est_intersecteur"])

    if insp.has_table("presence_activite"):
        cols = {c["name"] for c in insp.get_columns("presence_activite")}
        if "signature_token" not in cols:
            op.add_column("presence_activite", sa.Column("signature_token", sa.String(length=64), nullable=True))
            op.create_index("ix_presence_activite_signature_token", "presence_activite", ["signature_token"], unique=True)


def downgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if insp.has_table("presence_activite"):
        cols = {c["name"] for c in insp.get_columns("presence_activite")}
        if "signature_token" in cols:
            op.drop_index("ix_presence_activite_signature_token", table_name="presence_activite")
            op.drop_column("presence_activite", "signature_token")
    if insp.has_table("atelier_activite"):
        cols = {c["name"] for c in insp.get_columns("atelier_activite")}
        if "est_intersecteur" in cols:
            op.drop_index("ix_atelier_activite_est_intersecteur", table_name="atelier_activite")
            op.drop_column("atelier_activite", "est_intersecteur")
