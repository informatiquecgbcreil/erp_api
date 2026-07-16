"""Module Transitions : thématiques, étiquetage ateliers, défis, mesures

Revision ID: dd44ee55ff66
Revises: cc33dd44ee55
Create Date: 2026-07-13

- ``transition_thematique``          : référentiel des thématiques (seedé au premier usage) ;
- ``atelier_transition_thematique``  : ateliers ↔ thématiques (étiquetage) ;
- ``atelier_transition_objectif``    : ateliers ↔ objectifs sectoriels (croisement) ;
- ``defi_transition``                : défis des habitants / familles ;
- ``transition_mesure``              : mesures chiffrées saisies par action.

Migration DÉFENSIVE et purement ADDITIVE : aucune table ni colonne existante
n'est modifiée.
"""
from alembic import op
import sqlalchemy as sa


revision = "dd44ee55ff66"
down_revision = "cc33dd44ee55"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if not insp.has_table("transition_thematique"):
        op.create_table(
            "transition_thematique",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("code", sa.String(length=40), nullable=False),
            sa.Column("libelle", sa.String(length=160), nullable=False),
            sa.Column("ordre", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("actif", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("code", name="uq_transition_thematique_code"),
        )
        op.create_index("ix_transition_thematique_code", "transition_thematique", ["code"])

    if not insp.has_table("atelier_transition_thematique"):
        op.create_table(
            "atelier_transition_thematique",
            sa.Column("atelier_id", sa.Integer(), sa.ForeignKey("atelier_activite.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("thematique_id", sa.Integer(), sa.ForeignKey("transition_thematique.id", ondelete="CASCADE"), primary_key=True),
        )

    if not insp.has_table("atelier_transition_objectif"):
        op.create_table(
            "atelier_transition_objectif",
            sa.Column("atelier_id", sa.Integer(), sa.ForeignKey("atelier_activite.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("objectif_id", sa.Integer(), sa.ForeignKey("objectif_sectoriel.id", ondelete="CASCADE"), primary_key=True),
        )

    if not insp.has_table("defi_transition"):
        op.create_table(
            "defi_transition",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("titre", sa.String(length=200), nullable=False),
            sa.Column("thematique_id", sa.Integer(), sa.ForeignKey("transition_thematique.id", ondelete="CASCADE"), nullable=False),
            sa.Column("objectif_id", sa.Integer(), sa.ForeignKey("objectif_sectoriel.id", ondelete="SET NULL"), nullable=True),
            sa.Column("participant_id", sa.Integer(), sa.ForeignKey("participant.id", ondelete="CASCADE"), nullable=True),
            sa.Column("foyer_id", sa.Integer(), sa.ForeignKey("foyer.id", ondelete="CASCADE"), nullable=True),
            sa.Column("statut", sa.String(length=20), nullable=False, server_default="en_cours"),
            sa.Column("date_engagement", sa.Date(), nullable=False),
            sa.Column("date_realisation", sa.Date(), nullable=True),
            sa.Column("commentaire", sa.String(length=500), nullable=True),
            sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
        for col in ("thematique_id", "objectif_id", "participant_id", "foyer_id", "statut", "date_engagement"):
            op.create_index(f"ix_defi_transition_{col}", "defi_transition", [col])

    if not insp.has_table("transition_mesure"):
        op.create_table(
            "transition_mesure",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("atelier_id", sa.Integer(), sa.ForeignKey("atelier_activite.id", ondelete="CASCADE"), nullable=False),
            sa.Column("thematique_id", sa.Integer(), sa.ForeignKey("transition_thematique.id", ondelete="SET NULL"), nullable=True),
            sa.Column("libelle", sa.String(length=160), nullable=False),
            sa.Column("valeur", sa.Float(), nullable=False, server_default="0"),
            sa.Column("unite", sa.String(length=40), nullable=True),
            sa.Column("date_mesure", sa.Date(), nullable=False),
            sa.Column("commentaire", sa.String(length=255), nullable=True),
            sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
        for col in ("atelier_id", "thematique_id", "date_mesure"):
            op.create_index(f"ix_transition_mesure_{col}", "transition_mesure", [col])


def downgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    for table in ("transition_mesure", "defi_transition",
                  "atelier_transition_objectif", "atelier_transition_thematique",
                  "transition_thematique"):
        if insp.has_table(table):
            op.drop_table(table)
