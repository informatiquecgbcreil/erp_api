"""user dashboard preferences

Revision ID: 24b5c6d7e8f9
Revises: 23a4b5c6d7e8
Create Date: 2026-04-08 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = '24b5c6d7e8f9'
down_revision = '23a4b5c6d7e8'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'user_dashboard_preference',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('ui_mode', sa.String(length=20), nullable=False, server_default='simple'),
        sa.Column('quick_actions_json', sa.Text(), nullable=True),
        sa.Column('widgets_json', sa.Text(), nullable=True),
        sa.Column('customized', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id')
    )
    op.create_index(op.f('ix_user_dashboard_preference_user_id'), 'user_dashboard_preference', ['user_id'], unique=True)


def downgrade():
    op.drop_index(op.f('ix_user_dashboard_preference_user_id'), table_name='user_dashboard_preference')
    op.drop_table('user_dashboard_preference')
