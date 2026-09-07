"""Inscriptions annuelles : inscription familiale, membres du foyer.

Revision ID: de23fa45bc67
Revises: cd12ef34ab56
Create Date: 2026-09-07

- ``inscription_annuelle.type_inscription`` : individuelle / familiale ;
- ``inscription_annuelle.foyer_id``         : foyer créé ou rejoint ;
- ``inscription_annuelle.reglement_statut`` : rien / partiel / complet ;
- ``inscription_annuelle.reglement_du``     : montant dû figé (adhésion +
  participations), pour lister et exporter sans tout recalculer ;
- ``inscription_annuelle_membre``           : les autres membres du foyer
  déclarés sur le bulletin (nom, prénom, date de naissance, filiation
  facultative, fiche participant créée le cas échéant).

Migration DÉFENSIVE et purement ADDITIVE.
"""
from alembic import op
import sqlalchemy as sa


revision = "de23fa45bc67"
down_revision = "cd12ef34ab56"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def _has_column(table: str, column: str) -> bool:
    if not _has_table(table):
        return False
    return column in {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade():
    if _has_table("inscription_annuelle"):
        if not _has_column("inscription_annuelle", "type_inscription"):
            op.add_column(
                "inscription_annuelle",
                sa.Column("type_inscription", sa.String(length=20), nullable=False, server_default="individuelle"),
            )
            op.create_index(
                "ix_inscription_annuelle_type_inscription", "inscription_annuelle", ["type_inscription"]
            )
        if not _has_column("inscription_annuelle", "foyer_id"):
            # Référence souple : sur SQLite, ajouter une colonne avec clé
            # étrangère à une table existante n'est pas rejouable en ALTER.
            # L'intégrité est portée par l'ORM (ondelete SET NULL côté
            # PostgreSQL, via la contrainte nommée ci-dessous).
            with op.batch_alter_table("inscription_annuelle") as batch:
                batch.add_column(sa.Column("foyer_id", sa.Integer(), nullable=True))
                batch.create_foreign_key(
                    "fk_inscription_annuelle_foyer", "foyer", ["foyer_id"], ["id"], ondelete="SET NULL"
                )
            op.create_index("ix_inscription_annuelle_foyer_id", "inscription_annuelle", ["foyer_id"])

        if not _has_column("inscription_annuelle", "reglement_statut"):
            op.add_column(
                "inscription_annuelle",
                sa.Column("reglement_statut", sa.String(length=20), nullable=False, server_default="rien"),
            )
            op.create_index(
                "ix_inscription_annuelle_reglement_statut", "inscription_annuelle", ["reglement_statut"]
            )
        if not _has_column("inscription_annuelle", "reglement_du"):
            op.add_column("inscription_annuelle", sa.Column("reglement_du", sa.Float(), nullable=True))

    if not _has_table("inscription_annuelle_membre"):
        op.create_table(
            "inscription_annuelle_membre",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("inscription_id", sa.Integer(), sa.ForeignKey("inscription_annuelle.id", ondelete="CASCADE"), nullable=False),
            sa.Column("nom", sa.String(length=120), nullable=True),
            sa.Column("prenom", sa.String(length=120), nullable=False),
            sa.Column("date_naissance", sa.Date(), nullable=True),
            sa.Column("lien_filiation", sa.String(length=80), nullable=True),
            sa.Column("participant_id", sa.Integer(), sa.ForeignKey("participant.id", ondelete="SET NULL"), nullable=True),
            sa.Column("ordre", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
        op.create_index("ix_inscription_annuelle_membre_inscription_id", "inscription_annuelle_membre", ["inscription_id"])
        op.create_index("ix_inscription_annuelle_membre_participant_id", "inscription_annuelle_membre", ["participant_id"])


def downgrade():
    if _has_table("inscription_annuelle_membre"):
        op.drop_table("inscription_annuelle_membre")
    for colonne in ("reglement_du", "reglement_statut"):
        if _has_column("inscription_annuelle", colonne):
            op.drop_column("inscription_annuelle", colonne)
    if _has_column("inscription_annuelle", "foyer_id"):
        with op.batch_alter_table("inscription_annuelle") as batch:
            batch.drop_constraint("fk_inscription_annuelle_foyer", type_="foreignkey")
            batch.drop_column("foyer_id")
    if _has_column("inscription_annuelle", "type_inscription"):
        op.drop_column("inscription_annuelle", "type_inscription")
