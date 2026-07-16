"""Adhésions, participation, foyers et barèmes tarifaires

Revision ID: a4b5c6d7e8f9
Revises: f3a4b5c6d7e8
Create Date: 2026-07-03

- ``foyer`` : rapproche des participants (famille) pour l'adhésion familiale.
- ``participant.foyer_id`` : rattachement optionnel à un foyer.
- ``tarif_bareme`` : barème des tarifs (adhésion individuelle/familiale,
  participation) par année scolaire, avec évolution possible en cours
  d'année (plusieurs lignes datées par (année, type)).
- ``cotisation`` : une obligation de règlement (participant ou foyer).
- ``paiement`` : les versements contre une cotisation.

Migration DÉFENSIVE : inspections préalables avant chaque opération.
"""
from alembic import op
import sqlalchemy as sa


revision = "a4b5c6d7e8f9"
down_revision = "f3a4b5c6d7e8"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    is_sqlite = bind.dialect.name == "sqlite"

    if not insp.has_table("foyer"):
        op.create_table(
            "foyer",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("nom", sa.String(length=200), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )

    if insp.has_table("participant"):
        cols = {c["name"] for c in insp.get_columns("participant")}
        if "foyer_id" not in cols:
            op.add_column("participant", sa.Column("foyer_id", sa.Integer(), nullable=True))
            op.create_index("ix_participant_foyer_id", "participant", ["foyer_id"])
            if not is_sqlite:
                op.create_foreign_key(
                    "fk_participant_foyer_id", "participant", "foyer", ["foyer_id"], ["id"]
                )

    if not insp.has_table("tarif_bareme"):
        op.create_table(
            "tarif_bareme",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("annee_scolaire", sa.Integer(), nullable=False),
            sa.Column("type_tarif", sa.String(length=30), nullable=False),
            sa.Column("montant", sa.Float(), nullable=False),
            sa.Column("date_debut", sa.Date(), nullable=False),
            sa.Column("commentaire", sa.String(length=255), nullable=True),
            sa.Column("created_by_user_id", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["created_by_user_id"], ["user.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_tarif_bareme_annee_scolaire", "tarif_bareme", ["annee_scolaire"])
        op.create_index("ix_tarif_bareme_type_tarif", "tarif_bareme", ["type_tarif"])
        op.create_index("ix_tarif_bareme_lookup", "tarif_bareme", ["annee_scolaire", "type_tarif", "date_debut"])

    if not insp.has_table("cotisation"):
        op.create_table(
            "cotisation",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("annee_scolaire", sa.Integer(), nullable=False),
            sa.Column("type_cotisation", sa.String(length=30), nullable=False),
            sa.Column("participant_id", sa.Integer(), nullable=True),
            sa.Column("foyer_id", sa.Integer(), nullable=True),
            sa.Column("montant_du", sa.Float(), nullable=False, server_default="0"),
            sa.Column("date_reference", sa.Date(), nullable=False),
            sa.Column("created_by_user_id", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["participant_id"], ["participant.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["foyer_id"], ["foyer.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["created_by_user_id"], ["user.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_cotisation_annee_scolaire", "cotisation", ["annee_scolaire"])
        op.create_index("ix_cotisation_type_cotisation", "cotisation", ["type_cotisation"])
        op.create_index("ix_cotisation_participant_id", "cotisation", ["participant_id"])
        op.create_index("ix_cotisation_foyer_id", "cotisation", ["foyer_id"])
        op.create_index("ix_cotisation_participant_annee", "cotisation", ["participant_id", "annee_scolaire", "type_cotisation"])
        op.create_index("ix_cotisation_foyer_annee", "cotisation", ["foyer_id", "annee_scolaire"])

    if not insp.has_table("paiement"):
        op.create_table(
            "paiement",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("cotisation_id", sa.Integer(), nullable=False),
            sa.Column("montant", sa.Float(), nullable=False),
            sa.Column("date_paiement", sa.Date(), nullable=False),
            sa.Column("mode", sa.String(length=20), nullable=False, server_default="especes"),
            sa.Column("commentaire", sa.String(length=255), nullable=True),
            sa.Column("created_by_user_id", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["cotisation_id"], ["cotisation.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["created_by_user_id"], ["user.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_paiement_cotisation_id", "paiement", ["cotisation_id"])


def downgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if insp.has_table("paiement"):
        op.drop_table("paiement")
    if insp.has_table("cotisation"):
        op.drop_table("cotisation")
    if insp.has_table("tarif_bareme"):
        op.drop_table("tarif_bareme")
    if insp.has_table("participant"):
        cols = {c["name"] for c in insp.get_columns("participant")}
        if "foyer_id" in cols:
            if bind.dialect.name != "sqlite":
                try:
                    op.drop_constraint("fk_participant_foyer_id", "participant", type_="foreignkey")
                except Exception:
                    pass
            op.drop_index("ix_participant_foyer_id", table_name="participant")
            op.drop_column("participant", "foyer_id")
    if insp.has_table("foyer"):
        op.drop_table("foyer")
