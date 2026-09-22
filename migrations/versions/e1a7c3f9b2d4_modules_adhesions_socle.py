"""Module « Adhésions et caisse » séparé des finances, socle toujours actif.

Jusqu'ici, adhésions, caisse, impayés et tarifs faisaient partie du module
« Finances et projets ». Ils forment désormais le module « adhesions ».
Pour qu'aucune structure ne perde un écran à la mise à jour : toute
sélection enregistrée qui contenait « finances » reçoit aussi « adhesions ».
Le socle « presences » est ajouté partout (il ne se désactive plus).
Une sélection vide (NULL, « tout est actif ») n'est pas modifiée.
"""
import json

from alembic import op
import sqlalchemy as sa

revision = "e1a7c3f9b2d4"
down_revision = "d8f2b0c5e416"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    lignes = conn.execute(sa.text(
        "SELECT id, enabled_modules_json FROM instance_settings WHERE enabled_modules_json IS NOT NULL"
    )).fetchall()
    for identifiant, brut in lignes:
        try:
            modules = json.loads(brut)
        except (TypeError, ValueError):
            continue  # réglage illisible : l'application le traite déjà prudemment
        if not isinstance(modules, list):
            continue
        nouveaux = set(modules) | {"presences"}
        if "finances" in nouveaux:
            nouveaux.add("adhesions")
        if nouveaux != set(modules):
            conn.execute(
                sa.text("UPDATE instance_settings SET enabled_modules_json = :valeur WHERE id = :id"),
                {"valeur": json.dumps(sorted(nouveaux)), "id": identifiant},
            )


def downgrade():
    conn = op.get_bind()
    lignes = conn.execute(sa.text(
        "SELECT id, enabled_modules_json FROM instance_settings WHERE enabled_modules_json IS NOT NULL"
    )).fetchall()
    for identifiant, brut in lignes:
        try:
            modules = json.loads(brut)
        except (TypeError, ValueError):
            continue
        if isinstance(modules, list) and "adhesions" in modules:
            conn.execute(
                sa.text("UPDATE instance_settings SET enabled_modules_json = :valeur WHERE id = :id"),
                {"valeur": json.dumps(sorted(m for m in modules if m != "adhesions")), "id": identifiant},
            )
