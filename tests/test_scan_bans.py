"""What an image check does to its uploader (ruqqus/helpers/scan_bans.py), on SQLite with the Log's tables built from the migrations. The owner, 2026-11-17 and 18:
an automatic check only QUARANTINES; an admin decides what follows. So a match puts the uploader and their alts on hold (`auto_quarantine.hold`, trigger `image`),
writes the staff's entry, the public line ("Automatic image check", naming nobody) and the match as a separate linked entry, tells the person after the caller's commit,
and never bans, suspends or cascades. A second check, an admin, a bot, a deleted or a banned account writes nothing, except that a match of the CDN's child-abuse list
is always a level-4 record (and makes the hold it belongs to, its alts' entries included, level 4's: a level 3 admin may not lift what they were never shown the reason
for). The real app, Postgres and the CDN's answer are tests/pg/test_scan_bans.py; what each caller hands over is tests/test_csam_ban.py and tests/test_media_safety.py."""
import re
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from ruqqus.helpers import action_log as log
from ruqqus.helpers import auto_quarantine as aq
from ruqqus.helpers import log_notices, quarantine_review, scan_bans

import log_tables

ROOT = Path(__file__).resolve().parent.parent
MIGRATION = "2026-12-09_action_log.sql"
SITE_LIST = "matched a picture on the site's banned list: Spam picture"
CDN = "the CDN answered 451 for an uploaded picture"


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


@pytest.fixture
def app_stand_in(monkeypatch):
    """`send_pending` outside a request opens the app's own request context to send from (the scan's greenlet has none): the app is not importable here (no gevent), so a plain Flask app stands in."""
    import sys

    from flask import Flask

    monkeypatch.setitem(sys.modules, "ruqqus.__main__", SimpleNamespace(app=Flask("scan-bans-test")))


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
        conn.execute(text("INSERT INTO users (id, username, admin_level) VALUES (1, 'Laba', 0), (20, 'admin', 3)"))
        for uid, name in ((30, "uploader"), (31, "alt_a"), (32, "alt_b"), (33, "bystander")):
            conn.execute(text("INSERT INTO users (id, username) VALUES (:i, :n)"), {"i": uid, "n": name})
    with Session(engine) as session:
        yield session


def account(db, uid, alts=()):
    """What the scan holds: an account with its id, name and the `alts_threaded` the real `User` answers with."""
    name = db.execute(text("SELECT username FROM users WHERE id = :u"), {"u": uid}).scalar()
    return SimpleNamespace(id=uid, username=name, alts_threaded=lambda _db: [account(db, a) for a in alts])


def state(db, uid):
    return tuple(db.execute(text("SELECT quarantined_utc > 0, quarantine_kind, is_banned, unban_utc FROM users WHERE id = :u"), {"u": uid}).first())


def entries(db, **where):
    clause = " AND ".join(f"{k} = :{k}" for k in where) or "1 = 1"
    return [dict(r._mapping) for r in db.execute(text(f"SELECT * FROM log_entries WHERE {clause} ORDER BY id"), where)]


def queued(db):
    return list(db.info.get(aq._PENDING, []))


# --- a match puts the uploader and the alts on hold, and bans nobody --------------------------------------------------------------------------------

def test_the_uploader_and_their_alts_are_held_and_nobody_is_banned(db):
    entry = scan_bans.quarantine_uploader(db, account(db, 30, alts=(31, 32)), detail=SITE_LIST)
    assert entry
    for uid in (30, 31, 32):
        assert state(db, uid) == (True, 1, 0, 0)                          # held by an automatic check; not banned, not suspended
    assert state(db, 33) == (False, 0, 0, 0)                              # a bystander is not touched
    assert not entries(db, action="ban_user") and not entries(db, action="suspend_user")


