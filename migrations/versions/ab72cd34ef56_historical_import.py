"""Lots et provenance de migration, créneau source, année seule.

Revision ID: ab72cd34ef56
Revises: de23fa45bc67
"""
from alembic import op
import sqlalchemy as sa

revision = "ab72cd34ef56"
down_revision = "de23fa45bc67"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("participant", sa.Column("annee_naissance", sa.Integer(), nullable=True))
    op.add_column("session_activite", sa.Column("creneau_source", sa.String(80), nullable=True))
    op.create_table(
        "historical_import_batch",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("file_hash", sa.String(64), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("secteurs_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="SET NULL")),
        sa.Column("plan_digest", sa.String(64), nullable=False),
        sa.Column("decisions_json", sa.Text(), nullable=False),
        sa.Column("summary_json", sa.Text(), nullable=False),
        sa.UniqueConstraint("file_hash", name="uq_historical_file"),
    )
    op.create_index("ix_historical_import_batch_file_hash", "historical_import_batch", ["file_hash"])
    op.create_table(
        "historical_import_source",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("batch_id", sa.String(36), sa.ForeignKey("historical_import_batch.id"), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("source_key", sa.String(255), nullable=False),
        sa.Column("source_sheet", sa.String(100)),
        sa.Column("source_row", sa.Integer()),
        sa.Column("source_column", sa.Integer()),
        sa.Column("source_cell", sa.String(30)),
        sa.Column("participant_id", sa.Integer(), sa.ForeignKey("participant.id", ondelete="SET NULL")),
        sa.Column("atelier_id", sa.Integer(), sa.ForeignKey("atelier_activite.id", ondelete="SET NULL")),
        sa.Column("session_id", sa.Integer(), sa.ForeignKey("session_activite.id", ondelete="SET NULL")),
        sa.Column("presence_id", sa.Integer(), sa.ForeignKey("presence_activite.id", ondelete="SET NULL")),
        sa.Column("created_target", sa.Boolean(), nullable=False),
        sa.Column("raw_json", sa.Text(), nullable=False),
        sa.Column("decision_json", sa.Text(), nullable=False),
        sa.UniqueConstraint("batch_id", "kind", "source_key", name="uq_historical_source"),
    )
    op.create_index("ix_historical_import_source_batch_id", "historical_import_source", ["batch_id"])
    op.create_index("ix_historical_import_source_participant_id", "historical_import_source", ["participant_id"])


def downgrade():
    # Exporter les preuves avant downgrade : aucune donnée métier supprimée.
    op.drop_table("historical_import_source")
    op.drop_table("historical_import_batch")
    with op.batch_alter_table("session_activite") as batch:
        batch.drop_column("creneau_source")
    with op.batch_alter_table("participant") as batch:
        batch.drop_column("annee_naissance")
