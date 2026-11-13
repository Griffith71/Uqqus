"""The Log, slice D2: the paper trail made complete (helpers/action_log.py, migration 2026-12-30_log_paper_trail.sql). On SQLite with the
tables built from the migrations (tests/log_tables.py).

The promises: an automatic decision is public in generic words and its detail is a separate level-4 entry; a staff-only action never
reaches the public whatever the guild; an entry that failed to write is counted and never undoes the action; a mod-log row whose copy
failed is copied later with its rule and reason; a timed suspension that ran out says so once; a post author's hide of a comment names
nobody below staff; a correction is a new linked entry."""
import re
from pathlib import Path

import log_tables
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from ruqqus.helpers import action_log as log

ROOT = Path(__file__).resolve().parent.parent
MIGRATION = "2026-12-09_action_log.sql"
NOW = 1_793_000_000
DAY = 86400


def _statements():
    yield from log_tables.migration_statements((ROOT / "scripts" / "migrations" / MIGRATION).read_text(encoding="utf-8"))


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        for statement in _statements():
            conn.execute(text(statement))
        log_tables.apply(conn, modactions=True)
        conn.execute(text("CREATE TABLE submissions (id INTEGER PRIMARY KEY, author_id INTEGER, repost_id INTEGER DEFAULT 0, is_anonymous BOOLEAN DEFAULT 0)"))
        conn.execute(text("CREATE TABLE comments (id INTEGER PRIMARY KEY, author_id INTEGER, is_anonymous BOOLEAN DEFAULT 0)"))
        conn.execute(text("CREATE TABLE boards (id INTEGER PRIMARY KEY, is_private BOOLEAN DEFAULT 0, is_circle BOOLEAN DEFAULT 0)"))
        conn.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, admin_level INTEGER DEFAULT 0, is_banned INTEGER DEFAULT 0, "
                          "unban_utc INTEGER DEFAULT 0)"))
        conn.execute(text("INSERT INTO boards VALUES (1, 0, 0), (2, 1, 0), (3, 0, 1)"))
        conn.execute(text("INSERT INTO users (id, admin_level) VALUES (1, 0), (10, 0), (11, 0), (20, 3), (21, 4), (30, 0)"))
    with Session(engine) as session:
        yield session


def one(db, sql, **params):
    return db.execute(text(sql), params).first()


def column_names(sql, table):
    block = re.search(rf"CREATE TABLE (?:public\.)?{table} \((.*?)\n\);", sql, re.S)
    assert block, table
    names = set()
    for line in block.group(1).splitlines():
        line = line.strip()
        if line and not line.startswith(("CONSTRAINT", "--")):
            names.add(line.split()[0])
    return names


# --- the tables the tests build are the tables schema.sql has -------------------------------------------------------------------

def test_the_sqlite_tables_have_the_columns_schema_sql_has(db):
    schema = (ROOT / "schema.sql").read_text(encoding="utf-8")
    for table in ("log_entries", "log_failures", "log_appeals"):
        have = {row[1] for row in db.execute(text(f"PRAGMA table_info({table})"))}
        assert column_names(schema, table) == have, table


def test_the_migration_and_schema_agree_on_the_new_columns():
    migration = (ROOT / "scripts" / "migrations" / "2026-12-30_log_paper_trail.sql").read_text(encoding="utf-8")
    schema = (ROOT / "schema.sql").read_text(encoding="utf-8")
    for fragment in ("until_utc bigint DEFAULT 0 NOT NULL", "linked_entry_id bigint", "'guildmaster', 'admin', 'system', 'author'",
                     "log_appeals_once_idx", "log_failures"):
        assert fragment in migration and fragment in schema, fragment
    assert "('2026-12-30_log_paper_trail.sql')" in schema


# --- who may read an entry made without a guild --------------------------------------------------------------------------------

def test_a_decision_about_what_members_read_is_public_with_or_without_a_guild_and_a_private_guilds_is_the_guilds(db):
    for action in ("remove_broadcast_message", "community_note_add", "end_space"):
        made = log.record(db, action, 20, role=log.ADMIN, now=NOW)
        assert one(db, "SELECT audience FROM log_entries WHERE id = :i", i=made)[0] == log.PUBLIC, action
    private = log.record(db, "remove_space_message", 20, role=log.ADMIN, board_id=2, now=NOW)
    assert one(db, "SELECT audience FROM log_entries WHERE id = :i", i=private)[0] == log.GUILD


