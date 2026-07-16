"""depense affectations multi financeurs

Revision ID: 33e4f5a6b7c8
Revises: 32d3e4f5a6b7
Create Date: 2026-04-27

Ajoute les imputations multi-financeurs des depenses.
Une depense reste unique, mais peut etre repartie entre plusieurs sources : CAF, QPV, FONJEP, fonds propres, etc.
"""
from alembic import op
import sqlalchemy as sa

revision = "33e4f5a6b7c8"
down_revision = "32d3e4f5a6b7"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def upgrade():
    bind = op.get_bind()

    if not _has_table("depense_affectation"):
        op.create_table(
            "depense_affectation",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("depense_id", sa.Integer(), sa.ForeignKey("depense.id", ondelete="CASCADE"), nullable=False),
            sa.Column("source_type", sa.String(length=30), nullable=False, server_default="subvention"),
            sa.Column("subvention_id", sa.Integer(), sa.ForeignKey("subvention.id", ondelete="SET NULL"), nullable=True),
            sa.Column("ligne_budget_id", sa.Integer(), sa.ForeignKey("ligne_budget.id", ondelete="SET NULL"), nullable=True),
            sa.Column("libelle_source", sa.String(length=200), nullable=True),
            sa.Column("montant", sa.Float(), nullable=False, server_default="0"),
            sa.Column("commentaire", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_depense_affectation_depense", "depense_affectation", ["depense_id"])
        op.create_index("ix_depense_affectation_subvention", "depense_affectation", ["subvention_id"])
        op.create_index("ix_depense_affectation_ligne", "depense_affectation", ["ligne_budget_id"])

    # Migration douce : les depenses existantes liees a une ligne budget sont considerees comme imputees a 100% sur cette ligne.
    meta = sa.MetaData()
    depense = sa.Table("depense", meta, autoload_with=bind)
    ligne = sa.Table("ligne_budget", meta, autoload_with=bind)
    aff = sa.Table("depense_affectation", meta, autoload_with=bind)

    existing_depense_ids = {row[0] for row in bind.execute(sa.select(aff.c.depense_id)).all()}
    rows = bind.execute(
        sa.select(depense.c.id, depense.c.ligne_budget_id, depense.c.montant, ligne.c.subvention_id)
        .select_from(depense.join(ligne, depense.c.ligne_budget_id == ligne.c.id))
        .where(depense.c.ligne_budget_id.is_not(None))
    ).all()

    to_insert = []
    for dep_id, ligne_id, montant, subvention_id in rows:
        if dep_id in existing_depense_ids:
            continue
        to_insert.append({
            "depense_id": dep_id,
            "source_type": "subvention",
            "subvention_id": subvention_id,
            "ligne_budget_id": ligne_id,
            "libelle_source": None,
            "montant": float(montant or 0),
        })
    if to_insert:
        bind.execute(aff.insert(), to_insert)


def downgrade():
    if _has_table("depense_affectation"):
        op.drop_table("depense_affectation")
