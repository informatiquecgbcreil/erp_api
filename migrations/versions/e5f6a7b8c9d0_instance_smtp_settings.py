"""add smtp fields to instance settings

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-03-01 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'e5f6a7b8c9d0'
down_revision = 'd4e5f6a7b8c9'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('instance_settings', sa.Column('smtp_host', sa.String(length=255), nullable=True))
    op.add_column('instance_settings', sa.Column('smtp_port', sa.Integer(), nullable=True))
    op.add_column('instance_settings', sa.Column('smtp_username', sa.String(length=255), nullable=True))
    op.add_column('instance_settings', sa.Column('smtp_password', sa.String(length=255), nullable=True))
    op.add_column('instance_settings', sa.Column('smtp_use_tls', sa.Boolean(), nullable=True))
    op.add_column('instance_settings', sa.Column('smtp_sender', sa.String(length=255), nullable=True))


def downgrade():
    op.drop_column('instance_settings', 'smtp_sender')
    op.drop_column('instance_settings', 'smtp_use_tls')
    op.drop_column('instance_settings', 'smtp_password')
    op.drop_column('instance_settings', 'smtp_username')
    op.drop_column('instance_settings', 'smtp_port')
    op.drop_column('instance_settings', 'smtp_host')