def test_what_the_staff_do_to_run_the_site_is_never_public_whatever_the_guild(db):
    for action in sorted(log.RUNNING_ACTIONS):
        for board in (None, 1):
            made = log.record(db, action, 20, role=log.ADMIN, board_id=board, now=NOW)
            assert one(db, "SELECT audience FROM log_entries WHERE id = :i", i=made)[0] == log.STAFF, (action, board)


def test_record_staff_says_what_was_done_to_the_staff_only_and_refuses_an_action_that_is_not_one(db):
    made = log.record_staff(db, "admin_word_filter", type("A", (), {"id": 20})(), "added the word 'xyzzy' (any language)")
    row = one(db, "SELECT audience, actor_role, staff_note, reason FROM log_entries WHERE id = :i", i=made)
    assert tuple(row) == (log.STAFF, "admin", "added the word 'xyzzy' (any language)", "")
    assert log.fetch(db, level=0)[0] == []                                      # the public sees nothing of it
    assert [r["id"] for r in log.fetch(db, level=3)[0]] == [made]
    with pytest.raises(ValueError):
        log.record_staff(db, "ban_user", None, "x")
    system = log.record_staff(db, "automatic_hold", None, "held at sign-up: 3 sign-ups from one network", target_user_id=30)
    assert tuple(one(db, "SELECT actor_role, actor_id FROM log_entries WHERE id = :i", i=system)) == ("system", log.SITE_ACCOUNT_ID)


# --- what the site decided by itself ------------------------------------------------------------------------------------------

def test_an_automatic_removal_is_public_in_generic_words_and_its_detail_is_a_separate_level_4_entry(db):
    db.execute(text("INSERT INTO submissions (id, author_id) VALUES (7, 30)"))
    public = log.record_automatic(db, "ban_post", post_id=7, board_id=1, rule="illegal", reason=log.AUTOMATIC_IMAGE_REASON,
                                  target_user_id=None)
    detail = log.record_detail(db, public, "the CDN answered 451 for the picture", post_id=7, board_id=1)
    first = one(db, "SELECT actor_id, actor_role, audience, rule_key, reason, staff_note FROM log_entries WHERE id = :i", i=public)
    second = one(db, "SELECT actor_role, audience, staff_note, linked_entry_id, action FROM log_entries WHERE id = :i", i=detail)
    assert tuple(first) == (log.SITE_ACCOUNT_ID, "system", log.PUBLIC, "illegal", "Automatic image check", "")
    assert tuple(second) == ("system", log.LEGAL, "the CDN answered 451 for the picture", public, "suppress_content")
    public_rows = [r["id"] for r in log.fetch(db, level=0)[0]]
    assert public_rows == [public]                                              # the detail is not the public's
    assert "451" not in str(log.fetch(db, level=0)[0])
    assert [r["id"] for r in log.fetch(db, level=3)[0]] == [public]             # nor an ordinary admin's
    assert {r["id"] for r in log.fetch(db, level=4)[0]} == {public, detail}


def test_an_automatic_detail_without_an_entry_to_attach_to_writes_nothing(db):
    assert log.record_detail(db, None, "x") is None
    assert one(db, "SELECT COUNT(*) FROM log_entries")[0] == 0


# --- a failed write is counted, and never undoes the action -------------------------------------------------------------------

