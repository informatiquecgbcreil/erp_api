"""Rôle Accueil : toute la structure (décision de la direction).

Le secrétariat d'accueil compte les heures bénévoles, prend les inscriptions
et les paiements du public, tient la caisse et réalise les statistiques de
tous les secteurs. Le gabarit du rôle reçoit la portée structure, le comptage
de la caisse, les statistiques et bilans, les questionnaires et les
partenaires ; cette migration accorde les mêmes droits au rôle Accueil déjà
en place, une seule fois : un retrait ultérieur dans l'écran des droits est
respecté. Sans rôle Accueil (base neuve), rien à faire : le gabarit s'applique
à sa création. Rejouable.
"""
import sqlalchemy as sa
from alembic import op

revision = "e6c8a0b2d4f5"
down_revision = "d5b7c9e1f3a4"
branch_labels = None
depends_on = None

AJOUTS = (
    "scope:all_secteurs", "caisse:edit",
    "stats:view", "stats:view_all", "statsimpact:view", "statsimpact:view_all", "bilans:view",
    "questionnaires:view", "questionnaires:edit", "questionnaires:respond", "questionnaires:export",
    "partenaires:edit",
)


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if not all(insp.has_table(t) for t in ("role", "permission", "role_permissions")):
        return
    role_id = bind.execute(sa.text("SELECT id FROM role WHERE code = 'accueil'")).scalar()
    if role_id is None:
        return
    for code in AJOUTS:
        perm_id = bind.execute(sa.text("SELECT id FROM permission WHERE code = :c"), {"c": code}).scalar()
        if perm_id is None:
            continue  # créée au démarrage par le RBAC ; le gabarit ne vaut que pour un rôle neuf
        deja = bind.execute(sa.text(
            "SELECT 1 FROM role_permissions WHERE role_id = :r AND permission_id = :p"),
            {"r": role_id, "p": perm_id}).scalar()
        if not deja:
            bind.execute(sa.text("INSERT INTO role_permissions (role_id, permission_id) VALUES (:r, :p)"),
                         {"r": role_id, "p": perm_id})


def downgrade():
    # Rien à défaire de façon sûre : on ne sait pas quels droits le rôle avait
    # déjà avant (ajustements manuels). Le retour arrière passe par l'écran des
    # droits ou par la restauration d'une sauvegarde.
    pass
