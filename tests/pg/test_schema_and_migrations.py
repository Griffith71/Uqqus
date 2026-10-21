"""schema.sql, the migration ledger and the migration files, against a real Postgres. The project's rule is that every
migration file is safe to run twice; until now nothing checked it, and the files were only ever applied by hand."""
import sys
from pathlib import Path

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import migrate  # noqa: E402


def shape(conn):
    """Every table's columns and every index: what a migration run twice must leave unchanged."""
    columns = conn.execute(text(
        "SELECT table_name, column_name, data_type, is_nullable, coalesce(column_default, '') "
        "FROM information_schema.columns WHERE table_schema = 'public' ORDER BY 1, 2")).all()
    indexes = conn.execute(text(
        "SELECT tablename, indexname, indexdef FROM pg_indexes WHERE schemaname = 'public' ORDER BY 1, 2")).all()
    constraints = conn.execute(text(
        "SELECT conrelid::regclass::text, conname, pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE connamespace = 'public'::regnamespace ORDER BY 1, 2")).all()
    return [tuple(r) for r in columns], [tuple(r) for r in indexes], [tuple(r) for r in constraints]


def test_a_database_made_from_schema_sql_lists_every_migration(pg_engine):
    with pg_engine.connect() as conn:
        assert migrate.pending(conn) == []


def test_every_migration_file_can_run_again_and_changes_nothing(pg_engine):
    with pg_engine.connect() as conn:
        before = shape(conn)
        for name in migrate.files():
            try:
                migrate.run_file(conn, name)
            except Exception as e:                                       # name the file, not just the driver's error
                raise AssertionError(f"{name} failed when run a second time: {str(e).splitlines()[0]}") from e
        after = shape(conn)
    for label, was, now in zip(("column", "index", "constraint"), before, after):
        added, gone = sorted(set(now) - set(was)), sorted(set(was) - set(now))
        assert not added and not gone, f"a migration run twice changed a {label}: added {added[:5]}, removed {gone[:5]}"


def test_the_ledger_still_lists_exactly_the_migration_files(pg_engine):
    with pg_engine.connect() as conn:
        recorded = {r[0] for r in conn.execute(text("SELECT name FROM schema_migrations"))}
    assert recorded == set(migrate.files())
