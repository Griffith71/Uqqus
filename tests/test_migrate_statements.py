"""scripts/migrate.py sends a file that builds an index CONCURRENTLY one statement at a time (Postgres refuses that command
inside a multi-statement string) and every other file whole (a DO block holds semicolons of its own)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import migrate  # noqa: E402


def test_a_concurrent_index_file_is_split_without_its_comments():
    sql = "-- a note; with a semicolon\nCREATE INDEX CONCURRENTLY IF NOT EXISTS a ON t (x);\n\nCREATE INDEX CONCURRENTLY IF NOT EXISTS b ON t (y);\n"
    assert migrate.statements(sql) == ["CREATE INDEX CONCURRENTLY IF NOT EXISTS a ON t (x);",
                                       "CREATE INDEX CONCURRENTLY IF NOT EXISTS b ON t (y);"]


def test_any_other_file_goes_whole():
    sql = "DO $$ BEGIN PERFORM 1; PERFORM 2; END $$;\nALTER TABLE t ADD COLUMN IF NOT EXISTS c integer;\n"
    assert migrate.statements(sql) == [sql]


def test_the_real_concurrent_files_split_into_plain_statements():
    for path in sorted(migrate.MIGRATIONS.glob("*.sql")):
        text = path.read_text(encoding="utf-8")
        if "CONCURRENTLY" in text.upper():
            parts = migrate.statements(text)
            assert parts and all(p.upper().startswith("CREATE INDEX CONCURRENTLY") for p in parts), path.name