def test_the_staff_entry_names_the_account_and_the_public_line_names_nobody_and_nothing_matched(db):
    entry = scan_bans.quarantine_uploader(db, account(db, 30), detail=SITE_LIST)
    (staff,) = entries(db, id=entry)
    assert (staff["action"], staff["actor_role"], staff["target_user_id"], staff["audience"]) == ("quarantine_user", "system", 30, log.STAFF)
    assert staff["staff_note"] == "image" and "Spam picture" not in staff["staff_note"]            # the match is a separate entry
    assert not entries(db, action="automatic_quarantine") and log.fetch(db, level=0, log_type="sanction")[0] == []     # nothing public until the cycle writes it
    assert aq.publish_due(db, now=int(time.time()) + aq.PUBLISH_DELAY + 1) == 1
    (public,) = entries(db, action="automatic_quarantine")
    assert (public["rule_key"], public["reason"], public["audience"], public["actor_role"]) == ("illegal", "Automatic image check", log.PUBLIC, "system")
    assert (public["target_user_id"], public["target_submission_id"], public["target_comment_id"], public["board_id"]) == (None,) * 4
    assert public["linked_entry_id"] == entry
    visitor, _more = log.fetch(db, level=0, log_type="sanction")
    assert [e["action"] for e in visitor] == ["automatic_quarantine"]


def test_the_cdns_match_is_a_level_4_entry_linked_to_the_staffs_and_naming_the_uploader(db):
    entry = scan_bans.quarantine_uploader(db, account(db, 30), detail=CDN, legal=True)
    (detail,) = entries(db, linked_entry_id=entry, action="suppress_content")
    assert (detail["audience"], detail["staff_note"], detail["target_user_id"], detail["actor_role"]) == (log.LEGAL, CDN, 30, "system")
    assert detail["id"] not in {e["id"] for e in log.fetch(db, level=3)[0]} and detail["id"] in {e["id"] for e in log.fetch(db, level=4)[0]}
    assert detail["id"] not in {e["id"] for e in log.fetch(db, level=0)[0]}


def test_a_cdn_match_makes_the_hold_and_the_alts_level_4s_and_leaves_the_public_line_public(db):
    """A level 3 admin must not be able to lift a hold they were never shown the reason for (rule C12): the staff entry and the alts' entries are legal-level, so the queue
    masks them, the release is refused and the appeal is level 4's; the public line, which names nobody, stays public."""
    entry = scan_bans.quarantine_uploader(db, account(db, 30, alts=(31, 32)), detail=CDN, legal=True)
    (staff,) = entries(db, id=entry)
    assert staff["audience"] == log.LEGAL and staff["action"] == "quarantine_user"
    alts = entries(db, action="quarantine_user", linked_entry_id=entry)
    assert sorted(a["target_user_id"] for a in alts) == [31, 32] and {a["audience"] for a in alts} == {log.LEGAL}
    assert not entries(db, action="automatic_quarantine")
    assert aq.publish_due(db, now=int(time.time()) + aq.PUBLISH_DELAY + 1) == 1
    (public,) = entries(db, action="automatic_quarantine")
    assert public["audience"] == log.PUBLIC and public["linked_entry_id"] == entry
    level3, level4 = {e["id"] for e in log.fetch(db, level=3)[0]}, {e["id"] for e in log.fetch(db, level=4)[0]}
    assert staff["id"] not in level3 and staff["id"] in level4 and not ({a["id"] for a in alts} & level3)
    assert public["id"] in level3 and public["id"] in {e["id"] for e in log.fetch(db, level=0)[0]}
    current = aq.current_entry(db, 30)
    assert current["audience"] == log.LEGAL
    assert quarantine_review.queue(db, level=3) == ([], False) and quarantine_review.queue_count(db, level=3) == 0        # a legal-level hold is not in level 3's queue at all
    rows4, _more = quarantine_review.queue(db, level=4)
    assert {r["user_id"]: r["trigger"] for r in rows4} == {30: "image", 31: "image", 32: "image"}
    assert quarantine_review.release_refusal(current["audience"], 3) and quarantine_review.release_refusal(current["audience"], 4) is None


def test_the_site_lists_match_leaves_the_hold_at_the_staff_level(db):
    entry = scan_bans.quarantine_uploader(db, account(db, 30, alts=(31,)), detail=SITE_LIST)
    assert {e["audience"] for e in entries(db, action="quarantine_user")} == {log.STAFF}
    assert aq.current_entry(db, 30)["audience"] == log.STAFF and entry


