"""The Log's entries for what an admin's mass decision reached (helpers/log_cascade.py, on SQLite with the tables built from the migrations).

The promises (the second review, C79): every item an obliteration's cascade or a nuke removes has an entry of its own, linked to the cascade's main entry,
with the audience `action_log.record` would have given it and never an account; a re-run writes nothing twice; the work is set-based (a few statements
for thousands of items); a failure is counted and undoes nothing else; and the readers (an item's log, an account's log, a guild's own log) find them."""
import re
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session

from ruqqus.helpers import action_log as log
from ruqqus.helpers import log_cascade

import log_tables

ROOT = Path(__file__).resolve().parent.parent
NOW = 1_793_000_000
ADMIN = 20
AUTHOR = 30


def _migration():
    return (ROOT / "scripts" / "migrations" / "2026-12-09_action_log.sql").read_text(encoding="utf-8")


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        for statement in log_tables.migration_statements(_migration()):
            conn.execute(text(statement))
        log_tables.apply(conn, modactions=True)
        conn.execute(text("CREATE TABLE submissions (id INTEGER PRIMARY KEY, board_id INTEGER, audience INTEGER DEFAULT 0, author_id INTEGER, "
                          "repost_id INTEGER DEFAULT 0, is_anonymous BOOLEAN DEFAULT 0, is_banned BOOLEAN DEFAULT 0)"))
        conn.execute(text("CREATE TABLE submissions_aux (id INTEGER, ban_reason VARCHAR(128) DEFAULT '')"))
        conn.execute(text("CREATE TABLE comments (id INTEGER PRIMARY KEY, parent_submission INTEGER, author_id INTEGER, is_anonymous BOOLEAN DEFAULT 0, "
                          "is_banned BOOLEAN DEFAULT 0)"))
        conn.execute(text("CREATE TABLE boards (id INTEGER PRIMARY KEY, is_private BOOLEAN DEFAULT 0, is_circle BOOLEAN DEFAULT 0)"))
        conn.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, admin_level INTEGER DEFAULT 0)"))
        conn.execute(text("CREATE TABLE contributors (id INTEGER PRIMARY KEY, user_id INTEGER, board_id INTEGER, is_active BOOLEAN)"))
        conn.execute(text("CREATE TABLE mods (id INTEGER PRIMARY KEY, user_id INTEGER, board_id INTEGER, invite_rescinded BOOLEAN)"))
        conn.execute(text("INSERT INTO boards VALUES (1, 0, 0), (2, 1, 0), (3, 0, 1)"))        # public, private, Circle
        conn.execute(text("INSERT INTO users VALUES (1, 0), (20, 3), (21, 4), (30, 0)"))
    with Session(engine) as session:
        session.engine_for_tests = engine
        yield session


def add_post(db, pid, board=1, audience=0, anon=False, author=AUTHOR, repost=0, banned=False):
    db.execute(text("INSERT INTO submissions (id, board_id, audience, author_id, repost_id, is_anonymous, is_banned) VALUES (:i, :b, :a, :u, :r, :n, :x)"),
               {"i": pid, "b": board, "a": audience, "u": author, "r": repost, "n": anon, "x": banned})
    db.execute(text("INSERT INTO submissions_aux (id, ban_reason) VALUES (:i, '')"), {"i": pid})


def add_comment(db, cid, post, anon=False, author=AUTHOR, banned=False):
    db.execute(text("INSERT INTO comments (id, parent_submission, author_id, is_anonymous, is_banned) VALUES (:i, :p, :u, :n, :x)"),
               {"i": cid, "p": post, "u": author, "n": anon, "x": banned})


def entries(db, where="1 = 1", **params):
    return [dict(r._mapping) for r in db.execute(text(f"SELECT * FROM log_entries WHERE {where} ORDER BY id"), params)]


def count(db, sql, **params):
    return db.execute(text(sql), params).scalar()


# --- one entry per item, as record would write it --------------------------------------------------------------------------------

