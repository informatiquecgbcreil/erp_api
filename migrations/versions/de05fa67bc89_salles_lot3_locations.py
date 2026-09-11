"""Salles lot 3 : preneurs, grille tarifaire, réservations.

Ajoute le socle des mises à disposition : qui loue (``categorie_preneur``,
``preneur``), à quel prix (``tarif_salle``, ``prestation_salle``,
``majoration_salle``), et sous quelle forme (``reservation``,
``reservation_prestation``). Chaque date d'une réservation reste une
``occupation``, d'où la simple colonne de rattachement.

Migration défensive : le démarrage exécute aussi ``db.create_all()``.

Revision ID: de05fa67bc89
Revises: cd94ef56ab78
Create Date: 2026-09-11
"""
import sqlalchemy as sa
from alembic import op

revision = "de05fa67bc89"
down_revision = "cd94ef56ab78"
branch_labels = None
depends_on = None

AJOUTS_COLONNES = [
    ("occupation", "reservation_id", sa.Integer(), "reservation"),
    ("site", "tva_applicable", sa.Boolean(), None),
    ("site", "mention_tva", sa.String(length=255), None),
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
    sqlite = op.get_bind().dialect.name == "sqlite"

    if "categorie_preneur" not in tables:
        op.create_table(
            "categorie_preneur",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("code", sa.String(length=40), nullable=False),
            sa.Column("libelle", sa.String(length=120), nullable=False),
            sa.Column("description", sa.String(length=255), nullable=True),
            sa.Column("ordre", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("gratuit_par_defaut", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("actif", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("code", name="uq_categorie_preneur_code"),
        )
        op.create_index("ix_categorie_preneur_code", "categorie_preneur", ["code"], unique=True)
        op.create_index("ix_categorie_preneur_actif", "categorie_preneur", ["actif"])

    if "preneur" not in tables:
        op.create_table(
            "preneur",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("nom", sa.String(length=180), nullable=False),
            sa.Column("categorie_id", sa.Integer(), nullable=True),
            sa.Column("partenaire_id", sa.Integer(), nullable=True),
            sa.Column("contact_nom", sa.String(length=160), nullable=True),
            sa.Column("email", sa.String(length=180), nullable=True),
            sa.Column("telephone", sa.String(length=60), nullable=True),
            sa.Column("adresse", sa.String(length=255), nullable=True),
            sa.Column("code_postal", sa.String(length=10), nullable=True),
            sa.Column("ville", sa.String(length=120), nullable=True),
            sa.Column("siret", sa.String(length=20), nullable=True),
            sa.Column("representant", sa.String(length=160), nullable=True),
            sa.Column("assurance_rc_fin", sa.Date(), nullable=True),
            sa.Column("assurance_reference", sa.String(length=160), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("actif", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_by_user_id", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["categorie_id"], ["categorie_preneur.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["partenaire_id"], ["partenaire.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["created_by_user_id"], ["user.id"], ondelete="SET NULL"),
        )
        for colonne in ("nom", "categorie_id", "partenaire_id", "assurance_rc_fin", "actif"):
            op.create_index(f"ix_preneur_{colonne}", "preneur", [colonne])

    if "tarif_salle" not in tables:
        op.create_table(
            "tarif_salle",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("espace_id", sa.Integer(), nullable=False),
            sa.Column("categorie_id", sa.Integer(), nullable=False),
            sa.Column("unite", sa.String(length=20), nullable=False),
            sa.Column("montant", sa.Float(), nullable=False),
            sa.Column("date_debut", sa.Date(), nullable=False),
            sa.Column("commentaire", sa.String(length=255), nullable=True),
            sa.Column("created_by_user_id", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["espace_id"], ["espace.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["categorie_id"], ["categorie_preneur.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["created_by_user_id"], ["user.id"], ondelete="SET NULL"),
        )
        for colonne in ("espace_id", "categorie_id", "unite", "date_debut"):
            op.create_index(f"ix_tarif_salle_{colonne}", "tarif_salle", [colonne])
        op.create_index(
            "ix_tarif_salle_lookup", "tarif_salle",
            ["espace_id", "categorie_id", "unite", "date_debut"],
        )

    if "prestation_salle" not in tables:
        op.create_table(
            "prestation_salle",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("libelle", sa.String(length=160), nullable=False),
            sa.Column("description", sa.String(length=255), nullable=True),
            sa.Column("montant", sa.Float(), nullable=False, server_default="0"),
            sa.Column("unite", sa.String(length=20), nullable=False, server_default="forfait"),
            sa.Column("ordre", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("actif", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
        op.create_index("ix_prestation_salle_actif", "prestation_salle", ["actif"])

    if "majoration_salle" not in tables:
        op.create_table(
            "majoration_salle",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("libelle", sa.String(length=160), nullable=False),
            sa.Column("condition", sa.String(length=30), nullable=False),
            sa.Column("seuil_minute", sa.Integer(), nullable=True),
            sa.Column("pourcentage", sa.Float(), nullable=True),
            sa.Column("montant_fixe", sa.Float(), nullable=True),
            sa.Column("actif", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
        op.create_index("ix_majoration_salle_condition", "majoration_salle", ["condition"])
        op.create_index("ix_majoration_salle_actif", "majoration_salle", ["actif"])

    if "reservation" not in tables:
        op.create_table(
            "reservation",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("reference", sa.String(length=30), nullable=False),
            sa.Column("preneur_id", sa.Integer(), nullable=False),
            sa.Column("espace_id", sa.Integer(), nullable=False),
            sa.Column("categorie_id", sa.Integer(), nullable=True),
            sa.Column("titre", sa.String(length=200), nullable=False),
            sa.Column("effectif", sa.Integer(), nullable=True),
            sa.Column("statut", sa.String(length=20), nullable=False, server_default="option"),
            sa.Column("option_expire_le", sa.Date(), nullable=True),
            sa.Column("montant_calcule", sa.Float(), nullable=False, server_default="0"),
            sa.Column("detail_json", sa.Text(), nullable=True),
            sa.Column("montant_manuel", sa.Float(), nullable=True),
            sa.Column("motif_montant_manuel", sa.String(length=255), nullable=True),
            sa.Column("gratuite", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("motif_gratuite", sa.String(length=255), nullable=True),
            sa.Column("caution_montant", sa.Float(), nullable=True),
            sa.Column("caution_encaissee", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("caution_restituee_le", sa.Date(), nullable=True),
            sa.Column("acompte_montant", sa.Float(), nullable=True),
            sa.Column("acompte_regle_le", sa.Date(), nullable=True),
            sa.Column("solde_regle_le", sa.Date(), nullable=True),
            sa.Column("referent", sa.String(length=160), nullable=True),
            sa.Column("cles_remises_le", sa.Date(), nullable=True),
            sa.Column("cles_rendues_le", sa.Date(), nullable=True),
            sa.Column("conditions_particulieres", sa.Text(), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("created_by_user_id", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["preneur_id"], ["preneur.id"], ondelete="RESTRICT"),
            sa.ForeignKeyConstraint(["espace_id"], ["espace.id"], ondelete="RESTRICT"),
            sa.ForeignKeyConstraint(["categorie_id"], ["categorie_preneur.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["created_by_user_id"], ["user.id"], ondelete="SET NULL"),
            sa.UniqueConstraint("reference", name="uq_reservation_reference"),
        )
        op.create_index("ix_reservation_reference", "reservation", ["reference"], unique=True)
        for colonne in ("preneur_id", "espace_id", "categorie_id", "statut",
                        "option_expire_le", "created_at"):
            op.create_index(f"ix_reservation_{colonne}", "reservation", [colonne])

    if "reservation_prestation" not in tables:
        op.create_table(
            "reservation_prestation",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("reservation_id", sa.Integer(), nullable=False),
            sa.Column("prestation_id", sa.Integer(), nullable=True),
            sa.Column("libelle", sa.String(length=160), nullable=False),
            sa.Column("montant_unitaire", sa.Float(), nullable=False, server_default="0"),
            sa.Column("unite", sa.String(length=20), nullable=False, server_default="forfait"),
            sa.Column("quantite", sa.Float(), nullable=False, server_default="1"),
            sa.ForeignKeyConstraint(["reservation_id"], ["reservation.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["prestation_id"], ["prestation_salle.id"], ondelete="SET NULL"),
        )
        op.create_index("ix_reservation_prestation_reservation_id",
                        "reservation_prestation", ["reservation_id"])
        op.create_index("ix_reservation_prestation_prestation_id",
                        "reservation_prestation", ["prestation_id"])

    for table, colonne, type_sql, cible in AJOUTS_COLONNES:
        if table not in tables or colonne in _colonnes(table):
            continue
        defaut = sa.false() if isinstance(type_sql, sa.Boolean) else None
        op.add_column(table, sa.Column(colonne, type_sql, nullable=True, server_default=defaut))
        if cible:
            op.create_index(f"ix_{table}_{colonne}", table, [colonne])
            # SQLite ne sait pas ajouter une contrainte après coup ; l'ORM
            # porte déjà la cascade côté application.
            if not sqlite:
                op.create_foreign_key(
                    f"fk_{table}_{colonne}", table, cible, [colonne], ["id"], ondelete="CASCADE",
                )


def downgrade():
    tables = set(_insp().get_table_names())
    for table, colonne, _type, _cible in AJOUTS_COLONNES:
        if table in tables and colonne in _colonnes(table):
            with op.batch_alter_table(table) as batch_op:
                batch_op.drop_column(colonne)
    for table in ("reservation_prestation", "reservation", "majoration_salle",
                  "prestation_salle", "tarif_salle", "preneur", "categorie_preneur"):
        if table in tables:
            op.drop_table(table)
