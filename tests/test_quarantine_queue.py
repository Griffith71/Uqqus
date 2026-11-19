"""The staff queue, the person's appeal and what a release does to an appeal (helpers/quarantine_review.py, routes/quarantine_pages.py, the quarantine branch of
routes/log_appeal_pages.py), on SQLite with the Log's tables built from the migrations; the routes themselves run on the real app in tests/pg/test_quarantine_queue.py.

The promises: only the account itself may appeal its own quarantine, once, and only through its own page (never `/log/<id>/appeal`, whose id is not the person's to
know); the site's admins decide it, never the system; what the person is shown never names the trigger of ban-evasion detection or the rule of an alt's hold; a
release settles the appeal still waiting; a legal-level hold is level 4's; and no code here writes `quarantined_utc` or `quarantine_kind` (helpers/auto_quarantine.py is
the one place)."""
import re
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from ruqqus.helpers import action_log as log
from ruqqus.helpers import auto_quarantine as aq
from ruqqus.helpers import log_appeals, log_notices, quarantine_review

import log_tables

ROOT = Path(__file__).resolve().parent.parent
RUQQUS = ROOT / "ruqqus"
NOW = int(time.time())


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        for statement in log_tables.migration_statements(read("scripts", "migrations", "2026-12-09_action_log.sql")):
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
        for uid, name in ((30, "spammer"), (31, "alt_a"), (33, "bystander")):
            conn.execute(text("INSERT INTO users (id, username) VALUES (:i, :n)"), {"i": uid, "n": name})
    with Session(engine) as session:
        yield session


def who(db, uid):
    return SimpleNamespace(id=uid, username=db.execute(text("SELECT username FROM users WHERE id = :u"), {"u": uid}).scalar())


def entries(db, **where):
    clause = " AND ".join(f"{k} = :{k}" for k in where) or "1 = 1"
    return [dict(r._mapping) for r in db.execute(text(f"SELECT * FROM log_entries WHERE {clause} ORDER BY id"), where)]


def publish(db, at=None):
    """The cycle's step that writes the public lines, as if it ran just past the delay after `at` (default: now)."""
    return aq.publish_due(db, now=int(at if at is not None else time.time()) + aq.PUBLISH_DELAY + 1)


def held(db, uid=30, trigger="spam", detail="six similar posts in a minute", **kwargs):
    entry = aq.hold(db, who(db, uid), trigger, detail=detail, notify=False, **kwargs)
    assert entry
    return entry


# --- what an appeal of a quarantine is ---------------------------------------------------------------------------------------------------------------

def test_a_quarantine_is_appealable_about_a_person_and_decided_by_the_sites_admins(db):
    assert log_appeals.APPEALABLE["quarantine_user"] == ("unquarantine_user", None)
    assert "quarantine_user" in log_appeals.ABOUT_A_PERSON and "quarantine_user" in log_appeals.OWN_PAGE
    entry = log_appeals.entry_of(db, held(db))
    assert log_appeals.subject_of(db, entry) == 30                                         # the account the staff's entry names
    assert log_appeals.board_for(db, entry) is None and log_appeals.permission_for(entry) is None          # the site's admins, no guild
    assert entry["actor_role"] == "system" and entry["actor_id"] == 1


def test_only_the_account_itself_may_appeal_it_once_and_three_a_day(db):
    entry_id = held(db)
    entry = log_appeals.entry_of(db, entry_id)
    for other in (31, 33, 20, 1):
        assert log_appeals.can_appeal(db, entry, other) == "There is nothing here to appeal."
    assert log_appeals.can_appeal(db, entry, 30, now=NOW) is None
    appeal = log_appeals.file_appeal(db, entry_id, 30, "That was an ordinary post.", now=NOW)
    assert appeal
    assert log_appeals.can_appeal(db, entry, 30, now=NOW) == log_appeals.ALREADY_APPEALED
    with pytest.raises(log_appeals.AppealRefused):
        log_appeals.file_appeal(db, entry_id, 30, "Again.", now=NOW)
    # it waits in the SITE's queue, not a guild's, and carries the account that appealed
    (waiting,) = log_appeals.open_queue(db, None)
    assert (waiting["entry_id"], waiting["user_id"], waiting["action"], waiting["actor_role"]) == (entry_id, 30, "quarantine_user", "system")


