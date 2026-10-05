"""Espace salarié : secteur figé sur chaque ligne, file d'envoi des e-mails.

- ``secteur`` sur heure_supplementaire, demande_recuperation et
  frais_kilometrique : le secteur du salarié AU MOMENT de la saisie, pour
  que les exports et bilans par secteur ne réimputent pas le passé quand
  quelqu'un change de secteur. Les lignes existantes reprennent le secteur
  actuel de la fiche (seule information disponible).
- ``courriel_rh`` : file d'envoi des e-mails (réessais sans bloquer).
- ``preference_courriel_rh`` : désinscription personnelle des e-mails.

Rejouable : chaque création vérifie d'abord l'existence.
"""
import sqlalchemy as sa
from alembic import op

revision = "c4a6b8d0e2f3"
down_revision = "b3f5a7c9d1e2"
branch_labels = None
depends_on = None

TABLES_SECTEUR = ("heure_supplementaire", "demande_recuperation", "frais_kilometrique")


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    for table in TABLES_SECTEUR:
        if not insp.has_table(table):
            continue
        if "secteur" not in {c["name"] for c in insp.get_columns(table)}:
            with op.batch_alter_table(table) as batch:
                batch.add_column(sa.Column("secteur", sa.String(80), nullable=True))
        if f"ix_{table}_secteur" not in {i["name"] for i in sa.inspect(bind).get_indexes(table)}:
            op.create_index(f"ix_{table}_secteur", table, ["secteur"])
        bind.execute(sa.text(
            f"UPDATE {table} SET secteur = (SELECT s.secteur FROM salarie s WHERE s.id = {table}.salarie_id) "
            "WHERE secteur IS NULL"))

    insp = sa.inspect(bind)
    if not insp.has_table("courriel_rh"):
        op.create_table(
            "courriel_rh",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
            sa.Column("destinataire", sa.String(255), nullable=False),
            sa.Column("evenement", sa.String(60), nullable=False),
            sa.Column("sujet", sa.String(255), nullable=False),
            sa.Column("corps", sa.Text(), nullable=False),
            sa.Column("objet_type", sa.String(40), nullable=True),
            sa.Column("objet_id", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("envoye_le", sa.DateTime(), nullable=True),
            sa.Column("tentatives", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("derniere_erreur", sa.String(500), nullable=True),
        )
    existants = {i["name"] for i in sa.inspect(bind).get_indexes("courriel_rh")}
    for col in ("user_id", "evenement", "envoye_le"):
        if f"ix_courriel_rh_{col}" not in existants:
            op.create_index(f"ix_courriel_rh_{col}", "courriel_rh", [col])

    if not sa.inspect(bind).has_table("preference_courriel_rh"):
        op.create_table(
            "preference_courriel_rh",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
            sa.Column("actif", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
    if "ix_preference_courriel_rh_user_id" not in {i["name"] for i in sa.inspect(bind).get_indexes("preference_courriel_rh")}:
        op.create_index("ix_preference_courriel_rh_user_id", "preference_courriel_rh", ["user_id"], unique=True)


def downgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    for table in ("preference_courriel_rh", "courriel_rh"):
        if insp.has_table(table):
            op.drop_table(table)
    for table in TABLES_SECTEUR:
        if not insp.has_table(table) or "secteur" not in {c["name"] for c in insp.get_columns(table)}:
            continue
        if f"ix_{table}_secteur" in {i["name"] for i in sa.inspect(bind).get_indexes(table)}:
            op.drop_index(f"ix_{table}_secteur", table_name=table)
        with op.batch_alter_table(table) as batch:
            batch.drop_column("secteur")
