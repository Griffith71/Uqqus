"""The Log (helpers/action_log.py, on SQLite with the tables built from migration 2026-12-09_action_log.sql).
The promises: who may see an entry is decided in one place and is kept when a guild changes; a private or Circle
guild's entries never reach the public; admins are never named to the public; a snapshot is private and is emptied
on its date; a ModAction copy is made once and carries no marker text as its reason; the reader's SQL gives each kind
of reader exactly their entries."""
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from ruqqus.helpers import action_log as log

import log_tables

ROOT = Path(__file__).resolve().parent.parent
MIGRATION = "2026-12-09_action_log.sql"
NOW = 1_793_000_000
DAY = 86400


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


def _statements():
    yield from log_tables.migration_statements(read("scripts", "migrations", MIGRATION))


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        for statement in _statements():
            conn.execute(text(statement))
        # the later migrations (2026-12-21 and 2026-12-30), in SQLite's words (the migrations themselves are Postgres)
        log_tables.apply(conn)
        conn.execute(text("CREATE TABLE submissions (id INTEGER PRIMARY KEY, author_id INTEGER, repost_id INTEGER DEFAULT 0, is_anonymous BOOLEAN DEFAULT 0)"))
        conn.execute(text("CREATE TABLE comments (id INTEGER PRIMARY KEY, author_id INTEGER, is_anonymous BOOLEAN DEFAULT 0)"))
        conn.execute(text("CREATE TABLE boards (id INTEGER PRIMARY KEY, is_private BOOLEAN DEFAULT 0, is_circle BOOLEAN DEFAULT 0)"))
        conn.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, admin_level INTEGER DEFAULT 0)"))
        conn.execute(text("CREATE TABLE contributors (id INTEGER PRIMARY KEY, user_id INTEGER, board_id INTEGER, is_active BOOLEAN)"))
        conn.execute(text("CREATE TABLE mods (id INTEGER PRIMARY KEY, user_id INTEGER, board_id INTEGER, invite_rescinded BOOLEAN)"))
        conn.execute(text("INSERT INTO boards VALUES (1, 0, 0), (2, 1, 0), (3, 0, 1)"))        # public, private, Circle
        conn.execute(text("INSERT INTO users VALUES (1, 0), (10, 0), (11, 0), (20, 3), (21, 4), (30, 0)"))
    with Session(engine) as session:
        yield session


def entry(db, action="hide_post_from_guild", actor=10, board=1, **kw):
    return log.record(db, action, actor, board_id=board, now=NOW, **kw)


def audience(db, entry_id):
    return db.execute(text("SELECT audience FROM log_entries WHERE id = :i"), {"i": entry_id}).scalar()


# --- the one audience rule ------------------------------------------------------------------------------------------------

def test_a_public_guilds_entry_is_public_and_a_private_or_circle_guilds_is_the_guilds(db):
    assert audience(db, entry(db, board=1)) == log.PUBLIC
    assert audience(db, entry(db, board=2)) == log.GUILD
    assert audience(db, entry(db, board=3)) == log.GUILD


def test_what_is_not_tied_to_a_guild_is_not_the_publics(db):
    assert audience(db, entry(db, action="update_settings", board=None)) == log.STAFF


def test_an_account_suspension_is_public_and_names_no_guild_or_item(db):
    e = log.record(db, "ban_user", 20, target_user_id=30, rule="evasion", reason="Ban evasion with another account.",
                   staff_note="seen with @x", role=log.ADMIN, now=NOW)
    row = db.execute(text("SELECT audience, board_id, target_submission_id, target_comment_id, actor_role FROM log_entries "
                          "WHERE id = :i"), {"i": e}).first()
    assert tuple(row) == (log.PUBLIC, None, None, None, "admin")


def test_staff_only_and_legal_actions_are_never_public_whatever_the_guild(db):
    assert audience(db, entry(db, action="clear_display_name", board=1, actor=20, role=log.ADMIN)) == log.STAFF
    assert audience(db, entry(db, action="force_sensitive_user", board=None, actor=20, role=log.ADMIN)) == log.STAFF
    assert audience(db, entry(db, action="legal_removal", board=1, actor=1)) == log.LEGAL
    assert audience(db, entry(db, action="suppress_content", board=1, actor=21, role=log.ADMIN)) == log.LEGAL
    # a ModAction's own notes for the two cases the site's code already marks
    assert audience(db, entry(db, action="ban_post", board=1, actor=1, note="CSAM detected")) == log.LEGAL
    assert audience(db, entry(db, action="ban_post", board=1, actor=1, note="banned image")) == log.LEGAL
    assert audience(db, entry(db, action="ban_post", board=1, actor=1, note="ban evasion")) == log.STAFF


def test_a_floor_only_narrows(db):
    assert audience(db, entry(db, board=1, floor=log.STAFF)) == log.STAFF
    assert log.audience_for("hide_post_from_guild", board_id=2, private=True, floor=log.PUBLIC) == log.GUILD