def test_an_appeal_is_five_hundred_characters_of_plain_text(db):
    entry_id = held(db)
    appeal = log_appeals.file_appeal(db, entry_id, 30, "<b>x</b>\n\n" + "y" * 800)
    reason = db.execute(text("SELECT reason FROM log_appeals WHERE id = :a"), {"a": appeal}).scalar()
    assert len(reason) == log_appeals.REASON_MAX and "\n" not in reason


def test_a_decision_already_put_right_is_not_appealable_and_the_appeal_is_once_per_quarantine(db):
    first = held(db)
    log_appeals.file_appeal(db, first, 30, "Please look again.")
    assert aq.release(db, 30, admin_id=20, note="fine") and not aq.current_entry(db, 30)
    assert log_appeals.is_undone(db, log_appeals.entry_of(db, first))
    assert log_appeals.can_appeal(db, log_appeals.entry_of(db, first), 30) == "This decision has already been lifted."
    second = held(db, detail="again")                                                   # held again: a NEW entry, its own appeal
    assert second != first and not log_appeals.is_undone(db, log_appeals.entry_of(db, second))
    assert log_appeals.can_appeal(db, log_appeals.entry_of(db, second), 30) is None


def test_nobody_conflicts_with_the_system_and_the_appeal_can_be_decided_once(db):
    entry_id = held(db)
    appeal = log_appeals.file_appeal(db, entry_id, 30, "Not spam.")
    row = log_appeals.appeal_of(db, appeal)
    assert log_appeals.conflicted(db, row, 20) is False and log_appeals.conflicted(db, row, 21) is False          # the system decided it: any admin may
    assert log_appeals.decide(db, appeal, "reversed", 20, "ok") is True
    assert log_appeals.decide(db, appeal, "upheld", 21, "no") is False
    assert not log_appeals.open_queue(db, None)


# --- what the person is shown, what the staff are shown ------------------------------------------------------------------------------------------------

def test_the_person_is_shown_the_generic_wording_and_the_rule_of_a_check_that_names_one(db):
    held(db, 30, "spam")
    entry = aq.current_entry(db, 30)
    shown = quarantine_review.shown_to_person(entry)
    assert (shown["rule_key"], shown["reason"]) == ("evasion", "Automatic spam check") and shown["created_utc"] > 0
    assert "six similar" not in str(shown) and "spam |" not in str(shown)


def test_ban_evasion_detection_and_an_alts_hold_name_no_rule_and_no_trigger_to_the_person(db):
    held(db, 30, "evasion", detail="its browser had been used by a suspended account")
    shown = quarantine_review.shown_to_person(aq.current_entry(db, 30))
    assert shown["rule_key"] == "" and shown["reason"] == "Automatic check"
    # an alt held beside a main account: the main's trigger and rule are not the alt's to be told
    db.execute(text("INSERT INTO users (id, username) VALUES (40, 'main'), (41, 'its_alt')"))
    held(db, 40, "link_malicious", detail="a banned domain", alts=[who(db, 41)])
    alt = aq.current_entry(db, 41)
    assert alt["alt_of"] == "main"
    shown = quarantine_review.shown_to_person(alt)
    assert shown["rule_key"] == "" and shown["reason"] == "Automatic check" and "main" not in str(shown)
    assert quarantine_review.shown_to_person(aq.current_entry(db, 40))["reason"] == "Automatic link check"


def test_the_staff_line_says_which_check_what_it_saw_and_for_an_alt_whose(db):
    held(db, 30, "spam", detail="six similar posts in a minute")
    assert quarantine_review.check_line(aq.current_entry(db, 30)) == "Spam guard (similar posts or comments, a bad link): six similar posts in a minute"
    db.execute(text("INSERT INTO users (id, username) VALUES (40, 'main'), (41, 'its_alt')"))
    held(db, 40, "evasion", detail="x", alts=[who(db, 41)])
    assert quarantine_review.check_line(aq.current_entry(db, 41)) == "Ban-evasion detection: an alt of @main"
    assert quarantine_review.check_line({"trigger": "", "detail": "", "alt_of": ""}) == "An automatic check"
    assert set(quarantine_review.TRIGGER_LABELS) == set(aq.TRIGGERS)                         # a new trigger needs a label


