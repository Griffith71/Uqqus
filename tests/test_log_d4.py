"""The Log, slice D4: order, indexes, the word filter's rating of the Log's own words, the cleanup of private copies and what the export gives
(the audit's M5, M6, M7, M8, S7, S8 and L4).

The promises: the public Log is ordered by when a thing happened, not by the id a backfill gave it, and the older-entries cursor still works on
that order; the pages about one account read through the indexes there are; a reason a reader's word filter hides is not drawn to that reader
(the guild's log and the history page too); closing an account empties what it erased itself and leaves what is kept by a duty and other people's
records; the export holds what the account may be given; every action that must say why is refused with no reason by the route that makes it;
and the image scan's callbacks never add the request's own objects to a second session."""
import re
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from ruqqus.helpers import account_cleanup, action_log as log, data_export

import log_tables

ROOT = Path(__file__).resolve().parent.parent
NOW = 1_793_000_000
DAY = 86400


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        for statement in log_tables.migration_statements(read("scripts", "migrations", "2026-12-09_action_log.sql")):
            conn.execute(text(statement))
        log_tables.apply(conn)
        conn.execute(text("CREATE TABLE submissions (id INTEGER PRIMARY KEY, author_id INTEGER, repost_id INTEGER DEFAULT 0, is_anonymous BOOLEAN DEFAULT 0)"))
        conn.execute(text("CREATE TABLE comments (id INTEGER PRIMARY KEY, author_id INTEGER, is_anonymous BOOLEAN DEFAULT 0)"))
        conn.execute(text("CREATE TABLE boards (id INTEGER PRIMARY KEY, is_private BOOLEAN DEFAULT 0, is_circle BOOLEAN DEFAULT 0)"))
        conn.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, admin_level INTEGER DEFAULT 0)"))
        conn.execute(text("CREATE TABLE contributors (id INTEGER PRIMARY KEY, user_id INTEGER, board_id INTEGER, is_active BOOLEAN)"))
        conn.execute(text("CREATE TABLE mods (id INTEGER PRIMARY KEY, user_id INTEGER, board_id INTEGER, invite_rescinded BOOLEAN)"))
        conn.execute(text("CREATE TABLE content_edit_history (id INTEGER PRIMARY KEY, reason VARCHAR(256), summary VARCHAR(256), "
                          "text_severity SMALLINT NOT NULL DEFAULT 0)"))
        conn.execute(text("CREATE INDEX submissions_author_idx ON submissions (author_id)"))      # schema.sql has them: the plan below starts from the author
        conn.execute(text("CREATE INDEX comments_author_idx ON comments (author_id)"))
        conn.execute(text("INSERT INTO boards VALUES (1, 0, 0), (2, 1, 0), (3, 0, 1)"))
        conn.execute(text("INSERT INTO users VALUES (1, 0), (10, 0), (11, 0), (20, 3), (21, 4), (30, 0)"))
    with Session(engine) as session:
        yield session


def entry(db, action="hide_post_from_guild", actor=10, board=1, **kw):
    kw.setdefault("now", NOW)
    return log.record(db, action, actor, board_id=board, **kw)


# --- M8: the Log's own words go through the word filter ---------------------------------------------------------------------------

PROFANE = "this is fucking nonsense"


def test_a_reason_is_rated_when_it_is_written_and_a_clean_one_is_not(db):
    clean = entry(db, reason="No jokes about the moderators, please.")
    rude = entry(db, reason=PROFANE)
    rating = lambda e: db.execute(text("SELECT reason_severity FROM log_entries WHERE id = :i"), {"i": e}).scalar()
    assert rating(clean) == 0
    assert rating(rude) >= 1, "a profane reason carries the rating, which the reader's filter level then hides"
    assert log.rate(db, "") == 0 and log.rate(db, None) == 0


def test_a_reason_added_afterwards_is_rated_too(db):
    e = entry(db)
    db.execute(text("UPDATE log_entries SET legacy_modaction_id = 7 WHERE id = :e"), {"e": e})
    log.annotate(db, 7, reason=PROFANE)
    assert db.execute(text("SELECT reason_severity FROM log_entries WHERE id = :i"), {"i": e}).scalar() >= 1
    log.annotate(db, 7, reason="A calm reason.")
    assert db.execute(text("SELECT reason_severity FROM log_entries WHERE id = :i"), {"i": e}).scalar() == 0