def test_an_entry_keeps_its_audience_when_the_guild_later_goes_public(db):
    e = entry(db, board=2)
    db.execute(text("UPDATE boards SET is_private = 0 WHERE id = 2"))
    assert audience(db, e) == log.GUILD
    rows, _ = log.fetch(db, level=0)
    assert e not in [r["id"] for r in rows]


# --- who is the actor -------------------------------------------------------------------------------------------------------

def test_the_role_comes_from_the_site_account_the_marker_or_the_caller(db):
    assert log.role_of(1, 0) == log.SYSTEM
    assert log.role_of(20, 3, "admin action") == log.ADMIN and log.role_of(20, 3, "admin_action") == log.ADMIN
    assert log.role_of(20, 3, "siege") == log.ADMIN and log.role_of(10, 0, "siege") == log.GUILDMASTER
    assert log.role_of(10, 0, "") == log.GUILDMASTER
    assert log.role_of(20, 3, "", role=log.GUILDMASTER) == log.GUILDMASTER        # an admin acting as a guildmaster of the guild
    e = entry(db, action="ban_post", actor=20, note="admin action")
    assert db.execute(text("SELECT actor_role FROM log_entries WHERE id = :i"), {"i": e}).scalar() == "admin"


def test_an_admin_is_never_named_to_anyone_below_staff_and_the_staff_note_never_leaves(db):
    entry(db, action="ban_post", actor=20, board=1, note="admin action", reason="Spam", staff_note="secret")
    for level in (0,):
        rows, _ = log.fetch(db, level=level)
        assert rows[0]["actor_id"] is None and "staff_note" not in rows[0] and "snapshot_id" not in rows[0]
    rows, _ = log.fetch(db, level=3)
    assert rows[0]["actor_id"] == 20 and rows[0]["staff_note"] == "secret"
    assert "staff_note" not in log.PUBLIC_COLUMNS


# --- reasons ----------------------------------------------------------------------------------------------------------------

def test_a_reason_is_plain_one_line_text_and_is_bounded(db):
    assert log.clean_reason("  two   words \n and <b>more</b> ") == "two words and <b>more</b>"
    assert len(log.clean_reason("x" * 900)) == log.REASON_MAX
    e = entry(db, reason="<script>alert(1)</script>")
    assert db.execute(text("SELECT reason FROM log_entries WHERE id = :i"), {"i": e}).scalar() == "<script>alert(1)</script>"


def test_an_action_that_needs_a_reason_refuses_none_when_asked_to(db):
    with pytest.raises(log.ReasonRequired):
        entry(db, action="ban_user", board=None, actor=20, require_reason=True, reason="  ")
    assert entry(db, action="unban_user", board=None, actor=20, require_reason=True)         # lifting one needs none
    assert entry(db, action="ban_user", board=None, actor=20, require_reason=True, reason="Spam")
    assert entry(db, action="hide_post_from_guild", require_reason=False)                    # the legacy copy never refuses


def test_the_rule_is_one_of_the_fixed_keys(db):
    assert log.parse_rule("illegal") == "illegal" and log.parse_rule("made up") == "" and log.parse_rule(None) == ""
    e = entry(db, rule="made up")
    assert db.execute(text("SELECT rule_key FROM log_entries WHERE id = :i"), {"i": e}).scalar() == ""


# --- snapshots ---------------------------------------------------------------------------------------------------------------

class Post:
    author_id, board_id, title, url, body, body_html = 30, 1, "A title", None, "the body", "<p>the body</p>"


def test_a_snapshot_is_kept_privately_and_emptied_on_its_date_with_the_entry_left(db):
    e = entry(db, snapshot=log.snapshot_of(Post()))
    s = db.execute(text("SELECT id, expires_utc, body FROM log_snapshots WHERE entry_id = :e"), {"e": e}).first()
    assert s.expires_utc == NOW + log.SNAPSHOT_DAYS * DAY and s.body == "the body"
    assert db.execute(text("SELECT snapshot_id FROM log_entries WHERE id = :e"), {"e": e}).scalar() == s.id
    assert log.scrub_due(db, NOW + 89 * DAY) == 0
    assert log.scrub_due(db, NOW + 91 * DAY) == 1
    after = db.execute(text("SELECT title, body, body_html, scrubbed_utc FROM log_snapshots WHERE id = :i"), {"i": s.id}).first()
    assert tuple(after[:3]) == (None, None, None) and after.scrubbed_utc == NOW + 91 * DAY
    assert db.execute(text("SELECT count(*) FROM log_entries WHERE id = :e"), {"e": e}).scalar() == 1
    assert log.scrub_due(db, NOW + 200 * DAY) == 0                                          # once


