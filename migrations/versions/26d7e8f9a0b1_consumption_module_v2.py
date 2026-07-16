"""consumption module v2

Revision ID: 26d7e8f9a0b1
Revises: 25c6d7e8f9a0_search_perf_reconcile
Create Date: 2026-04-22

Migration rendue compatible SQLite/PostgreSQL et tolérante aux exécutions partielles.
"""
from alembic import op
import sqlalchemy as sa

revision = "26d7e8f9a0b1"
down_revision = "25c6d7e8f9a0"
branch_labels = None
depends_on = None


def _bind():
    return op.get_bind()


def _dialect():
    return (_bind().dialect.name or "").lower()


def _inspector():
    return sa.inspect(_bind())


def _has_table(table_name: str) -> bool:
    return _inspector().has_table(table_name)


def _has_column(table_name: str, column_name: str) -> bool:
    if not _has_table(table_name):
        return False
    return any(col["name"] == column_name for col in _inspector().get_columns(table_name))


def _has_fk(table_name: str, fk_name: str) -> bool:
    if not _has_table(table_name):
        return False
    return any(fk.get("name") == fk_name for fk in _inspector().get_foreign_keys(table_name))


def upgrade():
    # Les IF permettent de réparer une migration SQLite qui a échoué au milieu :
    # certaines tables peuvent déjà avoir été créées avant le crash Alembic.
    if not _has_table("materiel_type"):
        op.create_table(
            "materiel_type",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("nom", sa.String(length=120), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("actif", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("ordre", sa.Integer(), nullable=False, server_default="0"),
        )

    if not _has_table("materiel_consommation_config"):
        op.create_table(
            "materiel_consommation_config",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("label", sa.String(length=120), nullable=False),
            sa.Column("date_debut", sa.Date(), nullable=False),
            sa.Column("date_fin", sa.Date(), nullable=True),
            sa.Column("co2_kg_par_kwh", sa.Float(), nullable=False, server_default="0.06"),
            sa.Column("actif", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("notes", sa.Text(), nullable=True),
        )

    if not _has_table("materiel_consommation_ligne"):
        op.create_table(
            "materiel_consommation_ligne",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("config_id", sa.Integer(), sa.ForeignKey("materiel_consommation_config.id"), nullable=False),
            sa.Column("materiel_id", sa.Integer(), sa.ForeignKey("materiel_type.id"), nullable=False),
            sa.Column("watts", sa.Float(), nullable=False, server_default="0"),
        )

    if not _has_table("session_materiel"):
        op.create_table(
            "session_materiel",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("session_id", sa.Integer(), sa.ForeignKey("session_activite.id"), nullable=False),
            sa.Column("materiel_id", sa.Integer(), sa.ForeignKey("materiel_type.id"), nullable=False),
            sa.Column("quantite", sa.Integer(), nullable=False, server_default="1"),
        )

    if not _has_column("session_activite", "consommation_config_id"):
        op.add_column(
            "session_activite",
            sa.Column("consommation_config_id", sa.Integer(), nullable=True),
        )

    # PostgreSQL sait ajouter la contrainte directement.
    # SQLite ne supporte pas ALTER TABLE ADD CONSTRAINT : on évite donc cette étape en dev SQLite.
    if _dialect() != "sqlite":
        if not _has_fk("session_activite", "fk_session_activite_consommation_config"):
            op.create_foreign_key(
                "fk_session_activite_consommation_config",
                "session_activite",
                "materiel_consommation_config",
                ["consommation_config_id"],
                ["id"],
            )


def downgrade():
    if _dialect() != "sqlite":
        if _has_fk("session_activite", "fk_session_activite_consommation_config"):
            op.drop_constraint(
                "fk_session_activite_consommation_config",
                "session_activite",
                type_="foreignkey",
            )

    if _has_column("session_activite", "consommation_config_id"):
        op.drop_column("session_activite", "consommation_config_id")

    for table_name in (
        "session_materiel",
        "materiel_consommation_ligne",
        "materiel_consommation_config",
        "materiel_type",
    ):
        if _has_table(table_name):
            op.drop_table(table_name)
