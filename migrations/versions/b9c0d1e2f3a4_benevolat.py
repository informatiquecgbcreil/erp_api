"""Bénévolat : drapeau bénévole + heures de bénévolat

Revision ID: b9c0d1e2f3a4
Revises: a8b9c0d1e2f3
Create Date: 2026-07-02

- ``participant.est_benevole`` : bénévole du centre (SENACS, valorisation 87).
- ``benevole_heures`` : heures réalisées (date, durée, mission, secteur).

Migration DÉFENSIVE : inspections préalables avant chaque opération.
"""
from alembic import op
import sqlalchemy as sa


revision = "b9c0d1e2f3a4"
down_revision = "a8b9c0d1e2f3"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if insp.has_table("participant"):
        cols = {c["name"] for c in insp.get_columns("participant")}
        if "est_benevole" not in cols:
            op.add_column("participant", sa.Column("est_benevole", sa.Boolean(), nullable=False, server_default=sa.false()))
            try:
                op.create_index("ix_participant_est_benevole", "participant", ["est_benevole"])
            except Exception:
                pass

    if not insp.has_table("benevole_heures"):
        op.create_table(
            "benevole_heures",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("participant_id", sa.Integer(), nullable=False),
            sa.Column("date_action", sa.Date(), nullable=False),
            sa.Column("heures", sa.Float(), nullable=False, server_default="0"),
            sa.Column("mission", sa.String(length=200), nullable=True),
            sa.Column("secteur", sa.String(length=80), nullable=True),
            sa.Column("commentaire", sa.Text(), nullable=True),
            sa.Column("created_by_user_id", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["participant_id"], ["participant.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["created_by_user_id"], ["user.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_benevole_heures_participant_id", "benevole_heures", ["participant_id"])
        op.create_index("ix_benevole_heures_date_action", "benevole_heures", ["date_action"])
        op.create_index("ix_benevole_heures_secteur", "benevole_heures", ["secteur"])


def downgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if insp.has_table("benevole_heures"):
        op.drop_table("benevole_heures")
    if insp.has_table("participant"):
        cols = {c["name"] for c in insp.get_columns("participant")}
        if "est_benevole" in cols:
            try:
                op.drop_index("ix_participant_est_benevole", table_name="participant")
            except Exception:
                pass
            op.drop_column("participant", "est_benevole")