def test_parse_note_reads_what_the_hold_wrote(db):
    assert quarantine_review.parse_note("spam | six posts") == {"trigger": "spam", "detail": "six posts", "alt_of": ""}
    assert quarantine_review.parse_note("evasion | alt of @main") == {"trigger": "evasion", "detail": "alt of @main", "alt_of": "main"}
    assert quarantine_review.parse_note(None) == {"trigger": "", "detail": "", "alt_of": ""}


def test_the_queue_leaves_a_legal_hold_out_of_level_3_entirely_the_list_and_the_count(db):
    """The review: a masked row named the account to level 3 and said an appeal waited, and the count on /admin included it: link_minors is the one trigger with a legal floor."""
    first = held(db, 30, "spam")
    legal = held(db, 31, "link_minors", detail="the domain's reason")
    log_appeals.file_appeal(db, first, 30, "Not spam.")
    log_appeals.file_appeal(db, legal, 31, "Not about minors.")
    rows3, more3 = quarantine_review.queue(db, level=3)
    assert [r["username"] for r in rows3] == ["spammer"] and not more3
    assert quarantine_review.queue_count(db, level=3) == 1
    assert "alt_a" not in str(rows3) and "minors" not in str(rows3) and "domain" not in str(rows3)           # nothing of the held account, its check or its appeal
    row = rows3[0]
    assert (row["entry_id"], row["trigger"], row["detail"], row["appeal"], row["legal"], row["audience"]) == (first, "spam", "six similar posts in a minute", "open", False, log.STAFF)
    rows4, _more = quarantine_review.queue(db, level=4)
    by_name = {r["username"]: r for r in rows4}
    assert set(by_name) == {"spammer", "alt_a"} and quarantine_review.queue_count(db, level=4) == 2
    assert (by_name["alt_a"]["legal"], by_name["alt_a"]["trigger"], by_name["alt_a"]["appeal"], by_name["alt_a"]["audience"]) == (True, "link_minors", "open", log.LEGAL)
    # the same for the person who is not an admin at all, or an unknown level: never wider than level 3
    assert quarantine_review.queue_count(db, level=0) == 1 and quarantine_review.queue_count(db, level=None) == 1 and quarantine_review.queue_count(db, level="x") == 1
    assert [r["username"] for r in quarantine_review.queue(db, level=0)[0]] == ["spammer"]


def test_an_alt_held_beside_a_legal_main_account_is_legal_too_and_left_out_of_level_3(db):
    db.execute(text("INSERT INTO users (id, username) VALUES (40, 'main'), (41, 'its_alt')"))
    held(db, 40, "link_minors", detail="a domain", alts=[who(db, 41)])
    assert quarantine_review.queue(db, level=3) == ([], False) and quarantine_review.queue_count(db, level=3) == 0
    assert {r["username"] for r in quarantine_review.queue(db, level=4)[0]} == {"main", "its_alt"} and quarantine_review.queue_count(db, level=4) == 2


def test_a_banned_or_released_account_is_in_nobodys_queue_and_a_hold_with_no_entry_is_still_listed(db):
    held(db, 30), held(db, 31)
    db.execute(text("UPDATE users SET is_banned = 1 WHERE id = 30"))
    assert [r["user_id"] for r in quarantine_review.queue(db, level=3)[0]] == [31] and quarantine_review.queue_count(db, level=3) == 1
    assert aq.release(db, 31, admin_id=20, note="fine")
    assert quarantine_review.queue(db, level=4) == ([], False) and quarantine_review.queue_count(db, level=4) == 0
    # a hold whose entry could not be written is still a hold an admin may lift: it shows with no entry, and (it has no audience) to level 3 as well
    db.execute(text("UPDATE users SET quarantined_utc = :now, quarantine_kind = 1 WHERE id = 33"), {"now": NOW})
    (row,), _more = quarantine_review.queue(db, level=3)
    assert (row["user_id"], row["entry_id"], row["trigger"], row["appeal"], row["legal"]) == (33, None, "", "", False)
    assert quarantine_review.queue_count(db, level=3) == 1


