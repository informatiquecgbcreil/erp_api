"""insertion positionnements and certification enhancements

Revision ID: 22f3a4b5c6d7
Revises: 21e2f3a4b5c6
Create Date: 2026-04-03 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "22f3a4b5c6d7"
down_revision = "21e2f3a4b5c6"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "participant_insertion_positionnement",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("participant_id", sa.Integer(), sa.ForeignKey("participant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("parcours_id", sa.Integer(), sa.ForeignKey("participant_insertion_parcours.id", ondelete="SET NULL"), nullable=True),
        sa.Column("niveau_id", sa.Integer(), sa.ForeignKey("insertion_niveau_ref.id"), nullable=True),
        sa.Column("date_positionnement", sa.Date(), nullable=True),
        sa.Column("type_positionnement", sa.String(length=32), nullable=False, server_default="entree"),
        sa.Column("commentaire", sa.Text(), nullable=True),
        sa.Column("legacy_source", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=True),
        sa.Column("updated_by_user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=True),
    )
    op.create_index("ix_participant_insertion_positionnement_participant_id", "participant_insertion_positionnement", ["participant_id"], unique=False)
    op.create_index("ix_participant_insertion_positionnement_parcours_id", "participant_insertion_positionnement", ["parcours_id"], unique=False)
    op.create_index("ix_participant_insertion_positionnement_legacy_source", "participant_insertion_positionnement", ["legacy_source"], unique=False)

    with op.batch_alter_table("participant_insertion_certification") as batch_op:
        batch_op.add_column(sa.Column("parcours_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("date_passage", sa.Date(), nullable=True))
        batch_op.add_column(sa.Column("resultat", sa.String(length=32), nullable=True))
        batch_op.create_index("ix_participant_insertion_certification_parcours_id", ["parcours_id"], unique=False)
        batch_op.create_foreign_key(
            "fk_participant_insertion_certification_parcours_id",
            "participant_insertion_parcours",
            ["parcours_id"],
            ["id"],
            ondelete="SET NULL",
        )

    op.execute(
        sa.text(
            "UPDATE participant_insertion_certification "
            "SET date_passage = date_obtention "
            "WHERE date_passage IS NULL AND date_obtention IS NOT NULL"
        )
    )
    op.execute(
        sa.text(
            "UPDATE participant_insertion_certification "
            "SET resultat = 'obtenu' "
            "WHERE resultat IS NULL AND date_obtention IS NOT NULL"
        )
    )


def downgrade():
    with op.batch_alter_table("participant_insertion_certification") as batch_op:
        batch_op.drop_constraint("fk_participant_insertion_certification_parcours_id", type_="foreignkey")
        batch_op.drop_index("ix_participant_insertion_certification_parcours_id")
        batch_op.drop_column("resultat")
        batch_op.drop_column("date_passage")
        batch_op.drop_column("parcours_id")

    op.drop_index("ix_participant_insertion_positionnement_legacy_source", table_name="participant_insertion_positionnement")
    op.drop_index("ix_participant_insertion_positionnement_parcours_id", table_name="participant_insertion_positionnement")
    op.drop_index("ix_participant_insertion_positionnement_participant_id", table_name="participant_insertion_positionnement")
    op.drop_table("participant_insertion_positionnement")