def test_illegal_content_is_held_a_year_and_only_level_four_may_open_it(db):
    e = entry(db, action="legal_removal", actor=1, snapshot=log.snapshot_of(Post(), legal_hold=True))
    s = dict(db.execute(text("SELECT * FROM log_snapshots WHERE entry_id = :e"), {"e": e}).first()._mapping)
    assert s["expires_utc"] == NOW + log.LEGAL_HOLD_DAYS * DAY
    assert not log.snapshot_visible(s, viewer_id=30, level=0) and not log.snapshot_visible(s, viewer_id=20, level=3)
    assert log.snapshot_visible(s, viewer_id=21, level=4)


def test_who_may_open_a_snapshot(db):
    e = entry(db, snapshot=log.snapshot_of(Post()))
    s = dict(db.execute(text("SELECT * FROM log_snapshots WHERE entry_id = :e"), {"e": e}).first()._mapping)
    assert log.snapshot_visible(s, viewer_id=30, level=0)                                    # its author
    assert not log.snapshot_visible(s, viewer_id=11, level=0)                                # anyone else
    assert not log.snapshot_visible(s, viewer_id=None, level=0)
    assert log.snapshot_visible(s, viewer_id=11, level=0, guild_mod=True)                    # the guild's guildmaster
    assert log.snapshot_visible(s, viewer_id=20, level=3)
    s["scrubbed_utc"] = NOW
    assert not log.snapshot_visible(s, viewer_id=30, level=4)


def test_a_self_delete_is_kept_privately_and_is_not_in_the_log(db):
    log.keep_snapshot(db, log.snapshot_of(Post(), kind=log.GRACE), now=NOW)
    assert db.execute(text("SELECT count(*) FROM log_entries")).scalar() == 0
    assert db.execute(text("SELECT count(*) FROM log_snapshots")).scalar() == 1


# --- the copy of a guild mod-log row -------------------------------------------------------------------------------------------

def test_a_mod_action_is_copied_once_with_its_note_as_plain_text(db):
    args = (77, 10, 1, "hide_post_from_guild", 0, 5, 0, "off &amp; away &lt;b&gt;", NOW)
    first = log.mirror_modaction(db, *args)
    assert first and log.mirror_modaction(db, *args) is None
    row = db.execute(text("SELECT reason, target_user_id, target_submission_id, legacy_modaction_id, log_type FROM log_entries "
                          "WHERE id = :i"), {"i": first}).first()
    assert tuple(row) == ("off & away <b>", None, 5, 77, "content")


def test_the_markers_the_site_writes_are_not_a_reason(db):
    e = log.mirror_modaction(db, 78, 20, 1, "ban_post", 0, 5, 0, "admin action", NOW)
    assert db.execute(text("SELECT reason, actor_role FROM log_entries WHERE id = :i"), {"i": e}).first() == ("", "admin")
    e = log.mirror_modaction(db, 79, 1, 1, "ban_post", 0, 6, 0, "CSAM detected", NOW)
    assert db.execute(text("SELECT reason, audience FROM log_entries WHERE id = :i"), {"i": e}).first() == ("", log.LEGAL)


def test_a_reason_is_what_is_left_when_the_marker_is_taken_away():
    assert log.reason_in("admin action") == "" and log.reason_in("Admin_Action") == "" and log.reason_in("siege") == ""
    assert log.reason_in("admin action | Spamming.") == "Spamming."
    assert log.reason_in("sensitive content forced on by the site's admins") == "sensitive content forced on by the site's admins"
    assert log.reason_in("off topic") == "off topic" and log.reason_in(None) == ""


def test_the_admins_marker_in_a_longer_note_still_makes_an_admin(db):
    e = log.mirror_modaction(db, 90, 20, 1, "update_settings", 0, 0, 0, "sensitive content forced on by the site's admins", NOW)
    assert db.execute(text("SELECT actor_role FROM log_entries WHERE id = :i"), {"i": e}).scalar() == "admin"


def test_a_route_adds_what_it_knows_to_the_copy(db):
    log.mirror_modaction(db, 80, 20, 1, "ban_post", 0, 5, 0, "admin action", NOW)
    entry_id = log.annotate(db, 80, rule="illegal", reason="Links to malware", staff_note="reported twice", snapshot=log.snapshot_of(Post()))
    row = db.execute(text("SELECT rule_key, reason, staff_note, snapshot_id FROM log_entries WHERE id = :i"), {"i": entry_id}).first()
    assert tuple(row[:3]) == ("illegal", "Links to malware", "reported twice") and row[3]
    assert log.annotate(db, 999, reason="x") is None


# --- the reader's SQL -----------------------------------------------------------------------------------------------------------

def _seed(db):
    ids = {
        "public": entry(db, board=1, reason="p"),
        "private": entry(db, board=2, reason="g"),
        "staff": log.record(db, "clear_display_name", 20, target_user_id=30, role=log.ADMIN, now=NOW),
        "legal": log.record(db, "legal_removal", 1, board_id=1, now=NOW),
    }
    return ids


def seen(db, **kw):
    return {r["id"] for r in log.fetch(db, **kw)[0]}


