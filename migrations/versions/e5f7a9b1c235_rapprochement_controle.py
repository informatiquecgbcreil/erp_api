"""Rapprochement des bulletins : contrôle des doublons avec la somme historique.

Consolidation après la PR #60 (défaut 1). Quand un bulletin portait à la fois
un report partiel en versements et une somme « à qualifier » reprise par la
PR #59 (b5d8e3a1f264), la décision « somme distincte » de la PR #60 ajoutait
le manquant sans annuler la somme historique : 20 + 30 + 10 = 60 € une fois
tout qualifié, pour 30 € reçus.

Effets :
- ajoute ``rapprochement_bulletin.controle`` / ``controle_note`` ;
- signale (``app.services.reprise_reglements.controler_decisions``) les
  rapprochements déjà décidés dans ce cas (``doublon_a_annuler``) et ceux où
  la somme historique a déjà été qualifiée alors qu'un report existe
  (``a_verifier``) ;
- ne crée, ne modifie ni ne supprime AUCUN encaissement : la correction
  (annulation motivée de la somme historique) se fait en un clic depuis
  Caisse → Rapprochement des bulletins, les cas indécidables y sont présentés
  pour vérification. Rejouable.
"""
import os
import sys

import sqlalchemy as sa
from alembic import op

revision = "e5f7a9b1c235"
down_revision = "d2e4f6a8b013"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    colonnes = {c["name"] for c in sa.inspect(bind).get_columns("rapprochement_bulletin")}
    if "controle" not in colonnes:
        with op.batch_alter_table("rapprochement_bulletin") as batch:
            batch.add_column(sa.Column("controle", sa.String(30), nullable=True))
            batch.add_column(sa.Column("controle_note", sa.String(255), nullable=True))
            batch.create_index("ix_rapprochement_bulletin_controle", ["controle"])
    racine = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    if racine not in sys.path:
        sys.path.insert(0, racine)
    from app.services.reprise_reglements import controler_decisions
    controler_decisions(bind)


def downgrade():
    with op.batch_alter_table("rapprochement_bulletin") as batch:
        batch.drop_index("ix_rapprochement_bulletin_controle")
        batch.drop_column("controle_note")
        batch.drop_column("controle")
