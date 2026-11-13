"""Appeals against the Log (helpers/log_appeals.py, the Log's slice D2), on SQLite with the tables built from the migrations.

The promises: only the person the decision is about may appeal it, once, and three a day; a decision already put right can't be
appealed; a guildmaster's decision is decided by that guild's guildmasters with the right permission and an admin's by the site's
admins, never by the one who made it while another could; a decision is made once."""
from pathlib import Path

import log_tables
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from ruqqus.helpers import action_log as log
from ruqqus.helpers import log_appeals as appeals

ROOT = Path(__file__).resolve().parent.parent
NOW = 1_793_000_000
DAY = 86400


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        for statement in log_tables.migration_statements((ROOT / "scripts" / "migrations" / "2026-12-09_action_log.sql").read_text(encoding="utf-8")):
            conn.execute(text(statement))
        log_tables.apply(conn, modactions=True)
        conn.execute(text("CREATE TABLE submissions (id INTEGER PRIMARY KEY, author_id INTEGER, repost_id INTEGER DEFAULT 0, is_anonymous BOOLEAN DEFAULT 0)"))
        conn.execute(text("CREATE TABLE comments (id INTEGER PRIMARY KEY, author_id INTEGER, is_anonymous BOOLEAN DEFAULT 0)"))
        conn.execute(text("CREATE TABLE forwardrels (id INTEGER PRIMARY KEY, forward_submission_id INTEGER, forwarded_by_id INTEGER)"))
        conn.execute(text("CREATE TABLE comment_forwardrels (id INTEGER PRIMARY KEY, promoted_submission_id INTEGER, promoted_by_id INTEGER)"))
        conn.execute(text("CREATE TABLE boards (id INTEGER PRIMARY KEY, is_private BOOLEAN DEFAULT 0, is_circle BOOLEAN DEFAULT 0)"))
        conn.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, admin_level INTEGER DEFAULT 0, is_banned INTEGER DEFAULT 0, "
                          "unban_utc INTEGER DEFAULT 0, is_deleted BOOLEAN DEFAULT 0)"))
        conn.execute(text("CREATE TABLE mods (id INTEGER PRIMARY KEY, user_id INTEGER, board_id INTEGER, accepted BOOLEAN DEFAULT 1, "
                          "invite_rescinded BOOLEAN DEFAULT 0, perm_full BOOLEAN DEFAULT 0, perm_content BOOLEAN DEFAULT 0, "
                          "perm_access BOOLEAN DEFAULT 0)"))
        conn.execute(text("INSERT INTO boards VALUES (1, 0, 0), (3, 0, 0)"))
        # 10 and 11 are guildmasters of +3 (10: content, 11: access), 20 and 21 admins, 30 a member, 31 the forwarder
        conn.execute(text("INSERT INTO users (id, admin_level) VALUES (1, 0), (10, 0), (11, 0), (12, 0), (20, 3), (21, 3), (30, 0), (31, 0), (32, 0)"))
        conn.execute(text("INSERT INTO mods (user_id, board_id, perm_content) VALUES (10, 3, 1)"))
        conn.execute(text("INSERT INTO mods (user_id, board_id, perm_access) VALUES (11, 3, 1)"))
        conn.execute(text("INSERT INTO submissions (id, author_id) VALUES (7, 30), (8, 30)"))
        conn.execute(text("INSERT INTO forwardrels (forward_submission_id, forwarded_by_id) VALUES (8, 31)"))
        conn.execute(text("INSERT INTO comments (id, author_id) VALUES (50, 30), (51, 32)"))
    with Session(engine) as session:
        yield session


def hide_post(db, post=7, actor=10, board=3):
    return log.record(db, "hide_post_from_guild", actor, board_id=board, post_id=post, rule="guild_rule", reason="Off topic", now=NOW)


# --- who may appeal what ------------------------------------------------------------------------------------------------------

def test_the_person_a_decision_is_about_is_the_one_who_may_appeal_it(db):
    exile = log.record(db, "exile_user", 10, board_id=3, target_user_id=30, now=NOW)
    ban = log.record_ban(db, 31, 20, days=0, reason="Spam")
    comment = log.record(db, "hide_comment_from_guild", 10, board_id=3, comment_id=50, now=NOW)
    for entry_id, person in ((exile, 30), (ban, 31), (comment, 30)):
        entry = appeals.entry_of(db, entry_id)
        assert appeals.subject_of(db, entry) == person
        assert appeals.can_appeal(db, entry, person, NOW) is None
        for stranger in (1, 10, 20, 32):
            assert appeals.can_appeal(db, entry, stranger, NOW) is not None, (entry_id, stranger)


