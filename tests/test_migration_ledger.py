"""The migration ledger (scripts/migrate.py, audit D-02): schema.sql records every migration file it includes, and
the script's own pieces work on SQLite."""
import importlib.util
import re
from pathlib import Path

from sqlalchemy import create_engine

ROOT = Path(__file__).resolve().parent.parent


def _script():
    spec = importlib.util.spec_from_file_location("migrate_under_test", ROOT / "scripts" / "migrate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_schema_sql_lists_every_migration():
    schema = (ROOT / "schema.sql").read_text(encoding="utf-8")
    listed = re.findall(r"\('(\d{4}-\d{2}-\d{2}_[\w-]+\.sql)'\)", schema.split("INSERT INTO public.schema_migrations", 1)[1])
    assert listed == sorted(p.name for p in (ROOT / "scripts" / "migrations").glob("*.sql"))


def test_pending_and_record():
    migrate = _script()
    engine = create_engine("sqlite://")
    with engine.connect() as conn:
        conn.exec_driver_sql("CREATE TABLE schema_migrations (name TEXT PRIMARY KEY, applied_utc INTEGER DEFAULT 0)")
        assert migrate.pending(conn) == migrate.files()
        migrate.record(conn, migrate.files()[0])
        migrate.record(conn, migrate.files()[0])                      # twice is harmless
        assert migrate.pending(conn) == migrate.files()[1:]
