"""pedagogie mvp light: modules + mapping objectif competence

Revision ID: b1c2d3e4f5a6
Revises: a57b82ae0f18
Create Date: 2026-02-22 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b1c2d3e4f5a6'
down_revision = 'a57b82ae0f18'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'pedagogie_module',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('nom', sa.String(length=160), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('actif', sa.Boolean(), nullable=False, server_default=sa.text('1')),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_pedagogie_module_actif'), 'pedagogie_module', ['actif'], unique=False)
    op.create_index(op.f('ix_pedagogie_module_nom'), 'pedagogie_module', ['nom'], unique=True)

    op.create_table(
        'module_competence',
        sa.Column('module_id', sa.Integer(), nullable=False),
        sa.Column('competence_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['competence_id'], ['competence.id']),
        sa.ForeignKeyConstraint(['module_id'], ['pedagogie_module.id']),
        sa.PrimaryKeyConstraint('module_id', 'competence_id')
    )

    op.create_table(
        'atelier_module',
        sa.Column('atelier_id', sa.Integer(), nullable=False),
        sa.Column('module_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['atelier_id'], ['atelier_activite.id']),
        sa.ForeignKeyConstraint(['module_id'], ['pedagogie_module.id']),
        sa.PrimaryKeyConstraint('atelier_id', 'module_id')
    )

    op.create_table(
        'session_module',
        sa.Column('session_id', sa.Integer(), nullable=False),
        sa.Column('module_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['module_id'], ['pedagogie_module.id']),
        sa.ForeignKeyConstraint(['session_id'], ['session_activite.id']),
        sa.PrimaryKeyConstraint('session_id', 'module_id')
    )

    op.create_table(
        'objectif_competence_map',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('objectif_id', sa.Integer(), nullable=False),
        sa.Column('competence_id', sa.Integer(), nullable=False),
        sa.Column('poids', sa.Float(), nullable=False, server_default='1.0'),
        sa.Column('actif', sa.Boolean(), nullable=False, server_default=sa.text('1')),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['competence_id'], ['competence.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['objectif_id'], ['objectif.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('objectif_id', 'competence_id', name='uq_objectif_competence_map')
    )
    op.create_index(op.f('ix_objectif_competence_map_actif'), 'objectif_competence_map', ['actif'], unique=False)
    op.create_index(op.f('ix_objectif_competence_map_competence_id'), 'objectif_competence_map', ['competence_id'], unique=False)
    op.create_index(op.f('ix_objectif_competence_map_objectif_id'), 'objectif_competence_map', ['objectif_id'], unique=False)

    # bootstrap from legacy objectif_competence table for retro-compat.
    op.execute(
        """
        INSERT INTO objectif_competence_map (objectif_id, competence_id, poids, actif, created_at)
        SELECT objectif_id, competence_id, 1.0, 1, CURRENT_TIMESTAMP
        FROM objectif_competence
        """
    )


def downgrade():
    op.drop_index(op.f('ix_objectif_competence_map_objectif_id'), table_name='objectif_competence_map')
    op.drop_index(op.f('ix_objectif_competence_map_competence_id'), table_name='objectif_competence_map')
    op.drop_index(op.f('ix_objectif_competence_map_actif'), table_name='objectif_competence_map')
    op.drop_table('objectif_competence_map')

    op.drop_table('session_module')
    op.drop_table('atelier_module')
    op.drop_table('module_competence')

    op.drop_index(op.f('ix_pedagogie_module_nom'), table_name='pedagogie_module')
    op.drop_index(op.f('ix_pedagogie_module_actif'), table_name='pedagogie_module')
    op.drop_table('pedagogie_module')