def test_every_item_gets_a_mod_log_row_and_an_entry_linked_to_the_main_one_and_naming_no_account(db):
    add_post(db, 10, board=1)
    add_post(db, 11, board=2)
    add_comment(db, 100, 10)
    main = log.record_staff(db, "admin_nuke", type("A", (), {"id": ADMIN})(), "removed 2 posts and 1 comment", target_user_id=AUTHOR, reason="Spam run")
    made = log_cascade.record_cascade(db, "ban", ADMIN, post_ids=[10, 11], comment_ids=[100], reason="Spam run", rule="evasion", linked_entry_id=main, now=NOW)
    assert made == 3
    rows = entries(db, "action IN ('ban_post', 'ban_comment')")
    assert [(r["action"], r["target_submission_id"], r["target_comment_id"], r["board_id"]) for r in rows] == \
        [("ban_post", 10, None, 1), ("ban_post", 11, None, 2), ("ban_comment", None, 100, 1)]
    for r in rows:
        assert (r["log_type"], r["actor_id"], r["actor_role"], r["rule_key"], r["reason"], r["linked_entry_id"], r["created_utc"]) == \
            ("content", ADMIN, "admin", "evasion", "Spam run", main, NOW)
        assert r["target_user_id"] is None and r["staff_note"] == "" and r["snapshot_id"] is None, "no account, no private note, no copy of any text"
        assert r["legacy_modaction_id"] is not None
    mods = [dict(r._mapping) for r in db.execute(text("SELECT * FROM modactions ORDER BY id"))]
    assert [(m["user_id"], m["board_id"], m["kind"], m["target_submission_id"], m["target_comment_id"], m["_note"]) for m in mods] == \
        [(ADMIN, 1, "ban_post", 10, 0, "admin action | Spam run"), (ADMIN, 2, "ban_post", 11, 0, "admin action | Spam run"),
         (ADMIN, 1, "ban_comment", 0, 100, "admin action | Spam run")]
    assert sorted(m["id"] for m in mods) == sorted(r["legacy_modaction_id"] for r in rows), "an entry is the copy of its own mod-log row"
    assert log.reconcile(db, now=NOW + 3600) == (0, 0), "nothing is left for the repair step to copy"


def test_an_obliterated_copys_entry_is_the_stub_s_words_the_rule_only(db):
    add_post(db, 10, board=1)
    add_post(db, 11, board=1, repost=10)
    stub = log.record(db, "obliterate_post", ADMIN, board_id=1, post_id=10, role=log.ADMIN, note="admin action", now=NOW)
    assert log_cascade.record_cascade(db, "obliterate", ADMIN, post_ids=[11], rule="illegal", linked_entry_id=stub, skip_recorded=True, now=NOW) == 1
    row = entries(db, "action = 'obliterate_post' AND target_submission_id = 11")[0]
    assert (row["rule_key"], row["reason"], row["audience"], row["linked_entry_id"], row["log_type"], row["actor_role"]) == ("illegal", "", log.PUBLIC, stub, "content", "admin")
    assert db.execute(text("SELECT kind, _note FROM modactions WHERE target_submission_id = 11")).first() == ("obliterate_post", "admin action")


# --- the same audience rules as `record`, in SQL ---------------------------------------------------------------------------------

def _scenario(db):
    add_post(db, 10, board=1)                           # public guild
    add_post(db, 11, board=2)                           # private guild
    add_post(db, 12, board=3)                           # Circle guild
    add_post(db, 13, board=None)                        # no guild
    add_post(db, 14, board=1, audience=4)               # a post for circles
    add_post(db, 15, board=1, anon=True)                # anonymous
    add_comment(db, 100, 10)
    add_comment(db, 101, 11)
    add_comment(db, 102, 14)                            # under a post for circles
    add_comment(db, 103, 10, anon=True)
    return [10, 11, 12, 13, 14, 15], [100, 101, 102, 103]


