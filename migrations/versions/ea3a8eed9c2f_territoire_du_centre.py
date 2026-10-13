"""Territoire du centre : ville siège, QPV suivis, quartiers distingués.

Les bilans SENACS, les indicateurs, les tableaux de bord, la carte et les
feuilles d'émargement portaient en dur le territoire du centre de Creil.
Ces six colonnes le rendent réglable (Administration > Identité).

Une base qui compte déjà des habitants ou des quartiers à Creil reçoit le
territoire qu'elle avait implicitement (Creil, QPV Hauts de Creil, quartier
Rouher distingué) : ses bilans restent identiques après la mise à jour. Une
base neuve n'en reçoit aucun. Rejouable.
"""
import sqlalchemy as sa
from alembic import op

revision = "ea3a8eed9c2f"
down_revision = "e6c8a0b2d4f5"
branch_labels = None
depends_on = None

COLONNES = (
    ("territoire_ville", sa.String(length=120)),
    ("territoire_code_postal", sa.String(length=10)),
    ("territoire_qpv_suivis", sa.Text()),
    ("territoire_quartiers_distingues", sa.Text()),
    ("territoire_latitude", sa.Float()),
    ("territoire_longitude", sa.Float()),
)


def upgrade():
    bind = op.get_bind()
    existantes = {c["name"] for c in sa.inspect(bind).get_columns("instance_settings")}
    for nom, type_ in COLONNES:
        if nom not in existantes:
            op.add_column("instance_settings", sa.Column(nom, type_, nullable=True))

    from app.services.territoire import reprendre_territoire_historique

    reprendre_territoire_historique(bind)


def downgrade():
    for nom, _type in reversed(COLONNES):
        op.drop_column("instance_settings", nom)
