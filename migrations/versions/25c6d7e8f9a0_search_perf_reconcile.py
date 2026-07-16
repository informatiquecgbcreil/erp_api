"""search perf reconcile after dashboard prefs

Revision ID: 25c6d7e8f9a0
Revises: 24b5c6d7e8f9
Create Date: 2026-04-08 12:30:00.000000
"""

from alembic import op
from sqlalchemy import inspect


revision = "25c6d7e8f9a0"
down_revision = "24b5c6d7e8f9"
branch_labels = None
depends_on = None


def _is_postgresql() -> bool:
    bind = op.get_bind()
    return bool(bind and bind.dialect and bind.dialect.name == "postgresql")


def _index_names(table_name: str) -> set[str]:
    bind = op.get_bind()
    return {idx.get("name") for idx in inspect(bind).get_indexes(table_name)}


def _create_index_if_missing(name: str, table_name: str, columns: list[str]):
    if name in _index_names(table_name):
        return
    op.create_index(name, table_name, columns, unique=False)


def upgrade():
    _create_index_if_missing("ix_participant_nom_prenom_search", "participant", ["nom", "prenom"])
    _create_index_if_missing("ix_participant_ville_search", "participant", ["ville"])
    _create_index_if_missing("ix_participant_email_search", "participant", ["email"])
    _create_index_if_missing("ix_participant_telephone_search", "participant", ["telephone"])
    _create_index_if_missing("ix_participant_created_secteur_search", "participant", ["created_secteur"])
    _create_index_if_missing("ix_quartier_ville_nom_search", "quartier", ["ville", "nom"])
    _create_index_if_missing("ix_projet_secteur_nom_search", "projet", ["secteur", "nom"])
    _create_index_if_missing("ix_subvention_secteur_annee_nom_search", "subvention", ["secteur", "annee_exercice", "nom"])
    _create_index_if_missing("ix_atelier_activite_secteur_nom_search", "atelier_activite", ["secteur", "nom"])
    _create_index_if_missing("ix_partenaire_nom_search", "partenaire", ["nom"])
    _create_index_if_missing("ix_partenaire_email_contact_search", "partenaire", ["email_contact"])
    _create_index_if_missing("ix_partenaire_email_general_search", "partenaire", ["email_general"])

    if _is_postgresql():
        op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        op.execute("""
            CREATE INDEX IF NOT EXISTS ix_participant_search_trgm
            ON participant
            USING gin ((lower(
                coalesce(prenom, '') || ' ' ||
                coalesce(nom, '') || ' ' ||
                coalesce(email, '') || ' ' ||
                coalesce(telephone, '') || ' ' ||
                coalesce(ville, '') || ' ' ||
                coalesce(adresse, '') || ' ' ||
                coalesce(created_secteur, '')
            )) gin_trgm_ops)
        """)
        op.execute("""
            CREATE INDEX IF NOT EXISTS ix_quartier_search_trgm
            ON quartier
            USING gin ((lower(
                coalesce(ville, '') || ' ' ||
                coalesce(nom, '') || ' ' ||
                coalesce(description, '')
            )) gin_trgm_ops)
        """)
        op.execute("""
            CREATE INDEX IF NOT EXISTS ix_projet_search_trgm
            ON projet
            USING gin ((lower(
                coalesce(nom, '') || ' ' ||
                coalesce(secteur, '') || ' ' ||
                coalesce(description, '')
            )) gin_trgm_ops)
        """)
        op.execute("""
            CREATE INDEX IF NOT EXISTS ix_subvention_search_trgm
            ON subvention
            USING gin ((lower(
                coalesce(nom, '') || ' ' ||
                coalesce(secteur, '') || ' ' ||
                coalesce(cast(annee_exercice as text), '')
            )) gin_trgm_ops)
        """)
        op.execute("""
            CREATE INDEX IF NOT EXISTS ix_atelier_search_trgm
            ON atelier_activite
            USING gin ((lower(
                coalesce(nom, '') || ' ' ||
                coalesce(secteur, '') || ' ' ||
                coalesce(description, '') || ' ' ||
                coalesce(type_atelier, '')
            )) gin_trgm_ops)
        """)
        op.execute("""
            CREATE INDEX IF NOT EXISTS ix_partenaire_search_trgm
            ON partenaire
            USING gin ((lower(
                coalesce(nom, '') || ' ' ||
                coalesce(contact_prenom, '') || ' ' ||
                coalesce(contact_nom, '') || ' ' ||
                coalesce(email_contact, '') || ' ' ||
                coalesce(email_general, '') || ' ' ||
                coalesce(tel_contact, '') || ' ' ||
                coalesce(tel_general, '') || ' ' ||
                coalesce(adresse, '') || ' ' ||
                coalesce(description, '')
            )) gin_trgm_ops)
        """)


def downgrade():
    if _is_postgresql():
        op.execute("DROP INDEX IF EXISTS ix_partenaire_search_trgm")
        op.execute("DROP INDEX IF EXISTS ix_atelier_search_trgm")
        op.execute("DROP INDEX IF EXISTS ix_subvention_search_trgm")
        op.execute("DROP INDEX IF EXISTS ix_projet_search_trgm")
        op.execute("DROP INDEX IF EXISTS ix_quartier_search_trgm")
        op.execute("DROP INDEX IF EXISTS ix_participant_search_trgm")

    for table_name, index_name in [
        ("partenaire", "ix_partenaire_email_general_search"),
        ("partenaire", "ix_partenaire_email_contact_search"),
        ("partenaire", "ix_partenaire_nom_search"),
        ("atelier_activite", "ix_atelier_activite_secteur_nom_search"),
        ("subvention", "ix_subvention_secteur_annee_nom_search"),
        ("projet", "ix_projet_secteur_nom_search"),
        ("quartier", "ix_quartier_ville_nom_search"),
        ("participant", "ix_participant_created_secteur_search"),
        ("participant", "ix_participant_telephone_search"),
        ("participant", "ix_participant_email_search"),
        ("participant", "ix_participant_ville_search"),
        ("participant", "ix_participant_nom_prenom_search"),
    ]:
        if index_name in _index_names(table_name):
            op.drop_index(index_name, table_name=table_name)