def test_the_guilds_log_leaves_out_a_reason_the_readers_filter_hides(db):
    rude = entry(db, reason=PROFANE)
    calm = entry(db, reason="Off topic.")
    db.execute(text("UPDATE log_entries SET legacy_modaction_id = id + 100"))
    ids = [rude + 100, calm + 100]
    child = log.notes_for(db, ids, word_level=2)
    standard = log.notes_for(db, ids, word_level=1)
    off = log.notes_for(db, ids, word_level=0)
    assert child[rude + 100]["reason"] == "" and child[calm + 100]["reason"] == "Off topic."
    assert off[rude + 100]["reason"] == PROFANE, "a reader with the filter off sees it"
    assert standard[rude + 100]["reason"] in ("", PROFANE)          # Standard hides only the extreme words
    assert log.notes_for(db, ids)[rude + 100]["reason"] == PROFANE, "no level named: the caller is staff or draws no member text"


def test_the_rescan_rates_the_rows_again_and_counts_what_changed(db):
    e = entry(db, reason="Off topic.")
    db.execute(text("UPDATE log_entries SET reason = :r WHERE id = :e"), {"r": PROFANE, "e": e})
    db.execute(text("INSERT INTO content_edit_history (id, reason, summary, text_severity) VALUES (1, NULL, 'fixed a typo', 2), (2, NULL, :s, 0)"), {"s": PROFANE})
    changed = log.rescan_severities(db, lambda words: 1 if "fucking" in words else 0, batch=1)        # a batch of one reads every row by its cursor
    assert changed == 3
    assert db.execute(text("SELECT reason_severity FROM log_entries WHERE id = :e"), {"e": e}).scalar() == 1
    assert [r[0] for r in db.execute(text("SELECT text_severity FROM content_edit_history ORDER BY id"))] == [0, 1]
    assert log.rescan_severities(db, lambda words: 1 if "fucking" in words else 0) == 0, "run again, nothing changes"


def test_the_history_models_before_insert_hook_rates_reason_and_summary_together():
    source = read("ruqqus", "classes", "content_history.py")
    assert '@event.listens_for(ContentEditHistory, "before_insert")' in source and "target.text_severity" in source
    assert "words_shown(entry.text_severity, v)" in read("ruqqus", "templates", "content_history.html")
    assert source.count("text_severity") >= 3


# --- M6: the order of the Log -------------------------------------------------------------------------------------------------------

def test_the_log_is_in_the_order_things_happened_not_the_order_they_were_written(db):
    # a backfill run after live entries exist gives the OLD entries the higher ids
    live = entry(db, now=NOW + 3 * DAY)
    backfilled_a = entry(db, now=NOW + 1 * DAY)
    backfilled_b = entry(db, now=NOW + 2 * DAY)
    rows, _ = log.fetch(db, level=0)
    assert [r["id"] for r in rows] == [live, backfilled_b, backfilled_a]


def test_the_older_entries_cursor_walks_that_order_without_skipping_or_repeating(db):
    ids = [entry(db, now=NOW + n * 60) for n in (5, 1, 9, 3, 7, 2, 8, 4, 6, 0)]          # ids 1..10, times out of order
    seen, cursor = [], None
    for _ in range(10):
        rows, more = log.fetch(db, level=0, limit=3, before_id=cursor)
        seen += [r["id"] for r in rows]
        if not more:
            break
        cursor = rows[-1]["id"]
    by_time = [i for _, i in sorted(((NOW + n * 60, i) for n, i in zip((5, 1, 9, 3, 7, 2, 8, 4, 6, 0), ids)), reverse=True)]
    assert seen == by_time and len(set(seen)) == 10


def test_two_entries_in_one_second_keep_a_stable_order_by_id(db):
    a, b, c = (entry(db, now=NOW) for _ in range(3))
    rows, _ = log.fetch(db, level=0)
    assert [r["id"] for r in rows] == [c, b, a]
    page, more = log.fetch(db, level=0, limit=1, before_id=c)
    assert [r["id"] for r in page] == [b] and more is True


# --- M7: the pages about one account read through indexes ---------------------------------------------------------------------------

