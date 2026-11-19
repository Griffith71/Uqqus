"""A redaction reaches every copy of the reason (the second review of the Log, C48; ruqqus/helpers/reason_copies.py), on SQLite with the tables built from the
migrations (tests/log_tables.py) and the few columns the copies live in.

The promises: redacting an entry (`action_log.correct`) also clears the words from the guild's own mod-log note, the removed or hidden item's reason, the
banned account's and guild's, the exile list's and the hide's history rows, **only** where they are still that decision's own (a later decision of the same
kind keeps its line), **never** the site's own messages or what the decision did (a banned account stays banned, an admin's marker stays in the note so the
guild's log still says "the site"), once only, and a copy that fails is counted and named without undoing the redaction or another copy; the entry's own
reason is never edited and the correction is still a new, linked, staff-only entry."""
import html
import re
from pathlib import Path

import log_tables
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from ruqqus.helpers import action_log as log
from ruqqus.helpers import reason_copies

ROOT = Path(__file__).resolve().parent.parent
MIGRATION = "2026-12-09_action_log.sql"
NOW = 1_793_000_000
ADMIN = type("A", (), {"id": 21})()          # the level-4 admin who redacts

TABLES = (
    "CREATE TABLE submissions (id INTEGER PRIMARY KEY, audience INTEGER DEFAULT 0, author_id INTEGER, repost_id INTEGER DEFAULT 0, is_anonymous BOOLEAN DEFAULT 0)",
    "CREATE TABLE comments (id INTEGER PRIMARY KEY, parent_submission INTEGER, author_id INTEGER, is_anonymous BOOLEAN DEFAULT 0)",
    "CREATE TABLE boards (id INTEGER PRIMARY KEY, is_private BOOLEAN DEFAULT 0, is_circle BOOLEAN DEFAULT 0, is_banned BOOLEAN DEFAULT 0, ban_reason VARCHAR(256))",
    "CREATE TABLE users (id INTEGER PRIMARY KEY, admin_level INTEGER DEFAULT 0, is_banned INTEGER DEFAULT 0, unban_utc INTEGER DEFAULT 0, ban_reason VARCHAR)",
    "CREATE TABLE submissions_aux (id INTEGER, ban_reason VARCHAR(128), hidden_reason VARCHAR(128) DEFAULT '')",
    "CREATE TABLE comments_aux (id INTEGER, ban_reason VARCHAR(256), hidden_reason VARCHAR(256) DEFAULT '', author_hidden_reason VARCHAR(256) DEFAULT '')",
    "CREATE TABLE bans (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, board_id INTEGER, is_active BOOLEAN NOT NULL DEFAULT 1, mod_note VARCHAR(128), "
    "alias_post_id INTEGER, alias_n INTEGER)",
    "CREATE TABLE content_edit_history (id INTEGER PRIMARY KEY AUTOINCREMENT, actor_id INTEGER, target_submission_id INTEGER DEFAULT 0, "
    "target_comment_id INTEGER DEFAULT 0, board_id INTEGER DEFAULT 0, action VARCHAR(32), reason VARCHAR(256), created_utc INTEGER DEFAULT 0, "
    "text_severity SMALLINT NOT NULL DEFAULT 0, summary VARCHAR(256), edit_group BIGINT, previous_body VARCHAR)",
)


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        for statement in log_tables.migration_statements((ROOT / "scripts" / "migrations" / MIGRATION).read_text(encoding="utf-8")):
            conn.execute(text(statement))
        log_tables.apply(conn, modactions=True)
        for statement in TABLES:
            conn.execute(text(statement))
        conn.execute(text("INSERT INTO boards (id) VALUES (1), (2)"))
        conn.execute(text("INSERT INTO users (id, admin_level) VALUES (10, 0), (11, 0), (20, 3), (21, 4), (30, 0), (31, 0), (32, 0)"))
        conn.execute(text("INSERT INTO submissions (id, author_id) VALUES (7, 30), (8, 30)"))
        conn.execute(text("INSERT INTO comments (id, parent_submission, author_id) VALUES (70, 7, 31), (71, 7, 31)"))
        conn.execute(text("INSERT INTO submissions_aux (id, ban_reason, hidden_reason) VALUES (7, '', ''), (8, '', '')"))
        conn.execute(text("INSERT INTO comments_aux (id, ban_reason, hidden_reason, author_hidden_reason) VALUES (70, '', '', ''), (71, '', '', '')"))
    with Session(engine) as session:
        yield session


def one(db, sql, **params):
    return db.execute(text(sql), params).first()


def value(db, sql, **params):
    return one(db, sql, **params)[0]


def modaction(db, kind, user, *, post=None, comment=None, target_user=None, note=None, at=NOW, board=1):
    """A guild mod-log row and its entry in the Log, as the site makes them (the entry is the mirror of the row). Returns (modaction id, entry id)."""
    made = db.execute(text(
        "INSERT INTO modactions (user_id, board_id, target_user_id, target_submission_id, target_comment_id, created_utc, kind, _note) "
        "VALUES (:u, :b, :tu, :tp, :tc, :at, :k, :n) RETURNING id"),
        {"u": user, "b": board, "tu": target_user, "tp": post, "tc": comment, "at": at, "k": kind, "n": note}).first()[0]
    assert log.mirror_from_table(db, made) is not None
    return made, value(db, "SELECT id FROM log_entries WHERE legacy_modaction_id = :m", m=made)


def history(db, action, reason, *, post=None, comment=None, at=NOW):
    db.execute(text("INSERT INTO content_edit_history (actor_id, target_submission_id, target_comment_id, board_id, action, reason, created_utc, text_severity) "
                    "VALUES (10, :p, :c, 1, :a, :r, :at, 1)"), {"p": post or 0, "c": comment or 0, "a": action, "r": reason, "at": at})


