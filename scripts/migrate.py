"""Which migrations a database has, and applying the rest (audit D-02: nothing recorded it, and the dev database once
ran 12 migrations behind the code without anyone knowing).

Every file in scripts/migrations/ is recorded in `schema_migrations` when applied. A database made from schema.sql
already lists them all.

    PYTHONPATH=. python scripts/migrate.py --status      which are applied, which are pending
    PYTHONPATH=. python scripts/migrate.py --apply       apply the pending ones, oldest first, and record each
    PYTHONPATH=. python scripts/migrate.py --baseline    record every file as applied without running anything
                                                         (once, for a database migrated by hand before the ledger)

Each file runs on its own autocommit connection, as `psql -f` would (some use CREATE INDEX CONCURRENTLY, which can't
run inside a transaction). Every migration is written to be safe to run twice, so a file that fails part way can be
fixed and run again.
"""
import os
import sys
import time
from pathlib import Path

from sqlalchemy import create_engine, text

MIGRATIONS = Path(__file__).resolve().parent / "migrations"
LEDGER = """CREATE TABLE IF NOT EXISTS schema_migrations (
    name character varying(255) PRIMARY KEY,
    applied_utc integer DEFAULT 0 NOT NULL
)"""


def files():
    return sorted(p.name for p in MIGRATIONS.glob("*.sql"))


def applied(conn):
    conn.execute(text(LEDGER))
    return {row[0] for row in conn.execute(text("SELECT name FROM schema_migrations"))}


def pending(conn):
    done = applied(conn)
    return [name for name in files() if name not in done]


def record(conn, name):
    conn.execute(text("INSERT INTO schema_migrations (name, applied_utc) VALUES (:n, :t) ON CONFLICT (name) DO NOTHING"),
                 {"n": name, "t": int(time.time())})


def main(argv):
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL is not set")
        return 2
    engine = create_engine(url, isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        if "--baseline" in argv:
            for name in files():
                record(conn, name)
            print(f"recorded {len(files())} migrations as applied")
            return 0
        todo = pending(conn)
        if "--apply" in argv:
            for name in todo:
                print(f"applying {name}")
                conn.exec_driver_sql((MIGRATIONS / name).read_text(encoding="utf-8"))
                record(conn, name)
            print("up to date" if not todo else f"applied {len(todo)}")
            return 0
        print(f"{len(files()) - len(todo)} applied, {len(todo)} pending")
        for name in todo:
            print(f"  pending: {name}")
        return 1 if todo else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