@pytest.mark.parametrize("anonymous_floor", [log.PUBLIC, log.STAFF])
@pytest.mark.parametrize("reason", ["", "Spam", "this was ban evasion", "CSAM was found"])
def test_the_audience_of_each_entry_is_what_record_gives_the_same_item(db, reason, anonymous_floor):
    posts, comments = _scenario(db)
    plain, _ = log_cascade.note_for(reason)
    anonymous = {15: True, 103: True}
    expected = {}
    for post_id in posts:
        board = db.execute(text("SELECT board_id FROM submissions WHERE id = :i"), {"i": post_id}).scalar()
        floor = anonymous_floor if anonymous.get(post_id) else log.PUBLIC
        expected[("p", post_id)] = db.execute(text("SELECT audience FROM log_entries WHERE id = :i"), {"i": log.record(
            db, "ban_post", ADMIN, board_id=board, post_id=post_id, reason=reason, note=plain, role=log.ADMIN, floor=floor, now=NOW)}).scalar()
    for comment_id in comments:
        board = db.execute(text("SELECT s.board_id FROM comments c JOIN submissions s ON s.id = c.parent_submission WHERE c.id = :i"), {"i": comment_id}).scalar()
        floor = anonymous_floor if anonymous.get(comment_id) else log.PUBLIC
        expected[("c", comment_id)] = db.execute(text("SELECT audience FROM log_entries WHERE id = :i"), {"i": log.record(
            db, "ban_comment", ADMIN, board_id=board, comment_id=comment_id, reason=reason, note=plain, role=log.ADMIN, floor=floor, now=NOW)}).scalar()
    log_cascade.record_cascade(db, "ban", ADMIN, post_ids=posts, comment_ids=comments, reason=reason, anonymous_floor=anonymous_floor, now=NOW)
    got = {}
    for r in entries(db, "legacy_modaction_id IS NOT NULL"):
        key = ("p", r["target_submission_id"]) if r["target_submission_id"] else ("c", r["target_comment_id"])
        got[key] = r["audience"]
    assert got == expected
    if not reason:
        # the plain cases, spelled out: the guild's own, the staff's for what has no guild or is for circles, the floor for what is anonymous
        assert got[("p", 10)] == log.PUBLIC and got[("p", 11)] == log.GUILD and got[("p", 12)] == log.GUILD
        assert got[("p", 13)] == log.STAFF and got[("p", 14)] == log.STAFF and got[("c", 102)] == log.STAFF
        assert got[("p", 15)] == anonymous_floor and got[("c", 103)] == anonymous_floor
    if reason == "CSAM was found":
        assert set(got.values()) == {log.LEGAL}, "a marker in the reason raises the whole cascade, as it raises one entry"


def test_a_floor_only_narrows(db):
    add_post(db, 10, board=1)
    log_cascade.record_cascade(db, "ban", ADMIN, post_ids=[10], floor=log.STAFF, now=NOW)
    assert entries(db, "legacy_modaction_id IS NOT NULL")[0]["audience"] == log.STAFF


def test_an_item_with_a_missing_guild_row_is_the_staffs(db):
    add_post(db, 10, board=99)                          # a board that no longer has a row: public by `record` too (it is not private)
    log_cascade.record_cascade(db, "ban", ADMIN, post_ids=[10], now=NOW)
    expected = log.record(db, "ban_post", ADMIN, board_id=99, post_id=10, role=log.ADMIN, note="admin action", now=NOW)
    assert entries(db, "legacy_modaction_id IS NOT NULL")[0]["audience"] == db.execute(text("SELECT audience FROM log_entries WHERE id = :i"), {"i": expected}).scalar()


# --- idempotent -------------------------------------------------------------------------------------------------------------------

def test_a_re_run_that_skips_what_is_recorded_writes_nothing_and_one_that_does_not_writes_again(db):
    add_post(db, 10)
    add_post(db, 11)
    assert log_cascade.record_cascade(db, "obliterate", ADMIN, post_ids=[10, 11], skip_recorded=True, now=NOW) == 2
    assert log_cascade.record_cascade(db, "obliterate", ADMIN, post_ids=[10, 11], skip_recorded=True, now=NOW) == 0
    assert count(db, "SELECT COUNT(*) FROM modactions") == 2, "not even a mod-log row"
    # an item that already has an entry of the action (the stub of an earlier obliteration of that very copy) is left out as well
    add_post(db, 12)
    log.record(db, "obliterate_post", ADMIN, board_id=1, post_id=12, role=log.ADMIN, note="admin action", now=NOW)
    assert log_cascade.record_cascade(db, "obliterate", ADMIN, post_ids=[10, 12], skip_recorded=True, now=NOW) == 0
    # a removal that is a new decision (removed, reinstated, removed again) is recorded again: nothing is skipped
    assert log_cascade.record_cascade(db, "ban", ADMIN, post_ids=[10], now=NOW) == 1
    assert log_cascade.record_cascade(db, "ban", ADMIN, post_ids=[10], now=NOW) == 1
    assert count(db, "SELECT COUNT(*) FROM log_entries WHERE action = 'ban_post' AND target_submission_id = 10") == 2


