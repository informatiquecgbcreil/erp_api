"""Modules activés par structure, sans suppression de données."""
from alembic import op
import sqlalchemy as sa

revision = "d8f2b0c5e416"
down_revision = "c7e1a9b4d305"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("instance_settings", sa.Column("enabled_modules_json", sa.Text(), nullable=True))
    # Révoquer uniquement le passe-droit livré historiquement au rôle finance.
    # Les comptes ayant aussi un rôle direction/admin_tech gardent ces droits.
    op.execute(sa.text("DELETE FROM role_permissions WHERE role_id IN "
                       "(SELECT id FROM role WHERE code = 'finance') AND permission_id IN "
                       "(SELECT id FROM permission WHERE code IN ('admin:users', 'admin:rbac', 'users:edit'))"))


def downgrade():
    op.drop_column("instance_settings", "enabled_modules_json")
