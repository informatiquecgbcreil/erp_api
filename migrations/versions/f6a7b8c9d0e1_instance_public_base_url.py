"""add public base url to instance settings

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-03-02 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'f6a7b8c9d0e1'
down_revision = 'e5f6a7b8c9d0'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('instance_settings', sa.Column('public_base_url', sa.String(length=255), nullable=True))


def downgrade():
    op.drop_column('instance_settings', 'public_base_url')
