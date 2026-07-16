"""Questionnaires : type et rattachement à un projet

Revision ID: c4d5e6f7a8b9
Revises: b2c3d4e5f6a7
Create Date: 2026-06-30

Ajoute au questionnaire :
- ``type_questionnaire`` (autre / satisfaction / avant / après),
- ``projet_id`` (rattachement optionnel à un projet pour la mesure d'impact).

Migration DÉFENSIVE : ``add_column`` protégé par inspection des colonnes.
"""
from alembic import op
import sqlalchemy as sa


revision = "c4d5e6f7a8b9"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


COLUMNS = [
    ("type_questionnaire", sa.Column("type_questionnaire", sa.String(length=30), nullable=False, server_default="autre")),
    ("projet_id", sa.Column("projet_id", sa.Integer(), nullable=True)),
]


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if not insp.has_table("questionnaire"):
        return
    existing = {c["name"] for c in insp.get_columns("questionnaire")}
    for name, column in COLUMNS:
        if name not in existing:
            op.add_column("questionnaire", column)
    # index sur projet_id (best-effort)
    if "projet_id" not in existing:
        try:
            op.create_index("ix_questionnaire_projet_id", "questionnaire", ["projet_id"])
        except Exception:
            pass


def downgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if not insp.has_table("questionnaire"):
        return
    existing = {c["name"] for c in insp.get_columns("questionnaire")}
    try:
        op.drop_index("ix_questionnaire_projet_id", table_name="questionnaire")
    except Exception:
        pass
    for name, _ in reversed(COLUMNS):
        if name in existing:
            op.drop_column("questionnaire", name)