def test_an_entry_is_written_once_per_mod_log_row(db):
    add_post(db, 10)
    log_cascade.record_cascade(db, "ban", ADMIN, post_ids=[10], now=NOW)
    entry = entries(db)[0]
    # the repair step finds nothing to copy, and a second copy of the same row is refused by the unique index
    assert log.mirror_from_table(db, entry["legacy_modaction_id"]) is None
    assert count(db, "SELECT COUNT(*) FROM log_entries WHERE legacy_modaction_id = :m", m=entry["legacy_modaction_id"]) == 1


# --- bounded ----------------------------------------------------------------------------------------------------------------------

def _many(db, posts, comments):
    db.execute(text("INSERT INTO submissions (id, board_id, author_id) VALUES (:i, 1, 30)"), [{"i": i} for i in range(1, posts + 1)])
    if comments:
        db.execute(text("INSERT INTO comments (id, parent_submission, author_id) VALUES (:i, 1, 30)"), [{"i": 100_000 + i} for i in range(1, comments + 1)])
    return list(range(1, posts + 1)), [100_000 + i for i in range(1, comments + 1)]


def test_thousands_of_items_are_a_few_statements_not_a_statement_each(db):
    posts, comments = _many(db, 1200, 700)
    seen = []
    engine = db.engine_for_tests
    listener = lambda conn, cursor, statement, parameters, context, executemany: seen.append(statement)       # noqa: E731
    event.listen(engine, "before_cursor_execute", listener)
    try:
        made = log_cascade.record_cascade(db, "ban", ADMIN, post_ids=posts, comment_ids=comments, reason="A long run", now=NOW)
    finally:
        event.remove(engine, "before_cursor_execute", listener)
    assert made == 1900
    assert len(seen) < 40, f"{len(seen)} statements for 1,900 items"
    assert count(db, "SELECT COUNT(*) FROM modactions") == 1900 and count(db, "SELECT COUNT(*) FROM log_entries") == 1900


@pytest.mark.parametrize("size", [1, 499, 500, 501, 1000, 1001])
def test_chunk_boundaries_lose_and_duplicate_nothing(db, size):
    posts, _ = _many(db, size, 0)
    assert log_cascade.record_cascade(db, "ban", ADMIN, post_ids=posts, now=NOW) == size
    assert sorted(r["target_submission_id"] for r in entries(db)) == posts
    assert count(db, "SELECT COUNT(DISTINCT legacy_modaction_id) FROM log_entries") == size


def test_only_integers_reach_the_sql_and_a_repeated_id_is_one_item(db):
    add_post(db, 10)
    assert log_cascade.record_cascade(db, "ban", ADMIN, post_ids=["10", 10, 10.0], now=NOW) == 1
    with pytest.raises(ValueError):
        log_cascade.record_cascade(db, "ban", ADMIN, post_ids=["10); DROP TABLE log_entries; --"], now=NOW)
    with pytest.raises(ValueError):
        log_cascade.record_cascade(db, "freeze", ADMIN, post_ids=[10], now=NOW)
    assert count(db, "SELECT COUNT(*) FROM log_entries") == 1
    assert log_cascade.record_cascade(db, "ban", ADMIN, now=NOW) == 0, "nothing to record"


def test_an_item_that_does_not_exist_is_left_out(db):
    add_post(db, 10)
    assert log_cascade.record_cascade(db, "ban", ADMIN, post_ids=[10, 999], comment_ids=[555], now=NOW) == 1


# --- best effort ------------------------------------------------------------------------------------------------------------------

def test_a_failure_is_counted_and_undoes_nothing_else(db):
    add_post(db, 10)
    earlier = log.record(db, "ban_user", ADMIN, target_user_id=AUTHOR, role=log.ADMIN, now=NOW)
    db.execute(text("DROP TABLE boards"))                                   # the entry's own statement needs it: the savepoint must take the mod-log row back too
    assert log_cascade.record_cascade(db, "ban", ADMIN, post_ids=[10], reason="x", now=NOW) == 0
    assert count(db, "SELECT COUNT(*) FROM modactions") == 0, "all or nothing: no mod-log row without its entry"
    assert count(db, "SELECT COUNT(*) FROM log_entries WHERE id = :i", i=earlier) == 1
    failure = db.execute(text("SELECT action FROM log_failures")).all()
    assert [r[0] for r in failure] == ["ban_cascade"]
    assert log.failure_count(db, now=NOW + 60) == 1, "/admin says so"


