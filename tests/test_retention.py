"""helpers/retention.py (audit D-08): shared-draft edits, deleted live updates and old view counts are not kept
forever, and nothing a browser or a page still needs goes."""
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from ruqqus.helpers import retention
from ruqqus.helpers.collab_rules import RESYNC_AFTER

NOW = 2_000_000_000
DAY = 86400


@pytest.fixture
def db():
    session = Session(create_engine("sqlite://"))
    for ddl in (
        "CREATE TABLE collab_docs (id INTEGER PRIMARY KEY, revision INTEGER, updated_utc INTEGER, deleted_utc INTEGER)",
        "CREATE TABLE collab_ops (id INTEGER PRIMARY KEY, doc_id INTEGER, revision INTEGER)",
        "CREATE TABLE live_updates (id INTEGER PRIMARY KEY, body TEXT, body_html TEXT, deleted_utc INTEGER)",
        "CREATE TABLE post_view_days (id INTEGER PRIMARY KEY, post_id INTEGER, day INTEGER, views INTEGER)",
    ):
        session.execute(text(ddl))
    session.commit()
    return session


def revisions(db, doc):
    return [r[0] for r in db.execute(text("SELECT revision FROM collab_ops WHERE doc_id = :d ORDER BY revision"),
                                     {"d": doc})]


def test_a_busy_draft_keeps_what_a_browser_can_still_ask_for(db):
    top = RESYNC_AFTER + 20
    db.execute(text("INSERT INTO collab_docs VALUES (1, :r, :u, 0)"), {"r": top, "u": NOW})
    for r in range(1, top + 1):
        db.execute(text("INSERT INTO collab_ops (doc_id, revision) VALUES (1, :r)"), {"r": r})
    retention.prune(db, NOW)
    assert revisions(db, 1) == list(range(top - RESYNC_AFTER + 1, top + 1))       # every op after the oldest base


def test_quiet_closed_and_vanished_drafts_keep_no_edits(db):
    db.execute(text("INSERT INTO collab_docs VALUES (2, 3, :u, 0), (3, 3, :n, 5)"), {"u": NOW - 31 * DAY, "n": NOW})
    db.execute(text("INSERT INTO collab_ops (doc_id, revision) VALUES (2, 1), (2, 2), (3, 1), (9, 1)"))
    retention.prune(db, NOW)
    assert revisions(db, 2) == revisions(db, 3) == revisions(db, 9) == []


def test_deleted_live_updates_and_old_view_counts(db):
    db.execute(text("INSERT INTO live_updates VALUES (1, 'a', '<p>a</p>', :old), (2, 'b', '<p>b</p>', :new), "
                    "(3, 'c', '<p>c</p>', 0)"), {"old": NOW - 91 * DAY, "new": NOW - DAY})
    today = NOW // DAY
    db.execute(text("INSERT INTO post_view_days (post_id, day, views) VALUES (1, :a, 1), (1, :b, 1)"),
               {"a": today - 401, "b": today - 28})
    done = retention.prune(db, NOW)
    assert [r[0] for r in db.execute(text("SELECT body FROM live_updates ORDER BY id"))] == ["", "b", "c"]
    assert [r[0] for r in db.execute(text("SELECT day FROM post_view_days"))] == [today - 28]
    assert done["old view counts"] == 1


def test_a_missing_table_stops_nothing():
    session = Session(create_engine("sqlite://"))
    session.execute(text("CREATE TABLE post_view_days (id INTEGER PRIMARY KEY, post_id INTEGER, day INTEGER)"))
    done = retention.prune(session, NOW)
    assert done["old shared-draft edits"] == -1 and done["old view counts"] == 0