def test_the_queue_pages_by_what_a_level_3_admin_may_read_and_the_count_adds_up(db):
    """Fifty a page, newest first, counted after the legal holds are left out: a page never comes up short for a hold the reader can't see."""
    for uid in range(100, 154):
        db.execute(text("INSERT INTO users (id, username) VALUES (:i, :n)"), {"i": uid, "n": f"member{uid}"})
        held(db, uid, "link_minors" if uid in (110, 111, 112) else "spam")
    first, more = quarantine_review.queue(db, 1, level=3)
    second, more_second = quarantine_review.queue(db, 2, level=3)
    assert len(first) == aq.PAGE and more and len(second) == 1 and not more_second          # 51 rows are readable, 3 are not
    assert quarantine_review.queue_count(db, level=3) == 51 and quarantine_review.queue_count(db, level=4) == 54
    assert not ({110, 111, 112} & {r["user_id"] for r in first + second})
    assert [r["user_id"] for r in first] == sorted((r["user_id"] for r in first), reverse=True)           # newest (the highest id of one moment) first
    assert len(quarantine_review.queue(db, 1, level=4)[0]) == aq.PAGE and len(quarantine_review.queue(db, 2, level=4)[0]) == 4
    assert quarantine_review.queue(db, 0, level=3)[0] == first                                      # a page under 1 is the first


# --- a release lifts the hold the admin was shown ----------------------------------------------------------------------------------------------------

def test_the_entry_a_form_posts_is_read_as_ascii_digits_only():
    assert quarantine_review.parse_entry("12") == 12 and quarantine_review.parse_entry(" 7 ") == 7 and quarantine_review.parse_entry("0") == 0
    for bad in (None, "", " ", "x", "-3", "1.5", "1e3", "\u0661\u0662", "\uff11\uff12", "1" * 13, 5, b"3"):
        assert quarantine_review.parse_entry(bad) is None, bad


def test_a_release_is_stale_unless_it_names_the_hold_the_account_is_on_now(db):
    first = held(db, 30)
    current = aq.current_entry(db, 30)
    assert quarantine_review.is_stale(first, current) is False
    assert quarantine_review.is_stale(None, current) is True                       # a form that named nothing
    assert quarantine_review.is_stale(0, current) is True                          # one drawn for a row that had no entry
    assert aq.release(db, 30, admin_id=20, note="fine")
    second = held(db, 30, "link_malicious", detail="a banned domain")                 # held again: a NEW entry, as another admin's stale tab would meet it
    assert second != first
    current = aq.current_entry(db, 30)
    assert quarantine_review.is_stale(first, current) is True and quarantine_review.is_stale(second, current) is False
    # a hold with no entry: the form said 0
    assert quarantine_review.is_stale(0, None) is False and quarantine_review.is_stale(None, None) is True and quarantine_review.is_stale(first, None) is True


def test_the_lock_says_whether_an_automatic_check_holds_the_account_now(db):
    assert quarantine_review.lock_hold(db, 30) is False and quarantine_review.lock_hold(db, 999) is False
    held(db, 30)
    assert quarantine_review.lock_hold(db, 30) is True
    db.execute(text("UPDATE users SET quarantine_kind = 0 WHERE id = 30"))                      # the signup guard's hold is not this kind
    assert quarantine_review.lock_hold(db, 30) is False
    assert aq.release(db, 30, admin_id=20, note="x") is False


# --- a release settles the appeal; a legal hold is level 4's ---------------------------------------------------------------------------------------------

def test_a_release_settles_the_appeal_still_waiting_as_reversed_once(db):
    entry_id = held(db)
    appeal = log_appeals.file_appeal(db, entry_id, 30, "Not spam.")
    lifted = aq.release(db, 30, admin_id=20, note="an ordinary post")
    assert lifted and lifted is not True
    assert quarantine_review.close_appeals(db, entry_id, 20, lifted) == 1
    row = db.execute(text("SELECT status, decided_by, decision_note, reversal_entry_id FROM log_appeals WHERE id = :a"), {"a": appeal}).first()
    assert tuple(row) == ("reversed", 20, "Lifted by an admin.", lifted)
    assert quarantine_review.close_appeals(db, entry_id, 21) == 0                              # once
    (mine,) = [quarantine_review.appeal_of(db, entry_id, 30)]
    assert mine["status"] == "reversed"
    assert quarantine_review.appeal_of(db, entry_id, 33) is None