def test_each_reader_sees_exactly_their_entries(db):
    ids = _seed(db)
    assert seen(db, level=0) == {ids["public"]}                                              # a visitor
    assert seen(db, level=0, member_boards=[2]) == {ids["public"], ids["private"]}           # a member of the private guild reads its own entries
    assert seen(db, level=0, member_boards=[1, 3]) == {ids["public"]}                        # a member of other guilds only
    assert seen(db, level=3) == {ids["public"], ids["private"], ids["staff"]}                # an admin
    assert seen(db, level=4) == set(ids.values())                                            # a level-4 admin


def test_the_members_of_a_guild_come_from_the_tables_can_view_reads(db):
    db.execute(text("INSERT INTO contributors (user_id, board_id, is_active) VALUES (11, 2, 1), (11, 3, 0), (10, 1, 1)"))
    db.execute(text("INSERT INTO mods (user_id, board_id, invite_rescinded) VALUES (11, 1, 0), (30, 2, 1)"))
    assert log.member_board_ids(db, 11) == [1, 2]                                            # an inactive contributor is out
    assert log.member_board_ids(db, 30) == []                                                # a rescinded invitation is out
    assert log.member_board_ids(db, None) == []


def test_filters_and_paging(db):
    first = entry(db, board=1)
    entry(db, action="exile_user", board=1, target_user_id=30)
    third = entry(db, board=1, rule="illegal")
    rows, more = log.fetch(db, level=0, limit=2)
    assert [r["id"] for r in rows] == [third, third - 1] and more
    rows, more = log.fetch(db, level=0, before_id=third - 1)
    assert [r["id"] for r in rows] == [first] and not more
    assert [r["log_type"] for r in log.fetch(db, level=0, log_type="sanction")[0]] == ["sanction"]
    assert [r["id"] for r in log.fetch(db, level=0, rule="illegal")[0]] == [third]
    assert len(log.fetch(db, level=0, target_user_id=30)[0]) == 1


def test_a_redacted_entry_loses_its_reason_for_everyone_but_staff(db):
    e = entry(db, reason="named a person")
    log.redact(db, e, 21, now=NOW)
    assert log.fetch(db, level=0)[0][0]["reason"] == "" and log.fetch(db, level=3)[0][0]["reason"] == "named a person"
    assert log.fetch(db, level=0)[0][0]["redacted_utc"] == NOW


def test_the_visible_clause_never_takes_anything_but_numbers(db):
    sql, params = log.visible_clause(0, [2, 3])
    assert "DROP" not in sql and set(params.values()) == {2, 3}
    with pytest.raises(ValueError):
        log.visible_clause(0, ["2); DROP TABLE log_entries; --"])


# --- the tables and their source ------------------------------------------------------------------------------------------------

def test_the_migration_is_in_the_schema_and_its_ledger_and_is_safe_to_run_twice():
    migration, schema = read("scripts", "migrations", MIGRATION), read("schema.sql")
    for name in ("log_entries", "log_snapshots"):
        assert f"CREATE TABLE IF NOT EXISTS public.{name} (" in migration and f"CREATE TABLE public.{name} (" in schema
    assert "ADD COLUMN IF NOT EXISTS summary" in migration and "    summary character varying(256)" in schema
    assert f"('{MIGRATION}')" in schema.split("schema_migrations", 1)[-1]
    assert "REFERENCES" not in migration                         # no foreign keys to users, the project's rule
    for statement in migration.split(";"):
        statement = "\n".join(l for l in statement.splitlines() if not l.strip().startswith("--")).strip()
        if statement.startswith("CREATE"):
            assert "IF NOT EXISTS" in statement, statement[:80]


def test_the_entry_table_refuses_a_made_up_type_role_or_audience(db):
    from sqlalchemy.exc import IntegrityError
    for column, value in (("log_type", "other"), ("actor_role", "owner"), ("audience", 9)):
        values = {"log_type": "content", "actor_role": "admin", "audience": 0, column: value}
        with pytest.raises(IntegrityError):
            db.execute(text("INSERT INTO log_entries (created_utc, log_type, action, actor_role, audience) "
                            "VALUES (1, :log_type, 'x', :actor_role, :audience)"), values)
        db.rollback()


# --- the public page, the guild's log and the preserved copies (slice B) -------------------------------------------------------

def test_the_public_page_leaves_out_a_guild_that_has_gone_private_since(db):
    open_entry = entry(db, board=1)
    was_open = entry(db, board=1)
    db.execute(text("UPDATE boards SET is_private = 1 WHERE id = 1"))                         # +1 goes private after both were written
    kept = entry(db, board=None, action="ban_user", actor=20, role=log.ADMIN, target_user_id=30)
    assert seen(db, level=0, open_guilds_only=True) == {kept}                                 # no guild is named, an account sanction stays
    assert seen(db, level=0) == {kept}                                                        # the default is the public page's: no caller has to remember it
    assert seen(db, level=0, open_guilds_only=False) == {open_entry, was_open, kept}          # a guild's own page asks without it
    assert seen(db, level=3) == {open_entry, was_open, kept}                                  # staff read every guild
    db.execute(text("UPDATE boards SET is_private = 0, is_circle = 1 WHERE id = 1"))
    assert seen(db, level=0, open_guilds_only=True) == {kept}                                 # a Circle guild is no different


