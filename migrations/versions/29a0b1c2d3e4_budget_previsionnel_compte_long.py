"""budget previsionnel compte plus long

Revision ID: 29a0b1c2d3e4
Revises: 28f9a0b1c2d3
Create Date: 2026-04-25
"""
from alembic import op
import sqlalchemy as sa

revision = "29a0b1c2d3e4"
down_revision = "28f9a0b1c2d3"
branch_labels = None
depends_on = None


def _dialect():
    return (op.get_bind().dialect.name or "").lower()


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def _has_column(table: str, column: str) -> bool:
    if not _has_table(table):
        return False
    return any(c["name"] == column for c in sa.inspect(op.get_bind()).get_columns(table))


def upgrade():
    if not _has_column("budget_previsionnel_ligne", "compte"):
        return

    if _dialect() == "sqlite":
        with op.batch_alter_table("budget_previsionnel_ligne") as batch_op:
            batch_op.alter_column(
                "compte",
                existing_type=sa.String(length=20),
                type_=sa.String(length=120),
                existing_nullable=False,
                existing_server_default="60",
            )
    else:
        op.alter_column(
            "budget_previsionnel_ligne",
            "compte",
            existing_type=sa.String(length=20),
            type_=sa.String(length=120),
            existing_nullable=False,
            existing_server_default="60",
        )


def downgrade():
    if not _has_column("budget_previsionnel_ligne", "compte"):
        return

    if _dialect() == "sqlite":
        with op.batch_alter_table("budget_previsionnel_ligne") as batch_op:
            batch_op.alter_column(
                "compte",
                existing_type=sa.String(length=120),
                type_=sa.String(length=20),
                existing_nullable=False,
                existing_server_default="60",
            )
    else:
        op.alter_column(
            "budget_previsionnel_ligne",
            "compte",
            existing_type=sa.String(length=120),
            type_=sa.String(length=20),
            existing_nullable=False,
            existing_server_default="60",
        )
