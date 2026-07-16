"""Séances : marqueur d'export vers le portail CSAT

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b9c0
Create Date: 2026-07-01

Ajoute ``exported_csat_at`` à ``session_activite`` pour que l'export « Sessions »
vers le portail CSAT (sans API) ne propose plus que les séances jamais
transmises, au lieu de tout l'historique à chaque fois.

Comme les séances existantes ont déjà été saisies à la main dans CSAT avant
la mise en place de cet export, cette migration les marque **toutes** comme
déjà transmises (``exported_csat_at`` = leur date de création). Seules les
séances créées après ce déploiement remonteront dans les prochains exports.

Migration DÉFENSIVE : ``add_column`` protégé par inspection des colonnes ;
le backfill ne touche que les lignes encore à NULL (idempotent).
"""
from alembic import op
import sqlalchemy as sa


revision = "e6f7a8b9c0d1"
down_revision = "d5e6f7a8b9c0"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if not insp.has_table("session_activite"):
        return

    existing = {c["name"] for c in insp.get_columns("session_activite")}
    if "exported_csat_at" not in existing:
        op.add_column("session_activite", sa.Column("exported_csat_at", sa.DateTime(), nullable=True))

    session_activite = sa.table(
        "session_activite",
        sa.column("exported_csat_at", sa.DateTime()),
        sa.column("created_at", sa.DateTime()),
    )
    op.execute(
        session_activite.update()
        .where(session_activite.c.exported_csat_at.is_(None))
        .values(exported_csat_at=sa.func.coalesce(session_activite.c.created_at, sa.func.now()))
    )


def downgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if not insp.has_table("session_activite"):
        return
    existing = {c["name"] for c in insp.get_columns("session_activite")}
    if "exported_csat_at" in existing:
        op.drop_column("session_activite", "exported_csat_at")
