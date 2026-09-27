"""Montants de caisse : refus en base des valeurs non finies (NaN, infini).

PostgreSQL seulement : sur SQLite, NaN est déjà stocké comme NULL et refusé
par NOT NULL. Une contrainte n'est posée que si les données existantes la
respectent ; sinon la mise à jour continue (pas de boucle de redémarrage) et
le journal signale les lignes à corriger à la main.
"""
import logging

from alembic import op
import sqlalchemy as sa

revision = "f3b8d1a6c902"
down_revision = "cd91ef23ab45"
branch_labels = None
depends_on = None

log = logging.getLogger("alembic.runtime.migration")

BORNE = 1000000
# (table, colonne, nom de la contrainte) ; NaN est « plus grand que tout »
# pour PostgreSQL, donc BETWEEN le refuse comme il refuse l'infini.
CONTRAINTES = [
    ("paiement", "montant", "ck_paiement_montant_fini"),
    ("don", "montant", "ck_don_montant_fini"),
    ("caisse_mouvement", "montant", "ck_caisse_mouvement_montant_fini"),
    ("caisse_mouvement", "ecart", "ck_caisse_mouvement_ecart_fini"),
]


def _condition(colonne):
    return f"{colonne} IS NULL OR {colonne} BETWEEN -{BORNE} AND {BORNE}"


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    for table, colonne, nom in CONTRAINTES:
        fautives = bind.execute(sa.text(
            f"SELECT count(*) FROM {table} WHERE NOT ({_condition(colonne)})"
        )).scalar()
        if fautives:
            log.warning(
                "Contrainte %s non posée : %s ligne(s) de %s.%s non finie(s) ou hors bornes "
                "(SELECT id, %s FROM %s WHERE NOT (%s)) — à corriger à la main ; l'application refuse déjà toute nouvelle valeur non finie.",
                nom, fautives, table, colonne, colonne, table, _condition(colonne),
            )
            continue
        op.create_check_constraint(nom, table, _condition(colonne))


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    for table, _colonne, nom in CONTRAINTES:
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {nom}")