def test_filtering_by_an_admin_finds_nothing_below_staff(db):
    # the actor is hidden from the public, so filtering by that actor must not list exactly their entries
    by_admin = entry(db, action="ban_post", actor=20, role=log.ADMIN, board=1)
    by_gm = entry(db, board=1, actor=10)
    assert seen(db, level=0, actor_id=20) == set()
    assert seen(db, level=3, actor_id=20) == {by_admin}
    assert seen(db, level=0, actor_id=10) == {by_gm}


def test_a_guilds_own_log_leaves_out_the_rows_whose_entry_the_reader_may_not_read(db):
    db.execute(text("CREATE TABLE modactions (id INTEGER PRIMARY KEY, board_id INTEGER)"))
    db.execute(text("INSERT INTO modactions VALUES (1, 1), (2, 1), (3, 1), (4, 1)"))
    log.record(db, "hide_post_from_guild", 10, board_id=2, now=NOW, legacy_id=1)              # written while +2 was private: the guild's
    log.record(db, "ban_post", 20, board_id=1, now=NOW, legacy_id=2, role=log.ADMIN)
    log.record(db, "clear_display_name", 20, board_id=1, now=NOW, legacy_id=3, role=log.ADMIN)  # staff only
    # row 4 has no entry at all (the copy failed, the backfill has not run): it is shown as it always was

    def rows(level, members=()):
        sql, params = log.not_hidden_from(level, members)
        return {r[0] for r in db.execute(text(f"SELECT id FROM modactions WHERE {sql}"), params)}

    assert rows(0) == {2, 4}                      # a visitor: the public one, and the row with no entry
    assert rows(0, [2]) == {1, 2, 4}              # a member of the private guild reads the one written there
    assert rows(3) == {1, 2, 3, 4}                # staff read everything but legal
    assert rows(4) == {1, 2, 3, 4}


def test_the_rule_and_reason_of_a_page_of_rows_come_in_one_query(db):
    e1 = entry(db, board=1, reason="off topic", rule="good_faith", legacy_id=11)
    e2 = entry(db, board=1, reason="named a person", rule="content", legacy_id=12)
    log.redact(db, e2, 21, now=NOW)
    notes = log.notes_for(db, [11, 12, 13])
    assert notes[11] == {"rule_key": "good_faith", "reason": "off topic", "redacted_utc": 0}
    assert notes[12]["reason"] == "" and notes[12]["redacted_utc"] == NOW                     # a redacted reason stays out
    assert 13 not in notes and log.notes_for(db, []) == {} and e1


def _copy_of(db, **kw):
    return log.record(db, "hide_post_from_guild", 10, board_id=1, post_id=7, now=NOW, reason="r",
                      snapshot=log.snapshot_of(Item(7, 30, 1, "A title", "<p>text</p>"), 1), **kw)


class Item:
    def __init__(self, id, author_id, board_id, title, body_html):
        self.id, self.author_id, self.board_id, self.title, self.body_html = id, author_id, board_id, title, body_html
        self.url, self.body = None, "text"


def test_an_entry_and_its_copy_are_read_together_and_only_when_the_entry_is_visible(db):
    e = _copy_of(db)
    entry_row, snapshot = log.entry_with_copy(db, e, 0)
    assert entry_row["id"] == e and "snapshot_id" not in entry_row and "staff_note" not in entry_row
    assert (snapshot["title"], snapshot["author_id"], snapshot["board_id"]) == ("A title", 30, 1)
    private = log.record(db, "hide_post_from_guild", 10, board_id=2, now=NOW, snapshot=log.snapshot_of(Item(8, 30, 2, "x", "<p>y</p>"), 2))
    assert log.entry_with_copy(db, private, 0) == (None, None)                                # not the visitor's entry: it is not there
    assert log.entry_with_copy(db, private, 0, [2])[1]["title"] == "x"
    assert log.entry_with_copy(db, 9999, 4) == (None, None)


def test_copies_for_a_page_names_who_each_copy_belongs_to(db):
    with_copy, without = _copy_of(db), entry(db, board=1)
    copies = log.copies_for(db, [with_copy, without])
    assert set(copies) == {with_copy} and copies[with_copy]["author_id"] == 30 and copies[with_copy]["board_id"] == 1
    assert log.copies_for(db, []) == {}


def test_the_newest_copy_of_an_item_is_the_one_it_has(db):
    assert log.snapshot_for(db, post_id=7) is None
    log.keep_snapshot(db, log.snapshot_of(Item(7, 30, 1, "old", "<p>1</p>")), now=NOW)
    log.keep_snapshot(db, log.snapshot_of(Item(7, 30, 1, "new", "<p>2</p>")), now=NOW + 5)
    assert log.snapshot_for(db, post_id=7)["title"] == "new"
    log.scrub_for(db, post_ids=[7], now=NOW + 9)
    assert log.snapshot_for(db, post_id=7) is None                                            # a purged item has no copy
    assert log.snapshot_for(db) is None and log.snapshot_for(db, comment_id=0) is None


