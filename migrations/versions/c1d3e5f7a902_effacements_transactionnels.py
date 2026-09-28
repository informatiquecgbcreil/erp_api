"""Registre des effacements RGPD tenu en base, dans la transaction de l'effacement.

Consolidation après la PR #59 (défaut B) : l'ancien registre hors base était
écrit avant la validation SQL, un effacement annulé pouvait donc être
réappliqué après une restauration. Désormais chaque effacement validé laisse
une ligne ``effacement_rgpd`` (écrite dans la même transaction), recopiée
ensuite hors base. L'ancien fichier ``runtime/effacements.json`` n'est pas
modifié ici : ses entrées sont classées au démarrage de l'application
(``app.services.registre_effacements.importer_ancien_registre``), jamais
réappliquées à l'aveugle.
"""
import sqlalchemy as sa
from alembic import op

revision = "c1d3e5f7a902"
down_revision = "b8d0f2a4c593"
branch_labels = None
depends_on = None


def upgrade():
    if "effacement_rgpd" in set(sa.inspect(op.get_bind()).get_table_names()):
        return
    op.create_table(
        "effacement_rgpd",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("cle", sa.String(80), nullable=False, unique=True),
        sa.Column("nature", sa.String(20), nullable=False),
        sa.Column("participant_id", sa.Integer(), nullable=False),
        sa.Column("participant_cree_le", sa.DateTime(), nullable=True),
        sa.Column("precision", sa.String(12), nullable=False, server_default="microseconde"),
        sa.Column("etat", sa.String(12), nullable=False, server_default="confirme"),
        sa.Column("origine", sa.String(20), nullable=False, server_default="application"),
        sa.Column("cree_le", sa.DateTime(), nullable=False),
        sa.Column("exporte_le", sa.DateTime(), nullable=True),
        sa.Column("decide_le", sa.DateTime(), nullable=True),
        sa.Column("decide_par_user_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
        sa.Column("note", sa.String(255), nullable=True),
    )
    op.create_index("ix_effacement_rgpd_participant_id", "effacement_rgpd", ["participant_id"])
    op.create_index("ix_effacement_rgpd_etat", "effacement_rgpd", ["etat"])
    op.create_index("ix_effacement_rgpd_exporte_le", "effacement_rgpd", ["exporte_le"])


def downgrade():
    op.drop_table("effacement_rgpd")
