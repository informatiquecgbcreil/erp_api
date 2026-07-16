"""orientations acces droit

Revision ID: 38d9e0f1a2b3
Revises: 37c8d9e0f1a2
Create Date: 2026-06-05

Ajoute les competences d'orientation aux partenaires et les fiches
d'orientation acces aux droits.
"""
from alembic import op
import sqlalchemy as sa

revision = "38d9e0f1a2b3"
down_revision = "37c8d9e0f1a2"
branch_labels = None
depends_on = None


def _bind():
    return op.get_bind()


def _insp():
    return sa.inspect(_bind())


def _has_table(name: str) -> bool:
    return _insp().has_table(name)


def _columns(table: str) -> set[str]:
    if not _has_table(table):
        return set()
    return {col.get("name") for col in _insp().get_columns(table)}


def _has_index(table: str, name: str) -> bool:
    if not _has_table(table):
        return False
    return any(idx.get("name") == name for idx in _insp().get_indexes(table))


def _add_col(table: str, name: str, column):
    if name not in _columns(table):
        op.add_column(table, column)


def upgrade():
    if _has_table("partenaire"):
        _add_col("partenaire", "competences_orientation_json", sa.Column("competences_orientation_json", sa.Text(), nullable=True))
        _add_col("partenaire", "territoire_couvert", sa.Column("territoire_couvert", sa.Text(), nullable=True))
        _add_col("partenaire", "modalites_orientation", sa.Column("modalites_orientation", sa.Text(), nullable=True))
        _add_col("partenaire", "niveau_orientation", sa.Column("niveau_orientation", sa.String(length=40), nullable=True))

    if not _has_table("orientation_acces_droit"):
        op.create_table(
            "orientation_acces_droit",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("date_orientation", sa.Date(), nullable=False),
            sa.Column("secteur", sa.String(length=80), nullable=True),
            sa.Column("domaine", sa.String(length=80), nullable=False),
            sa.Column("demande", sa.String(length=255), nullable=False),
            sa.Column("statut", sa.String(length=40), nullable=False, server_default="oriente"),
            sa.Column("urgence", sa.String(length=30), nullable=False, server_default="normale"),
            sa.Column("suite_prevue", sa.Date(), nullable=True),
            sa.Column("note", sa.Text(), nullable=True),
            sa.Column("demandeur_libre", sa.String(length=180), nullable=True),
            sa.Column("participant_id", sa.Integer(), sa.ForeignKey("participant.id", ondelete="SET NULL"), nullable=True),
            sa.Column("partenaire_id", sa.Integer(), sa.ForeignKey("partenaire.id", ondelete="SET NULL"), nullable=True),
            sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )

    for name, cols in (
        ("ix_orientation_acces_droit_date_orientation", ["date_orientation"]),
        ("ix_orientation_acces_droit_secteur", ["secteur"]),
        ("ix_orientation_acces_droit_domaine", ["domaine"]),
        ("ix_orientation_acces_droit_statut", ["statut"]),
        ("ix_orientation_acces_droit_urgence", ["urgence"]),
        ("ix_orientation_acces_droit_suite_prevue", ["suite_prevue"]),
        ("ix_orientation_acces_droit_participant_id", ["participant_id"]),
        ("ix_orientation_acces_droit_partenaire_id", ["partenaire_id"]),
        ("ix_orientation_acces_droit_created_at", ["created_at"]),
    ):
        if _has_table("orientation_acces_droit") and not _has_index("orientation_acces_droit", name):
            op.create_index(name, "orientation_acces_droit", cols)


def downgrade():
    if _has_table("orientation_acces_droit"):
        op.drop_table("orientation_acces_droit")
    if _has_table("partenaire"):
        for col in ("niveau_orientation", "modalites_orientation", "territoire_couvert", "competences_orientation_json"):
            if col in _columns("partenaire"):
                op.drop_column("partenaire", col)
