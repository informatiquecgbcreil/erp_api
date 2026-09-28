"""Règlements des anciens bulletins : classement prouvé / suivi / à rapprocher.

Correctif du défaut A de la reprise ``b5d8e3a1f264`` (déjà publiée, peut-être
déjà exécutée) : un bulletin portant de l'argent y était ignoré dès qu'un
versement quelconque existait pour la personne ou son foyer la même année.
Ce versement ne prouve pas le report ; la somme pouvait disparaître du suivi.

``b5d8e3a1f264`` n'est pas modifiée : une base reprise d'une installation
ancienne et une base déjà passée par la PR #59 arrivent ici dans le même
état, et c'est ce seul code (``app.services.reprise_reglements``, version
notée sur chaque ligne) qui classe les bulletins dans les deux cas.

Effets :
- crée ``rapprochement_bulletin`` : une ligne par bulletin portant de
  l'argent, valeurs d'origine figées, classement et preuve ;
- ne crée, ne modifie ni ne supprime AUCUN encaissement ni versement : les
  sommes ambiguës attendent une décision humaine (Caisse → Rapprochement des
  bulletins), les encaissements et rapprochements créés depuis la PR #59
  restent tels quels ;
- rejouable : une clé déjà classée n'est pas reprise.

Limite : sur une base déjà passée par la PR #59, la colonne du bulletin a pu
être réécrite par le recalcul automatique (miroir du total versé) depuis.
La valeur d'origine est alors dans les sauvegardes antérieures : la page de
rapprochement permet de les comparer (lecture seule du lot).
"""
import os
import sys

import sqlalchemy as sa
from alembic import op

revision = "d2e4f6a8b013"
down_revision = "c1d3e5f7a902"
branch_labels = None
depends_on = None


def _classement():
    racine = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    if racine not in sys.path:
        sys.path.insert(0, racine)
    from app.services import reprise_reglements
    return reprise_reglements


def upgrade():
    bind = op.get_bind()
    if "rapprochement_bulletin" not in set(sa.inspect(bind).get_table_names()):
        op.create_table(
            "rapprochement_bulletin",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("cle", sa.String(80), nullable=False, unique=True),
            sa.Column("inscription_annuelle_id", sa.Integer(),
                      sa.ForeignKey("inscription_annuelle.id", ondelete="SET NULL"), nullable=True),
            sa.Column("annee_scolaire", sa.Integer(), nullable=False),
            sa.Column("participant_id", sa.Integer(), sa.ForeignKey("participant.id", ondelete="SET NULL"),
                      nullable=True),
            sa.Column("foyer_id", sa.Integer(), nullable=True),
            sa.Column("montant_origine", sa.Float(), nullable=False),
            sa.Column("mode_origine", sa.String(20), nullable=True),
            sa.Column("date_origine", sa.Date(), nullable=True),
            sa.Column("statut_bulletin", sa.String(30), nullable=True),
            sa.Column("source", sa.String(20), nullable=False, server_default="base"),
            sa.Column("source_detail", sa.String(120), nullable=True),
            sa.Column("classement", sa.String(20), nullable=False),
            sa.Column("montant_prouve", sa.Float(), nullable=False, server_default="0"),
            sa.Column("montant_ecart", sa.Float(), nullable=False, server_default="0"),
            sa.Column("preuve", sa.Text(), nullable=True),
            sa.Column("motif", sa.String(255), nullable=True),
            sa.Column("encaissement_ancien_id", sa.Integer(),
                      sa.ForeignKey("encaissement.id", ondelete="SET NULL"), nullable=True),
            sa.Column("version_classement", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("cree_le", sa.DateTime(), nullable=False),
            sa.Column("decision", sa.String(30), nullable=True),
            sa.Column("decision_montant", sa.Float(), nullable=True),
            sa.Column("decision_note", sa.String(255), nullable=True),
            sa.Column("encaissement_id", sa.Integer(), sa.ForeignKey("encaissement.id", ondelete="SET NULL"),
                      nullable=True),
            sa.Column("decide_par_user_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="SET NULL"),
                      nullable=True),
            sa.Column("decide_le", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_rapprochement_bulletin_inscription_annuelle_id", "rapprochement_bulletin",
                        ["inscription_annuelle_id"])
        op.create_index("ix_rapprochement_bulletin_classement", "rapprochement_bulletin", ["classement"])
        op.create_index("ix_rapprochement_bulletin_decision", "rapprochement_bulletin", ["decision"])
    _classement().classer_tout(bind)


def downgrade():
    op.drop_table("rapprochement_bulletin")
