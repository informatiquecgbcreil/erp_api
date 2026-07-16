"""add plan projet-atelier-module and objectif.module_id

Revision ID: c3d4e5f6a7b8
Revises: b1c2d3e4f5a6
Create Date: 2026-02-23 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = 'c3d4e5f6a7b8'
down_revision = 'b1c2d3e4f5a6'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('objectif') as batch_op:
        batch_op.add_column(sa.Column('module_id', sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f('ix_objectif_module_id'), ['module_id'], unique=False)
        batch_op.create_foreign_key('fk_objectif_module_id_pedagogie_module', 'pedagogie_module', ['module_id'], ['id'])

    op.create_table(
        'plan_projet_atelier_module',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('projet_id', sa.Integer(), nullable=False),
        sa.Column('atelier_id', sa.Integer(), nullable=False),
        sa.Column('module_id', sa.Integer(), nullable=False),
        sa.Column('actif', sa.Boolean(), nullable=False, server_default=sa.text('1')),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['atelier_id'], ['atelier_activite.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['module_id'], ['pedagogie_module.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['projet_id'], ['projet.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('projet_id', 'atelier_id', 'module_id', name='uq_plan_projet_atelier_module')
    )
    op.create_index(op.f('ix_plan_projet_atelier_module_actif'), 'plan_projet_atelier_module', ['actif'], unique=False)
    op.create_index(op.f('ix_plan_projet_atelier_module_atelier_id'), 'plan_projet_atelier_module', ['atelier_id'], unique=False)
    op.create_index(op.f('ix_plan_projet_atelier_module_module_id'), 'plan_projet_atelier_module', ['module_id'], unique=False)
    op.create_index(op.f('ix_plan_projet_atelier_module_projet_id'), 'plan_projet_atelier_module', ['projet_id'], unique=False)


def downgrade():
    op.drop_index(op.f('ix_plan_projet_atelier_module_projet_id'), table_name='plan_projet_atelier_module')
    op.drop_index(op.f('ix_plan_projet_atelier_module_module_id'), table_name='plan_projet_atelier_module')
    op.drop_index(op.f('ix_plan_projet_atelier_module_atelier_id'), table_name='plan_projet_atelier_module')
    op.drop_index(op.f('ix_plan_projet_atelier_module_actif'), table_name='plan_projet_atelier_module')
    op.drop_table('plan_projet_atelier_module')

    with op.batch_alter_table('objectif') as batch_op:
        batch_op.drop_constraint('fk_objectif_module_id_pedagogie_module', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_objectif_module_id'))
        batch_op.drop_column('module_id')