def test_the_account_scope_is_a_union_of_three_indexed_reads_and_has_the_meaning_it_had(db):
    db.execute(text("INSERT INTO submissions (id, author_id, is_anonymous) VALUES (1, 30, 0), (2, 30, 1), (3, 31, 0)"))
    db.execute(text("INSERT INTO comments (id, author_id, is_anonymous) VALUES (1, 30, 0), (2, 30, 1), (3, 31, 0)"))
    about_user = entry(db, action="exile_user", target_user_id=30)
    on_post = entry(db, post_id=1)
    on_anonymous_post = entry(db, post_id=2)
    on_comment = entry(db, action="hide_comment_from_guild", comment_id=1)
    on_anonymous_comment = entry(db, action="hide_comment_from_guild", comment_id=2)
    someone_elses = entry(db, post_id=3)
    sql, params = log.about_scope(user_id=30)
    shown, _ = log.fetch(db, level=0, scope=(sql, params))
    assert {r["id"] for r in shown} == {about_user, on_post, on_comment}, "an anonymous item is left out of an account's log"
    everything, _ = log.fetch(db, level=3, scope=log.about_scope(user_id=30, include_anonymous=True))
    assert {r["id"] for r in everything} == {about_user, on_post, on_anonymous_post, on_comment, on_anonymous_comment}
    assert someone_elses not in {r["id"] for r in everything}
    plan = " ".join(str(r[-1]) for r in db.execute(text("EXPLAIN QUERY PLAN SELECT id FROM log_entries WHERE " + sql), params))
    assert "log_entries_target_user_idx" in plan and "log_entries_post_idx" in plan and "log_entries_comment_idx" in plan, plan


def test_the_migration_and_schema_carry_the_indexes_and_columns():
    migration = read("scripts", "migrations", "2026-12-31_log_d4.sql")
    schema = read("schema.sql")
    for name in ("log_entries_post_idx", "log_entries_comment_idx", "log_entries_order_idx"):
        assert f"CREATE INDEX IF NOT EXISTS {name}" in migration and f"CREATE INDEX {name} ON public.log_entries" in schema
    assert "ADD COLUMN IF NOT EXISTS reason_severity" in migration and "reason_severity smallint DEFAULT 0 NOT NULL" in schema
    assert "ADD COLUMN IF NOT EXISTS text_severity" in migration and "text_severity smallint DEFAULT 0 NOT NULL" in schema
    assert "('2026-12-31_log_d4.sql')" in schema
    assert "DROP INDEX" not in migration, "an older migration creates log_entries_created_idx: dropping it breaks 'safe to run twice'"


# --- M5: closing an account, and the export ----------------------------------------------------------------------------------------

def _snapshot(db, kind, author=30, legal=False):
    class Item:
        author_id, board_id, title, url, body, body_html = author, 1, "A title", None, "words " + kind, "<p>words</p>"
    snapshot = log.snapshot_of(Item(), legal_hold=legal, kind=None if legal else kind, hider_id=11 if kind == log.AUTHOR_HIDE else None)
    return log.keep_snapshot(db, snapshot, now=NOW)


def test_closing_an_account_empties_the_copies_of_what_it_erased_and_keeps_the_rest(db):
    own = _snapshot(db, log.GRACE)
    guild_hide = _snapshot(db, log.HIDE)
    author_hide = _snapshot(db, log.AUTHOR_HIDE)
    legal = _snapshot(db, log.GRACE, legal=True)
    someone_elses = _snapshot(db, log.GRACE, author=31)
    step = dict(account_cleanup.STEPS)["empty their private copies"]
    db.execute(text(step), {"u": 30, "now": NOW + DAY})
    body = lambda i: db.execute(text("SELECT body FROM log_snapshots WHERE id = :i"), {"i": i}).scalar()
    assert body(own) is None, "what they erased themselves goes with the account"
    assert body(guild_hide) and body(author_hide), "other people's record of a decision empties on its own date"
    assert body(legal), "a legal hold is kept by a duty"
    assert body(someone_elses), "another account's copy is not touched"
    assert db.execute(text("SELECT scrubbed_utc FROM log_snapshots WHERE id = :i"), {"i": own}).scalar() == NOW + DAY