def hide_post(db, reason, *, post=7, at=NOW, user=10):
    """A guild's hide of a post, as the route writes it: the live reason, the history row and the mod-log row (its note the escaped reason)."""
    db.execute(text("UPDATE submissions_aux SET hidden_reason = :r WHERE id = :p"), {"r": reason, "p": post})
    history(db, "guild_hide", reason, post=post, at=at)
    return modaction(db, "hide_post_from_guild", user, post=post, note=html.escape(reason)[:256], at=at)


def redact(db, entry, what="a private detail slipped into the reason"):
    return log.correct(db, entry, ADMIN, what)


def staff_note(db, correction):
    return value(db, "SELECT staff_note FROM log_entries WHERE id = :i", i=correction)


def failures(db):
    return db.execute(text("SELECT action, detail FROM log_failures")).all()


# --- the pure parts --------------------------------------------------------------------------------------------------------------------

def test_a_guild_log_note_keeps_only_the_marker_that_says_an_admin_did_it():
    assert reason_copies.scrubbed_note("Advertising &amp; spam") == ""                 # a guildmaster's reason, stored escaped
    assert reason_copies.scrubbed_note("admin action | the private reason") == "admin action"
    assert reason_copies.scrubbed_note("admin_action | the private reason") == "admin_action"
    assert reason_copies.scrubbed_note("category=Art | admin action") == "admin action"
    assert reason_copies.scrubbed_note("Admin Action | Why") == "admin action"        # the marker is read in lower case, as ModAction.by_admin does
    for nothing in ("admin action", "siege", "", None, "csam detected", "ban evasion", "spam", "Spam"):
        assert reason_copies.scrubbed_note(nothing) is None, nothing                 # no reason in it: nothing to take out


def test_whenever_the_note_held_an_admin_marker_it_still_does_so_the_guild_log_keeps_saying_the_site_did_it():
    for note in ("admin action | x", "admin_action | x", "x | by the site's admins", "category=x | admin action", "Admin Action | Why"):
        kept = reason_copies.scrubbed_note(note)
        assert any(mark in kept.lower() for mark in log.ADMIN_MARKERS), note
    assert reason_copies.scrubbed_note("by the site's admins | x") == "by the site's admins"


def test_same_words_ignores_case_spacing_and_entities_compares_a_prefix_and_never_matches_nothing():
    assert reason_copies.same_words("Too  much   SHOUTING", "too much shouting")
    assert reason_copies.same_words("Ben &amp; Jerry", "Ben & Jerry")
    assert reason_copies.same_words("x" * 300, "x" * 120 + " cut off elsewhere")
    assert not reason_copies.same_words("one reason", "another reason")
    assert not reason_copies.same_words("", "") and not reason_copies.same_words(None, "x") and not reason_copies.same_words("x", "   ")


def test_describe_says_what_was_cleared_and_what_could_not_be_and_is_capped():
    assert reason_copies.describe("a private detail", [], []) == "a private detail"
    assert reason_copies.describe("a  private\n detail", ["the guild log's note", "history rows (2)"], []) == \
        "a private detail (also cleared: the guild log's note, history rows (2))"
    assert reason_copies.describe("why", ["a"], ["b", "c"]) == "why (also cleared: a) (could not clear: b, c)"
    assert len(reason_copies.describe("w" * 300, ["x" * 200], ["y" * 200])) <= log.REASON_MAX


# --- a guildmaster's hide ---------------------------------------------------------------------------------------------------------------

def test_redacting_a_hide_clears_the_note_the_items_reason_and_the_history_row_and_changes_nothing_else(db):
    reason = "PRIVATE-HIDE-DETAIL spam & worse"
    ma, entry = hide_post(db, reason)
    before = one(db, "SELECT reason, rule_key, staff_note, audience, actor_role, action FROM log_entries WHERE id = :i", i=entry)
    assert before[0] == reason and value(db, "SELECT _note FROM modactions WHERE id = :m", m=ma) == html.escape(reason)

    correction = redact(db, entry)
    assert value(db, "SELECT _note FROM modactions WHERE id = :m", m=ma) == ""
    assert value(db, "SELECT hidden_reason FROM submissions_aux WHERE id = 7") == ""
    assert one(db, "SELECT reason, text_severity FROM content_edit_history WHERE action = 'guild_hide'") == (None, 0)
    after = one(db, "SELECT reason, rule_key, staff_note, audience, actor_role, action FROM log_entries WHERE id = :i", i=entry)
    assert after == before, "the entry is never edited: its reason is what staff still read"
    assert value(db, "SELECT redacted_utc FROM log_entries WHERE id = :i", i=entry) > 0
    assert staff_note(db, correction) == ("a private detail slipped into the reason (also cleared: the guild log's note, the hidden post's reason, "
                                          "history rows (1))")
    assert one(db, "SELECT action, audience, linked_entry_id, actor_role FROM log_entries WHERE id = :i", i=correction) == ("redact_entry", log.STAFF, entry, "admin")
    assert failures(db) == []
    assert log.fetch(db, level=0)[0][0]["reason"] == ""                                # and the Log's own readers see nothing, as before


def test_a_second_run_finds_nothing_and_a_redaction_never_writes_twice(db):
    _, entry = hide_post(db, "PRIVATE-TWICE")
    redact(db, entry)
    assert reason_copies.scrub(db, entry) == ([], [])
    assert value(db, "SELECT COUNT(*) FROM log_entries WHERE action = 'redact_entry'") == 1


