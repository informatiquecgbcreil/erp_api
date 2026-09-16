"""Répartition de la participation : les arrêtés figés.

La participation finance les secteurs. Jusqu'ici, elle était attribuée en
entier au « secteur qui fait venir » — ce qui devient faux dès que la
personne circule entre les secteurs. Le calcul au prorata des venues
répare ça, mais il est VIVANT : tant que l'année court, la part d'un
secteur bouge à chaque séance pointée.

Vivant, c'est juste. Inexploitable tel quel pour engager de l'argent :
un référent à qui on annonce 420 € en mars ne peut pas en voir 380 € en
juin parce que quelqu'un a fréquenté un autre secteur entre-temps.

D'où ces deux tables. À une date choisie — fin de trimestre, 31 août — on
rejoue l'année telle qu'elle était connue CE JOUR-LÀ, et on écrit le
résultat. Le détail est conservé personne par personne et secteur par
secteur : c'est ce qui permet de répondre des mois plus tard à
« pourquoi le Numérique a-t-il 420 € ? ».

Le nom du participant est recopié dans la ligne. Une fiche peut être
renommée, fusionnée ou supprimée ensuite ; un arrêté qui deviendrait
illisible pour autant ne serait pas une pièce justificative.

Revision ID: c7e1a9b4d305
Revises: ab38cd49ef02
Create Date: 2026-09-16
"""
import sqlalchemy as sa
from alembic import op

revision = "c7e1a9b4d305"
down_revision = "ab38cd49ef02"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "repartition_arretee",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("annee_scolaire", sa.Integer(), nullable=False),
        sa.Column("date_arrete", sa.Date(), nullable=False),
        sa.Column("libelle", sa.String(length=160), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        # Les totaux sont recopiés de la somme des lignes pour lister les
        # arrêtés sans relire chaque détail. Le détail reste la source.
        sa.Column("total_du", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column("total_regle", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column("cree_par_user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False,
                  server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["cree_par_user_id"], ["user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_repartition_arretee_annee_scolaire", "repartition_arretee",
                    ["annee_scolaire"])
    op.create_index("ix_repartition_arretee_date_arrete", "repartition_arretee",
                    ["date_arrete"])
    # Deux pièces différentes portant la même date et le même intitulé, c'est
    # la garantie qu'un jour quelqu'un cite la mauvaise. La base le refuse.
    op.create_index("uq_repartition_arretee_annee_date", "repartition_arretee",
                    ["annee_scolaire", "date_arrete"], unique=True)

    op.create_table(
        "repartition_arretee_ligne",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("arrete_id", sa.Integer(), nullable=False),
        sa.Column("secteur", sa.String(length=120), nullable=False),
        sa.Column("participant_id", sa.Integer(), nullable=True),
        sa.Column("participant_nom", sa.String(length=240), nullable=True),
        sa.Column("venues", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("montant_du", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column("montant_regle", sa.Float(), nullable=False, server_default=sa.text("0")),
        # server_default en 'false' et non '0' : PostgreSQL refuse un entier
        # dans une colonne booléenne, et deux migrations s'y sont déjà
        # cassé les dents sur ce projet.
        sa.Column("repli", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.ForeignKeyConstraint(["arrete_id"], ["repartition_arretee.id"],
                                ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["participant_id"], ["participant.id"],
                                ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_repartition_arretee_ligne_arrete_id",
                    "repartition_arretee_ligne", ["arrete_id"])
    op.create_index("ix_repartition_arretee_ligne_secteur",
                    "repartition_arretee_ligne", ["secteur"])
    op.create_index("ix_repartition_arretee_ligne_participant_id",
                    "repartition_arretee_ligne", ["participant_id"])


def downgrade():
    op.drop_index("ix_repartition_arretee_ligne_participant_id",
                  table_name="repartition_arretee_ligne")
    op.drop_index("ix_repartition_arretee_ligne_secteur",
                  table_name="repartition_arretee_ligne")
    op.drop_index("ix_repartition_arretee_ligne_arrete_id",
                  table_name="repartition_arretee_ligne")
    op.drop_table("repartition_arretee_ligne")
    op.drop_index("uq_repartition_arretee_annee_date", table_name="repartition_arretee")
    op.drop_index("ix_repartition_arretee_date_arrete", table_name="repartition_arretee")
    op.drop_index("ix_repartition_arretee_annee_scolaire", table_name="repartition_arretee")
    op.drop_table("repartition_arretee")
