"""Portail des apprenants : code participant + tentatives + état de synchro.

Migration manuelle défensive (db.create_all au démarrage peut avoir déjà
créé les tables/colonnes ; on garde des gardes sa.inspect).

Revision ID: 7d8e9f0a1b23
Revises: 6c7d8e9f0a12
Create Date: 2026-06-16
"""
import sqlalchemy as sa
from alembic import op

revision = "7d8e9f0a1b23"
down_revision = "6c7d8e9f0a12"
branch_labels = None
depends_on = None


def _a_table(nom: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(nom)


def _a_colonne(table: str, colonne: str) -> bool:
    insp = sa.inspect(op.get_bind())
    if not insp.has_table(table):
        return False
    return colonne in {c["name"] for c in insp.get_columns(table)}


def upgrade():
    if _a_table("participant") and not _a_colonne("participant", "portail_code"):
        op.add_column("participant", sa.Column("portail_code", sa.String(length=120), nullable=True))
        op.create_index("ix_participant_portail_code", "participant", ["portail_code"])

    if not _a_table("portail_attempt"):
        op.create_table(
            "portail_attempt",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("attempt_id", sa.String(length=255), nullable=False),
            sa.Column("external_id", sa.String(length=64), nullable=True),
            sa.Column("participant_id", sa.Integer(),
                      sa.ForeignKey("participant.id", ondelete="SET NULL"), nullable=True),
            sa.Column("activity", sa.String(length=255), nullable=True),
            sa.Column("activity_type", sa.String(length=120), nullable=True),
            sa.Column("theme", sa.String(length=255), nullable=True),
            sa.Column("score", sa.Float(), nullable=True),
            sa.Column("max_score", sa.Float(), nullable=True),
            sa.Column("pct", sa.Float(), nullable=True),
            sa.Column("duration_ms", sa.Integer(), nullable=True),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.UniqueConstraint("attempt_id", name="uq_portail_attempt_attempt_id"),
        )
        op.create_index("ix_portail_attempt_external_id", "portail_attempt", ["external_id"])
        op.create_index("ix_portail_attempt_participant_id", "portail_attempt", ["participant_id"])
        op.create_index("ix_portail_attempt_finished_at", "portail_attempt", ["finished_at"])

    if not _a_table("portail_sync_state"):
        op.create_table(
            "portail_sync_state",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("last_since", sa.String(length=40), nullable=True),
            sa.Column("last_run_at", sa.DateTime(), nullable=True),
            sa.Column("last_status", sa.String(length=255), nullable=True),
        )


def downgrade():
    for nom in ("portail_attempt", "portail_sync_state"):
        if _a_table(nom):
            op.drop_table(nom)
    if _a_colonne("participant", "portail_code"):
        op.drop_column("participant", "portail_code")