def test_redacting_an_older_hide_leaves_a_newer_hides_line_and_the_newer_one_clears_the_rest(db):
    ma1, e1 = hide_post(db, "FIRST-PRIVATE-REASON", at=NOW)
    ma2, e2 = hide_post(db, "SECOND-REASON-STANDS", at=NOW + 600)
    redact(db, e1)
    assert value(db, "SELECT hidden_reason FROM submissions_aux WHERE id = 7") == "SECOND-REASON-STANDS", "the post still carries the later decision's words"
    assert value(db, "SELECT _note FROM modactions WHERE id = :m", m=ma2) == "SECOND-REASON-STANDS"
    assert value(db, "SELECT _note FROM modactions WHERE id = :m", m=ma1) == ""
    assert db.execute(text("SELECT reason FROM content_edit_history ORDER BY id")).all() == [(None,), ("SECOND-REASON-STANDS",)]
    redact(db, e2)
    assert value(db, "SELECT hidden_reason FROM submissions_aux WHERE id = 7") == ""
    assert db.execute(text("SELECT reason FROM content_edit_history ORDER BY id")).all() == [(None,), (None,)]


def test_a_hide_on_another_post_is_not_touched(db):
    hide_post(db, "ONE-POST-REASON", post=7)
    _, other = hide_post(db, "OTHER-POST-REASON", post=8)
    redact(db, other)
    assert value(db, "SELECT hidden_reason FROM submissions_aux WHERE id = 7") == "ONE-POST-REASON"
    assert value(db, "SELECT hidden_reason FROM submissions_aux WHERE id = 8") == ""


def test_a_history_row_with_other_words_in_the_same_minute_is_left_alone(db):
    _, entry = hide_post(db, "PRIVATE-WORDS")
    history(db, "guild_hide", "different words entirely")
    history(db, "guild_unhide", "PRIVATE-WORDS")                                       # another kind of row
    history(db, "guild_hide", "PRIVATE-WORDS", at=NOW + 7200)                          # another decision, hours later
    redact(db, entry)
    assert db.execute(text("SELECT action, reason FROM content_edit_history ORDER BY id")).all() == [
        ("guild_hide", None), ("guild_hide", "different words entirely"), ("guild_unhide", "PRIVATE-WORDS"), ("guild_hide", "PRIVATE-WORDS")]


def test_a_hide_comment_clears_the_comments_line_and_its_history_row(db):
    db.execute(text("UPDATE comments_aux SET hidden_reason = 'PRIVATE-COMMENT-HIDE' WHERE id = 70"))
    history(db, "guild_hide", "PRIVATE-COMMENT-HIDE", comment=70)
    _, entry = modaction(db, "hide_comment_from_guild", 10, comment=70, note="PRIVATE-COMMENT-HIDE")
    correction = redact(db, entry)
    assert value(db, "SELECT hidden_reason FROM comments_aux WHERE id = 70") == ""
    assert value(db, "SELECT reason FROM content_edit_history") is None
    assert staff_note(db, correction).endswith("(also cleared: the guild log's note, the hidden comment's reason, history rows (1))")


def test_a_post_authors_hide_of_a_comment_clears_its_reason_and_history_but_has_no_guild_log_row(db):
    db.execute(text("UPDATE comments_aux SET author_hidden_reason = 'PRIVATE-AUTHOR-REASON', hidden_reason = 'A-GUILDS-OWN' WHERE id = 70"))
    history(db, "author_hide", "PRIVATE-AUTHOR-REASON", comment=70)
    entry = log.record(db, "author_hide_comment", 10, role=log.AUTHOR, board_id=1, comment_id=70, reason="PRIVATE-AUTHOR-REASON", now=NOW)
    correction = redact(db, entry)
    assert one(db, "SELECT author_hidden_reason, hidden_reason FROM comments_aux WHERE id = 70") == ("", "A-GUILDS-OWN"), "only its own column"
    assert value(db, "SELECT reason FROM content_edit_history") is None
    assert staff_note(db, correction).endswith("(also cleared: the reason a post's author gave for hiding a comment, history rows (1))")


# --- an admin's removal -----------------------------------------------------------------------------------------------------------------

def test_an_admins_removal_clears_the_removed_pages_reason_and_the_note_keeps_its_marker(db):
    db.execute(text("UPDATE submissions_aux SET ban_reason = '<p><strong>PRIVATE-REMOVAL</strong> text</p>' WHERE id = 7"))
    ma, _ = modaction(db, "ban_post", 20, post=7, note="admin action")
    entry = value(db, "SELECT id FROM log_entries WHERE legacy_modaction_id = :m", m=ma)
    log.annotate(db, ma, reason="PRIVATE-REMOVAL text", rule="evasion")
    assert one(db, "SELECT actor_role, reason FROM log_entries WHERE id = :i", i=entry) == ("admin", "PRIVATE-REMOVAL text")
    correction = redact(db, entry)
    assert value(db, "SELECT ban_reason FROM submissions_aux WHERE id = 7") == ""
    assert value(db, "SELECT _note FROM modactions WHERE id = :m", m=ma) == "admin action", "a bare marker is not a reason: left as it was"
    assert staff_note(db, correction).endswith("(also cleared: the removed post's reason)")


