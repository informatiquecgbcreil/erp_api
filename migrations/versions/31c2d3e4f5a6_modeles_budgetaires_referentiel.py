"""modeles budgetaires referentiel

Revision ID: 31c2d3e4f5a6
Revises: 30b1c2d3e4f5
Create Date: 2026-04-26
"""
from alembic import op
import sqlalchemy as sa

revision = "31c2d3e4f5a6"
down_revision = "30b1c2d3e4f5"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def upgrade():
    if not _has_table("budget_modele_referentiel"):
        op.create_table(
            "budget_modele_referentiel",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("nom", sa.String(length=180), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("secteur", sa.String(length=80), nullable=True),
            sa.Column("actif", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("ordre", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_budget_modele_ref_secteur", "budget_modele_referentiel", ["secteur"])
        op.create_index("ix_budget_modele_ref_actif", "budget_modele_referentiel", ["actif"])

    if not _has_table("budget_modele_ligne_referentiel"):
        op.create_table(
            "budget_modele_ligne_referentiel",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("modele_id", sa.Integer(), sa.ForeignKey("budget_modele_referentiel.id"), nullable=False),
            sa.Column("categorie_id", sa.Integer(), sa.ForeignKey("budget_categorie_referentiel.id"), nullable=False),
            sa.Column("montant_defaut", sa.Float(), nullable=True),
            sa.Column("ordre", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("commentaire", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_budget_modele_ligne_modele", "budget_modele_ligne_referentiel", ["modele_id"])
        op.create_index("ix_budget_modele_ligne_categorie", "budget_modele_ligne_referentiel", ["categorie_id"])

    bind = op.get_bind()
    meta = sa.MetaData()
    modele = sa.Table("budget_modele_referentiel", meta, autoload_with=bind)
    ligne = sa.Table("budget_modele_ligne_referentiel", meta, autoload_with=bind)
    cat = sa.Table("budget_categorie_referentiel", meta, autoload_with=bind)
    compte = sa.Table("budget_compte_referentiel", meta, autoload_with=bind)

    existing = bind.execute(sa.select(sa.func.count()).select_from(modele)).scalar() or 0
    if existing:
        return

    def cat_id(compte_code, libelle):
        stmt = (
            sa.select(cat.c.id)
            .select_from(cat.join(compte, cat.c.compte_id == compte.c.id))
            .where(compte.c.code == compte_code, cat.c.libelle == libelle)
        )
        return bind.execute(stmt).scalar()

    defaults = [
        ("Budget numérique standard", "Modèle général pour un secteur numérique associatif.", 10, [
            ("60", "Achat de petit équipement"),
            ("60", "Fournitures d’activité"),
            ("62", "Communication"),
            ("62", "Abonnements et logiciels"),
            ("64", "Salaires chargés"),
            ("74", "CAF"),
            ("74", "Politique de la ville"),
            ("74", "FONJEP"),
        ]),
        ("AAP CAF accès aux droits", "Base de budget pour un appel à projet accès aux droits / autonomie.", 20, [
            ("64", "Heures d’animation"),
            ("60", "Fournitures d’activité"),
            ("62", "Prestations intervenants"),
            ("62", "Communication"),
            ("74", "CAF"),
        ]),
        ("AAP Politique de la ville", "Base de budget pour un appel à projet QPV / politique de la ville.", 30, [
            ("64", "Salaires chargés"),
            ("60", "Achat de petit équipement"),
            ("62", "Communication"),
            ("62", "Prestations intervenants"),
            ("74", "Politique de la ville"),
        ]),
    ]

    for nom, description, ordre, cats in defaults:
        result = bind.execute(modele.insert().values(nom=nom, description=description, ordre=ordre, actif=True))
        try:
            modele_id = result.inserted_primary_key[0]
        except Exception:
            modele_id = bind.execute(sa.select(modele.c.id).where(modele.c.nom == nom)).scalar()

        line_rows = []
        for idx, (code, label) in enumerate(cats, start=1):
            cid = cat_id(code, label)
            if cid:
                line_rows.append(dict(modele_id=modele_id, categorie_id=cid, montant_defaut=0.0, ordre=idx))
        if line_rows:
            bind.execute(ligne.insert(), line_rows)


def downgrade():
    if _has_table("budget_modele_ligne_referentiel"):
        op.drop_table("budget_modele_ligne_referentiel")
    if _has_table("budget_modele_referentiel"):
        op.drop_table("budget_modele_referentiel")