def test_a_guild_copys_decision_is_about_whoever_forwarded_it_not_the_original_author(db):
    entry = appeals.entry_of(db, hide_post(db, post=8))
    assert appeals.subject_of(db, entry) == 31
    assert appeals.can_appeal(db, entry, 31, NOW) is None and appeals.can_appeal(db, entry, 30, NOW) is not None
    assert appeals.subject_of(db, appeals.entry_of(db, hide_post(db, post=7))) == 30        # a post that was never forwarded: its author


def test_a_stranger_and_an_entry_that_cant_be_appealed_get_the_same_answer(db):
    hide = appeals.entry_of(db, hide_post(db))
    staff_only = appeals.entry_of(db, log.record_staff(db, "admin_word_filter", type("A", (), {"id": 20})(), "x"))
    answers = {appeals.can_appeal(db, hide, 1, NOW), appeals.can_appeal(db, staff_only, 30, NOW), appeals.can_appeal(db, None, 30, NOW)}
    assert answers == {"There is nothing here to appeal."}
    assert str(appeals.can_appeal(db, hide, 1, NOW)) == "There is nothing here to appeal."      # never says who decided or why


def test_it_is_once_per_decision_and_three_a_day(db):
    ids = [hide_post(db, post=7) for _ in range(4)]
    first = appeals.file_appeal(db, ids[0], 30, "I did not break the rule", now=NOW)
    assert first
    with pytest.raises(appeals.AppealRefused, match="already appealed"):
        appeals.file_appeal(db, ids[0], 30, "again", now=NOW + 5)
    appeals.file_appeal(db, ids[1], 30, "second", now=NOW + 10)
    appeals.file_appeal(db, ids[2], 30, "third", now=NOW + 20)
    with pytest.raises(appeals.AppealRefused, match="three appeals a day"):
        appeals.file_appeal(db, ids[3], 30, "fourth", now=NOW + 30)
    assert appeals.file_appeal(db, ids[3], 30, "fourth, a day later", now=NOW + DAY + 100)


def test_an_appeal_needs_words_and_they_are_plain_text_cut_to_500(db):
    entry = hide_post(db)
    with pytest.raises(appeals.AppealRefused, match="Say why"):
        appeals.file_appeal(db, entry, 30, "   \n  ", now=NOW)
    made = appeals.file_appeal(db, entry, 30, "line one\n\n   line   two " + "x" * 800, now=NOW)
    reason = db.execute(text("SELECT reason FROM log_appeals WHERE id = :i"), {"i": made}).scalar()
    assert reason.startswith("line one line two xxx") and len(reason) == 500 and "\n" not in reason


def test_a_decision_already_put_right_cant_be_appealed(db):
    hide = hide_post(db)
    log.record(db, "unhide_post_from_guild", 10, board_id=3, post_id=7, now=NOW + 5)
    assert appeals.can_appeal(db, appeals.entry_of(db, hide), 30, NOW + 10) == "This decision has already been lifted."
    ban = log.record_ban(db, 31, 20, days=0, reason="x")
    assert appeals.can_appeal(db, appeals.entry_of(db, ban), 31, NOW) is None
    log.record_unban(db, 31, 20)
    assert appeals.can_appeal(db, appeals.entry_of(db, ban), 31, NOW) == "This decision has already been lifted."
    # an unhide in another guild puts nothing right here
    other = hide_post(db, post=8)
    log.record(db, "unhide_post_from_guild", 10, board_id=1, post_id=8, now=NOW + 5)
    assert appeals.can_appeal(db, appeals.entry_of(db, other), 31, NOW + 10) is None


# --- who decides ------------------------------------------------------------------------------------------------------------

def test_a_guildmasters_decision_goes_to_that_guilds_guildmasters_and_everything_else_to_the_admins(db):
    assert appeals.board_for(db, appeals.entry_of(db, hide_post(db))) == 3
    assert appeals.board_for(db, appeals.entry_of(db, log.record(db, "ban_post", 20, role=log.ADMIN, board_id=3, post_id=7))) is None
    assert appeals.board_for(db, appeals.entry_of(db, log.record_automatic(db, "ban_post", post_id=7, board_id=3))) is None
    assert appeals.board_for(db, appeals.entry_of(db, log.record(db, "author_hide_comment", 30, role=log.AUTHOR, board_id=3, comment_id=51))) is None
    assert appeals.board_for(db, appeals.entry_of(db, log.record(db, "hide_post_from_guild", 10, board_id=2, post_id=7))) is None     # +systemprofile
    on_site = appeals.file_appeal(db, log.record_ban(db, 31, 20, days=0, reason="x"), 31, "no", now=NOW)
    in_guild = appeals.file_appeal(db, hide_post(db), 30, "no", now=NOW)
    assert [a["id"] for a in appeals.open_queue(db, None)] == [on_site]
    assert [a["id"] for a in appeals.open_queue(db, 3)] == [in_guild]