def test_an_entry_that_could_not_be_written_is_counted_and_returns_none(db, monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("the table is locked")
    monkeypatch.setattr(log, "record", broken)
    assert log.record_quietly(db, "ban_user", 20, target_user_id=30) is None
    assert log.failure_count(db) == 1
    row = one(db, "SELECT action, detail FROM log_failures")
    assert row[0] == "ban_user" and "table is locked" in row[1]


def test_only_recent_failures_count_and_a_missing_table_reads_as_none(db):
    db.execute(text("INSERT INTO log_failures (created_utc, action) VALUES (:old, 'a'), (:new, 'b')"),
               {"old": NOW - 30 * DAY, "new": NOW - DAY})
    assert log.failure_count(db, now=NOW) == 1
    db.execute(text("DROP TABLE log_failures"))
    assert log.failure_count(db, now=NOW) == 0


# --- a mod-log row whose copy failed is copied later ---------------------------------------------------------------------------

def modaction(db, kind="hide_post_from_guild", user=10, board=1, post=7, note="", created=NOW - 3600):
    return db.execute(text("INSERT INTO modactions (user_id, board_id, kind, target_submission_id, target_user_id, "
                           "target_comment_id, _note, created_utc) VALUES (:u, :b, :k, :p, 0, 0, :n, :c) RETURNING id"),
                      {"u": user, "b": board, "k": kind, "p": post, "n": note, "c": created}).first()[0]


def test_annotate_copies_the_row_itself_when_its_copy_failed_and_keeps_the_rule_and_reason(db):
    mid = modaction(db, note="spam")
    assert one(db, "SELECT COUNT(*) FROM log_entries")[0] == 0                  # the after-insert copy "failed"
    entry = log.annotate(db, mid, rule="evasion", reason="Posting the same link everywhere")
    row = one(db, "SELECT action, board_id, target_submission_id, rule_key, reason, legacy_modaction_id FROM log_entries WHERE id = :i",
              i=entry)
    assert tuple(row) == ("hide_post_from_guild", 1, 7, "evasion", "Posting the same link everywhere", mid)
    assert log.annotate(db, mid, rule="evasion") == entry                       # asked again: the same entry, no second copy
    assert one(db, "SELECT COUNT(*) FROM log_entries")[0] == 1


def test_annotate_of_a_row_that_does_not_exist_is_none(db):
    assert log.annotate(db, 999, rule="evasion") is None


def test_reconcile_copies_old_rows_with_no_entry_once_and_leaves_a_row_being_made_right_now(db):
    old, fresh, copied = modaction(db, created=NOW - 3600), modaction(db, created=NOW - 10), modaction(db, created=NOW - 3600)
    log.mirror_from_table(db, copied)
    assert log.unmirrored_count(db, now=NOW) == 1
    assert log.reconcile(db, now=NOW) == (1, 0)
    assert one(db, "SELECT COUNT(*) FROM log_entries WHERE legacy_modaction_id = :i", i=old)[0] == 1
    assert one(db, "SELECT COUNT(*) FROM log_entries WHERE legacy_modaction_id = :i", i=fresh)[0] == 0
    assert log.reconcile(db, now=NOW) == (0, 0)                                 # run twice: nothing more
    assert log.reconcile(db, now=NOW + 3600) == (1, 0)                          # a row that has waited long enough is copied
    assert log.unmirrored_count(db, now=NOW + 3600) == 0


def test_reconcile_counts_a_row_it_cannot_copy_and_goes_on_with_the_rest(db, monkeypatch):
    first, second = modaction(db), modaction(db)
    real = log.mirror_from_table

    def flaky(conn, modaction_id):
        if modaction_id == first:
            raise RuntimeError("boom")
        return real(conn, modaction_id)
    monkeypatch.setattr(log, "mirror_from_table", flaky)
    assert log.reconcile(db, now=NOW) == (1, 1)
    assert one(db, "SELECT COUNT(*) FROM log_entries WHERE legacy_modaction_id = :i", i=second)[0] == 1
    assert log.failure_count(db, now=NOW + 10) == 1


# --- a timed suspension that ran out says so, once ----------------------------------------------------------------------------

def suspend(db, user=30, days=7, at=NOW - 10 * DAY, **kw):
    made = log.record_ban(db, user, 20, days=days, reason="Spamming links", rule="evasion", until_utc=at + days * DAY, **kw)
    db.execute(text("UPDATE log_entries SET created_utc = :t WHERE id = :i"), {"t": at, "i": made})
    db.execute(text("UPDATE users SET is_banned = 20, unban_utc = :u WHERE id = :id"), {"u": at + days * DAY, "id": user})
    return made


def test_a_suspension_keeps_its_end_and_a_ban_keeps_none(db):
    timed = log.record_ban(db, 30, 20, days=7, reason="x", until_utc=NOW + 7 * DAY)
    permanent = log.record_ban(db, 11, 20, days=0, reason="x")
    assert one(db, "SELECT action, until_utc FROM log_entries WHERE id = :i", i=timed)[1] == NOW + 7 * DAY
    assert tuple(one(db, "SELECT action, until_utc FROM log_entries WHERE id = :i", i=permanent)) == ("ban_user", 0)


def test_a_lapsed_suspension_writes_one_system_entry_linked_to_it_and_as_private_as_it_was(db):
    public = suspend(db)
    staff_only = suspend(db, user=11, floor=log.STAFF)
    assert log.lapse_due(db, now=NOW) == 2
    lapse = one(db, "SELECT actor_role, actor_id, audience, reason, created_utc FROM log_entries WHERE linked_entry_id = :i", i=public)
    assert tuple(lapse) == ("system", log.SITE_ACCOUNT_ID, log.PUBLIC, "The suspension ended.", NOW - 3 * DAY)
    assert one(db, "SELECT audience FROM log_entries WHERE linked_entry_id = :i", i=staff_only)[0] == log.STAFF
    assert log.lapse_due(db, now=NOW) == 0                                      # one per suspension, however often it runs


def test_nothing_lapses_before_its_end_or_over_a_later_decision_or_while_the_account_is_suspended_still(db):
    early = suspend(db, user=30, days=7, at=NOW - 2 * DAY)                       # ends in five days
    lifted = suspend(db, user=11, days=1)
    log.record_unban(db, 11, 20)                                                # an admin lifted it: that is its end
    extended = suspend(db, user=10, days=1)
    db.execute(text("UPDATE users SET unban_utc = :u WHERE id = 10"), {"u": NOW + DAY})     # a parent's longer suspension moved it
    assert log.lapse_due(db, now=NOW) == 0
    assert one(db, "SELECT COUNT(*) FROM log_entries WHERE linked_entry_id IN (:a, :b, :c)", a=early, b=lifted, c=extended)[0] == 0
    db.execute(text("UPDATE users SET unban_utc = :u WHERE id = 10"), {"u": NOW - 1})
    assert log.lapse_due(db, now=NOW) == 1                                      # and it lapses when the real end has passed


def test_a_suspension_made_permanent_does_not_lapse(db):
    first = suspend(db, user=30)
    log.record_ban(db, 30, 20, days=0, reason="again")                          # a ban over it
    assert log.lapse_due(db, now=NOW) == 0
    assert one(db, "SELECT COUNT(*) FROM log_entries WHERE linked_entry_id = :i", i=first)[0] == 0


# --- the author role and corrections -------------------------------------------------------------------------------------------

def test_a_post_authors_hide_of_a_comment_names_nobody_below_staff_and_cannot_be_found_by_who(db):
    made = log.record(db, "author_hide_comment", 10, role=log.AUTHOR, board_id=1, now=NOW, reason="off topic")
    public = log.fetch(db, level=0)[0]
    assert [r["id"] for r in public] == [made] and public[0]["actor_id"] is None and public[0]["actor_role"] == "author"
    assert log.fetch(db, level=0, actor_id=10)[0] == []                         # filtering by their id would list exactly their hides
    assert log.fetch(db, level=3, actor_id=10)[0][0]["actor_id"] == 10          # staff see who
    assert log.actor_label({"actor_role": "author"}, 0) == "author"


def test_a_correction_hides_the_reason_and_is_a_new_linked_staff_entry_not_an_edit(db):
    original = log.record(db, "ban_user", 20, target_user_id=30, reason="a name that should not be public", role=log.ADMIN, now=NOW)
    note = log.correct(db, original, type("A", (), {"id": 21})(), "a private detail slipped into the reason")
    assert one(db, "SELECT redacted_by, reason FROM log_entries WHERE id = :i", i=original)[0] == 21
    assert log.fetch(db, level=0)[0][0]["reason"] == ""                         # the public reads nothing of it
    row = one(db, "SELECT action, audience, linked_entry_id, staff_note, actor_role FROM log_entries WHERE id = :i", i=note)
    assert tuple(row) == ("redact_entry", log.STAFF, original, "a private detail slipped into the reason", "admin")
    assert one(db, "SELECT COUNT(*) FROM log_entries")[0] == 2                  # the original stays, the correction is added
