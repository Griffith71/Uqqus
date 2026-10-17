"""Deleting an account (helpers/account_cleanup.py, audit I-05): every step runs against the real tables from
schema.sql, ends what would keep running and removes what is personal; a missing table never stops the rest."""
import re
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from ruqqus.helpers import account_cleanup

ROOT = Path(__file__).resolve().parent.parent


def _tables(names):
    schema = (ROOT / "schema.sql").read_text(encoding="utf-8")
    for name in names:
        block = re.search(rf"CREATE TABLE public\.{name} \((.*?)\n\);", schema, re.S).group(1)
        cols = []
        for line in block.splitlines():
            line = line.strip().rstrip(",")
            if not line or line.startswith(("CONSTRAINT", "PRIMARY KEY", "UNIQUE")):
                continue
            col, *rest = line.split()
            cols.append(f"{col} TEXT" if col not in ("id",) else "id INTEGER PRIMARY KEY")
        yield f"CREATE TABLE {name} ({', '.join(cols)})"


def test_every_step_matches_the_schema_and_does_its_job():
    names = {re.search(r"(?:FROM|UPDATE|INTO)\s+(\w+)", sql).group(1) for _, sql in account_cleanup.STEPS}
    names.add("circles")
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        for statement in _tables(sorted(names)):
            conn.execute(text(statement))
        conn.execute(text("INSERT INTO users (id, email, display_name, bio) VALUES (5, 'a@b.c', 'Ann', 'hi'), (6, 'x@y.z', 'Bob', '')"))
        conn.execute(text("INSERT INTO login_events (id, user_id, ip) VALUES (1, 5, '1.2.3.4'), (2, 6, '5.6.7.8')"))
        conn.execute(text("INSERT INTO circle_members (id, circle_id, user_id, tier, status, cancelled) VALUES (1, 9, 5, 'subscriber', 'active', 0)"))
        conn.execute(text("INSERT INTO chat_identities (user_id, key_blob_password) VALUES (5, 'locked')"))
    with Session(engine) as db:
        skipped = account_cleanup.close_account(db, 5, 1000)
        assert skipped == []                                    # every step fits the schema's columns
        assert db.execute(text("SELECT email, display_name, bio FROM users WHERE id = 5")).fetchone() == (None, "", "")
        assert db.execute(text("SELECT email FROM users WHERE id = 6")).scalar() == "x@y.z"   # nobody else touched
        assert db.execute(text("SELECT COUNT(*) FROM login_events WHERE user_id = 5")).scalar() == 0
        assert db.execute(text("SELECT status FROM circle_members WHERE id = 1")).scalar() == "ended"
        assert db.execute(text("SELECT key_blob_password FROM chat_identities WHERE user_id = 5")).scalar() is None


def test_a_missing_table_is_skipped_not_fatal():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE login_events (id INTEGER PRIMARY KEY, user_id INTEGER)"))
        conn.execute(text("INSERT INTO login_events VALUES (1, 5)"))
    with Session(engine) as db:
        skipped = account_cleanup.close_account(db, 5, 1000)
        assert "forget sign-in addresses" not in skipped and len(skipped) == len(account_cleanup.STEPS) - 1
        assert db.execute(text("SELECT COUNT(*) FROM login_events")).scalar() == 0


def test_delete_account_runs_it():
    settings = (ROOT / "ruqqus" / "routes" / "settings.py").read_text(encoding="utf-8")
    body = settings.split("def delete_account(", 1)[1].split("\n@app", 1)[0]
    assert "account_cleanup.close_account(g.db, v.id, int(time.time()))" in body and "_leave_chat_groups(v)" in body


def test_the_data_export_reads_every_section_from_the_schema():
    """Download your data (helpers/data_export.py, audit I-06): every section's query fits schema.sql."""
    from ruqqus.helpers import data_export
    names = set()
    for _, sql in data_export.SECTIONS:
        names |= set(re.findall(r"(?:FROM|JOIN)\s+(\w+)", sql))
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        for statement in _tables(sorted(names)):
            conn.execute(text(statement))
        conn.execute(text("INSERT INTO users (id, username, email) VALUES (5, 'ann', 'a@b.c'), (6, 'bob', 'x@y.z')"))
        conn.execute(text("INSERT INTO login_events (id, user_id, ip) VALUES (1, 5, '1.2.3.4'), (2, 6, '9.9.9.9')"))
    with Session(engine) as db:
        data = data_export.build(db, 5)
    assert set(data) == {"note"} | {name for name, _ in data_export.SECTIONS}       # no section failed
    assert data["account"][0]["email"] == "a@b.c" and [r["ip"] for r in data["sign_ins"]] == ["1.2.3.4"]
