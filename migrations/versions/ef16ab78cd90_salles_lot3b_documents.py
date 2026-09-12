"""Salles lot 3b : documents, identité pour contrats, ressources mobiles.

Ajoute l'identité du bailleur et les conditions générales sur le site (ce
qu'un contrat doit nommer), le suivi documentaire sur la réservation, et
le matériel mobile partagé entre les salles.

Revision ID: ef16ab78cd90
Revises: de05fa67bc89
Create Date: 2026-09-12
"""
import sqlalchemy as sa
from alembic import op

revision = "ef16ab78cd90"
down_revision = "de05fa67bc89"
branch_labels = None
depends_on = None

COLONNES = [
    # Identité du bailleur et conditions générales, portées par le site.
    ("site", "bailleur_nom", sa.String(length=180)),
    ("site", "bailleur_adresse", sa.String(length=255)),
    ("site", "bailleur_siret", sa.String(length=20)),
    ("site", "bailleur_representant", sa.String(length=160)),
    ("site", "bailleur_qualite", sa.String(length=120)),
    ("site", "conditions_generales", sa.Text()),
    # Une prestation facturée peut s'appuyer sur un matériel en stock
    # limité : le vidéoprojecteur se facture ET n'existe qu'en un exemplaire.
    ("prestation_salle", "ressource_id", sa.Integer()),
    # Suivi documentaire de la mise à disposition.
    ("reservation", "contrat_edite_le", sa.Date()),
    ("reservation", "contrat_signe_le", sa.Date()),
    ("reservation", "facture_numero", sa.String(length=30)),
    ("reservation", "facture_emise_le", sa.Date()),
    ("reservation", "etat_lieux_entree_le", sa.Date()),
    ("reservation", "etat_lieux_sortie_le", sa.Date()),
    ("reservation", "degradations_constatees", sa.Text()),
]


def _insp():
    return sa.inspect(op.get_bind())


def _colonnes(table: str) -> set[str]:
    insp = _insp()
    if not insp.has_table(table):
        return set()
    return {c["name"] for c in insp.get_columns(table)}


def upgrade():
    tables = set(_insp().get_table_names())

    if "ressource_mobile" not in tables:
        op.create_table(
            "ressource_mobile",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("nom", sa.String(length=160), nullable=False),
            sa.Column("description", sa.String(length=255), nullable=True),
            sa.Column("quantite", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("item_id", sa.Integer(), nullable=True),
            sa.Column("ordre", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("actif", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["item_id"], ["inventaire_item.id"], ondelete="SET NULL"),
        )
        op.create_index("ix_ressource_mobile_actif", "ressource_mobile", ["actif"])
        op.create_index("ix_ressource_mobile_item_id", "ressource_mobile", ["item_id"])

    if "occupation_ressource" not in tables:
        op.create_table(
            "occupation_ressource",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("occupation_id", sa.Integer(), nullable=False),
            sa.Column("ressource_id", sa.Integer(), nullable=False),
            sa.Column("quantite", sa.Integer(), nullable=False, server_default="1"),
            sa.ForeignKeyConstraint(["occupation_id"], ["occupation.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["ressource_id"], ["ressource_mobile.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("occupation_id", "ressource_id", name="uq_occupation_ressource"),
        )
        op.create_index("ix_occupation_ressource_occupation_id",
                        "occupation_ressource", ["occupation_id"])
        op.create_index("ix_occupation_ressource_ressource_id",
                        "occupation_ressource", ["ressource_id"])

    for table, colonne, type_sql in COLONNES:
        if table in tables and colonne not in _colonnes(table):
            op.add_column(table, sa.Column(colonne, type_sql, nullable=True))

    # SQLite ne sait pas poser une contrainte après coup ; l'ORM porte déjà
    # la mise à NULL à la suppression du matériel.
    if "prestation_salle" in tables and "ressource_id" in _colonnes("prestation_salle"):
        index_existants = {i["name"] for i in _insp().get_indexes("prestation_salle")}
        if "ix_prestation_salle_ressource_id" not in index_existants:
            op.create_index(
                "ix_prestation_salle_ressource_id", "prestation_salle", ["ressource_id"],
            )
        if op.get_bind().dialect.name != "sqlite":
            op.create_foreign_key(
                "fk_prestation_salle_ressource_id", "prestation_salle", "ressource_mobile",
                ["ressource_id"], ["id"], ondelete="SET NULL",
            )

    # Le numéro de facture doit rester unique : réutiliser un numéro est
    # bien plus grave comptablement que d'en sauter un.
    if "reservation" in tables and "facture_numero" in _colonnes("reservation"):
        index_existants = {i["name"] for i in _insp().get_indexes("reservation")}
        if "ix_reservation_facture_numero" not in index_existants:
            op.create_index(
                "ix_reservation_facture_numero", "reservation", ["facture_numero"], unique=True,
            )


def downgrade():
    tables = set(_insp().get_table_names())
    if "reservation" in tables:
        index_existants = {i["name"] for i in _insp().get_indexes("reservation")}
        if "ix_reservation_facture_numero" in index_existants:
            op.drop_index("ix_reservation_facture_numero", table_name="reservation")
    for table, colonne, _type in COLONNES:
        if table in tables and colonne in _colonnes(table):
            with op.batch_alter_table(table) as batch_op:
                batch_op.drop_column(colonne)
    for table in ("occupation_ressource", "ressource_mobile"):
        if table in tables:
            op.drop_table(table)