def test_the_site_lists_match_is_a_staff_entry_with_the_reason(db):
    entry = scan_bans.quarantine_uploader(db, account(db, 30), detail=SITE_LIST)
    (detail,) = entries(db, linked_entry_id=entry, action="admin_image_ban")
    assert (detail["audience"], detail["staff_note"], detail["target_user_id"]) == (log.STAFF, SITE_LIST, 30)
    assert detail["id"] in {e["id"] for e in log.fetch(db, level=3)[0]}


def test_the_alts_are_staff_only_with_no_public_line_and_no_notice_of_their_own(db):
    scan_bans.quarantine_uploader(db, account(db, 30, alts=(31, 32)), detail=SITE_LIST)
    aq.publish_due(db, now=int(time.time()) + aq.PUBLISH_DELAY + 1)
    for uid in (31, 32):
        (alt,) = entries(db, action="quarantine_user", target_user_id=uid)
        assert alt["audience"] == log.STAFF and "alt of @uploader" in alt["staff_note"]
    assert len(entries(db, action="automatic_quarantine")) == 1            # one public line, for the main account
    assert [item[1] for item in queued(db)] == [30]                         # one notice, to the uploader alone


# --- quiet and idempotent ----------------------------------------------------------------------------------------------------------------------------

def test_a_second_match_of_the_site_list_on_a_held_account_writes_nothing_and_tells_nobody(db):
    assert scan_bans.quarantine_uploader(db, account(db, 30, alts=(31,)), detail=SITE_LIST)
    count, notices = len(entries(db)), len(queued(db))
    assert scan_bans.quarantine_uploader(db, account(db, 30, alts=(32,)), detail=SITE_LIST) is None
    assert len(entries(db)) == count and len(queued(db)) == notices        # no second entry, no second detail, no second notice, and alt_b is left alone
    assert state(db, 32) == (False, 0, 0, 0)


def test_a_second_cdn_match_on_a_held_account_holds_nothing_new_but_keeps_its_own_level_4_record(db):
    """A held account may still change its own profile, pictures included, so a second match of the child-abuse list's must not leave no trace of the picture the
    site took down: one level-4 detail entry linked to the hold the account is on. Nothing else: no second hold, no public line, no notice, no alt."""
    first = scan_bans.quarantine_uploader(db, account(db, 30, alts=(31,)), detail=CDN, legal=True)
    assert first
    before = {e["id"] for e in entries(db)}
    notices, public = len(queued(db)), len(entries(db, action="automatic_quarantine"))
    assert scan_bans.quarantine_uploader(db, account(db, 30, alts=(32,)), detail=CDN + " again", legal=True) is None
    (detail,) = [e for e in entries(db) if e["id"] not in before]
    assert (detail["action"], detail["audience"], detail["actor_role"], detail["target_user_id"], detail["linked_entry_id"], detail["staff_note"]) == (
        "suppress_content", log.LEGAL, "system", 30, first, CDN + " again")
    assert len(queued(db)) == notices and len(entries(db, action="automatic_quarantine")) == public
    assert state(db, 32) == (False, 0, 0, 0)
    assert detail["id"] not in {e["id"] for e in log.fetch(db, level=3)[0]} and detail["id"] in {e["id"] for e in log.fetch(db, level=4)[0]}


