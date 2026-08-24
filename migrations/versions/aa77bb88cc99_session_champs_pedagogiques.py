"""Champs pédagogiques portés par la séance.

Revision ID: aa77bb88cc99
Revises: ff66aa77bb88
Create Date: 2026-08-24

Reprend (avec un identifiant de révision propre et chaîné sur la tête
courante) la migration locale « session pedagogical fields » développée
sur une installation : intention de séance, bilan qualitatif animateur,
pertinence, difficulté, participation, à reprendre, commentaire
pédagogique. Idempotente : chaque colonne n'est ajoutée que si elle
n'existe pas déjà — une base où la version locale était déjà passée
n'est pas modifiée.
"""
from alembic import op
import sqlalchemy as sa


revision = "aa77bb88cc99"
down_revision = "ff66aa77bb88"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def _has_column(table: str, column: str) -> bool:
    if not _has_table(table):
        return False
    return column in [c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)]


def upgrade():
    if not _has_table("session_activite"):
        return

    cols = [
        ("intention_seance", sa.Column("intention_seance", sa.String(length=255), nullable=True)),
        ("intention_seance_detail", sa.Column("intention_seance_detail", sa.Text(), nullable=True)),
        ("bilan_qualitatif", sa.Column("bilan_qualitatif", sa.Text(), nullable=True)),
        ("pertinence", sa.Column("pertinence", sa.String(length=30), nullable=True)),
        ("difficulte", sa.Column("difficulte", sa.String(length=30), nullable=True)),
        ("participation_groupe", sa.Column("participation_groupe", sa.String(length=30), nullable=True)),
        ("a_reprendre", sa.Column("a_reprendre", sa.Boolean(), nullable=True)),
        ("commentaire_pedagogique", sa.Column("commentaire_pedagogique", sa.Text(), nullable=True)),
    ]

    a_ajouter = [col for name, col in cols if not _has_column("session_activite", name)]
    if a_ajouter:
        with op.batch_alter_table("session_activite") as batch:
            for col in a_ajouter:
                batch.add_column(col)

    # Récupération douce des intentions créées provisoirement comme
    # Objectif.session_id : on copie la première intention trouvée, sans
    # supprimer les objectifs existants.
    if (
        _has_table("objectif")
        and _has_column("objectif", "session_id")
        and _has_column("session_activite", "intention_seance")
    ):
        bind = op.get_bind()
        bind.execute(sa.text("""
            UPDATE session_activite
            SET intention_seance = (
                SELECT o.titre
                FROM objectif o
                WHERE o.session_id = session_activite.id
                ORDER BY o.id ASC
                LIMIT 1
            )
            WHERE intention_seance IS NULL
              AND EXISTS (
                SELECT 1 FROM objectif o
                WHERE o.session_id = session_activite.id
              )
        """))
        if _has_column("session_activite", "intention_seance_detail"):
            bind.execute(sa.text("""
                UPDATE session_activite
                SET intention_seance_detail = (
                    SELECT o.description
                    FROM objectif o
                    WHERE o.session_id = session_activite.id
                    ORDER BY o.id ASC
                    LIMIT 1
                )
                WHERE intention_seance_detail IS NULL
                  AND EXISTS (
                    SELECT 1 FROM objectif o
                    WHERE o.session_id = session_activite.id
                  )
            """))


def downgrade():
    if not _has_table("session_activite"):
        return

    for name in [
        "commentaire_pedagogique",
        "a_reprendre",
        "participation_groupe",
        "difficulte",
        "pertinence",
        "bilan_qualitatif",
        "intention_seance_detail",
        "intention_seance",
    ]:
        if _has_column("session_activite", name):
            with op.batch_alter_table("session_activite") as batch:
                batch.drop_column(name)