def test_a_nuke_note_loses_the_reason_and_keeps_the_marker_so_the_guild_log_still_says_the_site(db):
    ma, entry = modaction(db, "ban_post", 20, post=7, note="admin action | PRIVATE-NUKE-WHY")
    assert value(db, "SELECT reason FROM log_entries WHERE id = :i", i=entry) == "PRIVATE-NUKE-WHY"
    db.execute(text("UPDATE submissions_aux SET ban_reason = 'PRIVATE-NUKE-WHY' WHERE id = 7"))
    redact(db, entry)
    assert value(db, "SELECT _note FROM modactions WHERE id = :m", m=ma) == "admin action"
    assert value(db, "SELECT ban_reason FROM submissions_aux WHERE id = 7") == ""
    # a note with the marker still reads as an admin's for the guild log, as ModAction.by_admin asks
    assert any(mark in value(db, "SELECT _note FROM modactions WHERE id = :m", m=ma).lower() for mark in log.ADMIN_MARKERS)


def test_a_later_removal_keeps_its_own_ban_reason_when_an_older_one_is_redacted(db):
    ma, entry = modaction(db, "ban_post", 20, post=7, note="admin action | OLD-ADMIN-REASON", at=NOW)
    modaction(db, "ban_post", log.SITE_ACCOUNT_ID, post=7, note="spam", at=NOW + 900)               # the spam guard, later: it wrote its own message
    db.execute(text("UPDATE submissions_aux SET ban_reason = 'Automatic spam removal. The post was removed.' WHERE id = 7"))
    redact(db, entry)
    assert value(db, "SELECT ban_reason FROM submissions_aux WHERE id = 7") == "Automatic spam removal. The post was removed."
    assert value(db, "SELECT _note FROM modactions WHERE id = :m", m=ma) == "admin action"            # its own note is still cleared


def test_an_image_checks_later_removal_overwrites_nothing_so_it_does_not_keep_the_admins_words(db):
    _, entry = modaction(db, "ban_post", 20, post=7, note="admin action | ADMIN-REASON-STILL-ON-THE-POST", at=1_700_000_000)     # long before the check's own time
    db.execute(text("UPDATE submissions_aux SET ban_reason = 'ADMIN-REASON-STILL-ON-THE-POST' WHERE id = 7"))
    log.record_image_check_removal(db, post_id=7, board_id=1)                                           # writes no ban_reason on the post
    redact(db, entry)
    assert value(db, "SELECT ban_reason FROM submissions_aux WHERE id = 7") == ""


def test_the_sites_own_removal_messages_are_never_a_copy(db):
    spam, entry = modaction(db, "ban_post", log.SITE_ACCOUNT_ID, post=7, note="spam")
    db.execute(text("UPDATE submissions_aux SET ban_reason = 'Automatic spam removal. The post was removed.' WHERE id = 7"))
    redact(db, entry)                                                                                   # the entry's reason is "Automatic spam check"
    assert value(db, "SELECT ban_reason FROM submissions_aux WHERE id = 7") == "Automatic spam removal. The post was removed."
    assert value(db, "SELECT _note FROM modactions WHERE id = :m", m=spam) == "spam", "the site's own word in the note is no reason either"
    csam, entry = modaction(db, "ban_post", log.SITE_ACCOUNT_ID, post=8, note="CSAM detected")
    log.annotate(db, csam, reason="CSAM [1]")
    db.execute(text("UPDATE submissions_aux SET ban_reason = 'CSAM [1]' WHERE id = 8"))
    redact(db, entry)
    assert value(db, "SELECT ban_reason FROM submissions_aux WHERE id = 8") == "CSAM [1]", "a constant of the site's, not a person's words"


# --- an account's ban, an account's alts, a guild's ban --------------------------------------------------------------------------------

def ban(db, user, reason, *, days=0, actor=20, at=NOW, automatic=False, floor=log.PUBLIC):
    db.execute(text("UPDATE users SET is_banned = :a, ban_reason = :r, unban_utc = :u WHERE id = :i"),
               {"a": actor, "r": reason, "u": (at + days * 86400) if days else 0, "i": user})
    return log.record(db, "suspend_user" if days else "ban_user", actor, target_user_id=user, reason=reason, role=log.SYSTEM if automatic else log.ADMIN,
                      floor=floor, now=at, until_utc=(at + days * 86400) if days else 0)


def test_an_account_ban_reason_is_cleared_and_the_ban_itself_is_not_touched(db):
    entry = ban(db, 30, "PRIVATE-BAN-DETAIL", days=7)
    correction = redact(db, entry)
    assert one(db, "SELECT ban_reason, is_banned, unban_utc FROM users WHERE id = 30") == ("", 20, NOW + 7 * 86400), "still suspended, to the same day"
    assert staff_note(db, correction).endswith("(also cleared: the account's ban reason)")
    assert value(db, "SELECT reason FROM log_entries WHERE id = :i", i=entry) == "PRIVATE-BAN-DETAIL"


def test_a_later_ban_that_gave_a_reason_keeps_its_reason_but_one_that_gave_none_does_not_count(db):
    first = ban(db, 30, "OLD-PRIVATE-REASON", at=NOW)
    later = ban(db, 30, "NEW-REASON-STANDS", at=NOW + 3600, days=3)
    redact(db, first)
    assert value(db, "SELECT ban_reason FROM users WHERE id = 30") == "NEW-REASON-STANDS"
    redact(db, later)
    assert value(db, "SELECT ban_reason FROM users WHERE id = 30") == ""
    # a later ban with no words leaves users.ban_reason as the earlier one wrote it (User.ban only writes it when there are some)
    again = ban(db, 31, "PRIVATE-REASON-FOR-31", at=NOW)
    log.record(db, "ban_user", 20, target_user_id=31, reason="", role=log.ADMIN, now=NOW + 60)
    redact(db, again)
    assert value(db, "SELECT ban_reason FROM users WHERE id = 31") == ""


