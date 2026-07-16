"""orientation ville quartier

Revision ID: 39e0f1a2b3c4
Revises: 38d9e0f1a2b3
Create Date: 2026-06-05

Ajoute le rattachement territorial fin aux orientations acces aux droits.
"""
from alembic import op
import sqlalchemy as sa

revision = "39e0f1a2b3c4"
down_revision = "38d9e0f1a2b3"
branch_labels = None
depends_on = None


def _bind():
    return op.get_bind()


def _insp():
    return sa.inspect(_bind())


def _has_table(name: str) -> bool:
    return _insp().has_table(name)


def _columns(table: str) -> set[str]:
    if not _has_table(table):
        return set()
    return {col.get("name") for col in _insp().get_columns(table)}


def _has_index(table: str, name: str) -> bool:
    if not _has_table(table):
        return False
    return any(idx.get("name") == name for idx in _insp().get_indexes(table))


def _add_col(table: str, name: str, column):
    if name not in _columns(table):
        op.add_column(table, column)


def upgrade():
    if not _has_table("orientation_acces_droit"):
        return

    _add_col("orientation_acces_droit", "ville", sa.Column("ville", sa.String(length=120), nullable=True))
    _add_col("orientation_acces_droit", "quartier_id", sa.Column("quartier_id", sa.Integer(), nullable=True))

    for name, cols in (
        ("ix_orientation_acces_droit_ville", ["ville"]),
        ("ix_orientation_acces_droit_quartier_id", ["quartier_id"]),
    ):
        if not _has_index("orientation_acces_droit", name):
            op.create_index(name, "orientation_acces_droit", cols)


def downgrade():
    if not _has_table("orientation_acces_droit"):
        return

    for name in ("ix_orientation_acces_droit_quartier_id", "ix_orientation_acces_droit_ville"):
        if _has_index("orientation_acces_droit", name):
            op.drop_index(name, table_name="orientation_acces_droit")

    for col in ("quartier_id", "ville"):
        if col in _columns("orientation_acces_droit"):
            op.drop_column("orientation_acces_droit", col)
