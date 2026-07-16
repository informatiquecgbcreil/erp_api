"""add active flag and continuity link for ateliers

Revision ID: 07a8b9c0d1e2
Revises: f6a7b8c9d0e1
Create Date: 2026-03-02 00:00:01.000000
"""
from alembic import op
import sqlalchemy as sa


revision = '07a8b9c0d1e2'
down_revision = 'f6a7b8c9d0e1'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('atelier_activite', sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column('atelier_activite', sa.Column('continuity_parent_id', sa.Integer(), nullable=True))
    op.create_index(op.f('ix_atelier_activite_is_active'), 'atelier_activite', ['is_active'], unique=False)
    op.create_index(op.f('ix_atelier_activite_continuity_parent_id'), 'atelier_activite', ['continuity_parent_id'], unique=False)


def downgrade():
    op.drop_index(op.f('ix_atelier_activite_continuity_parent_id'), table_name='atelier_activite')
    op.drop_index(op.f('ix_atelier_activite_is_active'), table_name='atelier_activite')
    op.drop_column('atelier_activite', 'continuity_parent_id')
    op.drop_column('atelier_activite', 'is_active')