class Mod:
    def __init__(self, board_id, accepted=True, rescinded=False):
        self.board_id, self.accepted, self.invite_rescinded = board_id, accepted, rescinded


class Viewer:
    def __init__(self, id, admin_level=0, mods=()):
        self.id, self.admin_level, self.moderates = id, admin_level, list(mods)


def test_who_may_open_a_copy_is_the_author_the_guilds_guildmasters_and_the_admins(db):
    log.keep_snapshot(db, log.snapshot_of(Item(7, 30, 1, "t", "<p>x</p>")), now=NOW)
    copy = log.snapshot_for(db, post_id=7)
    assert log.may_open_copy(copy, Viewer(30))                                               # its author
    assert log.may_open_copy(copy, Viewer(20, 3))                                            # an admin
    assert log.may_open_copy(copy, Viewer(10, mods=[Mod(1)]))                                # a guildmaster of that guild
    assert not log.may_open_copy(copy, Viewer(10, mods=[Mod(2), Mod(1, accepted=False), Mod(1, rescinded=True)]))
    assert not log.may_open_copy(copy, Viewer(11)) and not log.may_open_copy(copy, None) and not log.may_open_copy(None, Viewer(30))
    legal = log.snapshot_of(Item(8, 30, 1, "t", "<p>x</p>"), legal_hold=True)
    log.keep_snapshot(db, legal, now=NOW)
    held = log.snapshot_for(db, post_id=8)
    assert not log.may_open_copy(held, Viewer(20, 3)) and not log.may_open_copy(held, Viewer(30)) and log.may_open_copy(held, Viewer(21, 4))


def test_the_visible_clause_can_be_aliased_for_a_subquery():
    assert log.visible_clause(0, alias="e") == ("e.audience = 0", {})
    sql, params = log.visible_clause(0, [2], alias="e")
    assert sql == "(e.audience = 0 OR (e.audience = 1 AND e.board_id IN (:vb0)))" and params == {"vb0": 2}
    assert log.visible_clause(3, alias="e")[0] == "e.audience <= 2"


# --- the log of one post, one comment or one account ----------------------------------------------------------------------------

@pytest.fixture
def items(db):
    """Posts and comments by accounts 30 and 31 (one of each anonymous), a forwarded copy and another person's repost."""
    db.execute(text("CREATE TABLE content_edit_history (id INTEGER PRIMARY KEY AUTOINCREMENT, actor_id INTEGER, target_submission_id INTEGER DEFAULT 0, "
                    "target_comment_id INTEGER DEFAULT 0, board_id INTEGER DEFAULT 0, action VARCHAR(32), created_utc INTEGER DEFAULT 0)"))
    db.execute(text("INSERT INTO submissions (id, author_id, repost_id, is_anonymous) VALUES "
                    "(100, 30, 0, 0), (101, 30, 100, 0), (102, 31, 100, 0), (103, 30, 0, 1), (104, 31, 0, 0)"))
    db.execute(text("INSERT INTO comments (id, author_id, is_anonymous) VALUES (200, 30, 0), (201, 30, 1), (202, 31, 0)"))


def listed(db, **kw):
    return {r["id"] for r in log.fetch(db, level=0, scope=log.about_scope(**kw))[0]}


def test_a_posts_log_is_the_post_and_its_copies_by_the_same_author_and_nothing_else(db, items):
    assert log.family_ids(db, 100) == [100, 101]                 # the copy (101) is the author's; 102 is somebody else's repost
    assert log.family_ids(db, 101) == [100, 101]                 # asked by a copy: the same family
    assert log.family_ids(db, 999) == [999]
    original = entry(db, board=1, post_id=100)
    copy = entry(db, board=1, post_id=101)
    elsewhere = entry(db, board=1, post_id=104)
    reposted = entry(db, board=1, post_id=102)
    assert listed(db, post_ids=log.family_ids(db, 100)) == {original, copy}
    assert listed(db, post_ids=[104]) == {elsewhere} and reposted not in listed(db, post_ids=log.family_ids(db, 100))


def test_a_comments_log_is_that_comment(db, items):
    mine, other = entry(db, board=1, comment_id=200), entry(db, board=1, comment_id=202)
    assert listed(db, comment_ids=[200]) == {mine} and listed(db, comment_ids=[202]) == {other}


