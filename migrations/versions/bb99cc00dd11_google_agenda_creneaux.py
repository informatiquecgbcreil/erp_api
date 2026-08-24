"""Synchro Google Agenda : créneaux hors ateliers.

Revision ID: bb99cc00dd11
Revises: aa77bb88cc99
Create Date: 2026-08-24

La correspondance google_agenda_evenement référence désormais une séance
OU un créneau hors ateliers : session_id devient nullable, creneau_id
apparaît (référence souple, sans clé étrangère : un créneau se supprime
définitivement et la correspondance doit lui survivre le temps d'aller
retirer l'événement côté Google).
"""
from alembic import op
import sqlalchemy as sa


revision = "bb99cc00dd11"
down_revision = "aa77bb88cc99"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if not insp.has_table("google_agenda_evenement"):
        return
    cols = {c["name"] for c in insp.get_columns("google_agenda_evenement")}

    with op.batch_alter_table("google_agenda_evenement") as batch:
        batch.alter_column("session_id", existing_type=sa.Integer(), nullable=True)
        if "creneau_id" not in cols:
            batch.add_column(sa.Column("creneau_id", sa.Integer(), nullable=True))
            batch.create_unique_constraint("uq_google_agenda_compte_creneau", ["compte_id", "creneau_id"])

    indexes = {i["name"] for i in sa.inspect(bind).get_indexes("google_agenda_evenement")}
    if "ix_google_agenda_evenement_creneau_id" not in indexes:
        op.create_index("ix_google_agenda_evenement_creneau_id", "google_agenda_evenement", ["creneau_id"])


def downgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if not insp.has_table("google_agenda_evenement"):
        return
    cols = {c["name"] for c in insp.get_columns("google_agenda_evenement")}
    if "creneau_id" in cols:
        # Les correspondances de créneaux disparaissent avec la colonne.
        bind.execute(sa.text("DELETE FROM google_agenda_evenement WHERE creneau_id IS NOT NULL"))
        indexes = {i["name"] for i in insp.get_indexes("google_agenda_evenement")}
        if "ix_google_agenda_evenement_creneau_id" in indexes:
            op.drop_index("ix_google_agenda_evenement_creneau_id", "google_agenda_evenement")
        with op.batch_alter_table("google_agenda_evenement") as batch:
            batch.drop_constraint("uq_google_agenda_compte_creneau", type_="unique")
            batch.drop_column("creneau_id")
    with op.batch_alter_table("google_agenda_evenement") as batch:
        batch.alter_column("session_id", existing_type=sa.Integer(), nullable=False)