def test_a_release_leaves_a_decided_appeal_and_another_accounts_alone(db):
    first, other = held(db, 30), held(db, 33)
    a, b = log_appeals.file_appeal(db, first, 30, "x"), log_appeals.file_appeal(db, other, 33, "y")
    log_appeals.decide(db, a, "upheld", 20, "it stands")
    assert quarantine_review.close_appeals(db, first, 20) == 0
    assert db.execute(text("SELECT status FROM log_appeals WHERE id = :a"), {"a": a}).scalar() == "upheld"
    assert db.execute(text("SELECT status FROM log_appeals WHERE id = :a"), {"a": b}).scalar() == "open"


def test_a_ban_for_good_settles_the_appeal_still_waiting_as_dismissed_and_leaves_the_hold_and_every_other_account_alone(db):
    entry_id, other = held(db), held(db, 33)
    appeal, theirs = log_appeals.file_appeal(db, entry_id, 30, "Not spam."), log_appeals.file_appeal(db, other, 33, "Nor me.")
    assert quarantine_review.close_appeals_for_ban(db, entry_id, 20) == 1
    row = db.execute(text("SELECT status, decided_by, decision_note, reversal_entry_id FROM log_appeals WHERE id = :a"), {"a": appeal}).first()
    assert tuple(row) == ("dismissed", 20, quarantine_review.CLOSED_BY_BAN, None)                    # not reversed: nothing is lifted
    assert "suspended" in quarantine_review.CLOSED_BY_BAN and "its own appeal" in quarantine_review.CLOSED_BY_BAN
    assert quarantine_review.close_appeals_for_ban(db, entry_id, 21) == 0                              # once
    assert db.execute(text("SELECT status FROM log_appeals WHERE id = :a"), {"a": theirs}).scalar() == "open"
    assert db.execute(text("SELECT quarantine_kind, quarantined_utc > 0 FROM users WHERE id = 30")).first() == (1, 1)      # the hold stands
    assert not entries(db, action="unquarantine_user")                                               # and nothing was lifted or written


def test_a_settled_appeal_cannot_be_decided_again_so_it_can_never_release_a_banned_account(db):
    entry_id = held(db)
    appeal = log_appeals.file_appeal(db, entry_id, 30, "Not spam.")
    quarantine_review.close_appeals_for_ban(db, entry_id, 20)
    assert log_appeals.decide(db, appeal, "reversed", 21, "release it") is False                       # the one conditional UPDATE: only an open appeal is decided
    assert not any(a["id"] == appeal for a in log_appeals.open_queue(db, None))                      # and it is no longer in /admin/appeals


def test_the_queue_and_its_count_leave_out_a_banned_account_and_bring_it_back_when_unbanned_with_the_hold_still_on(db):
    held(db)
    assert [r["user_id"] for r in quarantine_review.queue(db, 1, 3)[0]] == [30] and quarantine_review.queue_count(db, 3) == 1
    db.execute(text("UPDATE users SET is_banned = 20, unban_utc = 0 WHERE id = 30"))
    assert quarantine_review.queue(db, 1, 3)[0] == [] and quarantine_review.queue_count(db, 3) == 0 and quarantine_review.queue(db, 1, 4)[0] == []
    db.execute(text("UPDATE users SET is_banned = 0 WHERE id = 30"))                                  # an unban changes only the ban
    assert [r["user_id"] for r in quarantine_review.queue(db, 1, 3)[0]] == [30] and quarantine_review.queue_count(db, 3) == 1
    assert aq.current_entry(db, 30) is not None


def test_a_level_4_hold_is_refused_to_level_3_and_nothing_else_is(db):
    assert quarantine_review.release_refusal(log.LEGAL, 3) and quarantine_review.release_refusal(log.LEGAL, 4) is None
    assert quarantine_review.release_refusal(log.STAFF, 3) is None and quarantine_review.release_refusal(0, 3) is None
    assert quarantine_review.release_refusal(None, 3) is None