def test_a_profiles_log_is_what_was_done_to_the_account_and_to_everything_it_wrote_and_never_an_anonymous_item(db, items):
    hide_post = entry(db, board=1, post_id=100)                  # a post on their profile
    hide_copy = entry(db, board=1, post_id=101)                  # a guild's copy of it: not on their profile, still theirs
    hide_comment = entry(db, board=1, comment_id=200)
    exile = entry(db, action="exile_user", board=1, target_user_id=30)
    suspend = log.record(db, "ban_user", 20, target_user_id=30, role=log.ADMIN, now=NOW)
    anonymous_post = entry(db, board=1, post_id=103)
    anonymous_comment = entry(db, board=1, comment_id=201)
    someone_else = entry(db, board=1, post_id=104)
    entry(db, action="exile_user", board=1, target_user_id=31)
    public = listed(db, user_id=30)
    assert public == {hide_post, hide_copy, hide_comment, exile, suspend}
    assert anonymous_post not in public and anonymous_comment not in public and someone_else not in public
    assert listed(db, user_id=30, include_anonymous=True) == public | {anonymous_post, anonymous_comment}   # the admins' view


def test_a_log_about_nothing_lists_nothing(db, items):
    entry(db, board=1, post_id=100)
    assert log.about_scope() == ("1 = 0", {})
    assert listed(db) == set()


def test_the_scope_takes_only_numbers(db, items):
    sql, params = log.about_scope(post_ids=[100, 101], comment_ids=[200], user_id=30)
    assert "DROP" not in sql and set(params) == {"sp0", "sp1", "sc0", "about"} and set(params.values()) == {100, 101, 200, 30}
    with pytest.raises(ValueError):
        log.about_scope(post_ids=["1); DROP TABLE log_entries; --"])


def test_a_page_of_it_can_start_part_way_through(db, items):
    ids = [entry(db, board=1, post_id=100) for _ in range(5)]
    rows, more = log.fetch(db, level=0, scope=log.about_scope(post_ids=[100]), limit=2, offset=2)
    assert [r["id"] for r in rows] == [ids[2], ids[1]] and more


def test_an_authors_own_deletes_are_dates_with_no_words(db, items):
    for post, comment, actor, when in ((100, 0, 30, NOW), (0, 200, 30, NOW + 5), (104, 0, 31, NOW + 9), (103, 0, 30, NOW + 11)):
        db.execute(text("INSERT INTO content_edit_history (actor_id, target_submission_id, target_comment_id, action, created_utc) "
                        "VALUES (:a, :p, :c, 'delete', :t)"), {"a": actor, "p": post, "c": comment, "t": when})
    db.execute(text("INSERT INTO content_edit_history (actor_id, target_submission_id, action, created_utc) VALUES (30, 100, 'edit', 1)"))
    mine = log.author_deletes(db, actor_id=30)
    assert [(d["target_submission_id"], d["target_comment_id"]) for d in mine] == [(0, 200), (100, 0)]          # newest first; an anonymous one is left out
    assert all(set(d) == {"id", "created_utc", "actor_id", "target_submission_id", "target_comment_id"} for d in mine)   # no title, no body
    assert len(log.author_deletes(db, actor_id=30, include_anonymous=True)) == 3
    assert [d["target_submission_id"] for d in log.author_deletes(db, post_ids=[100, 101])] == [100]
    assert log.author_deletes(db) == [] and [d["target_comment_id"] for d in log.author_deletes(db, comment_ids=[200])] == [200]


# --- the first slice of the audit (2026-11-05): what a private copy is, who acted, and no pairing with an anonymous item ----------

def _copy_of_kind(db, post_id, kind, hider=None, board=1):
    return log.keep_snapshot(db, log.snapshot_of(Item(post_id, 30, board, "t", "<p>x</p>"), kind=kind, hider_id=hider), now=NOW)


def test_who_may_open_a_copy_depends_on_what_it_is_a_copy_of(db):
    """H2: the guildmasters read a guild's hide and nothing else, though every copy carries the guild's board id."""
    for post_id, kind, hider in ((21, log.HIDE, None), (22, log.AUTHOR_HIDE, 11), (23, log.GRACE, None), (24, log.DELETE, None)):
        _copy_of_kind(db, post_id, kind, hider)
    gm = Viewer(10, mods=[Mod(1)])
    opened = {post_id: log.may_open_copy(log.snapshot_for(db, post_id=post_id), gm) for post_id in (21, 22, 23, 24)}
    assert opened == {21: True, 22: False, 23: False, 24: False}
    for post_id in (21, 22, 23, 24):
        copy = log.snapshot_for(db, post_id=post_id)
        assert log.may_open_copy(copy, Viewer(30)) and log.may_open_copy(copy, Viewer(20, 3))     # its author and an admin: every kind
        assert not log.may_open_copy(copy, Viewer(12))
    hidden_by_post_author = log.snapshot_for(db, comment_id=None, post_id=22)
    assert log.may_open_copy(hidden_by_post_author, Viewer(11)) and not log.may_open_copy(log.snapshot_for(db, post_id=21), Viewer(11))


