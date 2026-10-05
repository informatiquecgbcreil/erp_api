"""Espace salarié : décisions prises par la personne concernée, signalées.

La direction peut décider de sa propre demande de récupération (ou retirer
des heures de son propre solde) : c'est permis, mais marqué en base pour
rester visible et contrôlable (fiche, filtre, accueil, journal).

Rejouable : chaque colonne n'est ajoutée que si elle manque.
"""
import sqlalchemy as sa
from alembic import op

revision = "b3f5a7c9d1e2"
down_revision = "a9d4e6f8b2c1"
branch_labels = None
depends_on = None

COLONNES = (("demande_recuperation", "decision_par_interesse"), ("heure_supplementaire", "par_interesse"))


def upgrade():
    insp = sa.inspect(op.get_bind())
    for table, colonne in COLONNES:
        if insp.has_table(table) and colonne not in {c["name"] for c in insp.get_columns(table)}:
            with op.batch_alter_table(table) as batch:
                batch.add_column(sa.Column(colonne, sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    insp = sa.inspect(op.get_bind())
    for table, colonne in COLONNES:
        if insp.has_table(table) and colonne in {c["name"] for c in insp.get_columns(table)}:
            with op.batch_alter_table(table) as batch:
                batch.drop_column(colonne)