def test_an_appeal_that_reverses_releases_the_hold_with_linked_entries_and_no_second_notice(db):
    entry_id = held(db)
    publish(db)                                                                                  # the hold's public line, written by the cycle
    db.info.pop(aq._PENDING, None)
    appeal = log_appeals.file_appeal(db, entry_id, 30, "Not spam.")
    # what routes/log_appeal_pages.py `_undo` does for the action
    made = aq.release(db, 30, admin_id=20, appeal_entry_id=entry_id, note="Reversed on appeal.", notify=True)
    assert made and made is not True
    assert not entries(db, action="automatic_quarantine_lifted")                                  # the lifting's line comes later too
    publish(db)
    (lift,) = entries(db, action="unquarantine_user")
    assert lift["id"] == made and lift["linked_entry_id"] == entry_id and lift["target_user_id"] == 30 and lift["audience"] == log.STAFF
    assert lift["staff_note"] == "lifted after an appeal"
    assert len(entries(db, action="automatic_quarantine_lifted")) == 1 and entries(db, id=entry_id)[0]["staff_note"].startswith("spam |")   # the original is never edited
    assert not [item for item in db.info.get(aq._PENDING, []) if item[0] == "lifted"]            # the appeal's own outcome notice tells the person
    assert log_appeals.decide(db, appeal, "reversed", 20, "ok", reversal_entry_id=made)
    assert log_appeals.is_undone(db, log_appeals.entry_of(db, entry_id))


# --- the notices ----------------------------------------------------------------------------------------------------------------------------------

def test_an_appeals_outcome_for_a_quarantine_has_its_own_words_and_no_link_to_a_staff_entry():
    for outcome in ("upheld", "reversed", "dismissed"):
        message, values = log_notices.outcome(outcome, entry_id=77, note="We looked.", action="quarantine_user")
        assert message in log_notices.QUARANTINE_OUTCOME_NOTICES.values() and message in log_notices.HELD_FROM_LIMITED
        assert set(values) == {"note"} and "%(log)s" not in message and "77" not in str(values)
        assert "hold" in message
    # every other action keeps the notice that links the entry
    message, values = log_notices.outcome("upheld", entry_id=77, note="We looked.")
    assert message == log_notices.UPHELD and values["log"] == "/log/77"
    assert log_notices.outcome("reversed", entry_id=5, note="x", action="ban_user")[0] == log_notices.REVERSED


# --- the wiring ----------------------------------------------------------------------------------------------------------------------------------

def _code(path):
    return re.sub(r'""".*?"""', "", read(*path), flags=re.S)


def test_the_routes_are_registered_and_carry_the_wrappers_in_the_order_the_other_pages_do():
    assert "from .quarantine_pages import *" in read("ruqqus", "routes", "__init__.py")
    source = read("ruqqus", "routes", "quarantine_pages.py")
    release = source.split('@app.post("/admin/quarantine/<int:user_id>/release")')[1].split("\ndef ", 1)[0]
    assert release.strip().startswith("@admin_level_required(3)") and "@validate_formkey" in release and "@not_quarantined" not in release
    body = source.split("def admin_quarantine_release(")[1].split("\ndef ", 1)[0]
    # the hold the form was drawn for: the row is locked, the entry the form posted is compared with the current one, and all of it before the note and the lift
    assert body.index("lock_hold(") < body.index("current_entry(") < body.index("is_stale(") < body.index("parse_reason(") < body.index("auto_quarantine.release(")
    assert 'parse_entry(request.form.get("entry"))' in body and "STALE" in body and "release_refusal(current[\"audience\"], v.admin_level)" in body
    post = source.split('@app.post("/appeal/quarantine")')[1].split("\ndef ", 1)[0].split("\n")
    decorators = [line.strip() for line in post if line.startswith("@")]
    assert decorators == ['@limiter.limit("20/hour")', "@auth_required", "@not_qr_limited", "@validate_formkey"]          # the way OUT of a hold: no `@not_quarantined`
    get = source.split('@app.get("/appeal/quarantine")')[1].split("\ndef ", 1)[0]
    assert "@auth_required" in get and "@limiter.limit" in get
    assert source.count("@app.get(") + source.count("@app.post(") == 4
    assert 'qr_limited_answer(v)' in source.split("def quarantine_appeal_form")[1].split("def quarantine_appeal_post")[0]


