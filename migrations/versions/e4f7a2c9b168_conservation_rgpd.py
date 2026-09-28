"""Conservation RGPD : journal relié aux fiches, file d'effacement, durées.

- journal_audit.participant_id : la fiche concernée, sans clé étrangère.
  Rempli pour les lignes existantes quand la cible désigne EXACTEMENT une
  fiche (« participant #12 », « participant#12 », ou « … (#12) » sur une
  action participant.*). Les lignes qui ne citent qu'un nom ne sont pas
  rattachées par approximation : la durée de conservation du journal les
  fera disparaître.
- pending_file_deletion : tentatives, bloque_le, motif (audit 3.6).
- instance_settings : durées de conservation réglables (audit 3.8).
"""
import re

import sqlalchemy as sa
from alembic import op

revision = "e4f7a2c9b168"
down_revision = "d8a1c5e3f786"
branch_labels = None
depends_on = None

_EXACT = re.compile(r"participant\s?#(\d+)\b", re.IGNORECASE)
_PARENTHESE = re.compile(r"\(#(\d+)\)")


def participant_de_la_cible(action, cible):
    """Identifiant de fiche désigné sans ambiguïté par la cible, sinon None."""
    if not cible:
        return None
    trouve = _EXACT.search(cible)
    if trouve:
        return int(trouve.group(1))
    if (action or "").startswith("participant."):
        trouve = _PARENTHESE.search(cible)
        if trouve:
            return int(trouve.group(1))
    return None


def _colonnes(table):
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade():
    if "participant_id" not in _colonnes("journal_audit"):
        with op.batch_alter_table("journal_audit") as batch:
            batch.add_column(sa.Column("participant_id", sa.Integer(), nullable=True))
            batch.create_index("ix_journal_audit_participant_id", ["participant_id"])
    bind = op.get_bind()
    lignes = bind.execute(sa.text(
        "SELECT id, action, cible FROM journal_audit WHERE participant_id IS NULL AND cible LIKE '%#%'"
    )).fetchall()
    for identifiant, action, cible in lignes:
        pid = participant_de_la_cible(action, cible)
        if pid is not None:
            bind.execute(sa.text("UPDATE journal_audit SET participant_id = :p WHERE id = :i"),
                         {"p": pid, "i": identifiant})

    colonnes = _colonnes("pending_file_deletion")
    with op.batch_alter_table("pending_file_deletion") as batch:
        if "tentatives" not in colonnes:
            batch.add_column(sa.Column("tentatives", sa.Integer(), nullable=False, server_default="0"))
        if "bloque_le" not in colonnes:
            batch.add_column(sa.Column("bloque_le", sa.DateTime(), nullable=True))
            batch.create_index("ix_pending_file_deletion_bloque_le", ["bloque_le"])
        if "motif" not in colonnes:
            batch.add_column(sa.Column("motif", sa.String(60), nullable=True))

    colonnes = _colonnes("instance_settings")
    with op.batch_alter_table("instance_settings") as batch:
        for nom in ("conservation_journal_jours", "conservation_bulletins_annees",
                    "conservation_donateurs_annees", "conservation_comptes_annees",
                    "conservation_imports_annees"):
            if nom not in colonnes:
                batch.add_column(sa.Column(nom, sa.Integer(), nullable=True))


def downgrade():
    with op.batch_alter_table("instance_settings") as batch:
        for nom in ("conservation_imports_annees", "conservation_comptes_annees",
                    "conservation_donateurs_annees", "conservation_bulletins_annees",
                    "conservation_journal_jours"):
            batch.drop_column(nom)
    with op.batch_alter_table("pending_file_deletion") as batch:
        batch.drop_index("ix_pending_file_deletion_bloque_le")
        batch.drop_column("motif")
        batch.drop_column("bloque_le")
        batch.drop_column("tentatives")
    with op.batch_alter_table("journal_audit") as batch:
        batch.drop_index("ix_journal_audit_participant_id")
        batch.drop_column("participant_id")
