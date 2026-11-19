"""What the site's own checks do to an account (ruqqus/helpers/auto_quarantine.py), on SQLite with the Log's tables built from the migrations: they quarantine,
they never ban; the staff's entry names the account and the public line names nobody; a second check on a held account writes nothing; only an admin or an
appeal lifts a hold; and no email, no signup-guard approval and nothing else can."""
import re
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from ruqqus.helpers import action_log as log
from ruqqus.helpers import auto_quarantine as aq
from ruqqus.helpers import log_notices, quarantine_review, signup_review_store

import log_tables

ROOT = Path(__file__).resolve().parent.parent
MIGRATION = "2026-12-09_action_log.sql"
NOW = int(time.time())


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        for statement in log_tables.migration_statements(read("scripts", "migrations", MIGRATION)):
            conn.execute(text(statement))
        log_tables.apply(conn)
        conn.execute(text("CREATE TABLE boards (id INTEGER PRIMARY KEY, is_private BOOLEAN DEFAULT 0, is_circle BOOLEAN DEFAULT 0)"))
        conn.execute(text("CREATE TABLE submissions (id INTEGER PRIMARY KEY, audience INTEGER DEFAULT 0, author_id INTEGER, repost_id INTEGER DEFAULT 0, is_anonymous BOOLEAN DEFAULT 0)"))
        conn.execute(text("CREATE TABLE comments (id INTEGER PRIMARY KEY, parent_submission INTEGER, author_id INTEGER, is_anonymous BOOLEAN DEFAULT 0)"))
        conn.execute(text(
            "CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, created_utc INTEGER DEFAULT 0, admin_level INTEGER DEFAULT 0, is_banned INTEGER DEFAULT 0, unban_utc INTEGER DEFAULT 0, "
            "is_deleted BOOLEAN DEFAULT 0, is_bot BOOLEAN DEFAULT 0, quarantined_utc INTEGER DEFAULT 0 NOT NULL, quarantine_kind INTEGER DEFAULT 0 NOT NULL, "
            "ban_evade INTEGER DEFAULT 0)"))
        conn.execute(text("CREATE TABLE signup_reviews (id INTEGER PRIMARY KEY, user_id INTEGER UNIQUE, created_utc INTEGER DEFAULT 0, score INTEGER DEFAULT 0, "
                          "reasons TEXT DEFAULT '', network TEXT DEFAULT '', name_shape TEXT DEFAULT '', decision TEXT DEFAULT '', decided_utc INTEGER DEFAULT 0, "
                          "decided_by INTEGER DEFAULT 0)"))
        conn.execute(text("INSERT INTO users (id, username, admin_level) VALUES (1, 'Laba', 0), (20, 'admin', 3), (21, 'owner', 4)"))
        for uid, name in ((30, "spammer"), (31, "alt_a"), (32, "alt_b"), (33, "bystander")):
            conn.execute(text("INSERT INTO users (id, username) VALUES (:i, :n)"), {"i": uid, "n": name})
    with Session(engine) as session:
        yield session


def who(db, uid):
    return SimpleNamespace(id=uid, username=db.execute(text("SELECT username FROM users WHERE id = :u"), {"u": uid}).scalar())


def state(db, uid):
    return tuple(db.execute(text("SELECT quarantined_utc > 0, quarantine_kind FROM users WHERE id = :u"), {"u": uid}).first())


def entries(db, **where):
    clause = " AND ".join(f"{k} = :{k}" for k in where) or "1 = 1"
    return [dict(r._mapping) for r in db.execute(text(f"SELECT * FROM log_entries WHERE {clause} ORDER BY id"), where)]


def queued(db):
    return list(db.info.get(aq._PENDING, []))


def publish(db, at=None):
    """Run the cycle's step that writes the public lines, as if it ran just past the delay after `at` (default: now)."""
    return aq.publish_due(db, now=int(at if at is not None else time.time()) + aq.PUBLISH_DELAY + 1)


# --- one quarantine: the staff's entry, the public line, the account ---------------------------------------------------------------------------

def test_a_check_quarantines_and_never_bans(db):
    entry = aq.hold(db, who(db, 30), "spam", detail="six similar posts in a minute")
    assert entry and state(db, 30) == (True, 1)
    assert db.execute(text("SELECT is_banned, unban_utc FROM users WHERE id = 30")).first() == (0, 0)
    assert not entries(db, action="ban_user") and not entries(db, action="suspend_user")


def test_the_staff_entry_names_the_account_the_trigger_and_the_detail_and_is_the_staffs_alone(db):
    entry = aq.hold(db, who(db, 30), "spam", detail="six similar posts in a minute")
    (row,) = entries(db, id=entry)
    assert (row["action"], row["log_type"], row["actor_role"], row["actor_id"], row["target_user_id"]) == ("quarantine_user", "sanction", "system", 1, 30)
    assert (row["rule_key"], row["reason"], row["audience"]) == ("evasion", "Automatic spam check", log.STAFF)
    assert row["staff_note"] == "spam | six similar posts in a minute"
    assert aq.trigger_of(row["staff_note"]) == "spam" and aq.detail_of(row["staff_note"]) == "six similar posts in a minute"


def test_the_public_line_names_no_account_no_item_and_no_guild(db):
    entry = aq.hold(db, who(db, 30), "spam", detail="six similar posts in a minute")
    assert entries(db, id=entry) and not entries(db, action="automatic_quarantine")      # hold() writes the staff entry only
    assert publish(db) == 1
    (public,) = entries(db, action="automatic_quarantine")
    assert (public["audience"], public["log_type"], public["actor_role"], public["actor_id"]) == (log.PUBLIC, "sanction", "system", 1)
    assert (public["target_user_id"], public["target_submission_id"], public["target_comment_id"], public["board_id"], public["subject_user_id"]) == (None,) * 5
    assert (public["rule_key"], public["reason"], public["alias_n"]) == ("evasion", "Automatic spam check", None)
    assert public["linked_entry_id"] == entry                      # a column only staff read
    assert "spam" not in public["staff_note"] and public["staff_note"] == ""