def test_the_page_finds_the_account_own_entry_and_never_reads_an_id_from_the_browser():
    source = read("ruqqus", "routes", "quarantine_pages.py")
    own = source.split("def _own_entry(")[1].split("\n@app", 1)[0]
    assert "auto_quarantine.current_entry(g.db, v.id)" in own and "is_suspended" in own and "is_auto_quarantined" in own
    for route in ("def quarantine_appeal_form", "def quarantine_appeal_post"):
        body = source.split(route)[1].split("\n@app", 1)[0]
        assert "request.args.get(\"entry" not in body and "request.form.get(\"entry" not in body and "entry_id" not in body.replace("entry[\"id\"]", ""), route


def test_the_generic_appeal_route_refuses_a_quarantine_entry_and_the_reversal_goes_through_the_one_release():
    code = read("ruqqus", "routes", "log_appeal_pages.py")
    assert "in log_appeals.OWN_PAGE" in code.split("def _appealable_or_404")[1].split("\n@app", 1)[0]
    undo = code.split('elif action == "quarantine_user":')[1].split("elif action in", 1)[0]
    assert "auto_quarantine.release(g.db, entry[\"target_user_id\"], admin_id=decider.id, appeal_entry_id=entry[\"id\"]" in undo
    assert "action=entry[\"action\"]" in code.split("def _settle")[1].split("@app.route")[0]


def test_the_queue_and_its_count_go_through_the_readers_level_and_never_the_masking_read():
    code = _code(("ruqqus", "routes", "quarantine_pages.py"))
    assert "quarantine_review.queue(g.db, page, level=level)" in code and "quarantine_review.queue_count(g.db, level)" in code
    assert "auto_quarantine.waiting(" not in code and "auto_quarantine.waiting_count(" not in code
    assert "def quarantine_waiting(viewer=None)" in code and "reader_level(getattr(viewer," in code
    review = _code(("ruqqus", "helpers", "quarantine_review.py"))
    assert review.count("_audience_clause(level)") == 3 and "FOR UPDATE" in review


def test_nothing_here_writes_the_hold_or_bans_anyone():
    for path in (("ruqqus", "routes", "quarantine_pages.py"), ("ruqqus", "helpers", "quarantine_review.py")):
        code = _code(path)
        assert not re.search(r"quarantined_utc\s*=|quarantine_kind\s*=|SET\s+quarantine|\.ban\(|\.unban\(|is_banned\s*=", code), path
    # the only things that change the hold are the module's own two functions
    assert "auto_quarantine.release(" in read("ruqqus", "routes", "quarantine_pages.py") and "auto_quarantine.hold(" not in read("ruqqus", "routes", "quarantine_pages.py")


def test_the_admin_page_lists_the_queue_and_the_appeals_page_says_what_the_check_saw():
    home = read("ruqqus", "templates", "admin", "admin_home.html")
    assert "/admin/quarantine" in home and "{{ quarantine_waiting(v) }}" in home and "quarantine_waiting()" not in home        # the count is the reader's own level's
    page = read("ruqqus", "templates", "admin", "quarantine.html")
    assert 'action="/admin/quarantine/{{ row.user_id }}/release"' in page and 'name="formkey"' in page and "required" in page
    assert '<input type="hidden" name="entry" value="{{ row.entry_id or 0 }}">' in page            # the hold this form was drawn for
    assert "{{ row.check }}" in page and "row.can_release" in page and "row.legal" not in page      # a legal hold is not drawn for anyone below level 4: no masked row
    assert "{{ r.check }}" in read("ruqqus", "templates", "admin", "appeals.html")


def test_the_persons_page_uses_words_the_site_already_has_and_a_form_to_its_own_route():
    page = read("ruqqus", "templates", "quarantine_appeal.html")
    assert 'action="/appeal/quarantine"' in page and 'name="formkey"' in page and "noindex" in page
    assert "{{ held }}" in page and "shown.reason" in page and "LOG_RULES" in page
    for sentence in ("Your appeal is waiting to be read.", "Your appeal was accepted, and the decision was reversed.", "Why was it wrong?", "Send appeal"):
        assert sentence in read("ruqqus", "templates", "log_appeal.html"), sentence               # the same msgids as the Log's own appeal page: no new string
        assert sentence in page, sentence