# --- what the reason does to the notes ------------------------------------------------------------------------------------------

def test_a_long_reason_is_kept_whole_in_the_entry_and_cut_cleanly_in_the_short_columns(db):
    reason = "<b>&\"'" * 85                                               # 510 characters, six times as long once escaped
    reason = log.clean_reason(reason)
    plain, stored = log_cascade.note_for(reason)
    assert plain == f"admin action | {reason}" and len(stored) <= log_cascade.NOTE_MAX and stored.startswith("admin action | &lt;b&gt;")
    assert not re.search(r"&[#\w]*$", stored), "never a cut entity"
    shown = log_cascade.fit_escaped(reason, log_cascade.BAN_REASON_MAX)
    assert len(shown) <= 128 and not re.search(r"&[#\w]*$", shown) and shown.startswith("&lt;b&gt;&amp;")
    assert len(shown) > 120, "it uses the room it has"
    assert log_cascade.fit_escaped("short", 128) == "short" and log_cascade.fit_escaped("", 128) == "" and log_cascade.fit_escaped(None, 128) == ""
    assert log_cascade.note_for("") == ("admin action", "admin action")
    add_post(db, 10)
    log_cascade.record_cascade(db, "ban", ADMIN, post_ids=[10], reason=reason, now=NOW)
    assert entries(db)[0]["reason"] == reason, "the entry holds all of it"
    assert len(db.execute(text("SELECT _note FROM modactions")).scalar()) <= 256


def test_a_reason_is_rated_by_the_word_filter_like_any_entrys(db):
    add_post(db, 10)
    log_cascade.record_cascade(db, "ban", ADMIN, post_ids=[10], reason="plain words", now=NOW)
    assert entries(db)[0]["reason_severity"] == 0


# --- taking an account's things down ------------------------------------------------------------------------------------------------

def test_taking_everything_down_returns_only_what_this_call_removed_and_cuts_the_reason_to_the_column(db):
    add_post(db, 10)
    add_post(db, 11, banned=True)                       # a guildmaster removed it: its own decision stands
    add_post(db, 12, author=31)                          # someone else's
    add_post(db, 13, anon=True)
    add_comment(db, 100, 10)
    add_comment(db, 101, 10, banned=True)
    add_comment(db, 102, 10, author=31)
    db.execute(text("UPDATE submissions_aux SET ban_reason = 'Removed by a guildmaster' WHERE id = 11"))
    posts, comments = log_cascade.take_down_all(db, AUTHOR, "<i>" * 100)
    assert (posts, comments) == ([10, 13], [100])
    flags = dict(db.execute(text("SELECT id, is_banned FROM submissions")).all())
    assert flags == {10: 1, 11: 1, 12: 0, 13: 1}
    reasons = dict(db.execute(text("SELECT id, ban_reason FROM submissions_aux")).all())
    assert reasons[11] == "Removed by a guildmaster" and reasons[12] == ""
    assert reasons[10] == reasons[13] and len(reasons[10]) <= 128 and reasons[10].startswith("&lt;i&gt;") and not re.search(r"&[#\w]*$", reasons[10])
    assert dict(db.execute(text("SELECT id, is_banned FROM comments")).all()) == {100: 1, 101: 1, 102: 0}
    assert log_cascade.take_down_all(db, AUTHOR, "again") == ([], []), "a re-run, or a second admin at once, takes down and records nothing"


def test_taking_down_a_huge_account_is_a_few_statements(db):
    posts, comments = _many(db, 1200, 700)
    db.execute(text("INSERT INTO submissions_aux (id, ban_reason) VALUES (:i, '')"), [{"i": i} for i in posts])
    seen = []
    engine = db.engine_for_tests
    listener = lambda conn, cursor, statement, parameters, context, executemany: seen.append(statement)       # noqa: E731
    event.listen(engine, "before_cursor_execute", listener)
    try:
        assert log_cascade.take_down_all(db, AUTHOR, "x") == (posts, comments)
    finally:
        event.remove(engine, "before_cursor_execute", listener)
    assert len(seen) < 10


# --- what the readers find ------------------------------------------------------------------------------------------------------------