def test_the_sites_own_ban_reasons_are_not_copies(db):
    entry = ban(db, 30, "Spamming.", actor=log.SITE_ACCOUNT_ID, automatic=True)
    redact(db, entry)
    assert value(db, "SELECT ban_reason FROM users WHERE id = 30") == "Spamming."


def test_the_alts_of_a_banned_account_lose_the_same_words_and_only_from_the_public_decision(db):
    main = ban(db, 30, "PRIVATE-REASON-ALL-THREE", at=NOW)
    ban(db, 31, "PRIVATE-REASON-ALL-THREE", at=NOW + 1, floor=log.STAFF)                  # its alts, banned in the same request: staff-only entries
    ban(db, 32, "PRIVATE-REASON-ALL-THREE", at=NOW + 2, floor=log.STAFF)
    ban(db, 10, "PRIVATE-REASON-ALL-THREE", at=NOW + 3)                                  # a different public ban with the same words: not an alt
    correction = redact(db, main)
    assert db.execute(text("SELECT id, ban_reason FROM users WHERE id IN (10, 30, 31, 32) ORDER BY id")).all() == [
        (10, "PRIVATE-REASON-ALL-THREE"), (30, ""), (31, ""), (32, "")]
    assert staff_note(db, correction).endswith("(also cleared: the account's ban reason, the alts' ban reasons)")


def test_redacting_an_alts_own_staff_entry_does_not_blank_the_main_accounts_line(db):
    ban(db, 30, "SHARED-WORDS-SHOWN-IN-THE-LOG", at=NOW)
    alt = ban(db, 31, "SHARED-WORDS-SHOWN-IN-THE-LOG", at=NOW + 1, floor=log.STAFF)
    redact(db, alt)
    assert value(db, "SELECT ban_reason FROM users WHERE id = 31") == ""
    assert value(db, "SELECT ban_reason FROM users WHERE id = 30") == "SHARED-WORDS-SHOWN-IN-THE-LOG", "the main entry is still public: its line stays until it is redacted"


def test_a_guild_ban_reason_is_cleared_and_the_guild_stays_banned(db):
    db.execute(text("UPDATE boards SET is_banned = 1, ban_reason = 'PRIVATE-GUILD-BAN' WHERE id = 2"))
    entry = log.record(db, "ban_guild", 21, board_id=2, reason="PRIVATE-GUILD-BAN", role=log.ADMIN, now=NOW)
    log.record(db, "unban_guild", 21, board_id=2, reason="reinstated", role=log.ADMIN, now=NOW + 10)                    # an unban is not a newer ban
    correction = redact(db, entry)
    assert one(db, "SELECT is_banned, ban_reason FROM boards WHERE id = 2") == (1, "")
    assert staff_note(db, correction).endswith("(also cleared: the guild's ban reason)")


def test_a_guilds_newer_ban_keeps_its_own_reason(db):
    db.execute(text("UPDATE boards SET ban_reason = 'SECOND-BAN-WORDS' WHERE id = 2"))
    first = log.record(db, "ban_guild", 21, board_id=2, reason="FIRST-BAN-WORDS", role=log.ADMIN, now=NOW)
    log.record(db, "ban_guild", 21, board_id=2, reason="SECOND-BAN-WORDS", role=log.ADMIN, now=NOW + 900)
    redact(db, first)
    assert value(db, "SELECT ban_reason FROM boards WHERE id = 2") == "SECOND-BAN-WORDS"


# --- the exile list ---------------------------------------------------------------------------------------------------------------------

def test_an_exile_reason_is_cleared_from_the_list_and_the_exile_stays_active(db):
    db.execute(text("INSERT INTO bans (user_id, board_id, is_active, mod_note) VALUES (30, 1, 1, 'PRIVATE-EXILE-WORDS'), (31, 1, 1, 'OTHER-ACCOUNT'), (30, 2, 1, 'OTHER-GUILD')"))
    ma, entry = modaction(db, "exile_user", 10, target_user=30)
    log.annotate(db, ma, reason="PRIVATE-EXILE-WORDS")
    correction = redact(db, entry)
    assert db.execute(text("SELECT user_id, board_id, is_active, mod_note FROM bans ORDER BY id")).all() == [
        (30, 1, 1, ""), (31, 1, 1, "OTHER-ACCOUNT"), (30, 2, 1, "OTHER-GUILD")]
    assert staff_note(db, correction).endswith("(also cleared: the exile list's reason)")


def test_an_exile_by_alias_is_found_through_its_subject_and_a_later_exile_keeps_its_line(db):
    db.execute(text("INSERT INTO bans (user_id, board_id, is_active, mod_note, alias_n) VALUES (30, 1, 1, 'ALIAS-PRIVATE-WORDS', 3)"))
    entry = log.record(db, "exile_alias", 10, board_id=1, reason="ALIAS-PRIVATE-WORDS", role=log.GUILDMASTER, now=NOW)
    db.execute(text("UPDATE log_entries SET subject_user_id = 30, alias_n = 3 WHERE id = :i"), {"i": entry})
    assert value(db, "SELECT target_user_id FROM log_entries WHERE id = :i", i=entry) is None, "the entry names no account"
    redact(db, entry)
    assert value(db, "SELECT mod_note FROM bans WHERE user_id = 30") == ""
    # the account is exiled again by name, with other words: redacting the older entry leaves them
    db.execute(text("UPDATE bans SET mod_note = 'NEW-NAME-EXILE-WORDS' WHERE user_id = 30"))
    older = log.record(db, "exile_user", 10, board_id=1, target_user_id=30, reason="ALIAS-PRIVATE-WORDS-AGAIN", role=log.GUILDMASTER, now=NOW - 5000)
    log.record(db, "exile_user", 10, board_id=1, target_user_id=30, reason="NEW-NAME-EXILE-WORDS", role=log.GUILDMASTER, now=NOW + 5000)
    redact(db, older)
    assert value(db, "SELECT mod_note FROM bans WHERE user_id = 30") == "NEW-NAME-EXILE-WORDS"