def test_the_public_line_is_dated_to_the_hour_and_the_staffs_entry_keeps_the_exact_time(db, monkeypatch):
    """A removal the same request made carries its exact second: an exact public line would pair the two and name the post's author (the review of 2026-11-18)."""
    at = (NOW // 3600) * 3600 + 1234                                                 # twenty minutes and some seconds into an hour
    monkeypatch.setattr(aq.time, "time", lambda: at)
    entry = aq.hold(db, who(db, 30), "spam")
    assert not entries(db, action="automatic_quarantine")
    publish(db, at)
    (public,) = entries(db, action="automatic_quarantine")
    assert public["created_utc"] == at - 1234 == aq.public_time(at) and entries(db, id=entry)[0]["created_utc"] == at
    assert aq.public_time(at - 1234) == at - 1234 and aq.public_time(at - 1235) == at - 1234 - 3600       # a step is the hour it falls in
    # an alt's staff entry keeps the exact time too, and the public line of an account is never later than the staff's
    aq.hold(db, who(db, 31), "spam", alts=[who(db, 32)])
    publish(db, at)
    assert {e["created_utc"] for e in entries(db, action="quarantine_user")} == {at}
    assert all(e["created_utc"] <= at for e in entries(db, action="automatic_quarantine"))


def test_the_public_lifting_is_dated_to_the_hour_and_a_hold_and_its_lifting_in_one_hour_keep_their_order(db, monkeypatch):
    at = (NOW // 3600) * 3600 + 700
    monkeypatch.setattr(aq.time, "time", lambda: at)
    entry = aq.hold(db, who(db, 30), "spam")
    publish(db, at)                                                                 # the hold's line is written while it still stands
    monkeypatch.setattr(aq.time, "time", lambda: at + 2000)                         # released in the same hour
    lifted = aq.release(db, 30, admin_id=20, note="fine")
    assert not entries(db, action="automatic_quarantine_lifted")                    # the lifting's line comes later too
    publish(db, at + 2000)
    (public,) = entries(db, action="automatic_quarantine_lifted")
    assert public["created_utc"] == at - 700 and entries(db, id=lifted)[0]["created_utc"] == at + 2000
    assert entries(db, id=entry)[0]["created_utc"] == at
    # the public Log lists them newest first by (created_utc, id): the same hour falls back to the id, so the lifting is still above the hold
    assert [e["action"] for e in log.fetch(db, level=0, log_type="sanction")[0]] == ["automatic_quarantine_lifted", "automatic_quarantine"]
    # a hold and its lifting in different hours keep their order by time
    monkeypatch.setattr(aq.time, "time", lambda: at + 3 * 3600)
    aq.hold(db, who(db, 31), "spam")
    publish(db, at + 3 * 3600)
    monkeypatch.setattr(aq.time, "time", lambda: at + 5 * 3600)
    aq.release(db, 31, admin_id=20)
    publish(db, at + 5 * 3600)
    lines = log.fetch(db, level=0, log_type="sanction")[0]
    assert [e["created_utc"] for e in lines] == sorted((e["created_utc"] for e in lines), reverse=True)
    assert all(e["created_utc"] % 3600 == 0 for e in lines)


def test_a_visitor_reads_the_public_line_and_not_the_staffs_and_staff_read_both(db):
    aq.hold(db, who(db, 30), "spam", detail="six similar posts in a minute")
    assert log.fetch(db, level=0, log_type="sanction")[0] == []                     # nothing public until the cycle writes it
    assert {e["action"] for e in log.fetch(db, level=3, log_type="sanction")[0]} == {"quarantine_user"}
    publish(db)
    public, _more = log.fetch(db, level=0, log_type="sanction")
    assert [e["action"] for e in public] == ["automatic_quarantine"]
    assert all(e["target_user_id"] is None for e in public) and "staff_note" not in public[0]
    staff, _more = log.fetch(db, level=3, log_type="sanction")
    assert {e["action"] for e in staff} == {"automatic_quarantine", "quarantine_user"}
    assert log.fetch(db, level=0, target_user_id=30)[0] == []       # the account's own log never lists it either


def test_the_public_line_is_there_for_every_trigger_but_evasion(db):
    for uid, trigger in ((30, "spam"), (31, "seo_spam"), (32, "link_malicious"), (33, "image")):
        aq.hold(db, who(db, uid), trigger)
    publish(db)
    assert [e["reason"] for e in entries(db, action="automatic_quarantine")] == [
        "Automatic spam check", "Automatic spam check", "Automatic link check", "Automatic image check"]
    db.execute(text("INSERT INTO users (id, username) VALUES (40, 'evader')"))
    assert aq.hold(db, who(db, 40), "evasion", detail="its browser had been used by a suspended account")
    publish(db)
    assert len(entries(db, action="automatic_quarantine")) == 4                  # no new public line: it would teach how alts are caught
    (evasion,) = entries(db, action="quarantine_user", target_user_id=40)
    assert evasion["audience"] == log.STAFF and evasion["rule_key"] == "evasion"


def test_a_link_about_minors_is_level_4s_to_read(db):
    entry = aq.hold(db, who(db, 30), "link_minors", detail="the domain's own reason")
    assert entries(db, id=entry)[0]["audience"] == log.LEGAL
    assert not entries(db, action="automatic_quarantine")
    publish(db)
    assert [e["id"] for e in log.fetch(db, level=3, log_type="sanction")[0]] == [entries(db, action="automatic_quarantine")[0]["id"]]
    assert entry in {e["id"] for e in log.fetch(db, level=4, log_type="sanction")[0]}
    (public,) = entries(db, action="automatic_quarantine")
    assert public["reason"] == "Automatic link check" and public["audience"] == log.PUBLIC


def test_an_image_checks_match_is_never_written_into_the_staff_entry(db):
    entry = aq.hold(db, who(db, 30), "image", detail="matched the CDN's list: hash 123")
    row = entries(db, id=entry)[0]
    assert row["staff_note"] == "image" and "hash" not in row["staff_note"]          # the match is a separate, linked, level-4 entry (action_log.record_detail)
    detail = log.record_detail(db, entry, "matched the CDN's list: hash 123", target_user_id=30, legal=True)
    assert entries(db, id=detail)[0]["audience"] == log.LEGAL and entries(db, id=detail)[0]["linked_entry_id"] == entry


def test_a_detail_is_cleaned_and_cut(db):
    entry = aq.hold(db, who(db, 30), "spam", detail="a\n\nb   " + "x" * 800)
    note = entries(db, id=entry)[0]["staff_note"]
    assert note.startswith("spam | a b ") and "\n" not in note and len(note) <= log.REASON_MAX


def test_an_unknown_trigger_is_a_programming_error(db):
    with pytest.raises(KeyError):
        aq.hold(db, who(db, 30), "made_up")
    assert state(db, 30) == (False, 0) and not entries(db)


# --- quiet and idempotent; who is never held ---------------------------------------------------------------------------------------------------

def test_a_second_check_on_a_held_account_writes_nothing_and_tells_nobody(db):
    first = aq.hold(db, who(db, 30), "spam")
    count = len(entries(db))
    assert first and aq.hold(db, who(db, 30), "spam") is None and aq.hold(db, who(db, 30), "link_malicious", alts=[who(db, 31)]) is None
    assert len(entries(db)) == count and len(queued(db)) == 1
    assert state(db, 31) == (False, 0)                                # its alts are not swept up by a check that changed nothing


@pytest.mark.parametrize("column, value", [("admin_level", 3), ("is_bot", 1), ("is_deleted", 1), ("is_banned", 1)])
def test_an_admin_a_bot_a_deleted_or_a_banned_account_is_never_quarantined(db, column, value):
    db.execute(text(f"UPDATE users SET {column} = :v WHERE id = 30"), {"v": value})
    assert aq.hold(db, who(db, 30), "spam") is None
    assert state(db, 30) == (False, 0) and not entries(db) and not queued(db)


def test_a_suspension_that_ran_out_no_longer_protects_and_one_that_has_not_does(db):
    db.execute(text("UPDATE users SET is_banned = 1, unban_utc = :t WHERE id = 30"), {"t": NOW + 3600})
    db.execute(text("UPDATE users SET is_banned = 1, unban_utc = :t WHERE id = 31"), {"t": NOW - 3600})
    assert aq.hold(db, who(db, 30), "spam") is None
    assert aq.hold(db, who(db, 31), "spam")


def test_a_signup_guard_hold_becomes_an_automatic_one_so_an_email_cannot_release_it(db):
    db.execute(text("UPDATE users SET quarantined_utc = :t WHERE id = 30"), {"t": NOW - 500})
    db.execute(text("INSERT INTO signup_reviews (user_id, created_utc, decision) VALUES (30, :t, '')"), {"t": NOW - 500})
    assert state(db, 30) == (True, 0)
    assert aq.hold(db, who(db, 30), "spam")
    assert state(db, 30) == (True, 1)
    # the guard's own queue does not list it, approving or verifying clears nothing, and neither does a bulk decision
    assert signup_review_store.waiting_count(db) == 0 and signup_review_store.held_among(db, [30]) == []
    assert signup_review_store.release(db, 30, "verified", NOW) is False
    assert signup_review_store.decide(db, [30], "approved", 20, NOW) == []
    assert state(db, 30) == (True, 1)
    assert db.execute(text("SELECT decision FROM signup_reviews WHERE user_id = 30")).scalar() == ""     # its review row was not closed as verified either


def test_the_guards_own_hold_is_still_released_by_it_and_a_later_automatic_hold_is_not_touched(db):
    db.execute(text("UPDATE users SET quarantined_utc = :t WHERE id IN (30, 31)"), {"t": NOW})
    for uid in (30, 31):
        db.execute(text("INSERT INTO signup_reviews (user_id, created_utc, decision) VALUES (:u, :t, '')"), {"u": uid, "t": NOW})
    assert signup_review_store.release(db, 30, "verified", NOW) is True and state(db, 30) == (False, 0)
    assert signup_review_store.decide(db, [31], "approved", 20, NOW) == [31] and state(db, 31) == (False, 0)


# --- the alts -------------------------------------------------------------------------------------------------------------------------------------------

def test_alts_are_held_with_staff_only_entries_and_no_public_line_and_no_notice(db):
    db.execute(text("UPDATE users SET admin_level = 3 WHERE id = 32"))             # an admin among them is left alone
    aq.hold(db, who(db, 30), "spam", alts=[who(db, 31), 32, 30, who(db, 33)])
    publish(db)
    assert [state(db, u) for u in (31, 32, 33)] == [(True, 1), (False, 0), (True, 1)]
    alt_entries = entries(db, action="quarantine_user")
    assert [(e["target_user_id"], e["audience"]) for e in alt_entries[1:]] == [(31, log.STAFF), (33, log.STAFF)]
    assert alt_entries[1]["staff_note"] == "spam | alt of @spammer" and alt_entries[1]["linked_entry_id"] == alt_entries[0]["id"]
    assert len(entries(db, action="automatic_quarantine")) == 1                    # the main account's line; an alt says which accounts are linked
    assert [(kind, uid) for kind, uid, *_ in queued(db)] == [("held", 30)]


def test_an_alt_already_held_by_a_check_is_not_written_again(db):
    aq.hold(db, who(db, 31), "spam")
    count = len(entries(db))
    aq.hold(db, who(db, 30), "spam", alts=[who(db, 31)])
    assert len(entries(db)) == count + 1                                           # the main's staff entry only: no entry for the alt held already
    publish(db)
    assert len(entries(db)) == count + 3 and len(entries(db, action="automatic_quarantine")) == 2     # a line for each main account, none for an alt


def test_a_link_about_minors_keeps_its_alts_entries_level_4_too(db):
    aq.hold(db, who(db, 30), "link_minors", alts=[31])
    assert {e["audience"] for e in entries(db, action="quarantine_user")} == {log.LEGAL}


# --- the account object -----------------------------------------------------------------------------------------------------------------------------------

def test_a_plain_object_reads_its_new_state_and_an_orm_one_is_expired(db):
    plain = who(db, 30)
    aq.hold(db, plain, "spam")
    assert plain.quarantined_utc > 0 and plain.quarantine_kind == 1


# --- the notice: after the caller's commit, checked again when it goes --------------------------------------------------------------------------------------------

def test_the_notice_is_queued_for_after_the_commit_with_the_rule_and_the_generic_reason(db):
    entry = aq.hold(db, who(db, 30), "spam")
    assert queued(db) == [("held", 30, entry, "evasion", "Automatic spam check")]
    aq.hold(db, who(db, 31), "evasion")
    assert queued(db)[-1][3] == "" and queued(db)[-1][4] == "Automatic check"       # ban-evasion detection names no rule to the person


def test_notify_false_queues_nothing(db):
    aq.hold(db, who(db, 30), "spam", notify=False)
    assert queued(db) == []


def test_a_queued_notice_goes_once_and_only_if_the_hold_and_its_entry_are_still_there(db, monkeypatch):
    told = []
    monkeypatch.setattr(log_notices, "tell_quarantined", lambda d, uid, entry, **kw: told.append(("held", uid, entry, kw)) or True)
    monkeypatch.setattr(log_notices, "tell_lifted", lambda d, uid, message, **kw: told.append(("lifted", uid, message)) or True)
    entry = aq.hold(db, who(db, 30), "spam")
    assert aq._send_all(db, db.info[aq._PENDING]) == 1 and queued(db) == []
    assert told == [("held", 30, entry, {"rule_key": "evasion", "reason": "Automatic spam check"})]

    # a transaction that rolled back (its entry is gone) or an account put right since tells nobody
    entry = aq.hold(db, who(db, 31), "spam")
    db.execute(text("DELETE FROM log_entries WHERE id = :e"), {"e": entry})
    assert aq._send_all(db, db.info[aq._PENDING]) == 0
    aq.hold(db, who(db, 32), "spam")
    db.execute(text("UPDATE users SET quarantined_utc = 0, quarantine_kind = 0 WHERE id = 32"))
    assert aq._send_all(db, db.info[aq._PENDING]) == 0 and len(told) == 1


def test_a_failing_notice_is_logged_and_never_stops_the_rest(db, monkeypatch):
    calls = []

    def boom(d, uid, entry, **kw):
        calls.append(uid)
        raise RuntimeError("mail is down")
    monkeypatch.setattr(log_notices, "tell_quarantined", boom)
    aq.hold(db, who(db, 30), "spam")
    aq.hold(db, who(db, 31), "spam")
    db.commit()                                                  # notices go after the caller's commit: a failure rolls back only what is not committed
    assert aq._send_all(db, db.info[aq._PENDING]) == 0 and calls == [30, 31] and queued(db) == []


# --- the staff's reads ----------------------------------------------------------------------------------------------------------------------------------------

def test_the_current_entry_is_the_accounts_own_automatic_quarantines(db):
    assert aq.current_entry(db, 30) is None
    first = aq.hold(db, who(db, 30), "spam", detail="one")
    current = aq.current_entry(db, 30)
    assert (current["id"], current["trigger"], current["detail"], current["audience"], current["alt_of"]) == (first, "spam", "one", log.STAFF, "")
    assert aq.current_entry(db, 33) is None and aq.current_entry(db, 20) is None
    aq.release(db, 30, admin_id=20)
    assert aq.current_entry(db, 30) is None
    second = aq.hold(db, who(db, 30), "link_malicious")
    assert aq.current_entry(db, 30)["id"] == second and second != first             # the newest, never the one that was lifted


def test_a_quarantine_is_lifted_by_the_entry_that_links_it(db):
    entry = aq.hold(db, who(db, 30), "spam")
    assert aq.lifted_by(db, entry) is None
    lifted = aq.release(db, 30, admin_id=20)
    assert aq.lifted_by(db, entry) == lifted and aq.lifted_by(db, lifted) is None


def test_an_alts_current_entry_says_whose_alt_it_is(db):
    aq.hold(db, who(db, 30), "spam", alts=[31])
    assert aq.current_entry(db, 31)["alt_of"] == "spammer"


def test_a_hold_with_no_entry_has_no_current_entry(db):
    db.execute(text("UPDATE users SET quarantined_utc = :t, quarantine_kind = 1 WHERE id = 30"), {"t": NOW})
    assert aq.current_entry(db, 30) is None
    rows, _more = quarantine_review.queue(db)
    assert [(r["user_id"], r["entry_id"], r["trigger"]) for r in rows] == [(30, None, "")]


def test_the_queue_lists_held_accounts_newest_first_with_trigger_and_appeal_state(db):
    older = aq.hold(db, who(db, 30), "spam", detail="one")
    db.execute(text("UPDATE users SET quarantined_utc = quarantined_utc - 100 WHERE id = 30"))
    aq.hold(db, who(db, 31), "seo_spam", detail="a link in a bio")
    db.execute(text("INSERT INTO log_appeals (entry_id, user_id, reason, created_utc, status) VALUES (:e, 30, 'not me', :t, 'open')"), {"e": older, "t": NOW})
    rows, more = quarantine_review.queue(db)
    assert not more and [(r["user_id"], r["trigger"], r["detail"], r["appeal"], r["username"]) for r in rows] == [
        (31, "seo_spam", "a link in a bio", "", "alt_a"), (30, "spam", "one", "open", "spammer")]
    assert quarantine_review.queue_count(db) == 2


def test_the_queue_leaves_out_what_is_decided_or_not_theirs(db):
    aq.hold(db, who(db, 30), "spam")
    aq.hold(db, who(db, 31), "spam")
    aq.hold(db, who(db, 32), "spam")
    aq.hold(db, who(db, 33), "spam")
    db.execute(text("UPDATE users SET is_banned = 1 WHERE id = 30"))                                  # an admin decided: banned
    db.execute(text("UPDATE users SET is_deleted = 1 WHERE id = 31"))
    db.execute(text("UPDATE users SET quarantine_kind = 0 WHERE id = 32"))                           # the signup guard's, in its own queue
    rows, _more = quarantine_review.queue(db)
    assert [r["user_id"] for r in rows] == [33] and quarantine_review.queue_count(db) == 1


def test_a_link_about_minors_is_not_in_a_level_3_readers_queue_at_all(db):
    """The staff queue is `quarantine_review.queue` (a second read here that masked the row, `auto_quarantine.waiting`, was never used by the app and is gone: a masked row
    still named the account to level 3)."""
    aq.hold(db, who(db, 30), "link_minors", detail="its reason")
    assert quarantine_review.queue(db, level=3) == ([], False) and quarantine_review.queue_count(db, level=3) == 0
    (row,), _ = quarantine_review.queue(db, level=4)
    assert (row["trigger"], row["detail"], row["legal"]) == ("link_minors", "its reason", True) and row["user_id"] == 30
    assert not hasattr(aq, "waiting") and not hasattr(aq, "waiting_count")


def test_the_queue_pages(db):
    for n in range(aq.PAGE + 2):
        db.execute(text("INSERT INTO users (id, username, quarantined_utc, quarantine_kind) VALUES (:i, :n, :t, 1)"), {"i": 100 + n, "n": f"u{n}", "t": NOW + n})
    first, more = quarantine_review.queue(db, 1)
    second, more2 = quarantine_review.queue(db, 2)
    assert more and not more2 and len(first) == aq.PAGE and len(second) == 2
    assert {r['user_id'] for r in first}.isdisjoint(r['user_id'] for r in second)


# --- letting one go -------------------------------------------------------------------------------------------------------------------------------------------

def test_an_admin_lifts_it_with_a_new_staff_entry_and_a_public_line_and_the_original_is_never_edited(db):
    entry = aq.hold(db, who(db, 30), "spam", detail="one")
    publish(db)
    before = entries(db, id=entry)[0]
    lifted = aq.release(db, 30, admin_id=20, note="a false alarm: ordinary posting")
    assert not entries(db, action="automatic_quarantine_lifted")
    publish(db)
    assert lifted and lifted is not True and state(db, 30) == (False, 0)
    (row,) = entries(db, id=lifted)
    assert (row["action"], row["log_type"], row["actor_role"], row["actor_id"], row["target_user_id"], row["audience"], row["linked_entry_id"]) == (
        "unquarantine_user", "sanction", "admin", 20, 30, log.STAFF, entry)
    assert row["reason"] == "a false alarm: ordinary posting" and row["staff_note"] == "lifted by an admin"
    assert entries(db, id=entry)[0] == before                                          # never edited
    (public,) = entries(db, action="automatic_quarantine_lifted")
    assert (public["audience"], public["target_user_id"], public["target_submission_id"], public["board_id"], public["actor_role"], public["reason"], public["rule_key"]) == (
        log.PUBLIC, None, None, None, "system", "", "")
    assert [e["action"] for e in log.fetch(db, level=0, log_type="sanction")[0]] == ["automatic_quarantine_lifted", "automatic_quarantine"]


def test_lifting_an_evasion_hold_has_no_public_line_and_resets_the_count(db):
    db.execute(text("UPDATE users SET ban_evade = 9 WHERE id = 30"))
    aq.hold(db, who(db, 30), "evasion")
    assert aq.release(db, 30, admin_id=20)
    assert not entries(db, action="automatic_quarantine_lifted") and not entries(db, action="automatic_quarantine")
    assert db.execute(text("SELECT ban_evade FROM users WHERE id = 30")).scalar() == 0           # or the next request would hold it again
    aq.hold(db, who(db, 31), "spam")
    db.execute(text("UPDATE users SET ban_evade = 9 WHERE id = 31"))
    aq.release(db, 31, admin_id=20)
    assert db.execute(text("SELECT ban_evade FROM users WHERE id = 31")).scalar() == 9           # another trigger's lifting leaves the evasion count alone


def test_nothing_is_lifted_that_an_automatic_check_did_not_place(db):
    assert aq.release(db, 30, admin_id=20) is False                                           # not held
    db.execute(text("UPDATE users SET quarantined_utc = :t WHERE id = 30"), {"t": NOW})           # the signup guard's hold
    assert aq.release(db, 30, admin_id=20) is False and state(db, 30) == (True, 0)
    assert not entries(db)


def test_a_second_release_writes_nothing(db):
    aq.hold(db, who(db, 30), "spam")
    assert aq.release(db, 30, admin_id=20)
    count = len(entries(db))
    assert aq.release(db, 30, admin_id=20) is False and len(entries(db)) == count


def test_a_successful_appeal_lifts_it_links_the_appeals_entry_and_queues_no_second_notice(db):
    entry = aq.hold(db, who(db, 30), "spam")
    publish(db)
    db.info[aq._PENDING].clear()
    lifted = aq.release(db, 30, admin_id=20, appeal_entry_id=entry, note="the appeal was right")
    assert lifted and entries(db, id=lifted)[0]["linked_entry_id"] == entry and entries(db, id=lifted)[0]["staff_note"] == "lifted after an appeal"
    assert queued(db) == []                                                                        # the appeal's own outcome notice tells the person
    publish(db)
    assert entries(db, action="automatic_quarantine_lifted")


def test_a_plain_release_queues_the_lifted_notice_and_notify_false_does_not(db):
    aq.hold(db, who(db, 30), "spam")
    aq.hold(db, who(db, 31), "spam")
    db.info[aq._PENDING].clear()
    aq.release(db, 30, admin_id=20)
    aq.release(db, 31, admin_id=20, notify=False)
    assert [(k, u) for k, u, *_ in queued(db)] == [("lifted", 30)]


def test_the_site_itself_can_lift_one_and_a_legal_holds_entry_stays_legal(db):
    entry = aq.hold(db, who(db, 30), "link_minors")
    lifted = aq.release(db, 30)
    row = entries(db, id=lifted)[0]
    assert (row["actor_role"], row["actor_id"], row["audience"], row["linked_entry_id"]) == ("system", 1, log.LEGAL, entry)


def test_lifting_closes_an_undecided_signup_review(db):
    db.execute(text("INSERT INTO signup_reviews (user_id, created_utc, decision) VALUES (30, :t, '')"), {"t": NOW})
    aq.hold(db, who(db, 30), "spam")
    aq.release(db, 30, admin_id=20)
    assert tuple(db.execute(text("SELECT decision, decided_by FROM signup_reviews WHERE user_id = 30")).first()) == ("approved", 20)


def test_held_again_after_a_release_the_new_hold_is_the_one_that_counts(db):
    first = aq.hold(db, who(db, 30), "spam")
    publish(db)
    aq.release(db, 30, admin_id=20)
    second = aq.hold(db, who(db, 30), "seo_spam")
    assert second and second != first and state(db, 30) == (True, 1)
    assert aq.current_entry(db, 30)["id"] == second
    publish(db)
    assert len(entries(db, action="automatic_quarantine")) == 2


# --- the public lines are written by the cycle, an hour or more later (`publish_due`) ---------------------------------------------------------------------------

def _at(monkeypatch, t):
    monkeypatch.setattr(aq.time, "time", lambda: t)


def test_publish_due_writes_nothing_before_the_delay_and_a_line_after_it(db, monkeypatch):
    at = (NOW // 3600) * 3600 + 900
    _at(monkeypatch, at)
    entry = aq.hold(db, who(db, 30), "spam")
    assert aq.publish_due(db, now=at) == 0 and aq.publish_due(db, now=at + aq.PUBLISH_DELAY - 1) == 0
    assert not entries(db, action="automatic_quarantine") and entries(db, id=entry)               # the staff entry is there at once
    assert aq.publish_due(db, now=at + aq.PUBLISH_DELAY) == 1
    (public,) = entries(db, action="automatic_quarantine")
    assert public["linked_entry_id"] == entry and public["created_utc"] == aq.public_time(at) == at - 900


def test_publish_due_is_idempotent(db):
    aq.hold(db, who(db, 30), "spam")
    aq.hold(db, who(db, 31), "image")
    assert publish(db) == 2
    assert publish(db) == 0 and publish(db, NOW + 10 * 3600) == 0
    assert len(entries(db, action="automatic_quarantine")) == 2


def test_publish_due_writes_no_line_for_evasion_and_evasion_does_not_starve_a_later_public_hold(db, monkeypatch):
    monkeypatch.setattr(aq, "PUBLISH_BATCH", 2)
    for uid in (30, 31, 32):
        aq.hold(db, who(db, uid), "evasion", detail="its browser had been used by a suspended account")
    aq.hold(db, who(db, 33), "spam")
    assert publish(db) == 1
    (public,) = entries(db, action="automatic_quarantine")
    assert public["reason"] == "Automatic spam check"
    assert publish(db) == 0


def test_an_alts_entry_gets_no_public_line(db):
    main = aq.hold(db, who(db, 30), "spam", alts=[who(db, 31), who(db, 32)])
    assert publish(db) == 1
    (public,) = entries(db, action="automatic_quarantine")
    assert public["linked_entry_id"] == main
    assert {e["target_user_id"] for e in entries(db, action="quarantine_user")} == {30, 31, 32}


def test_a_hold_lifted_before_its_line_was_due_gets_neither_a_hold_line_nor_a_lifted_line(db):
    aq.hold(db, who(db, 30), "spam")
    assert aq.release(db, 30, admin_id=20)
    assert publish(db, NOW + 5 * 3600) == 0
    assert not entries(db, action="automatic_quarantine") and not entries(db, action="automatic_quarantine_lifted")
    assert log.fetch(db, level=0, log_type="sanction")[0] == []


def test_a_hold_published_and_then_lifted_gets_its_lifted_line_only_after_the_delay_dated_to_the_hour_of_the_lifting(db, monkeypatch):
    base = (NOW // 3600) * 3600
    _at(monkeypatch, base + 100)
    entry = aq.hold(db, who(db, 30), "seo_spam")
    assert publish(db, base + 100) == 1
    _at(monkeypatch, base + 3 * 3600 + 1500)
    lifted = aq.release(db, 30, admin_id=20, note="fine")
    release_at = base + 3 * 3600 + 1500
    assert aq.publish_due(db, now=release_at + aq.PUBLISH_DELAY - 1) == 0 and not entries(db, action="automatic_quarantine_lifted")
    assert aq.publish_due(db, now=release_at + aq.PUBLISH_DELAY) == 1
    (line,) = entries(db, action="automatic_quarantine_lifted")
    assert line["linked_entry_id"] == lifted and line["created_utc"] == aq.public_time(release_at) == base + 3 * 3600
    assert (line["audience"], line["target_user_id"], line["target_submission_id"], line["board_id"], line["actor_role"]) == (log.PUBLIC, None, None, None, "system")
    assert entries(db, id=entry)[0]["created_utc"] == base + 100                                  # the staff entries keep the exact second
    assert aq.publish_due(db, now=release_at + 10 * 3600) == 0                                    # once


def test_a_link_about_minors_still_gets_a_public_line_that_names_nobody(db):
    entry = aq.hold(db, who(db, 30), "link_minors", detail="the domain's own reason")
    assert publish(db) == 1
    (public,) = entries(db, action="automatic_quarantine")
    assert (public["audience"], public["reason"], public["linked_entry_id"]) == (log.PUBLIC, "Automatic link check", entry)
    assert (public["target_user_id"], public["target_submission_id"], public["target_comment_id"], public["board_id"]) == (None,) * 4
    assert "minors" not in public["staff_note"] and public["staff_note"] == ""


def test_hold_and_release_write_no_public_line_themselves_and_the_cycle_runs_publish_due():
    body = read("ruqqus", "helpers", "auto_quarantine.py")
    for name in ("hold", "release"):
        code = body.split(f"def {name}(", 1)[1].split("\ndef ", 1)[0]
        assert '"automatic_quarantine' not in code.split('"""', 2)[2], name
    assert "auto_quarantine.publish_due(db" in read("scripts", "recomputes.py")


# --- the wording the person reads ------------------------------------------------------------------------------------------------------------------------------

def test_the_notice_says_what_it_is_and_never_what_was_matched():
    message, values = log_notices.QUARANTINED, {"why": log_notices.why_of("evasion", "Automatic spam check"), "appeal": log_notices.QUARANTINE_APPEAL, "site": "Laba"}
    from ruqqus.helpers.notice_parts import rendered
    body = rendered(message, values)
    assert "Evading and manipulating the site: Automatic spam check" in body and "(/appeal/quarantine)" in body
    assert "```\nEvading" in body                                                               # a reason sits inside the fenced block
    assert log_notices.why_of("", "Automatic check") == "Automatic check"
    assert log_notices.QUARANTINED in log_notices.HELD_FROM_LIMITED and log_notices.QUARANTINE_LIFTED in log_notices.HELD_FROM_LIMITED
    assert "%(" not in log_notices.QUARANTINE_LIFTED, "a lift notice carries no link and no value"
    assert "%(log)s" not in log_notices.QUARANTINED and "%(item)s" not in log_notices.QUARANTINED


# --- the source: one writer, one wiring ---------------------------------------------------------------------------------------------------------------------------

def test_only_the_module_sets_an_automatic_hold_and_only_it_clears_one():
    allowed_clears = {"auto_quarantine.py", "account_cleanup.py"}
    for path in (ROOT / "ruqqus").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        if path.name == "user.py" and "quarantine_kind = Column(" in source:
            source = source.replace("quarantine_kind = Column(", "")
        # an assignment: a SET clause (up to its WHERE), or a Python attribute assigned at the start of a line (a comparison inside SQL is neither)
        for match in re.finditer(r"SET(?:(?!WHERE)[^\"'`])*?\bquarantine_kind\s*=\s*(\S+)|^\s*[\w.]+\.quarantine_kind\s*=[^=]\s*(\S+)", source, re.M):
            value = (match.group(1) or match.group(2) or "").rstrip(",")
            assert path.name == "auto_quarantine.py" or (value in ("0", "0,") and path.name in allowed_clears), (path.name, match.group(0))


def test_nothing_else_sets_a_hold_at_all_so_every_hold_has_a_kind_an_entry_and_a_way_out():
    """The tripwire above watches `quarantine_kind`; a caller that set `quarantined_utc` alone would make a kind 0 hold, which an email releases, with no staff entry,
    no public line and no appeal (the review of 2026-11-18). Every write of `quarantined_utc` is in the module, in the signup guard's own store (which only
    ever clears a kind 0 hold), in account closure, in the sign-up's constructor, or the column's definition."""
    found = []
    for base in (ROOT / "ruqqus", ROOT / "scripts"):
        for path in base.rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            for match in re.finditer(r"\bSET\b[^\"'`\n]*?\bquarantined_utc\s*=|^\s*[\w.]+\.quarantined_utc\s*=[^=]|[(,]\s*quarantined_utc\s*=[^=]|^\s*quarantined_utc\s*=[^=]", source, re.M):
                at_name = match.start() + match.group(0).index("quarantined_utc")
                line = source[source.rfind("\n", 0, at_name) + 1:source.index("\n", at_name)]
                statement = source[match.start(): source.find('"', match.start())]
                found.append((path.name, line.strip(), statement))
    assert found, "the scan found nothing: it is broken"
    for name, line, statement in found:
        if name == "auto_quarantine.py":
            continue
        if name == "signup_review_store.py":
            assert "quarantined_utc = 0" in statement and "quarantine_kind = 0" in statement, (name, statement)   # a clear, and only of the guard's own hold
        elif name == "account_cleanup.py":
            assert "quarantined_utc = 0" in line, (name, line)
        elif name == "login.py":
            assert "quarantined_utc=signup_utc if held else 0" in line, (name, line)                                # the sign-up's own constructor argument
        elif name == "user.py":
            assert "Column(" in line, (name, line)
        else:
            raise AssertionError(f"{name} writes quarantined_utc, which only helpers/auto_quarantine.py may do (a hold of an automatic check): {line}")


def test_the_signup_guards_sql_never_clears_an_automatic_hold():
    store = read("ruqqus", "helpers", "signup_review_store.py")
    for statement in re.findall(r'UPDATE users SET quarantined_utc = 0[^"]*"', store):
        assert "quarantine_kind = 0" in statement, statement
    assert "u.quarantine_kind = 0" in store.split("_WAITING = (", 1)[1].split(")\n", 1)[0]
    mail = read("ruqqus", "mail", "mail.py")
    activate = mail.split("def activate(", 1)[1]
    assert "not (user.quarantine_kind or 0)" in activate and 'signup_review_store.release(g.db, user.id, "verified"' in activate
    assert "user.quarantined_utc = 0" not in activate


def test_the_automatic_code_paths_still_in_the_core_never_ban():
    wrappers = read("ruqqus", "helpers", "wrappers.py")
    evasion = wrappers.split("def check_ban_evade(", 1)[1].split("# Wrappers", 1)[0]
    assert ".ban(" not in evasion and 'auto_quarantine.hold(g.db, v, "evasion"' in evasion and "abort(403)" not in evasion
    assert "ban_post" not in evasion and "ban_comment" not in evasion                           # it removes nothing the account wrote


def test_the_held_refusal_for_an_automatic_hold_links_the_review_page_and_an_email_is_never_offered():
    from ruqqus.helpers import quarantine
    automatic = SimpleNamespace(id=5, is_quarantined=True, quarantine_kind=1)
    guard = SimpleNamespace(id=6, is_quarantined=True, quarantine_kind=0)
    assert quarantine.refusal(automatic, "post") == quarantine.HELD_AUTOMATIC and "email" not in quarantine.HELD_AUTOMATIC
    assert quarantine.refusal(guard, "post") == quarantine.HELD and quarantine.APPEAL_LINK == "/appeal/quarantine"
    wrappers = read("ruqqus", "helpers", "wrappers.py").split("def not_quarantined(", 1)[1].split("def not_child_locked(", 1)[0]
    assert 'link=APPEAL_LINK, link_text=tr("Ask for a review")' in wrappers and 'link="/settings/security"' in wrappers
    notice = read("ruqqus", "templates", "partials", "held_notice.html")
    assert "{% if v.quarantine_kind == 1 %}" in notice and 'href="/appeal/quarantine"' in notice


def test_every_trigger_has_its_rule_and_a_generic_reason_that_names_no_category():
    for key, spec in aq.TRIGGERS.items():
        assert spec.key == key and spec.rule in log.RULES and spec.reason.startswith("Automatic ")
        assert spec.floor in (log.STAFF, log.LEGAL)
    assert not aq.TRIGGERS["evasion"].public and not aq.TRIGGERS["evasion"].say_rule
    assert not aq.TRIGGERS["image"].keep_detail
    assert all(spec.public for key, spec in aq.TRIGGERS.items() if key != "evasion")
    assert {"spam", "seo_spam", "link_malicious", "link_illegal", "link_minors", "evasion", "image"} == set(aq.TRIGGERS)


def test_the_migration_is_mirrored_in_the_schema():
    migration = read("scripts", "migrations", "2027-01-10_automatic_quarantine.sql")
    schema = read("schema.sql")
    assert "ADD COLUMN IF NOT EXISTS quarantine_kind smallint DEFAULT 0 NOT NULL" in migration
    # membership only: a later migration appended to the ledger, or a column added after this one, must not fail this test
    assert re.search(r"^    quarantine_kind smallint DEFAULT 0 NOT NULL,?$", schema, re.M)
    assert "users_quarantine_kind_rule" in migration and "CHECK (quarantine_kind IN (0, 1))" in schema
    assert "('2027-01-10_automatic_quarantine.sql')" in schema
    assert "REFERENCES" not in migration
    names = re.findall(r"\('(\d{4}-\d\d-\d\d_[a-z0-9_]+\.sql)'\)", schema.split("INSERT INTO public.schema_migrations", 1)[1])
    assert names == sorted(names) and "2027-01-10_automatic_quarantine.sql" in names


def test_inside_a_request_the_delivery_is_scheduled_once_for_after_the_commit(db):
    import flask
    app = flask.Flask("quarantine-test")
    with app.test_request_context():
        aq.hold(db, who(db, 30), "spam")
        aq.hold(db, who(db, 31), "spam")
        aq.release(db, 31, admin_id=20)
        assert len(flask.g.after_commit) == 1                                     # one greenlet per request, whatever was queued
        assert [k for k, *_ in queued(db)] == ["held", "held", "lifted"]
    with app.test_request_context():                                              # the next request on the same session schedules its own
        aq.hold(db, who(db, 32), "spam")
        assert len(flask.g.after_commit) == 1


def test_outside_a_request_nothing_is_scheduled_and_the_caller_sends_after_its_own_commit(db, monkeypatch):
    import flask
    aq.hold(db, who(db, 30), "spam")
    assert not flask.has_request_context() and len(queued(db)) == 1
    told = []
    monkeypatch.setattr(log_notices, "tell_quarantined", lambda d, uid, entry, **kw: told.append(uid) or True)
    db.commit()
    assert aq._send_all(db, db.info[aq._PENDING]) == 1 and told == [30]


# --- a held guildmaster still READS what their guild hid (the review of 2026-11-18) --------------------------------------------------------------------------------

class _Board:
    """`Board.has_mod`, `leads` and `_mod_row` as classes/boards.py has them: `has_mod` says no to a held or limited account (its powers), `leads` is the guildmaster's
    row whoever they are (what they may read), and the permission is read from the same row."""

    def __init__(self, rows):
        self.rows = rows                                   # user id -> the set of permissions

    def _mod_row(self, user, perm=None):
        perms = self.rows.get(user.id)
        return perms is not None and bool(not perm or "full" in perms or perm in perms)

    def has_mod(self, user, perm=None):
        if user is None:
            return None
        if getattr(user, "qr_limited", False) or getattr(user, "is_quarantined", False):
            return False
        return self._mod_row(user, perm)

    def leads(self, user):
        return bool(user is not None and self._mod_row(user))


def test_a_held_guildmaster_with_the_content_permission_reads_what_the_guild_hid_and_has_none_of_its_powers():
    from ruqqus.helpers import visibility
    board = _Board({7: {"content"}, 8: {"full"}, 9: {"access"}})
    content, full, access = (SimpleNamespace(id=i, is_quarantined=False, qr_limited=False) for i in (7, 8, 9))
    held_content, held_access = SimpleNamespace(id=7, is_quarantined=True, qr_limited=False), SimpleNamespace(id=9, is_quarantined=True, qr_limited=False)
    limited = SimpleNamespace(id=8, is_quarantined=False, qr_limited=True)
    stranger = SimpleNamespace(id=10, is_quarantined=False, qr_limited=False)
    # the powers: a held or limited account has none (the model this test follows, classes/boards.py)
    assert not board.has_mod(held_content, "content") and not board.has_mod(limited, "content") and board.has_mod(content, "content")
    # the read: the permission is still asked, and a hold changes nothing about it
    for reader in (content, full, held_content, limited):
        assert visibility.guild_content_reader(board, reader), reader
    for reader in (access, held_access, stranger, None):
        assert not visibility.guild_content_reader(board, reader), reader
    assert not visibility.guild_content_reader(None, content)


def test_the_read_checks_of_a_guild_hidden_item_ask_the_read_rule_and_the_powers_keep_has_mod():
    sources = {name: read("ruqqus", *path) for name, path in (("visibility", ("helpers", "visibility.py")), ("comment", ("classes", "comment.py")),
                                                              ("submission", ("classes", "submission.py")))}
    reader = sources["visibility"].split("def guild_content_reader(", 1)[1].split("\ndef ", 1)[0]
    assert "board.leads(v)" in reader and 'board._mod_row(v, "content")' in reader and "has_mod" not in reader.split('"""', 2)[2]
    # the four places a held guildmaster must still read (a held account can read the site, `has_mod` is their powers)
    assert 'hidden_by_guild and guild_content_reader(board, v)' in sources["visibility"]
    assert 'visibility.guild_content_reader(self.post.board, v)' in sources["comment"]
    assert sources["submission"].count("visibility.guild_content_reader(self.board, v)") == 2
    for name in ("comment", "submission"):
        assert not re.search(r'board\.has_mod\(v, ["\']content["\']\)', sources[name]), f"{name}.py still reads what a guild hid through has_mod"
    assert not re.search(r'board\.has_mod\(v, ["\']content["\']\)', sources["visibility"])