def _nuked(db):
    add_post(db, 10, board=1)
    add_post(db, 11, board=2)
    add_post(db, 12, board=1, anon=True)
    add_comment(db, 100, 10)
    add_comment(db, 101, 10, anon=True)
    main = log.record_staff(db, "admin_nuke", type("A", (), {"id": ADMIN})(), "removed", target_user_id=AUTHOR, reason="Spam")
    log_cascade.record_cascade(db, "ban", ADMIN, post_ids=[10, 11, 12], comment_ids=[100, 101], reason="Spam", linked_entry_id=main,
                               anonymous_floor=log.STAFF, now=NOW)
    return main


def ids_of(rows):
    return {(r["target_submission_id"], r["target_comment_id"]) for r in rows if r["action"] in ("ban_post", "ban_comment")}


def test_an_items_own_log_and_the_publics_find_each_entry_the_reader_may_see(db):
    _nuked(db)
    public, _ = log.fetch(db, level=0, scope=log.about_scope(post_ids=[10, 11, 12], comment_ids=[100, 101]))
    assert ids_of(public) == {(10, None), (None, 100)}, "the public guild's named items; not the private guild's, not the anonymous ones"
    staff, _ = log.fetch(db, level=3, scope=log.about_scope(post_ids=[10, 11, 12], comment_ids=[100, 101], include_anonymous=True))
    assert ids_of(staff) == {(10, None), (11, None), (12, None), (None, 100), (None, 101)}
    for row in public + staff:
        assert row["target_user_id"] is None or row["action"] == "admin_nuke"


def test_the_accounts_log_lists_its_named_items_and_never_its_anonymous_ones_below_staff(db):
    _nuked(db)
    public, _ = log.fetch(db, level=0, scope=log.about_scope(user_id=AUTHOR))
    assert ids_of(public) == {(10, None), (None, 100)}
    staff, _ = log.fetch(db, level=3, scope=log.about_scope(user_id=AUTHOR, include_anonymous=True))
    assert ids_of(staff) == {(10, None), (11, None), (12, None), (None, 100), (None, 101)}
    assert [r for r in staff if r["action"] == "admin_nuke"], "the main entry names the account, for staff"
    assert not [r for r in public if r["action"] == "admin_nuke"]


def test_the_site_wide_log_never_shows_the_public_a_private_guilds_or_an_anonymous_removal(db):
    _nuked(db)
    rows, _ = log.fetch(db, level=0)
    assert ids_of(rows) == {(10, None), (None, 100)}
    assert all(r["actor_id"] is None for r in rows), "an admin is never named"
    assert "staff_note" not in rows[0] and "linked_entry_id" not in rows[0]


def test_a_guilds_own_log_shows_the_public_the_named_removal_and_hides_the_staffs(db):
    _nuked(db)
    where, params = log.not_hidden_from(0)
    shown = {r[0] for r in db.execute(text(f"SELECT target_submission_id FROM modactions WHERE board_id = 1 AND target_submission_id <> 0 AND {where}"), params)}
    assert shown == {10}, "the anonymous post's row is the staff's"
    where, params = log.not_hidden_from(3)
    assert {r[0] for r in db.execute(text(f"SELECT target_submission_id FROM modactions WHERE board_id = 1 AND target_submission_id <> 0 AND {where}"), params)} == {10, 12}
    # a member of the private guild reads its entry in their own guild's log
    where, params = log.not_hidden_from(0, member_boards=[2])
    assert {r[0] for r in db.execute(text(f"SELECT target_submission_id FROM modactions WHERE board_id = 2 AND {where}"), params)} == {11}
    where, params = log.not_hidden_from(0)
    assert {r[0] for r in db.execute(text(f"SELECT target_submission_id FROM modactions WHERE board_id = 2 AND {where}"), params)} == set()


def test_the_main_entry_is_staff_only_and_links_to_each_item(db):
    main = _nuked(db)
    row = entries(db, "id = :i", i=main)[0]
    assert (row["action"], row["audience"], row["target_user_id"], row["actor_role"], row["reason"]) == ("admin_nuke", log.STAFF, AUTHOR, "admin", "Spam")
    assert count(db, "SELECT COUNT(*) FROM log_entries WHERE linked_entry_id = :m", m=main) == 5
    staff_rows, _ = log.fetch(db, level=3)
    assert {r["linked_entry_id"] for r in staff_rows if r["action"] in ("ban_post", "ban_comment")} == {main}
