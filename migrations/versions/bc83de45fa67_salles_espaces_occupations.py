"""Salles & espaces : sites, arbre des espaces, occupations.

Crée le référentiel des lieux (``site``, ``espace``), la table unique des
``occupation`` (séances, créneaux, locations, blocages) et les trois
rattachements qui branchent l'existant : la salle d'une séance, la salle
d'un créneau d'agenda, l'emplacement d'un matériel d'inventaire.

Migration défensive : le démarrage exécute aussi ``db.create_all()``, la
migration peut donc arriver sur une base où les tables existent déjà.

Revision ID: bc83de45fa67
Revises: ab72cd34ef56
Create Date: 2026-09-11
"""
import sqlalchemy as sa
from alembic import op

revision = "bc83de45fa67"
down_revision = "ab72cd34ef56"
branch_labels = None
depends_on = None


def _inspecteur():
    return sa.inspect(op.get_bind())


def _tables() -> set[str]:
    return set(_inspecteur().get_table_names())


def _colonnes(table: str) -> set[str]:
    insp = _inspecteur()
    if not insp.has_table(table):
        return set()
    return {c["name"] for c in insp.get_columns(table)}


#: Rattachements ajoutés à des tables existantes.
#: ``SET NULL`` partout : supprimer une salle ne doit jamais détruire une
#: séance, un créneau ou du matériel — seulement les délier.
RATTACHEMENTS = [
    ("session_activite", "espace_id"),
    ("agenda_creneau", "espace_id"),
    ("inventaire_item", "espace_id"),
]


def upgrade():
    tables = _tables()

    if "site" not in tables:
        op.create_table(
            "site",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("nom", sa.String(length=180), nullable=False),
            sa.Column("code", sa.String(length=40), nullable=False),
            sa.Column("type_site", sa.String(length=30), nullable=False, server_default="propriete"),
            sa.Column("adresse", sa.String(length=255), nullable=True),
            sa.Column("code_postal", sa.String(length=10), nullable=True),
            sa.Column("ville", sa.String(length=120), nullable=True),
            sa.Column("proprietaire", sa.String(length=180), nullable=True),
            sa.Column("convention_reference", sa.String(length=120), nullable=True),
            sa.Column("convention_debut", sa.Date(), nullable=True),
            sa.Column("convention_fin", sa.Date(), nullable=True),
            sa.Column("regime_sous_location", sa.String(length=30), nullable=False, server_default="inconnue"),
            sa.Column("convention_notes", sa.Text(), nullable=True),
            sa.Column("valeur_locative_annuelle", sa.Float(), nullable=True),
            sa.Column("actif", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("code", name="uq_site_code"),
        )
        op.create_index("ix_site_code", "site", ["code"], unique=True)
        op.create_index("ix_site_type_site", "site", ["type_site"])
        op.create_index("ix_site_actif", "site", ["actif"])

    if "espace" not in tables:
        op.create_table(
            "espace",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("site_id", sa.Integer(), nullable=False),
            sa.Column("parent_id", sa.Integer(), nullable=True),
            sa.Column("nom", sa.String(length=180), nullable=False),
            sa.Column("type_espace", sa.String(length=30), nullable=False, server_default="salle"),
            sa.Column("ordre", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("reservable", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("louable", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("stockage", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("securise", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("detenteur_cle", sa.String(length=180), nullable=True),
            sa.Column("capacite_reglementaire", sa.Integer(), nullable=True),
            sa.Column("capacite_usage", sa.Integer(), nullable=True),
            sa.Column("surface_m2", sa.Float(), nullable=True),
            sa.Column("pmr", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("battement_minutes", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("habilitation_requise", sa.String(length=255), nullable=True),
            sa.Column("equipements", sa.Text(), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("valeur_locative_annuelle", sa.Float(), nullable=True),
            sa.Column("actif", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["site_id"], ["site.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["parent_id"], ["espace.id"], ondelete="CASCADE"),
        )
        op.create_index("ix_espace_site_id", "espace", ["site_id"])
        op.create_index("ix_espace_parent_id", "espace", ["parent_id"])
        op.create_index("ix_espace_type_espace", "espace", ["type_espace"])
        op.create_index("ix_espace_reservable", "espace", ["reservable"])
        op.create_index("ix_espace_louable", "espace", ["louable"])
        op.create_index("ix_espace_stockage", "espace", ["stockage"])
        op.create_index("ix_espace_actif", "espace", ["actif"])
        op.create_index("ix_espace_site_parent", "espace", ["site_id", "parent_id"])

    if "occupation" not in tables:
        op.create_table(
            "occupation",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("espace_id", sa.Integer(), nullable=False),
            sa.Column("date_jour", sa.Date(), nullable=False),
            # Minutes depuis minuit : les séances stockent leurs heures en
            # texte libre, impossible d'y comparer « 9:00 » et « 09:00 ».
            sa.Column("minute_debut", sa.Integer(), nullable=False),
            sa.Column("minute_fin", sa.Integer(), nullable=False),
            sa.Column("origine", sa.String(length=20), nullable=False, server_default="location"),
            sa.Column("statut", sa.String(length=20), nullable=False, server_default="confirme"),
            sa.Column("session_id", sa.Integer(), nullable=True),
            sa.Column("creneau_id", sa.Integer(), nullable=True),
            sa.Column("titre", sa.String(length=200), nullable=True),
            sa.Column("secteur", sa.String(length=80), nullable=True),
            sa.Column("effectif_prevu", sa.Integer(), nullable=True),
            sa.Column("note", sa.String(length=500), nullable=True),
            sa.Column("created_by_user_id", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["espace_id"], ["espace.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["session_id"], ["session_activite.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["creneau_id"], ["agenda_creneau.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["created_by_user_id"], ["user.id"], ondelete="SET NULL"),
        )
        op.create_index("ix_occupation_espace_id", "occupation", ["espace_id"])
        op.create_index("ix_occupation_date_jour", "occupation", ["date_jour"])
        op.create_index("ix_occupation_origine", "occupation", ["origine"])
        op.create_index("ix_occupation_statut", "occupation", ["statut"])
        op.create_index("ix_occupation_session_id", "occupation", ["session_id"])
        op.create_index("ix_occupation_creneau_id", "occupation", ["creneau_id"])
        op.create_index("ix_occupation_secteur", "occupation", ["secteur"])
        op.create_index("ix_occupation_espace_jour", "occupation", ["espace_id", "date_jour"])
        op.create_index(
            "ix_occupation_jour_plage", "occupation", ["date_jour", "minute_debut", "minute_fin"]
        )

    for table, colonne in RATTACHEMENTS:
        if table in tables and colonne not in _colonnes(table):
            op.add_column(table, sa.Column(colonne, sa.Integer(), nullable=True))
            op.create_index(f"ix_{table}_{colonne}", table, [colonne])
            # La contrainte n'est posée que là où ALTER TABLE ... ADD
            # CONSTRAINT existe : SQLite ne sait pas le faire après coup, et
            # l'ORM y assure déjà la mise à NULL à la suppression.
            if op.get_bind().dialect.name != "sqlite":
                op.create_foreign_key(
                    f"fk_{table}_{colonne}_espace", table, "espace",
                    [colonne], ["id"], ondelete="SET NULL",
                )


def downgrade():
    tables = _tables()
    for table, colonne in RATTACHEMENTS:
        if table in tables and colonne in _colonnes(table):
            with op.batch_alter_table(table) as batch_op:
                batch_op.drop_column(colonne)
    for table in ("occupation", "espace", "site"):
        if table in tables:
            op.drop_table(table)
