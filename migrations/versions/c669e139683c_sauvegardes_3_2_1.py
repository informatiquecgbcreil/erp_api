"""Sauvegardes 3-2-1 : copie cloud chiffrée et réglages dans l'administration.

Cinq colonnes sur instance_settings : le dossier cloud, la clé publique et la
clé privée protégée par la phrase secrète (la phrase elle-même n'est jamais
enregistrée), le nombre de sauvegardes gardées et le seuil d'alerte. Vides,
elles laissent les installations existantes telles quelles (configuration
BACKUP_RETENTION_LOTS / BACKUP_ALERT_DAYS, destinations déjà réglées).
Rejouable.
"""
import sqlalchemy as sa
from alembic import op

revision = "c669e139683c"
down_revision = "2362053b0763"
branch_labels = None
depends_on = None

COLONNES = (
    ("sauvegarde_cloud", sa.Text()),
    ("sauvegarde_cle_publique", sa.Text()),
    ("sauvegarde_cle_privee", sa.Text()),
    ("sauvegarde_conservation_lots", sa.Integer()),
    ("sauvegarde_alerte_jours", sa.Integer()),
)


def upgrade():
    existantes = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("instance_settings")}
    for nom, type_ in COLONNES:
        if nom not in existantes:
            op.add_column("instance_settings", sa.Column(nom, type_, nullable=True))


def downgrade():
    for nom, _type in reversed(COLONNES):
        op.drop_column("instance_settings", nom)
