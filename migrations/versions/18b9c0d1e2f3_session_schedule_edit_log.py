"""add session schedule edit log for traceability

Revision ID: 18b9c0d1e2f3
Revises: 07a8b9c0d1e2
Create Date: 2026-03-02 13:05:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = '18b9c0d1e2f3'
down_revision = '07a8b9c0d1e2'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'session_schedule_edit_log',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('session_id', sa.Integer(), nullable=False),
        sa.Column('atelier_id', sa.Integer(), nullable=True),
        sa.Column('secteur', sa.String(length=80), nullable=True),
        sa.Column('old_date', sa.Date(), nullable=True),
        sa.Column('old_start', sa.String(length=10), nullable=True),
        sa.Column('old_end', sa.String(length=10), nullable=True),
        sa.Column('new_date', sa.Date(), nullable=True),
        sa.Column('new_start', sa.String(length=10), nullable=True),
        sa.Column('new_end', sa.String(length=10), nullable=True),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('edited_by', sa.Integer(), nullable=True),
        sa.Column('edited_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['atelier_id'], ['atelier_activite.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['edited_by'], ['user.id']),
        sa.ForeignKeyConstraint(['session_id'], ['session_activite.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_session_schedule_edit_log_session_id'), 'session_schedule_edit_log', ['session_id'], unique=False)
    op.create_index(op.f('ix_session_schedule_edit_log_atelier_id'), 'session_schedule_edit_log', ['atelier_id'], unique=False)
    op.create_index(op.f('ix_session_schedule_edit_log_secteur'), 'session_schedule_edit_log', ['secteur'], unique=False)
    op.create_index(op.f('ix_session_schedule_edit_log_edited_by'), 'session_schedule_edit_log', ['edited_by'], unique=False)
    op.create_index(op.f('ix_session_schedule_edit_log_edited_at'), 'session_schedule_edit_log', ['edited_at'], unique=False)


def downgrade():
    op.drop_index(op.f('ix_session_schedule_edit_log_edited_at'), table_name='session_schedule_edit_log')
    op.drop_index(op.f('ix_session_schedule_edit_log_edited_by'), table_name='session_schedule_edit_log')
    op.drop_index(op.f('ix_session_schedule_edit_log_secteur'), table_name='session_schedule_edit_log')
    op.drop_index(op.f('ix_session_schedule_edit_log_atelier_id'), table_name='session_schedule_edit_log')
    op.drop_index(op.f('ix_session_schedule_edit_log_session_id'), table_name='session_schedule_edit_log')
    op.drop_table('session_schedule_edit_log')
