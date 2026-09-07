"""Module Inscriptions annuelles : bulletins de rentrée, ateliers souhaités,
disponibilités bénévolat, statut d'attente de 1re participation.

Revision ID: cd12ef34ab56
Revises: bb99cc00dd11
Create Date: 2026-09-07

- ``inscription_annuelle``         : le bulletin d'inscription (année scolaire) ;
- ``inscription_annuelle_atelier`` : ateliers souhaités (choix multiple) ;
- ``inscription_annuelle_dispo``   : créneaux bénévolat (jour × demi-journée) ;
- ``participant.statut_inscription``: statut spécial « en attente de 1re
  participation » posé sur la fiche créée depuis un bulletin.

Migration DÉFENSIVE et purement ADDITIVE : aucune table ni colonne existante
n'est supprimée ou retypée.
"""
from alembic import op
import sqlalchemy as sa


revision = "cd12ef34ab56"
down_revision = "bb99cc00dd11"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def _has_column(table: str, column: str) -> bool:
    if not _has_table(table):
        return False
    return column in {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade():
    if not _has_table("inscription_annuelle"):
        op.create_table(
            "inscription_annuelle",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("annee_scolaire", sa.Integer(), nullable=False),
            sa.Column("date_inscription", sa.Date(), nullable=False),
            sa.Column("nom", sa.String(length=120), nullable=False),
            sa.Column("prenom", sa.String(length=120), nullable=False),
            sa.Column("adresse", sa.String(length=255), nullable=True),
            sa.Column("code_postal", sa.String(length=10), nullable=True),
            sa.Column("ville", sa.String(length=120), nullable=True),
            sa.Column("email", sa.String(length=180), nullable=True),
            sa.Column("telephone", sa.String(length=60), nullable=True),
            sa.Column("date_naissance", sa.Date(), nullable=True),
            sa.Column("genre", sa.String(length=20), nullable=True),
            sa.Column("secteur_orienteur", sa.String(length=80), nullable=True),
            sa.Column("ateliers_libre", sa.Text(), nullable=True),
            sa.Column("commentaire", sa.Text(), nullable=True),
            sa.Column("benevolat_souhaite", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("benevolat_mission", sa.Text(), nullable=True),
            sa.Column("benevolat_dispo_inconnue", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("statut", sa.String(length=20), nullable=False, server_default="saisie"),
            sa.Column("participant_id", sa.Integer(), sa.ForeignKey("participant.id", ondelete="SET NULL"), nullable=True),
            sa.Column("premiere_participation_le", sa.Date(), nullable=True),
            sa.Column("reglement_confirme", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("reglement_montant", sa.Float(), nullable=True),
            sa.Column("reglement_mode", sa.String(length=20), nullable=True),
            sa.Column("reglement_date", sa.Date(), nullable=True),
            sa.Column("reglement_commentaire", sa.String(length=255), nullable=True),
            sa.Column("cotisation_id", sa.Integer(), sa.ForeignKey("cotisation.id", ondelete="SET NULL"), nullable=True),
            sa.Column("created_secteur", sa.String(length=80), nullable=True),
            sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
        for col in ("annee_scolaire", "secteur_orienteur", "benevolat_souhaite",
                    "statut", "participant_id", "reglement_confirme", "created_secteur"):
            op.create_index(f"ix_inscription_annuelle_{col}", "inscription_annuelle", [col])
        op.create_index(
            "ix_inscription_annuelle_recherche", "inscription_annuelle",
            ["annee_scolaire", "nom", "prenom"],
        )
        op.create_index(
            "ix_inscription_annuelle_suivi", "inscription_annuelle",
            ["annee_scolaire", "statut", "reglement_confirme"],
        )

    if not _has_table("inscription_annuelle_atelier"):
        op.create_table(
            "inscription_annuelle_atelier",
            sa.Column("inscription_id", sa.Integer(), sa.ForeignKey("inscription_annuelle.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("atelier_id", sa.Integer(), sa.ForeignKey("atelier_activite.id", ondelete="CASCADE"), primary_key=True),
        )

    if not _has_table("inscription_annuelle_dispo"):
        op.create_table(
            "inscription_annuelle_dispo",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("inscription_id", sa.Integer(), sa.ForeignKey("inscription_annuelle.id", ondelete="CASCADE"), nullable=False),
            sa.Column("jour", sa.String(length=12), nullable=False),
            sa.Column("demi_journee", sa.String(length=12), nullable=False),
            sa.UniqueConstraint("inscription_id", "jour", "demi_journee", name="uq_inscription_annuelle_dispo"),
        )
        op.create_index("ix_inscription_annuelle_dispo_inscription_id", "inscription_annuelle_dispo", ["inscription_id"])

    # Statut spécial sur la fiche participant. Les fiches existantes sont
    # « actif » : elles n'ont pas été créées par un bulletin de rentrée.
    if _has_table("participant") and not _has_column("participant", "statut_inscription"):
        op.add_column(
            "participant",
            sa.Column("statut_inscription", sa.String(length=40), nullable=False, server_default="actif"),
        )
        op.create_index("ix_participant_statut_inscription", "participant", ["statut_inscription"])


def downgrade():
    if _has_column("participant", "statut_inscription"):
        try:
            op.drop_index("ix_participant_statut_inscription", table_name="participant")
        except Exception:
            pass
        op.drop_column("participant", "statut_inscription")
    for table in ("inscription_annuelle_dispo", "inscription_annuelle_atelier", "inscription_annuelle"):
        if _has_table(table):
            op.drop_table(table)
