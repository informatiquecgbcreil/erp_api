"""Quartiers : appartenance à un QPV, et normalisation des villes.

Un QPV regroupe plusieurs quartiers d'usage. Le Rouher et la Cavée de
Senlis sont deux quartiers distincts du même QPV « Hauts de Creil » : il
faut pouvoir les séparer dans un bilan de quartier ET les additionner quand
le financeur demande le QPV.

Faute de champ, la seule façon de l'exprimer était de le coder dans le NOM
(« Rouher (QPV Hauts de Creil) », « Autres Hauts de Creil (QPV Hauts de
Creil) »), et deux exports retrouvaient l'information par recherche de
sous-chaîne, avec deux règles différentes :

- SENACS : « rouher » dans le nom, puis « hauts de creil », puis is_qpv ;
- export XLSX : « hors rouher », puis « rouher », puis « bas », puis
  « haut ».

Les deux tombaient en panne dès qu'un quartier s'appelait simplement
« Cavée de Senlis » — aucun des mots-clés n'y figure.

Cette migration ajoute la colonne ``qpv`` et la remplit à partir de ce que
les noms disent déjà, puis normalise l'écriture des villes pour que la
liste déroulante des quartiers cesse de se vider quand on écrit « Nogent
sur Oise » là où elle attend « Nogent-sur-Oise ».

Revision ID: ab38cd49ef02
Revises: fa27bc38de91
Create Date: 2026-09-14
"""
import sqlalchemy as sa
from alembic import op

revision = "ab38cd49ef02"
down_revision = "fa27bc38de91"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspecteur = sa.inspect(bind)
    if "quartier" not in set(inspecteur.get_table_names()):
        return

    colonnes = {c["name"] for c in inspecteur.get_columns("quartier")}
    if "qpv" not in colonnes:
        op.add_column("quartier", sa.Column("qpv", sa.String(length=120), nullable=True))
        op.create_index("ix_quartier_qpv", "quartier", ["qpv"])

    from app.services.referentiels import (
        deduire_qpv_depuis_le_nom,
        normaliser_villes_existantes,
    )

    # 1. Reprendre ce que les noms disaient déjà.
    for identifiant, nom, is_qpv in bind.execute(
        sa.text("SELECT id, nom, is_qpv FROM quartier")
    ).fetchall():
        qpv = deduire_qpv_depuis_le_nom(nom, bool(is_qpv))
        if qpv:
            bind.execute(
                sa.text("UPDATE quartier SET qpv = :qpv, is_qpv = 1 WHERE id = :id"),
                {"qpv": qpv, "id": identifiant},
            )

    # 2. Uniformiser l'écriture des villes, sans jamais rapprocher deux
    #    communes différentes : seules les variantes de forme sont touchées.
    normaliser_villes_existantes(bind)


def downgrade():
    bind = op.get_bind()
    inspecteur = sa.inspect(bind)
    if "quartier" not in set(inspecteur.get_table_names()):
        return
    colonnes = {c["name"] for c in inspecteur.get_columns("quartier")}
    if "qpv" in colonnes:
        try:
            op.drop_index("ix_quartier_qpv", table_name="quartier")
        except Exception:  # noqa: BLE001 — index absent selon les bases
            pass
        op.drop_column("quartier", "qpv")
