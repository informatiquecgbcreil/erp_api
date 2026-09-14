"""Référentiel du genre : une valeur enregistrée, un libellé qui suit l'âge.

Le même mot ne donnait pas le même comptage selon l'écran. « Fille » était
« Non renseigné » sur le tableau de bord, « femme » dans les indicateurs,
« Femmes » dans les stats d'impact et une ligne à part dans l'export SENACS.
« Garçon » n'était compté comme garçon nulle part.

Cette migration ramène les valeurs déjà saisies sur les codes du référentiel
(F / H / A / N). Le libellé affiché — fille ou femme, garçon ou homme — est
désormais calculé à la lecture, d'après l'âge à la date qui compte.

Elle ne casse rien si elle passe à côté d'une valeur exotique : toutes les
lectures passent par ``app.services.genre.normaliser``, qui reconnaît aussi
les anciennes écritures.

Revision ID: fa27bc38de91
Revises: ef16ab78cd90
Create Date: 2026-09-14
"""
import sqlalchemy as sa
from alembic import op

revision = "fa27bc38de91"
down_revision = "ef16ab78cd90"
branch_labels = None
depends_on = None

#: (table, colonne) portant un genre de personne. ``donateur_civilite``
#: n'en est PAS : c'est une civilité de courrier (M./Mme) sur un reçu
#: fiscal, pas une donnée statistique.
CIBLES = [
    ("participant", "genre"),
    ("inscription_annuelle", "genre"),
]


def _tables_presentes(bind):
    return set(sa.inspect(bind).get_table_names())


def upgrade():
    from app.services.genre import normaliser_colonne

    bind = op.get_bind()
    for table, colonne in CIBLES:
        normaliser_colonne(bind, table, colonne)


def downgrade():
    """Repasse les codes en libellés adultes.

    L'information « fille » ou « garçon » n'existait pas comme donnée avant
    cette migration — elle était mélangée au genre. On redescend donc sur
    Femme / Homme, ce que faisaient déjà la grande majorité des fiches.
    """
    bind = op.get_bind()
    tables = _tables_presentes(bind)
    libelles = {"F": "Femme", "H": "Homme", "A": "Autre", "N": "Préférez ne pas répondre"}

    for table, colonne in CIBLES:
        if table not in tables:
            continue
        colonnes = {c["name"] for c in sa.inspect(bind).get_columns(table)}
        if colonne not in colonnes:
            continue
        for code, libelle in libelles.items():
            bind.execute(
                sa.text(f"UPDATE {table} SET {colonne} = :libelle WHERE {colonne} = :code"),
                {"libelle": libelle, "code": code},
            )
