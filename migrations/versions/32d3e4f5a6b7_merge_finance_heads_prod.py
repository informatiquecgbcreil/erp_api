"""merge finance heads prod

Revision ID: 32d3e4f5a6b7
Revises: 29a0b1c2d3e4, 31c2d3e4f5a6
Create Date: 2026-04-27

Migration vide de fusion Alembic. Elle reunifie la branche prod historique 29 et la branche referentiel/modeles 31.
"""

revision = "32d3e4f5a6b7"
down_revision = ("29a0b1c2d3e4", "31c2d3e4f5a6")
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
