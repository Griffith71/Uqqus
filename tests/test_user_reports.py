"""Reporting an account (ruqqus/helpers/user_reports.py): the rules, and the SQL on SQLite (once per person per
account, the daily count, the admins' queue without reporters' names, dismissing)."""
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from ruqqus.helpers import user_reports as ur

NOW = 1_800_000_000


def make():
    db = Session(create_engine("sqlite://"))
    db.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT)"))
    db.execute(text("""CREATE TABLE user_reports (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, reporter_id INTEGER NOT NULL,
        reason TEXT DEFAULT '' NOT NULL, created_utc INTEGER DEFAULT 0 NOT NULL, resolved_utc INTEGER DEFAULT 0 NOT NULL,
        UNIQUE (user_id, reporter_id))"""))
    for uid, name in ((1, "spammer"), (2, "alice"), (3, "bob"), (4, "quiet")):
        db.execute(text("INSERT INTO users (id, username) VALUES (:i, :n)"), {"i": uid, "n": name})
    return db


def test_the_rules():
    assert ur.refusal(2, 2, False) == ("You can't report your own account.", 409)
    assert ur.refusal(2, 1, True)[1] == 410
    assert ur.refusal(2, 1, False) is None
    assert ur.parse_reason("  spam\x00 bot \n links ") == ("spam bot links", None)
    assert ur.parse_reason("   ")[1] and ur.parse_reason(None)[1]
    assert len(ur.parse_reason("x" * 500)[0]) == ur.REASON_MAX


def test_once_per_person_and_the_queue_names_no_reporter():
    db = make()
    assert ur.report(db, 1, 2, "spam", NOW) and ur.report(db, 1, 3, "bot", NOW + 1)
    assert not ur.report(db, 1, 2, "again", NOW + 2)
    assert ur.report(db, 4, 2, "rude", NOW)
    queue = ur.open_reports(db)
    assert [(r["username"], r["reports"]) for r in queue] == [("spammer", 2), ("quiet", 1)]
    assert queue[0]["reasons"] == ["bot", "spam"]
    assert "reporter_id" not in queue[0] and not any("alice" in str(v) or "bob" in str(v) for v in queue[0].values())


def test_the_daily_count_and_dismissing():
    db = make()
    for target in (1, 3, 4):
        ur.report(db, target, 2, "x", NOW - 10)
    ur.report(db, 1, 3, "old", NOW - 90000)
    assert ur.filed_today(db, 2, NOW) == 3 and ur.filed_today(db, 3, NOW) == 0
    assert ur.resolve(db, 1, NOW) == 2 and ur.resolve(db, 1, NOW) == 0
    assert [r["username"] for r in ur.open_reports(db)] == ["bob", "quiet"] or [r["username"] for r in ur.open_reports(db)] == ["quiet", "bob"]
