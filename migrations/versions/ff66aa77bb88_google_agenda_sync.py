"""Synchronisation Google Agenda (push temps réel via l'API Calendar).

Revision ID: ff66aa77bb88
Revises: ee55ff667788
Create Date: 2026-08-24
"""
from alembic import op
import sqlalchemy as sa


revision = "ff66aa77bb88"
down_revision = "ee55ff667788"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if not insp.has_table("google_agenda_compte"):
        op.create_table(
            "google_agenda_compte",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("google_email", sa.String(length=255), nullable=True),
            sa.Column("refresh_token", sa.Text(), nullable=False),
            sa.Column("access_token", sa.Text(), nullable=True),
            sa.Column("access_token_expire_at", sa.DateTime(), nullable=True),
            sa.Column("calendar_id", sa.String(length=255), nullable=True),
            sa.Column("actif", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("derniere_synchro", sa.DateTime(), nullable=True),
            sa.Column("derniere_erreur", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_google_agenda_compte_user_id", "google_agenda_compte", ["user_id"], unique=True)

    if not insp.has_table("google_agenda_evenement"):
        op.create_table(
            "google_agenda_evenement",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("compte_id", sa.Integer(), nullable=False),
            sa.Column("session_id", sa.Integer(), nullable=False),
            sa.Column("google_event_id", sa.String(length=255), nullable=False),
            sa.Column("empreinte", sa.String(length=64), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["compte_id"], ["google_agenda_compte.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["session_id"], ["session_activite.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("compte_id", "session_id", name="uq_google_agenda_compte_session"),
        )
        op.create_index("ix_google_agenda_evenement_compte_id", "google_agenda_evenement", ["compte_id"])
        op.create_index("ix_google_agenda_evenement_session_id", "google_agenda_evenement", ["session_id"])


def downgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if insp.has_table("google_agenda_evenement"):
        op.drop_table("google_agenda_evenement")
    if insp.has_table("google_agenda_compte"):
        op.drop_table("google_agenda_compte")
