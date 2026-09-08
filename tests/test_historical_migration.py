"""The additive Alembic migration can be upgraded and downgraded without deleting business records."""
import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
import sqlalchemy as sa


def test_historical_schema_round_trip_preserves_existing_rows(tmp_path):
    path = Path(__file__).parents[1] / "migrations/versions/ab72cd34ef56_historical_import.py"
    spec = importlib.util.spec_from_file_location("historical_revision", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine("sqlite:///" + (tmp_path / "schema.db").as_posix())
    try:
        with engine.begin() as connection:
            for table in ("user", "participant", "atelier_activite", "session_activite", "presence_activite"):
                connection.exec_driver_sql(f'CREATE TABLE "{table}" (id INTEGER PRIMARY KEY, legacy_value TEXT)')
                connection.exec_driver_sql(f'INSERT INTO "{table}" (id, legacy_value) VALUES (1, \'préservé\')')
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                assert "historical_import_batch" in sa.inspect(connection).get_table_names()
                assert "annee_naissance" in {c["name"] for c in sa.inspect(connection).get_columns("participant")}
                migration.downgrade()
                assert "historical_import_batch" not in sa.inspect(connection).get_table_names()
                assert "annee_naissance" not in {c["name"] for c in sa.inspect(connection).get_columns("participant")}
                assert connection.exec_driver_sql("SELECT legacy_value FROM participant").scalar_one() == "préservé"
                migration.upgrade()
                assert connection.exec_driver_sql("SELECT legacy_value FROM session_activite").scalar_one() == "préservé"
    finally:
        engine.dispose()