def test_a_cdn_match_on_an_account_another_check_already_holds_is_recorded_beside_that_hold_and_makes_it_level_4s(db):
    """The spam guard held the account (a staff entry, and an alt beside it). Its next upload is a match of the child-abuse list's: the queue row must no longer read
    'spam' to a level 3 admin who could then lift it without learning of the match."""
    aq.hold(db, account(db, 30), "spam", detail="six similar posts in a minute", alts=[account(db, 31)], notify=False)
    held = aq.current_entry(db, 30)
    assert held["audience"] == log.STAFF and held["trigger"] == "spam"
    before = {e["id"] for e in entries(db)}
    assert scan_bans.quarantine_uploader(db, account(db, 30, alts=(32,)), detail=CDN, legal=True) is None
    (detail,) = [e for e in entries(db) if e["id"] not in before]
    assert (detail["action"], detail["audience"], detail["target_user_id"], detail["linked_entry_id"], detail["staff_note"]) == ("suppress_content", log.LEGAL, 30, held["id"], CDN)
    assert aq.current_entry(db, 30)["audience"] == log.LEGAL
    assert {e["audience"] for e in entries(db, action="quarantine_user")} == {log.LEGAL}          # the main account's and the alt's entry
    assert aq.publish_due(db, now=int(time.time()) + aq.PUBLISH_DELAY + 1) == 1
    assert entries(db, action="automatic_quarantine")[0]["audience"] == log.PUBLIC
    assert quarantine_review.queue(db, level=3) == ([], False)
    assert {r["user_id"]: (r["legal"], r["trigger"]) for r in quarantine_review.queue(db, level=4)[0]} == {30: (True, "spam"), 31: (True, "spam")}
    assert state(db, 32) == (False, 0, 0, 0) and not queued(db)                                  # nobody new is held, nobody told


@pytest.mark.parametrize("column, value", [("admin_level", 3), ("is_bot", 1), ("is_deleted", 1), ("is_banned", 1)])
def test_an_admin_a_bot_a_deleted_or_a_banned_uploader_is_never_held_and_the_site_lists_match_leaves_no_entry(db, column, value):
    db.execute(text(f"UPDATE users SET {column} = :v WHERE id = 30"), {"v": value})
    assert scan_bans.quarantine_uploader(db, account(db, 30, alts=(31,)), detail=SITE_LIST) is None
    assert state(db, 30)[:2] == (False, 0) and state(db, 31)[:2] == (False, 0)
    assert not entries(db) and not queued(db)                              # not held, no public line, no detail, nobody told


@pytest.mark.parametrize("column, value", [("admin_level", 3), ("is_bot", 1), ("is_deleted", 1), ("is_banned", 1)])
def test_a_cdn_match_on_an_exempt_uploader_holds_nobody_but_is_still_a_level_4_record_naming_the_account(db, column, value):
    """The child-abuse list's match is the record of a picture the site took down: an admin's, a bot's, a deleted or an already banned account's too. Level 4 only, linked to
    nothing (there is no hold), with no public line, no notice and no alt touched."""
    db.execute(text(f"UPDATE users SET {column} = :v WHERE id = 30"), {"v": value})
    assert scan_bans.quarantine_uploader(db, account(db, 30, alts=(31,)), detail=CDN, legal=True) is None
    assert state(db, 30)[:2] == (False, 0) and state(db, 31)[:2] == (False, 0)
    (record,) = entries(db)
    assert (record["action"], record["audience"], record["actor_role"], record["target_user_id"], record["linked_entry_id"]) == (
        "suppress_content", log.LEGAL, "system", 30, None)
    assert record["staff_note"].startswith(CDN) and scan_bans.NOT_HELD in record["staff_note"]
    assert not queued(db) and not entries(db, action="automatic_quarantine")
    assert record["id"] in {e["id"] for e in log.fetch(db, level=4)[0]} and record["id"] not in {e["id"] for e in log.fetch(db, level=3)[0]}
    assert record["id"] not in {e["id"] for e in log.fetch(db, level=0)[0]}


def test_no_account_is_no_hold(db):
    assert scan_bans.quarantine_uploader(db, None, detail=CDN, legal=True) is None
    assert not entries(db)


def test_a_failure_to_hold_is_logged_and_the_caller_goes_on_to_remove_the_picture(db, monkeypatch, caplog):
    def boom(*a, **k):
        raise RuntimeError("the database said no")

    monkeypatch.setattr(aq, "hold", boom)
    assert scan_bans.quarantine_uploader(db, account(db, 30), detail=CDN, legal=True) is None
    assert "could not put its uploader on hold" in caplog.text
    assert state(db, 30)[:2] == (False, 0)
    (record,) = entries(db)                                                # a match of the child-abuse list is kept even when the hold could not be placed
    assert (record["action"], record["audience"], record["target_user_id"], record["linked_entry_id"]) == ("suppress_content", log.LEGAL, 30, None)
    assert scan_bans.NOT_PLACED in record["staff_note"]


