"""Origine des présences explicite et indépendante de la signature.

La migration a4c7e2f9d153 reconnaissait les présences de kiosque au nom de
leur fichier de signature et laissait les autres à NULL, lu comme « posée par
le personnel ». Or les signatures sont purgées selon la politique de
conservation : une présence de kiosque dont la signature a disparu serait
devenue une validation du personnel, ouvrant la modification de la fiche.

Désormais :
- signature du personnel encore présente (sig_s…, distance_s…) : personnel ;
- présence de kiosque déjà reconnue : kiosque (inchangé) ;
- tout le reste des lignes anciennes : « indeterminee », traitée comme une
  présence de kiosque (lecture seule pour le secteur) tant que l'équipe ne
  l'a pas validée depuis la feuille d'émargement (validation groupée
  possible). Aucune signature supprimée n'est restaurée.
"""
from alembic import op
import sqlalchemy as sa

revision = "d8a1c5e3f786"
down_revision = "c7e9f4b2a375"
branch_labels = None
depends_on = None


def upgrade():
    colonnes = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("presence_activite")}
    with op.batch_alter_table("presence_activite") as batch:
        if "validee_par_user_id" not in colonnes:
            batch.add_column(sa.Column("validee_par_user_id", sa.Integer(), nullable=True))
            batch.create_foreign_key("fk_presence_validee_par", "user", ["validee_par_user_id"], ["id"],
                                     ondelete="SET NULL")
        if "validee_le" not in colonnes:
            batch.add_column(sa.Column("validee_le", sa.DateTime(), nullable=True))
    op.execute(
        "UPDATE presence_activite SET origine = 'personnel' WHERE origine IS NULL AND ("
        "signature_path LIKE '%sig\\_s%' ESCAPE '\\' OR signature_path LIKE '%distance\\_s%' ESCAPE '\\')"
    )
    op.execute("UPDATE presence_activite SET origine = 'indeterminee' WHERE origine IS NULL")
    with op.batch_alter_table("presence_activite") as batch:
        batch.alter_column("origine", existing_type=sa.String(20), server_default="personnel")


def downgrade():
    with op.batch_alter_table("presence_activite") as batch:
        batch.alter_column("origine", existing_type=sa.String(20), server_default=None)
        batch.drop_constraint("fk_presence_validee_par", type_="foreignkey")
        batch.drop_column("validee_le")
        batch.drop_column("validee_par_user_id")
