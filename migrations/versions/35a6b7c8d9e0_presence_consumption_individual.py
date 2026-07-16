"""presence consumption individual

Revision ID: 35a6b7c8d9e0
Revises: 34f5a6b7c8d9
Create Date: 2026-05-13

Ajoute les consommations individuelles figées par présence.
"""
from alembic import op
import sqlalchemy as sa

revision = "35a6b7c8d9e0"
down_revision = "34f5a6b7c8d9"
branch_labels = None
depends_on = None


def _bind():
    return op.get_bind()


def _dialect():
    return (_bind().dialect.name or "").lower()


def _insp():
    return sa.inspect(_bind())


def _has_table(name: str) -> bool:
    return _insp().has_table(name)


def _has_index(table: str, name: str) -> bool:
    if not _has_table(table):
        return False
    return any(idx.get("name") == name for idx in _insp().get_indexes(table))


def upgrade():
    if not _has_table("presence_materiel_consommation"):
        op.create_table(
            "presence_materiel_consommation",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("presence_id", sa.Integer(), sa.ForeignKey("presence_activite.id", ondelete="CASCADE"), nullable=False),
            sa.Column("session_id", sa.Integer(), sa.ForeignKey("session_activite.id", ondelete="CASCADE"), nullable=False),
            sa.Column("participant_id", sa.Integer(), sa.ForeignKey("participant.id", ondelete="CASCADE"), nullable=False),
            sa.Column("materiel_id", sa.Integer(), sa.ForeignKey("materiel_type.id", ondelete="SET NULL"), nullable=True),
            sa.Column("quantite", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("materiel_nom_snapshot", sa.String(length=120), nullable=True),
            sa.Column("watts_snapshot", sa.Float(), nullable=False, server_default="0"),
            sa.Column("duree_minutes_snapshot", sa.Integer(), nullable=False, server_default="60"),
            sa.Column("kwh_snapshot", sa.Float(), nullable=False, server_default="0"),
            sa.Column("co2_kg_snapshot", sa.Float(), nullable=False, server_default="0"),
            sa.Column("co2_kg_par_kwh_snapshot", sa.Float(), nullable=False, server_default="0.06"),
            sa.Column("mode_calcul", sa.String(length=40), nullable=False, server_default="manuel"),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("presence_id", "materiel_id", "mode_calcul", name="uq_presence_materiel_conso_ligne"),
        )

    for name, cols in (
        ("ix_presence_materiel_conso_presence", ["presence_id"]),
        ("ix_presence_materiel_conso_session", ["session_id"]),
        ("ix_presence_materiel_conso_participant", ["participant_id"]),
        ("ix_presence_materiel_conso_materiel", ["materiel_id"]),
    ):
        if _has_table("presence_materiel_consommation") and not _has_index("presence_materiel_consommation", name):
            op.create_index(name, "presence_materiel_consommation", cols)


def downgrade():
    if _has_table("presence_materiel_consommation"):
        op.drop_table("presence_materiel_consommation")
