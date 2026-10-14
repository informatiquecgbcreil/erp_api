"""Mode entraînement : fausses données pour se former, puis passage au réel.

Trois colonnes sur instance_settings : actif, depuis quand, et la « photo »
(plus grand numéro de chaque table à l'activation). Aucune installation
n'est en mode entraînement : rien à reprendre. Rejouable.
"""
import sqlalchemy as sa
from alembic import op

revision = "2362053b0763"
down_revision = "75a9bfe05826"
branch_labels = None
depends_on = None

COLONNES = (
    ("entrainement_actif", sa.Boolean()),
    ("entrainement_depuis", sa.DateTime()),
    ("entrainement_photo", sa.Text()),
)


def upgrade():
    existantes = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("instance_settings")}
    for nom, type_ in COLONNES:
        if nom not in existantes:
            op.add_column("instance_settings", sa.Column(nom, type_, nullable=True))


def downgrade():
    for nom, _type in reversed(COLONNES):
        op.drop_column("instance_settings", nom)