def test_the_export_holds_what_the_log_says_about_them_and_their_own_copies_never_staff_entries_or_legal_holds(db):
    public = log.record(db, "exile_user", 10, board_id=1, target_user_id=30, reason="Spam links.", now=NOW)
    hidden = log.record(db, "suspend_user", 20, target_user_id=30, reason="A reason an owner hid.", role=log.ADMIN, now=NOW)
    db.execute(text("UPDATE log_entries SET redacted_utc = :n WHERE id = :e"), {"n": NOW, "e": hidden})
    staff = log.record(db, "age_lock", 20, target_user_id=30, role=log.ADMIN, floor=log.STAFF, staff_note="seen", now=NOW)
    by_alias = log.record(db, "exile_alias", 10, board_id=1, reason="Harassment.", now=NOW)           # an exile made by alias names no account: the subject is theirs
    db.execute(text("UPDATE log_entries SET alias_n = 2, subject_user_id = 30 WHERE id = :e"), {"e": by_alias})
    someone_elses = log.record(db, "exile_alias", 10, board_id=1, reason="Spam.", now=NOW)
    db.execute(text("UPDATE log_entries SET alias_n = 3, subject_user_id = 31 WHERE id = :e"), {"e": someone_elses})
    _snapshot(db, log.GRACE)
    _snapshot(db, log.GRACE, legal=True)
    sections = dict(data_export.SECTIONS)
    about = [dict(r._mapping) for r in db.execute(text(sections["log_entries_about_you"]), {"u": 30})]
    assert [r["id"] for r in about] == [public, hidden, by_alias], "a staff-only entry is not theirs to be given, one made by alias about them is"
    assert about[2]["alias_n"] == 2 and someone_elses not in [r["id"] for r in about], "and never another account's"
    assert about[1]["reason"] == "" and about[0]["reason"] == "Spam links.", "a reason an owner hid stays hidden"
    assert staff not in [r["id"] for r in about]
    copies = [dict(r._mapping) for r in db.execute(text(sections["private_copies"]), {"u": 30})]
    assert len(copies) == 1 and copies[0]["kind"] == log.GRACE and copies[0]["body"].startswith("words"), "never a copy kept for a legal duty"


# --- L4 / S7 / S8: the source says what the code does -----------------------------------------------------------------------------

# every action in REASON_REQUIRED, and the route that refuses it with no reason (the text it answers)
ENFORCED = {
    "hide_post_from_guild": ("ruqqus/routes/boards.py", "A reason is required."),
    "hide_comment_from_guild": ("ruqqus/routes/comments.py", "A reason is required."),
    "author_hide_comment": ("ruqqus/routes/comments.py", "A reason is required."),
    "ban_post": ("ruqqus/routes/admin_api.py", "A reason is required."),
    "ban_comment": ("ruqqus/routes/admin_api.py", "A reason is required."),
    "suspend_user": ("ruqqus/routes/admin_api.py", "A suspension or ban needs a public reason"),
    "ban_user": ("ruqqus/routes/admin_api.py", "A suspension or ban needs a public reason"),
    "ban_guild": ("ruqqus/routes/admin_api.py", "A guild ban needs a public reason"),
    "suppress_content": ("ruqqus/routes/admin_api.py", "not reason or reason_category not in"),
    "hide_revision": ("ruqqus/routes/admin_api.py", "A reason is required."),
}


def test_every_action_that_must_say_why_is_refused_with_no_reason_by_a_route_and_nothing_else_is_listed():
    assert set(ENFORCED) == log.REASON_REQUIRED, "REASON_REQUIRED says only what a route really enforces"
    for action, (path, words) in ENFORCED.items():
        assert words in read(path), (action, path)
    assert "exile_user" not in log.REASON_REQUIRED


def test_the_scan_callbacks_find_the_item_again_in_their_own_session():
    posts = read("ruqqus", "routes", "posts.py")
    assert not re.search(r"(?<![\w.])db\.add\(new_post\)", posts), \
        "a callback that adds the request's own post to a second session fails while the request is open (g.db.add is the request's own)"
    for needle in ("scanned_post_id = new_post.id", "banned = db.get(Submission, scanned_post_id)",
                   "scanned_body_post_id = new_post.id", "banned = db.get(Submission, scanned_body_post_id)"):
        assert needle in posts, needle


def test_the_dead_code_is_gone():
    aws = read("ruqqus", "helpers", "aws.py")
    assert "def check_csam(" not in aws and "_load_post_and_author" not in aws and "def check_csam_url(" in aws
    for path in ("ruqqus", "tests", "scripts"):
        for source in (ROOT / path).rglob("*.py"):
            if source.name == "test_log_d4.py":
                continue
            assert "legal_removal" not in source.read_text(encoding="utf-8"), source
    assert re.search(r"def scrub_due\(", read("ruqqus", "helpers", "action_log.py")) and "SCRUB_DUE" in read("ruqqus", "helpers", "retention.py")
