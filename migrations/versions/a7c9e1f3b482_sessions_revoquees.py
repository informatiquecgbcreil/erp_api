"""Sessions fermées par la déconnexion (mineur sécurité : cookie encore valable après déconnexion)."""
import sqlalchemy as sa
from alembic import op

revision = "a7c9e1f3b482"
down_revision = "f5a8b3d0c279"
branch_labels = None
depends_on = None


def upgrade():
    if "session_revoquee" not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table(
            "session_revoquee",
            sa.Column("sid", sa.String(64), primary_key=True),
            sa.Column("expire_le", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_session_revoquee_expire_le", "session_revoquee", ["expire_le"])


def downgrade():
    op.drop_index("ix_session_revoquee_expire_le", table_name="session_revoquee")
    op.drop_table("session_revoquee")
