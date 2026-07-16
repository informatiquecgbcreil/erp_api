"""Integration Recup vers le module RH.

Revision ID: ee55ff667788
Revises: dd44ee55ff66
Create Date: 2026-07-13
"""
from alembic import op
import sqlalchemy as sa


revision = "ee55ff667788"
down_revision = "dd44ee55ff66"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if insp.has_table("salarie"):
        cols = {c["name"] for c in insp.get_columns("salarie")}
        if "recup_user_id" not in cols:
            op.add_column("salarie", sa.Column("recup_user_id", sa.String(length=120), nullable=True))
        if "recup_email" not in cols:
            op.add_column("salarie", sa.Column("recup_email", sa.String(length=255), nullable=True))
        if "recup_active" not in cols:
            op.add_column("salarie", sa.Column("recup_active", sa.Boolean(), nullable=True))
        indexes = {idx["name"] for idx in sa.inspect(bind).get_indexes("salarie")}
        if "ix_salarie_recup_user_id" not in indexes:
            op.create_index("ix_salarie_recup_user_id", "salarie", ["recup_user_id"], unique=True)

    if not insp.has_table("recup_rh_snapshot"):
        op.create_table(
            "recup_rh_snapshot",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("salarie_id", sa.Integer(), nullable=False),
            sa.Column("year", sa.Integer(), nullable=False),
            sa.Column("balance_minutes", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("overtime_minutes", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("approved_recovery_minutes", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("pending_recovery_minutes", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("mileage_expense_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("mileage_distance_km", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("mileage_amount_cents", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("hourly_unloaded_cents", sa.Integer(), nullable=True),
            sa.Column("hourly_loaded_cents", sa.Integer(), nullable=True),
            sa.Column("worked_weeks_per_year", sa.Integer(), nullable=True),
            sa.Column("source_generated_at", sa.DateTime(), nullable=True),
            sa.Column("synced_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["salarie_id"], ["salarie.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("salarie_id", "year", name="uq_recup_rh_snapshot_salarie_year"),
        )
        op.create_index("ix_recup_rh_snapshot_salarie_id", "recup_rh_snapshot", ["salarie_id"])
        op.create_index("ix_recup_rh_snapshot_year", "recup_rh_snapshot", ["year"])


def downgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if insp.has_table("recup_rh_snapshot"):
        op.drop_table("recup_rh_snapshot")
    if insp.has_table("salarie"):
        cols = {c["name"] for c in insp.get_columns("salarie")}
        with op.batch_alter_table("salarie") as batch:
            for name in ("recup_active", "recup_email", "recup_user_id"):
                if name in cols:
                    batch.drop_column(name)