def test_the_permission_that_decides_is_the_one_the_action_asked_for(db):
    assert appeals.permission_for({"action": "hide_post_from_guild"}) == "content"
    assert appeals.permission_for({"action": "exile_user"}) == "access"
    assert appeals.permission_for({"action": "ban_user"}) is None


def test_the_one_who_decided_does_not_decide_the_appeal_while_another_could(db):
    # +3 has one guildmaster with content (10), and another with access only (11): 10 is alone for a hide, so they decide it
    hide = appeals.file_appeal(db, hide_post(db, actor=10), 30, "no", now=NOW)
    assert not appeals.conflicted(db, appeals.appeal_of(db, hide), 10, NOW)
    db.execute(text("INSERT INTO mods (user_id, board_id, perm_content) VALUES (12, 3, 1)"))
    assert appeals.conflicted(db, appeals.appeal_of(db, hide), 10, NOW)             # now 12 can: it isn't theirs
    assert not appeals.conflicted(db, appeals.appeal_of(db, hide), 12, NOW)
    # a full guildmaster counts for any permission, a deleted or suspended one does not
    db.execute(text("UPDATE mods SET perm_content = 0, perm_full = 1 WHERE user_id = 12"))
    assert appeals.conflicted(db, appeals.appeal_of(db, hide), 10, NOW)
    db.execute(text("UPDATE users SET is_deleted = 1 WHERE id = 12"))
    assert not appeals.conflicted(db, appeals.appeal_of(db, hide), 10, NOW)


def test_an_admin_does_not_decide_the_appeal_of_their_own_decision_while_another_admin_exists(db):
    ban = appeals.file_appeal(db, log.record_ban(db, 31, 20, days=0, reason="x"), 31, "no", now=NOW)
    assert appeals.conflicted(db, appeals.appeal_of(db, ban), 20, NOW)               # 21 could
    assert not appeals.conflicted(db, appeals.appeal_of(db, ban), 21, NOW)
    db.execute(text("UPDATE users SET admin_level = 0 WHERE id = 21"))
    assert not appeals.conflicted(db, appeals.appeal_of(db, ban), 20, NOW)           # the only admin decides their own
    # the system's decision has no one to be in conflict with
    spam = appeals.file_appeal(db, log.record_automatic(db, "ban_comment", comment_id=50, board_id=1), 30, "not spam", now=NOW)
    assert not appeals.conflicted(db, appeals.appeal_of(db, spam), 20, NOW)


def test_an_appeal_is_decided_once_and_the_outcome_is_one_of_three(db):
    made = appeals.file_appeal(db, hide_post(db), 30, "no", now=NOW)
    with pytest.raises(ValueError):
        appeals.decide(db, made, "maybe", 10, "x")
    assert appeals.decide(db, made, appeals.UPHELD, 10, "  The rule is clear.  ", now=NOW + 60) is True
    assert appeals.decide(db, made, appeals.REVERSED, 10, "changed my mind", now=NOW + 90) is False       # a second decision changes nothing
    row = db.execute(text("SELECT status, decided_by, decided_utc, decision_note FROM log_appeals WHERE id = :i"), {"i": made}).first()
    assert tuple(row) == ("upheld", 10, NOW + 60, "The rule is clear.")
    assert appeals.open_queue(db, 3) == [] and appeals.waiting_count(db, 3) == 0


def test_a_reversal_names_the_entry_it_wrote(db):
    made = appeals.file_appeal(db, hide_post(db), 30, "no", now=NOW)
    undo = log.record(db, "unhide_post_from_guild", 10, board_id=3, post_id=7, now=NOW + 5, linked_entry_id=1)
    assert appeals.decide(db, made, appeals.REVERSED, 10, "You are right.", now=NOW + 6, reversal_entry_id=undo)
    assert db.execute(text("SELECT reversal_entry_id FROM log_appeals WHERE id = :i"), {"i": made}).scalar() == undo
    mine = appeals.mine(db, 30)
    assert [(a["status"], a["decision_note"], a["action"]) for a in mine] == [("reversed", "You are right.", "hide_post_from_guild")]


def test_the_queue_names_the_item_by_id_and_never_carries_its_words(db):
    appeals.file_appeal(db, hide_post(db), 30, "I disagree", now=NOW)
    row = appeals.open_queue(db, 3)[0]
    assert set(row) >= {"id", "entry_id", "user_id", "reason", "action", "target_submission_id", "entry_reason", "rule_key"}
    assert not {"title", "body", "url"} & set(row)
