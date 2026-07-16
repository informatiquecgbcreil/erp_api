"""add passeport notes and attachments

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-02-24 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'd4e5f6a7b8c9'
down_revision = 'c3d4e5f6a7b8'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'passeport_note',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('participant_id', sa.Integer(), nullable=False),
        sa.Column('session_id', sa.Integer(), nullable=True),
        sa.Column('secteur', sa.String(length=80), nullable=True),
        sa.Column('categorie', sa.String(length=60), nullable=False),
        sa.Column('contenu', sa.Text(), nullable=False),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['created_by'], ['user.id']),
        sa.ForeignKeyConstraint(['participant_id'], ['participant.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['session_id'], ['session_activite.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_passeport_note_categorie'), 'passeport_note', ['categorie'], unique=False)
    op.create_index(op.f('ix_passeport_note_created_at'), 'passeport_note', ['created_at'], unique=False)
    op.create_index(op.f('ix_passeport_note_participant_id'), 'passeport_note', ['participant_id'], unique=False)
    op.create_index(op.f('ix_passeport_note_secteur'), 'passeport_note', ['secteur'], unique=False)
    op.create_index(op.f('ix_passeport_note_session_id'), 'passeport_note', ['session_id'], unique=False)

    op.create_table(
        'passeport_piece_jointe',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('participant_id', sa.Integer(), nullable=False),
        sa.Column('session_id', sa.Integer(), nullable=True),
        sa.Column('secteur', sa.String(length=80), nullable=True),
        sa.Column('categorie', sa.String(length=60), nullable=False),
        sa.Column('titre', sa.String(length=255), nullable=True),
        sa.Column('file_path', sa.String(length=255), nullable=False),
        sa.Column('original_name', sa.String(length=255), nullable=False),
        sa.Column('mime_type', sa.String(length=120), nullable=True),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['created_by'], ['user.id']),
        sa.ForeignKeyConstraint(['participant_id'], ['participant.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['session_id'], ['session_activite.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_passeport_piece_jointe_categorie'), 'passeport_piece_jointe', ['categorie'], unique=False)
    op.create_index(op.f('ix_passeport_piece_jointe_created_at'), 'passeport_piece_jointe', ['created_at'], unique=False)
    op.create_index(op.f('ix_passeport_piece_jointe_participant_id'), 'passeport_piece_jointe', ['participant_id'], unique=False)
    op.create_index(op.f('ix_passeport_piece_jointe_secteur'), 'passeport_piece_jointe', ['secteur'], unique=False)
    op.create_index(op.f('ix_passeport_piece_jointe_session_id'), 'passeport_piece_jointe', ['session_id'], unique=False)


def downgrade():
    op.drop_index(op.f('ix_passeport_piece_jointe_session_id'), table_name='passeport_piece_jointe')
    op.drop_index(op.f('ix_passeport_piece_jointe_secteur'), table_name='passeport_piece_jointe')
    op.drop_index(op.f('ix_passeport_piece_jointe_participant_id'), table_name='passeport_piece_jointe')
    op.drop_index(op.f('ix_passeport_piece_jointe_created_at'), table_name='passeport_piece_jointe')
    op.drop_index(op.f('ix_passeport_piece_jointe_categorie'), table_name='passeport_piece_jointe')
    op.drop_table('passeport_piece_jointe')

    op.drop_index(op.f('ix_passeport_note_session_id'), table_name='passeport_note')
    op.drop_index(op.f('ix_passeport_note_secteur'), table_name='passeport_note')
    op.drop_index(op.f('ix_passeport_note_participant_id'), table_name='passeport_note')
    op.drop_index(op.f('ix_passeport_note_created_at'), table_name='passeport_note')
    op.drop_index(op.f('ix_passeport_note_categorie'), table_name='passeport_note')
    op.drop_table('passeport_note')