def test_an_alias_actions_note_is_a_label_and_is_never_cleared(db):
    ma, entry = modaction(db, "exile_alias", 10, note="Anonymous #3")
    log.annotate(db, ma, reason="ALIAS-WORDS")
    redact(db, entry)
    assert value(db, "SELECT _note FROM modactions WHERE id = :m", m=ma) == "Anonymous #3"


# --- an author's edit summary ------------------------------------------------------------------------------------------------------------

def edit_history(db, summary, *, post=None, comment=None, actor=30, group=None, at=NOW, severity=1):
    """The history row of an author's edit, as the route writes it: the version edited away, the note the author gave and (a post's) the edit's group."""
    db.execute(text("INSERT INTO content_edit_history (actor_id, target_submission_id, target_comment_id, board_id, action, created_utc, summary, edit_group, "
                    "previous_body, text_severity) VALUES (:a, :p, :c, 1, 'edit', :at, :s, :g, 'the version edited away', :sev)"),
               {"a": actor, "p": post or 0, "c": comment or 0, "at": at, "s": summary, "g": group, "sev": severity})


def summaries(db):
    return db.execute(text("SELECT target_submission_id, target_comment_id, summary, text_severity, previous_body FROM content_edit_history ORDER BY id")).all()


def test_redacting_an_edit_clears_the_summary_on_the_post_and_on_every_copy_of_that_edit_and_keeps_the_version(db):
    note = "PRIVATE-EDIT-NOTE call me on 555-0100"
    entry = log.record(db, "edit_post", 30, role=log.AUTHOR, board_id=1, post_id=7, reason=note, now=NOW)
    edit_history(db, note, post=7, group=4242)                                       # the original
    edit_history(db, note, post=8, group=4242)                                       # a forwarded copy of it: the same edit, its own row
    edit_history(db, "ANOTHER-EDIT-OF-THE-SAME-POST", post=7, group=999, at=NOW + 3600)
    edit_history(db, note, post=8, group=5151, at=NOW + 3600)                       # someone else's later edit that happens to say the same on the copy
    correction = redact(db, entry)
    assert summaries(db) == [(7, 0, None, 0, "the version edited away"), (8, 0, None, 0, "the version edited away"),
                             (7, 0, "ANOTHER-EDIT-OF-THE-SAME-POST", 1, "the version edited away"), (8, 0, note, 1, "the version edited away")]
    assert staff_note(db, correction) == "a private detail slipped into the reason (also cleared: edit summaries (2))"
    assert value(db, "SELECT reason FROM log_entries WHERE id = :i", i=entry) == note, "the entry is never edited: staff still read it"
    assert failures(db) == []
    assert reason_copies.scrub(db, entry) == ([], []), "a second run finds nothing"


def test_redacting_a_comments_edit_clears_its_summary_and_nobody_elses(db):
    note = "PRIVATE-COMMENT-EDIT-NOTE"
    entry = log.record(db, "edit_comment", 31, role=log.AUTHOR, board_id=1, comment_id=70, reason=note, now=NOW)
    edit_history(db, note, comment=70, actor=31)                                     # no edit group: a comment has none
    edit_history(db, note, comment=71, actor=31)                                     # another comment
    edit_history(db, note, comment=70, actor=32)                                     # another account's edit of the same comment with the same words
    edit_history(db, "A-DIFFERENT-NOTE", comment=70, actor=31)
    correction = redact(db, entry)
    assert [row[2] for row in summaries(db)] == [None, note, note, "A-DIFFERENT-NOTE"]
    assert staff_note(db, correction).endswith("(also cleared: edit summaries (1))")


def test_the_summary_is_found_through_the_words_not_the_spelling_and_an_edit_without_one_clears_nothing(db):
    entry = log.record(db, "edit_post", 30, role=log.AUTHOR, board_id=1, post_id=7, reason="Ben & Jerry  FIXED", now=NOW)
    edit_history(db, "ben &amp; jerry fixed", post=7)                                # stored escaped, in another case
    edit_history(db, None, post=7)
    assert reason_copies.scrub(db, entry) == (["edit summaries (1)"], [])
    assert [row[2] for row in summaries(db)] == [None, None]
    bare = log.record(db, "edit_post", 30, role=log.AUTHOR, board_id=1, post_id=8, reason="", now=NOW)
    edit_history(db, "NOT-THIS-ENTRYS", post=8)
    assert reason_copies.scrub(db, bare) == ([], [])
    assert summaries(db)[-1][2] == "NOT-THIS-ENTRYS"


def test_an_edit_of_an_item_with_no_pointer_left_is_not_an_error(db):
    entry = log.record(db, "edit_post", 30, role=log.AUTHOR, board_id=1, reason="REASON-OF-AN-EDIT-WITH-NO-POINTER", now=NOW)
    edit_history(db, "REASON-OF-AN-EDIT-WITH-NO-POINTER", post=7)
    assert reason_copies.scrub(db, entry) == ([], [])


# --- what is not cleared ----------------------------------------------------------------------------------------------------------------

