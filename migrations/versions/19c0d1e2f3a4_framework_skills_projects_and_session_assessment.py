"""framework/skills + learning projects + atelier-project + session assessment

Revision ID: 19c0d1e2f3a4
Revises: 18b9c0d1e2f3
Create Date: 2026-03-02 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '19c0d1e2f3a4'
down_revision = '18b9c0d1e2f3'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'framework',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('code', sa.String(length=64), nullable=False),
        sa.Column('nom', sa.String(length=160), nullable=False),
        sa.Column('version', sa.String(length=32), nullable=True),
        sa.Column('lang', sa.String(length=8), nullable=True),
        sa.Column('source_url', sa.String(length=512), nullable=True),
        sa.Column('actif', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('code', name='uq_framework_code'),
    )
    op.create_index(op.f('ix_framework_actif'), 'framework', ['actif'], unique=False)
    op.create_index(op.f('ix_framework_code'), 'framework', ['code'], unique=False)

    op.create_table(
        'skill',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('framework_id', sa.Integer(), nullable=False),
        sa.Column('code', sa.String(length=64), nullable=False),
        sa.Column('label', sa.String(length=255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('domain_code', sa.String(length=32), nullable=True),
        sa.Column('domain_label', sa.String(length=255), nullable=True),
        sa.Column('sort_order', sa.Integer(), nullable=True),
        sa.Column('actif', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['framework_id'], ['framework.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('framework_id', 'code', name='uq_skill_framework_code'),
    )
    op.create_index(op.f('ix_skill_actif'), 'skill', ['actif'], unique=False)
    op.create_index(op.f('ix_skill_code'), 'skill', ['code'], unique=False)
    op.create_index(op.f('ix_skill_domain_code'), 'skill', ['domain_code'], unique=False)
    op.create_index(op.f('ix_skill_framework_id'), 'skill', ['framework_id'], unique=False)

    op.create_table(
        'learning_project',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('titre', sa.String(length=255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('date_debut', sa.Date(), nullable=True),
        sa.Column('date_fin', sa.Date(), nullable=True),
        sa.Column('public_cible', sa.String(length=255), nullable=True),
        sa.Column('framework_id_default', sa.Integer(), nullable=True),
        sa.Column('seuil_reussite', sa.Float(), nullable=False, server_default='0.6'),
        sa.Column('actif', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['created_by_id'], ['user.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['framework_id_default'], ['framework.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_learning_project_actif'), 'learning_project', ['actif'], unique=False)
    op.create_index(op.f('ix_learning_project_created_by_id'), 'learning_project', ['created_by_id'], unique=False)
    op.create_index(op.f('ix_learning_project_framework_id_default'), 'learning_project', ['framework_id_default'], unique=False)
    op.create_index(op.f('ix_learning_project_titre'), 'learning_project', ['titre'], unique=False)

    op.create_table(
        'learning_project_skill',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('skill_id', sa.Integer(), nullable=False),
        sa.Column('target_level', sa.String(length=32), nullable=True),
        sa.Column('poids', sa.Float(), nullable=False, server_default='1.0'),
        sa.Column('obligatoire', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('actif', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['project_id'], ['learning_project.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['skill_id'], ['skill.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('project_id', 'skill_id', name='uq_learning_project_skill'),
    )
    op.create_index(op.f('ix_learning_project_skill_actif'), 'learning_project_skill', ['actif'], unique=False)
    op.create_index(op.f('ix_learning_project_skill_project_id'), 'learning_project_skill', ['project_id'], unique=False)
    op.create_index(op.f('ix_learning_project_skill_skill_id'), 'learning_project_skill', ['skill_id'], unique=False)

    # Option B : atelier <-> projet (N-N)
    op.create_table(
        'atelier_project',
        sa.Column('atelier_id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['atelier_id'], ['atelier_activite.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['project_id'], ['learning_project.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('atelier_id', 'project_id'),
    )

    # Taggage des compétences au niveau SESSION
    op.create_table(
        'session_skill',
        sa.Column('session_id', sa.Integer(), nullable=False),
        sa.Column('skill_id', sa.Integer(), nullable=False),
        sa.Column('expected_level', sa.String(length=32), nullable=True),
        sa.Column('coverage', sa.Float(), nullable=False, server_default='1.0'),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['session_id'], ['session_activite.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['skill_id'], ['skill.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('session_id', 'skill_id'),
    )
    op.create_index(op.f('ix_session_skill_skill_id'), 'session_skill', ['skill_id'], unique=False)

    # Évaluation par session
    op.create_table(
        'session_assessment',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('session_id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=True),
        sa.Column('method', sa.String(length=32), nullable=False, server_default='OBSERVATION'),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('assessed_at', sa.DateTime(), nullable=True),
        sa.Column('assessed_by_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['assessed_by_id'], ['user.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['project_id'], ['learning_project.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['session_id'], ['session_activite.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_session_assessment_assessed_at'), 'session_assessment', ['assessed_at'], unique=False)
    op.create_index(op.f('ix_session_assessment_assessed_by_id'), 'session_assessment', ['assessed_by_id'], unique=False)
    op.create_index(op.f('ix_session_assessment_project_id'), 'session_assessment', ['project_id'], unique=False)
    op.create_index(op.f('ix_session_assessment_session_id'), 'session_assessment', ['session_id'], unique=False)

    op.create_table(
        'session_assessment_skill',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('session_assessment_id', sa.Integer(), nullable=False),
        sa.Column('skill_id', sa.Integer(), nullable=False),
        sa.Column('result', sa.String(length=16), nullable=False, server_default='EN_COURS'),
        sa.Column('score', sa.Integer(), nullable=True),
        sa.Column('observed_level', sa.String(length=32), nullable=True),
        sa.Column('comment', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['session_assessment_id'], ['session_assessment.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['skill_id'], ['skill.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('session_assessment_id', 'skill_id', name='uq_session_assessment_skill'),
        sa.CheckConstraint("result IN ('NA','EN_COURS','ACQUIS')", name='ck_session_assessment_skill_result'),
    )
    op.create_index(op.f('ix_session_assessment_skill_result'), 'session_assessment_skill', ['result'], unique=False)
    op.create_index(op.f('ix_session_assessment_skill_session_assessment_id'), 'session_assessment_skill', ['session_assessment_id'], unique=False)
    op.create_index(op.f('ix_session_assessment_skill_skill_id'), 'session_assessment_skill', ['skill_id'], unique=False)


def downgrade():
    op.drop_index(op.f('ix_session_assessment_skill_skill_id'), table_name='session_assessment_skill')
    op.drop_index(op.f('ix_session_assessment_skill_session_assessment_id'), table_name='session_assessment_skill')
    op.drop_index(op.f('ix_session_assessment_skill_result'), table_name='session_assessment_skill')
    op.drop_table('session_assessment_skill')

    op.drop_index(op.f('ix_session_assessment_session_id'), table_name='session_assessment')
    op.drop_index(op.f('ix_session_assessment_project_id'), table_name='session_assessment')
    op.drop_index(op.f('ix_session_assessment_assessed_by_id'), table_name='session_assessment')
    op.drop_index(op.f('ix_session_assessment_assessed_at'), table_name='session_assessment')
    op.drop_table('session_assessment')

    op.drop_index(op.f('ix_session_skill_skill_id'), table_name='session_skill')
    op.drop_table('session_skill')

    op.drop_table('atelier_project')

    op.drop_index(op.f('ix_learning_project_skill_skill_id'), table_name='learning_project_skill')
    op.drop_index(op.f('ix_learning_project_skill_project_id'), table_name='learning_project_skill')
    op.drop_index(op.f('ix_learning_project_skill_actif'), table_name='learning_project_skill')
    op.drop_table('learning_project_skill')

    op.drop_index(op.f('ix_learning_project_titre'), table_name='learning_project')
    op.drop_index(op.f('ix_learning_project_framework_id_default'), table_name='learning_project')
    op.drop_index(op.f('ix_learning_project_created_by_id'), table_name='learning_project')
    op.drop_index(op.f('ix_learning_project_actif'), table_name='learning_project')
    op.drop_table('learning_project')

    op.drop_index(op.f('ix_skill_framework_id'), table_name='skill')
    op.drop_index(op.f('ix_skill_domain_code'), table_name='skill')
    op.drop_index(op.f('ix_skill_code'), table_name='skill')
    op.drop_index(op.f('ix_skill_actif'), table_name='skill')
    op.drop_table('skill')

    op.drop_index(op.f('ix_framework_code'), table_name='framework')
    op.drop_index(op.f('ix_framework_actif'), table_name='framework')
    op.drop_table('framework')