def test_a_hold_whose_own_entry_could_not_be_written_still_keeps_the_cdn_match_at_level_4(db, monkeypatch):
    monkeypatch.setattr(aq, "hold", lambda *a, **k: 0)                    # the account was put on hold, its entry was not written
    assert scan_bans.quarantine_uploader(db, account(db, 30), detail=CDN, legal=True) == 0
    (record,) = entries(db)
    assert (record["action"], record["audience"], record["target_user_id"], record["linked_entry_id"]) == ("suppress_content", log.LEGAL, 30, None)
    assert scan_bans.NOT_WRITTEN in record["staff_note"]


def test_a_failure_to_keep_the_cdn_match_never_undoes_the_hold(db, monkeypatch, caplog):
    def boom(*a, **k):
        raise RuntimeError("the database said no")

    monkeypatch.setattr(log, "record_detail", boom)
    entry = scan_bans.quarantine_uploader(db, account(db, 30), detail=CDN, legal=True)
    assert entry and state(db, 30)[:2] == (True, 1)
    assert "could not keep the child-abuse match" in caplog.text


# --- the notice goes after the caller's commit, once ------------------------------------------------------------------------------------------------

def test_tell_sends_the_notice_once_with_the_rule_the_generic_reason_and_never_the_match(db, monkeypatch, app_stand_in):
    told = []
    monkeypatch.setattr(log_notices, "tell_quarantined", lambda d, uid, entry, **kw: told.append((uid, entry, kw)) or True)
    entry = scan_bans.quarantine_uploader(db, account(db, 30, alts=(31,)), detail=CDN, legal=True)
    db.commit()
    assert scan_bans.tell(db) == 1 and queued(db) == []
    assert told == [(30, entry, {"rule_key": "illegal", "reason": "Automatic image check"})]
    assert "CDN" not in repr(told) and "451" not in repr(told)
    assert scan_bans.tell(db) == 0 and len(told) == 1                       # once


def test_tell_says_nothing_when_nothing_was_held(db, monkeypatch):
    monkeypatch.setattr(log_notices, "tell_quarantined", lambda *a, **k: pytest.fail("nobody was held"))
    assert scan_bans.tell(db) == 0


def test_a_notice_that_fails_is_swallowed_and_the_scan_goes_on(db, monkeypatch, caplog):
    monkeypatch.setattr(aq, "send_pending", lambda _db: (_ for _ in ()).throw(RuntimeError("mail is down")))
    assert scan_bans.tell(db) == 0
    assert "was not sent" in caplog.text


# --- what the module is, in its source ---------------------------------------------------------------------------------------------------------------

def test_the_module_only_holds_it_never_bans_opens_a_context_or_cascades():
    source = read("ruqqus", "helpers", "scan_bans.py")
    code = re.sub(r'""".*?"""', "", source, flags=re.S)
    assert not re.search(r"\.ban\(|\.unban\(|is_banned|suspend|app_context|cascade|ban_uploader", code)
    assert "auto_quarantine.hold(db, user, \"image\"" in code and "record_detail(" in code and "begin_nested" in code
    assert "ban_uploader" not in re.sub(r'""".*?"""', "", read("ruqqus", "helpers", "media", "safety.py"), flags=re.S)


def test_the_banned_picture_form_asks_for_no_penalty_because_a_match_only_holds_the_uploader():
    """The form used to ask how many days to suspend the uploader and wrote it into `badpics.ban_time` (NOT NULL, read nowhere now: a match quarantines, nobody is
    suspended). An old form that still posts `ban_length` is ignored, and the staff's note says what a match does."""
    template = read("ruqqus", "templates", "admin", "image_ban.html")
    assert "ban_length" not in template and "Penalty" not in template and "time-input" not in template
    assert "puts the account that uploaded it on hold" in template
    admin = read("ruqqus", "routes", "admin.py")
    route = admin.split("def admin_image_ban(", 1)[1].split("\n@app.", 1)[0]
    assert "ban_length" not in re.sub(r"#.*", "", route), "the posted penalty is not read any more"
    assert "ban_time=0" in route and "int(request.form" not in route
    assert "puts the uploader on hold for an admin to review" in route and "suspends the uploader" not in route
