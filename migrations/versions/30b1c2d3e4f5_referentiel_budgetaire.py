"""referentiel budgetaire

Revision ID: 30b1c2d3e4f5
Revises: 27e8f9a0b1c2
Create Date: 2026-04-26
"""
from alembic import op
import sqlalchemy as sa

revision = "30b1c2d3e4f5"
down_revision = "27e8f9a0b1c2"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def upgrade():
    if not _has_table("budget_compte_referentiel"):
        op.create_table(
            "budget_compte_referentiel",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("nature", sa.String(length=10), nullable=False, server_default="charge"),
            sa.Column("code", sa.String(length=20), nullable=False),
            sa.Column("libelle", sa.String(length=160), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("secteur", sa.String(length=80), nullable=True),
            sa.Column("actif", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("ordre", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_budget_compte_ref_nature", "budget_compte_referentiel", ["nature"])
        op.create_index("ix_budget_compte_ref_secteur", "budget_compte_referentiel", ["secteur"])

    if not _has_table("budget_categorie_referentiel"):
        op.create_table(
            "budget_categorie_referentiel",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("compte_id", sa.Integer(), sa.ForeignKey("budget_compte_referentiel.id"), nullable=False),
            sa.Column("nature", sa.String(length=10), nullable=False, server_default="charge"),
            sa.Column("code", sa.String(length=40), nullable=True),
            sa.Column("libelle", sa.String(length=200), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("montant_defaut", sa.Float(), nullable=False, server_default="0"),
            sa.Column("secteur", sa.String(length=80), nullable=True),
            sa.Column("actif", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("ordre", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_budget_cat_ref_compte", "budget_categorie_referentiel", ["compte_id"])
        op.create_index("ix_budget_cat_ref_nature", "budget_categorie_referentiel", ["nature"])
        op.create_index("ix_budget_cat_ref_secteur", "budget_categorie_referentiel", ["secteur"])

    bind = op.get_bind()
    meta = sa.MetaData()
    compte = sa.Table("budget_compte_referentiel", meta, autoload_with=bind)
    cat = sa.Table("budget_categorie_referentiel", meta, autoload_with=bind)

    existing = bind.execute(sa.select(sa.func.count()).select_from(compte)).scalar() or 0
    if existing:
        return

    comptes = [
        ("charge", "60", "Achats", 10),
        ("charge", "61", "Services extérieurs", 20),
        ("charge", "62", "Autres services extérieurs", 30),
        ("charge", "63", "Impôts et taxes", 40),
        ("charge", "64", "Charges de personnel", 50),
        ("charge", "65", "Autres charges de gestion courante", 60),
        ("charge", "66", "Charges financières", 70),
        ("charge", "67", "Charges exceptionnelles", 80),
        ("charge", "68", "Dotations aux amortissements", 90),
        ("produit", "70", "Ventes / prestations", 110),
        ("produit", "73", "Dotations et produits de tarification", 120),
        ("produit", "74", "Subventions d’exploitation", 130),
        ("produit", "75", "Autres produits de gestion courante", 140),
        ("produit", "76", "Produits financiers", 150),
        ("produit", "77", "Produits exceptionnels", 160),
        ("produit", "78", "Reprises sur amortissements", 170),
        ("produit", "79", "Transferts de charges", 180),
    ]
    bind.execute(compte.insert(), [
        dict(nature=n, code=code, libelle=lib, ordre=ordre, actif=True)
        for n, code, lib, ordre in comptes
    ])

    compte_rows = {
        r.code: r.id
        for r in bind.execute(sa.select(compte.c.id, compte.c.code)).all()
    }
    nature_by_code = {code: nature for nature, code, _lib, _ordre in comptes}

    categories = [
        ("60", "Achat de petit équipement", 10),
        ("60", "Matières premières et fournitures", 20),
        ("60", "Fournitures administratives", 30),
        ("60", "Fournitures d’activité", 40),
        ("61", "Locations", 10),
        ("61", "Assurance", 20),
        ("61", "Documentation", 30),
        ("61", "Maintenance", 40),
        ("62", "Communication", 10),
        ("62", "Déplacements", 20),
        ("62", "Frais postaux et télécommunications", 30),
        ("62", "Prestations intervenants", 40),
        ("62", "Abonnements et logiciels", 50),
        ("63", "Taxes et contributions", 10),
        ("64", "Salaires chargés", 10),
        ("64", "Heures d’animation", 20),
        ("64", "Vacataires / intervenants", 30),
        ("65", "Adhésions / cotisations", 10),
        ("68", "Amortissement matériel", 10),
        ("70", "Prestations facturées", 10),
        ("73", "Dotation / tarification", 10),
        ("74", "CAF", 10),
        ("74", "Ville", 20),
        ("74", "État", 30),
        ("74", "Politique de la ville", 40),
        ("74", "FONJEP", 50),
        ("74", "Département", 60),
        ("74", "Région", 70),
        ("74", "Fondation / mécénat", 80),
        ("75", "Participation des usagers", 10),
        ("75", "Cotisations", 20),
        ("75", "Dons", 30),
        ("79", "Transfert de charges", 10),
    ]
    bind.execute(cat.insert(), [
        dict(
            compte_id=compte_rows[code],
            nature=nature_by_code[code],
            code=None,
            libelle=lib,
            ordre=ordre,
            montant_defaut=0.0,
            actif=True,
        )
        for code, lib, ordre in categories
    ])


def downgrade():
    if _has_table("budget_categorie_referentiel"):
        op.drop_table("budget_categorie_referentiel")
    if _has_table("budget_compte_referentiel"):
        op.drop_table("budget_compte_referentiel")
