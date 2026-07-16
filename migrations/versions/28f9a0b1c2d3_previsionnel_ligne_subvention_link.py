"""previsionnel line subvention link

Revision ID: 28f9a0b1c2d3
Revises: 27e8f9a0b1c2
Create Date: 2026-04-24
"""
from alembic import op
import sqlalchemy as sa

revision = "28f9a0b1c2d3"
down_revision = "27e8f9a0b1c2"
branch_labels = None
depends_on = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if not insp.has_table(table_name):
        return False
    return any(col["name"] == column_name for col in insp.get_columns(table_name))


def upgrade():
    if not _has_column("budget_previsionnel_ligne", "subvention_id"):
        with op.batch_alter_table("budget_previsionnel_ligne") as batch_op:
            batch_op.add_column(sa.Column("subvention_id", sa.Integer(), nullable=True))
    try:
        op.create_index("ix_budget_previsionnel_ligne_subvention_id", "budget_previsionnel_ligne", ["subvention_id"])
    except Exception:
        pass

    if op.get_bind().dialect.name != "sqlite":
        try:
            op.create_foreign_key(
                "fk_budget_prev_ligne_subvention",
                "budget_previsionnel_ligne",
                "subvention",
                ["subvention_id"],
                ["id"],
            )
        except Exception:
            pass


def downgrade():
    if op.get_bind().dialect.name != "sqlite":
        try:
            op.drop_constraint("fk_budget_prev_ligne_subvention", "budget_previsionnel_ligne", type_="foreignkey")
        except Exception:
            pass
    try:
        op.drop_index("ix_budget_previsionnel_ligne_subvention_id", table_name="budget_previsionnel_ligne")
    except Exception:
        pass
    if _has_column("budget_previsionnel_ligne", "subvention_id"):
        with op.batch_alter_table("budget_previsionnel_ligne") as batch_op:
            batch_op.drop_column("subvention_id")