def test_an_entry_with_no_reason_clears_nothing_and_a_pointer_stripped_entry_does_not_error(db):
    db.execute(text("UPDATE submissions_aux SET hidden_reason = 'STAYS' WHERE id = 7"))
    ma, entry = modaction(db, "hide_post_from_guild", 10, post=7, note="siege")                     # a bare marker: the entry has no reason
    assert value(db, "SELECT reason FROM log_entries WHERE id = :i", i=entry) == ""
    correction = redact(db, entry)
    assert value(db, "SELECT hidden_reason FROM submissions_aux WHERE id = 7") == "STAYS"
    assert staff_note(db, correction) == "a private detail slipped into the reason"
    stripped = log.record(db, "hide_post_from_guild", 10, board_id=1, reason="REASON-OF-AN-ITEM-WITH-NO-POINTER", role=log.GUILDMASTER, now=NOW)
    assert reason_copies.scrub(db, stripped) == ([], [])
    assert reason_copies.scrub(db, 99999) == ([], [])                                                # no such entry


def test_unrelated_actions_clear_nothing(db):
    db.execute(text("UPDATE submissions_aux SET hidden_reason = 'STAYS-ONE', ban_reason = 'STAYS-TWO' WHERE id = 7"))
    entry = log.record(db, "unhide_post_from_guild", 10, board_id=1, post_id=7, reason="a note on the lifting", role=log.GUILDMASTER, now=NOW)
    correction = redact(db, entry)
    assert one(db, "SELECT hidden_reason, ban_reason FROM submissions_aux WHERE id = 7") == ("STAYS-ONE", "STAYS-TWO")
    assert staff_note(db, correction) == "a private detail slipped into the reason"


def test_the_staff_notes_of_the_log_are_never_a_copy(db):
    ma, entry = hide_post(db, "PRIVATE-STAFF-CHECK")
    db.execute(text("UPDATE log_entries SET staff_note = 'PRIVATE-STAFF-CHECK context' WHERE id = :i"), {"i": entry})
    redact(db, entry)
    assert value(db, "SELECT staff_note FROM log_entries WHERE id = :i", i=entry) == "PRIVATE-STAFF-CHECK context"


# --- a copy that fails -------------------------------------------------------------------------------------------------------------------

def test_a_missing_table_is_counted_and_named_without_undoing_the_redaction_or_the_other_copies(db):
    ma, entry = hide_post(db, "PRIVATE-WITH-A-MISSING-TABLE")
    db.execute(text("DROP TABLE content_edit_history"))
    correction = redact(db, entry)
    assert value(db, "SELECT redacted_utc FROM log_entries WHERE id = :i", i=entry) > 0, "the redaction itself stands"
    assert value(db, "SELECT _note FROM modactions WHERE id = :m", m=ma) == "" and value(db, "SELECT hidden_reason FROM submissions_aux WHERE id = 7") == ""
    assert staff_note(db, correction) == ("a private detail slipped into the reason (also cleared: the guild log's note, the hidden post's reason) "
                                          "(could not clear: history rows)")
    rows = failures(db)
    assert len(rows) == 1 and rows[0][0] == "reason_copy" and "content_edit_history" in rows[0][1]


def test_a_missing_column_is_named_too(db):
    entry = ban(db, 30, "PRIVATE-REASON-AND-A-MISSING-COLUMN")
    db.execute(text("ALTER TABLE users DROP COLUMN ban_reason"))
    correction = redact(db, entry)
    assert value(db, "SELECT redacted_utc FROM log_entries WHERE id = :i", i=entry) > 0
    assert staff_note(db, correction) == "a private detail slipped into the reason (could not clear: the account's ban reason)"
    assert [r[0] for r in failures(db)] == ["reason_copy"]


def test_a_failure_of_the_whole_clearing_never_undoes_the_redaction(db, monkeypatch):
    def broken(db_, entry_id):
        raise RuntimeError("the copies could not even be looked for")
    monkeypatch.setattr(reason_copies, "scrub", broken)
    entry = ban(db, 30, "SOME-PRIVATE-WORDS")
    correction = redact(db, entry)
    assert value(db, "SELECT redacted_utc FROM log_entries WHERE id = :i", i=entry) > 0
    assert staff_note(db, correction) == "a private detail slipped into the reason (could not clear: every other copy)"
    assert [r[0] for r in failures(db)] == ["reason_copy"]


# --- redactions made before the copies were cleared ----------------------------------------------------------------------------------------

def test_scrub_redacted_mends_an_entry_redacted_before_and_a_second_run_changes_nothing(db):
    ma, entry = hide_post(db, "PRIVATE-REDACTED-LONG-AGO")
    _, kept = hide_post(db, "NOT-REDACTED", post=8)
    log.redact(db, entry, 21, now=NOW + 1)                                                          # the old way: the entry alone
    assert value(db, "SELECT hidden_reason FROM submissions_aux WHERE id = 7") == "PRIVATE-REDACTED-LONG-AGO"
    assert reason_copies.scrub_redacted(db) == (1, entry, 3)
    assert value(db, "SELECT hidden_reason FROM submissions_aux WHERE id = 7") == ""
    assert value(db, "SELECT _note FROM modactions WHERE id = :m", m=ma) == ""
    assert value(db, "SELECT hidden_reason FROM submissions_aux WHERE id = 8") == "NOT-REDACTED", "an entry nobody redacted is left alone"
    assert reason_copies.scrub_redacted(db) == (1, entry, 0)
    assert reason_copies.scrub_redacted(db, after=entry) == (0, entry, 0)
    assert value(db, "SELECT COUNT(*) FROM log_entries WHERE action = 'redact_entry'") == 0, "it writes no entry"
    assert kept


# --- the tripwire: a new writer of a reason column must be looked at --------------------------------------------------------------------------