def test_a_private_copy_must_say_what_it_is(db):
    with pytest.raises(ValueError):
        log.keep_snapshot(db, {"post_id": 5, "author_id": 30, "board_id": 1, "title": "t"}, now=NOW)
    assert log.snapshot_of(Item(5, 30, 1, "t", "<p>x</p>"))["kind"] == log.HIDE                    # a guild's hide unless the caller says
    assert log.snapshot_of(Item(5, 30, 1, "t", "<p>x</p>"), legal_hold=True)["kind"] == log.LEGAL_KIND


def test_a_viewer_gets_the_newest_copy_they_may_open(db):
    """An item can have a guild's hide and then its author's delete; a guildmaster can read only the first."""
    _copy_of_kind(db, 31, log.HIDE)
    _copy_of_kind(db, 31, log.GRACE)
    newest = log.snapshot_for(db, post_id=31)
    assert newest["kind"] == log.GRACE
    assert log.snapshot_for(db, post_id=31, viewer=Viewer(10, mods=[Mod(1)]))["kind"] == log.HIDE
    assert log.snapshot_for(db, post_id=31, viewer=Viewer(30))["kind"] == log.GRACE
    assert log.snapshot_for(db, post_id=31, viewer=Viewer(12)) is None


def test_a_guildmasters_words_do_not_make_them_an_admin_or_take_their_entry_out_of_the_public_log(db):
    """H3: the markers are what the site's own code writes; a reason is free text."""
    note = "admin action | suspected ban evasion, banned image, CSAM detected, by the site's admins"
    gm = entry(db, actor=10, board=1, note=note, reason="x")
    row = db.execute(text("SELECT actor_role, audience FROM log_entries WHERE id = :i"), {"i": gm}).first()
    assert tuple(row) == ("guildmaster", log.PUBLIC)
    admin = entry(db, actor=20, board=1, note="admin action | ban evasion")
    assert tuple(db.execute(text("SELECT actor_role, audience FROM log_entries WHERE id = :i"), {"i": admin}).first()) == ("admin", log.STAFF)
    system = entry(db, actor=1, board=1, note="banned image")
    assert tuple(db.execute(text("SELECT actor_role, audience FROM log_entries WHERE id = :i"), {"i": system}).first()) == ("system", log.LEGAL)
    assert log.role_of(10, 0, "admin action") == log.GUILDMASTER and log.role_of(20, 3, "admin action") == log.ADMIN
    assert log.audience_for("hide_post_from_guild", board_id=1, note="csam") == log.PUBLIC                  # untrusted words mean nothing
    assert log.audience_for("hide_post_from_guild", board_id=1, note="csam", trusted=True) == log.LEGAL


def test_a_log_reads_the_public_page_by_default_and_a_guilds_own_members_see_their_guild(db):
    private = entry(db, board=2)
    assert seen(db, level=0) == set()
    assert seen(db, level=0, member_boards=[2]) == {private}
    assert seen(db, level=3) == {private}


def test_an_entry_never_pairs_an_account_with_an_anonymous_item(db, items):
    """H6: an exile citing an anonymous post would say who wrote it."""
    kept = log.record(db, "exile_user", 10, board_id=1, target_user_id=30, post_id=104, now=NOW)         # 104 is not anonymous
    dropped = log.record(db, "exile_user", 10, board_id=1, target_user_id=30, post_id=103, now=NOW)      # 103 is
    dropped_comment = log.record(db, "exile_user", 10, board_id=1, target_user_id=30, comment_id=201, now=NOW)
    get = lambda i: tuple(db.execute(text("SELECT target_user_id, target_submission_id, target_comment_id FROM log_entries WHERE id = :i"), {"i": i}).first())
    assert get(kept) == (30, 104, None) and get(dropped) == (30, None, None) and get(dropped_comment) == (30, None, None)
    alone = log.record(db, "hide_post_from_guild", 10, board_id=1, post_id=103, now=NOW)                  # an item alone is untouched
    assert get(alone) == (None, 103, None)


def test_an_item_made_anonymous_after_an_exile_cited_it_stops_pairing_in_every_list(db, items):
    exile = log.record(db, "exile_user", 10, board_id=1, target_user_id=30, post_id=104, now=NOW)
    assert exile in listed(db, post_ids=[104]) and exile in listed(db, user_id=30)
    assert exile in {r["id"] for r in log.fetch(db, level=0, target_user_id=30)[0]}
    db.execute(text("UPDATE submissions SET is_anonymous = 1 WHERE id = 104"))
    assert exile not in listed(db, post_ids=[104])                                           # the item's log no longer names the person
    assert exile not in listed(db, user_id=30)                                               # the account's log no longer lists it
    assert exile not in {r["id"] for r in log.fetch(db, level=0, target_user_id=30)[0]}      # nor does the `to=` filter
    assert exile in {r["id"] for r in log.fetch(db, level=0)[0]}                             # the entry itself stays in the Log
    assert exile in listed(db, user_id=30, include_anonymous=True)                           # and staff see it
    assert exile in {r["id"] for r in log.fetch(db, level=3, target_user_id=30)[0]}
