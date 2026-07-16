"""Flux calendrier iCal personnel : jeton secret sur l'utilisateur

Revision ID: c6d7e8f9a0b1
Revises: b5c6d7e8f9a0
Create Date: 2026-07-06

``user.calendar_token`` : jeton du flux iCal personnel (abonnement Google
Agenda / Apple / Outlook). NULL tant que la synchro n'est pas activée.

Migration DÉFENSIVE : inspections préalables avant chaque opération.
"""
from alembic import op
import sqlalchemy as sa


revision = "c6d7e8f9a0b1"
down_revision = "b5c6d7e8f9a0"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if insp.has_table("user"):
        cols = {c["name"] for c in insp.get_columns("user")}
        if "calendar_token" not in cols:
            op.add_column("user", sa.Column("calendar_token", sa.String(length=64), nullable=True))
            op.create_index("ix_user_calendar_token", "user", ["calendar_token"], unique=True)


def downgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if insp.has_table("user"):
        cols = {c["name"] for c in insp.get_columns("user")}
        if "calendar_token" in cols:
            op.drop_index("ix_user_calendar_token", table_name="user")
            op.drop_column("user", "calendar_token")