# Every file under ruqqus/ that assigns a reason column, and how many assignments (a column definition counts). **A new one fails this test**: decide whether
# what it stores is a person's words that a decision's entry also holds, and if so clear it in helpers/reason_copies.py (and its tests); otherwise add it here
# with why it is not a copy.
WRITERS = {
    "classes/user.py": 2,             # the column, and `User.ban` (users.ban_reason: copied)
    "classes/comment.py": 6,          # the columns, and the setters: comments_aux.hidden_reason and author_hidden_reason (copied); ban_reason is only the spam guard's message
    "classes/submission.py": 4,       # the columns, and the setters: submissions_aux.ban_reason and hidden_reason (copied)
    "classes/images.py": 1,           # BadPic.ban_reason: the staff's own list of banned pictures, not a decision's reason
    "classes/boards.py": 1,           # the column (boards.ban_reason: copied)
    "classes/board_relationships.py": 1,   # bans.mod_note (copied)
    "routes/posts.py": 1,             # the spam guard's message: the site's own words
    "routes/admin_api.py": 7,         # an admin's removal (copied), a guild's ban (copied) and unban, the CSAM constant (the site's own words)
    "helpers/log_cascade.py": 1,      # an admin's nuke: submissions_aux.ban_reason of each post it removes (copied: `_removed_post_reason` clears it by place)
    "routes/log_appeal_pages.py": 1,  # a reversal empties a guild's ban reason
    "routes/admin.py": 1,             # BadPic.ban_reason
    "routes/alias_actions_pages.py": 2,    # bans.mod_note of an exile by alias (copied)
    "routes/boards.py": 3,            # a guild's hide (post.hidden_reason: copied) and an exile's note (copied)
    "routes/comments.py": 4,          # the spam guard's message (twice: the site's own words), a guild's hide and an author's hide (copied)
}
_ASSIGNS = re.compile(r"(?<![\w\"'])(?:\w+\.)*(?:ban_reason|hidden_reason|author_hidden_reason|mod_note)\s*=(?!=)")


def _writers():
    found = {}
    for path in (ROOT / "ruqqus").rglob("*.py"):
        if "translations" in path.parts or path.name == "reason_copies.py":
            continue
        count = 0
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("#"):
                continue
            code = line.split("  #")[0]
            count += len(_ASSIGNS.findall(code))
        if count:
            found[path.relative_to(ROOT / "ruqqus").as_posix()] = count
    return found


def test_no_new_writer_of_a_reason_column_goes_unlooked_at():
    assert _writers() == WRITERS, ("a writer of ban_reason / hidden_reason / author_hidden_reason / mod_note changed: if it stores the words of a decision "
                                   "the Log also holds, a redaction has to clear it (helpers/reason_copies.py); then update WRITERS here")


# The edit summary is a second place an edit entry's words live (content_edit_history.summary). Every file that writes one, and how many times: a new writer
# of a summary must be looked at the same way (does an entry hold the same words? then `_edit_summaries` must reach it).
SUMMARY_WRITERS = {"routes/posts.py": 1, "routes/comments.py": 1}
_SUMMARY = re.compile(r"(?<![\w.])summary\s*=(?!=)\s*action_log\.clean_reason")


def test_no_new_writer_of_an_edit_summary_goes_unlooked_at():
    found = {}
    for path in (ROOT / "ruqqus").rglob("*.py"):
        count = len(_SUMMARY.findall(path.read_text(encoding="utf-8")))
        if count:
            found[path.relative_to(ROOT / "ruqqus").as_posix()] = count
    assert found == SUMMARY_WRITERS, "a writer of content_edit_history.summary changed: a redaction of the edit's entry has to clear it (helpers/reason_copies.py)"


def test_every_live_column_the_module_clears_is_named_in_its_source_and_in_the_staff_note_labels():
    source = (ROOT / "ruqqus" / "helpers" / "reason_copies.py").read_text(encoding="utf-8")
    for fragment in ("UPDATE submissions_aux SET ban_reason", "UPDATE submissions_aux SET hidden_reason", "UPDATE comments_aux SET hidden_reason",
                     "UPDATE comments_aux SET author_hidden_reason", "UPDATE users SET ban_reason", "UPDATE boards SET ban_reason", "UPDATE bans SET mod_note",
                     "UPDATE modactions SET _note", "UPDATE content_edit_history SET reason", "UPDATE content_edit_history SET summary"):
        assert fragment in source, fragment
    assert [label for label, _, _ in reason_copies.COPIES] == [
        "the guild log's note", "the removed post's reason", "the hidden post's reason", "the hidden comment's reason",
        "the reason a post's author gave for hiding a comment", "the account's ban reason", "the alts' ban reasons", "the guild's ban reason",
        "the exile list's reason", "history rows", "edit summaries"]


def test_the_module_never_changes_what_a_decision_did():
    source = (ROOT / "ruqqus" / "helpers" / "reason_copies.py").read_text(encoding="utf-8")
    assert not re.search(r"\b(is_banned|unban_utc|is_active|hidden_by_guild|author_hidden|deleted_utc|is_approved)\s*=", source), \
        "a redaction hides words: it never unbans, unhides, restores or lifts an exile"
    assert source.count("UPDATE ") == 10 and "DELETE" not in source and "INSERT" not in source


def test_correct_runs_the_copies_and_imports_the_module_lazily():
    source = (ROOT / "ruqqus" / "helpers" / "action_log.py").read_text(encoding="utf-8")
    body = source.split("def correct(db, entry_id, admin, what):")[1].split("\n\n\ndef ")[0]
    assert "from ruqqus.helpers import reason_copies" in body and "reason_copies.scrub(db, entry_id)" in body and "reason_copies.describe(what, cleared, failed)" in body
    assert not re.search(r"^(?:from|import) .*reason_copies", source, re.M), "imported inside correct only: reason_copies reads this module's rules"
