"""Suivi des personnes déjà envoyées au portail CSAT.

CSAT (Centres Sociaux Acteurs des Transitions) n'a ni API ni détection des
doublons : réimporter une personne déjà présente l'y crée une seconde fois.
Comme pour les séances (``session_activite.exported_csat_at``), l'application
retient la date du dernier envoi de chaque personne ; l'export « Participants »
ne propose par défaut que les personnes jamais envoyées.

Effets : ajoute ``participant.exported_csat_at`` (vide pour tout le monde :
les personnes déjà saisies dans CSAT se marquent en important l'export CSV de
CSAT, page « Exporter les participants pour CSAT »). Aucune autre donnée
modifiée. Se défait (le suivi est perdu).
"""
import sqlalchemy as sa
from alembic import op

revision = "f1a3c5e7b902"
down_revision = "e5f7a9b1c235"
branch_labels = None
depends_on = None


def upgrade():
    colonnes = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("participant")}
    if "exported_csat_at" not in colonnes:
        with op.batch_alter_table("participant") as batch:
            batch.add_column(sa.Column("exported_csat_at", sa.DateTime(), nullable=True))
            batch.create_index("ix_participant_exported_csat_at", ["exported_csat_at"])


def downgrade():
    colonnes = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("participant")}
    if "exported_csat_at" in colonnes:
        with op.batch_alter_table("participant") as batch:
            batch.drop_index("ix_participant_exported_csat_at")
            batch.drop_column("exported_csat_at")
