"""The edges of an automatic quarantine, on the real app and Postgres (helpers/auto_quarantine.py holds; routes/login.py, helpers/wrappers.py `check_ban_evade` and
routes/admin_api.py `ban_user` / `unban_user` meet it). The owner, 2026-11-17 and 18: the site's own checks only quarantine; a real admin decides what follows.

- **A held account is not suspended**, so sign-up used to count only a suspended account in the browser's history as evasion, and a spammer held for spam who opened a
  new account in the same browser got no flag: the new account carries `ban_evade` 1 now (the guard's soft sign too) and `check_ban_evade` holds it as evasion. An account
  the signup guard holds (kind 0), one an admin released and a free one are not signs;
- **a ban for good of a held account closes the appeal waiting for the hold** (dismissed by the banning admin, no second notice: the ban's own tells the person), so a
  reversal of it can never release a banned account's hold; the queue leaves the banned account out; an alt banned with it is the same; **a suspension for a time leaves
  the appeal open** (the hold outlives it);
- **unbanning leaves the hold** (a state of its own, still in the queue) and the notice says the account is still on hold."""
import time
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from pg.test_log_trail_routes import admin_of, entries, get, notices_of, post, rows
from pg.test_quarantine_queue import appeal, appeals_of, count_for, hold_of, state, text_of
from pg.test_signup_guard import account, form_for, sign_up


@pytest.fixture(autouse=True)
def no_stale_notices(real_app):
    """A worker (and so a test's thread) may reuse one session, and its waiting notices live on it: what an earlier test queued is not this one's to send."""
    real_app[1]().info.pop("auto_quarantine_notices", None)
    yield
    real_app[1]().info.pop("auto_quarantine_notices", None)


# --- a new account in a browser an automatic check held ----------------------------------------------------------------------------------------------

def sign_up_after(real_app, earlier):
    """Sign up in a browser whose history holds the accounts `earlier` (what a browser keeps of the accounts signed in on it: routes/login.py `check_for_alts`).
    Returns (the new account's username, its client)."""
    app, _ = real_app
    client = app.test_client()
    form = form_for(client)
    with client.session_transaction() as session:
        session["history"] = [m.id for m in earlier]
    answer = sign_up(client, form)
    assert answer.status_code == 302 and answer.headers["Location"].endswith("/"), answer.headers.get("Location")
    assert account(real_app, form["username"]) is not None
    return form["username"], client


def evade_of(real_app, username):
    (row,) = rows(real_app, "SELECT id, ban_evade FROM users WHERE username = :u", u=username)
    return row


def test_a_new_account_in_a_browser_an_automatic_check_held_is_a_sign_of_evasion_and_is_then_held_as_evasion(real_app, make_member, monkeypatch):
    from ruqqus.helpers import wrappers
    spammer = make_member()
    entry = hold_of(real_app, spammer, "spam", detail="six similar posts in a minute")
    assert entry and state(real_app, spammer)["quarantine_kind"] == 1 and not state(real_app, spammer)["is_banned"]
    username, client = sign_up_after(real_app, [spammer])
    new = evade_of(real_app, username)
    assert new["ban_evade"] == 1
    (review,) = rows(real_app, "SELECT reasons FROM signup_reviews WHERE user_id = :u", u=new["id"])
    assert "ban_evasion" in review["reasons"].split(",")                                         # the guard's soft sign fired too
    assert rows(real_app, "SELECT 1 FROM alts WHERE (user1 = :a AND user2 = :b) OR (user1 = :b AND user2 = :a)", a=spammer.id, b=new["id"])

    # the next request rolls the dice (always a hit here) and holds it, as evasion, without refusing the request
    monkeypatch.setattr(wrappers.random, "randint", lambda a, b: 0)
    assert get(client, "/settings/profile").status_code == 200
    held = state(real_app, SimpleNamespace(id=new["id"]))
    assert held["quarantine_kind"] == 1 and held["quarantined_utc"] > 0 and not held["is_banned"]
    (staff,) = entries(real_app, action="quarantine_user", target_user_id=new["id"])
    assert staff["audience"] == 2 and staff["staff_note"].startswith("evasion | ") and "held by an automatic check" in staff["staff_note"]
    assert not entries(real_app, action="automatic_quarantine", linked_entry_id=staff["id"])     # ban-evasion detection has no public line


def test_a_free_account_a_released_one_and_the_signup_guards_hold_are_not_signs_of_evasion_and_a_suspended_one_still_is(real_app, make_member):
    free, released, guarded, suspended, boss = make_member(), make_member(), make_member(), make_member(), admin_of(make_member)
    hold_of(real_app, released)
    from ruqqus.helpers import auto_quarantine
    db = real_app[1]()
    db.rollback()
    assert auto_quarantine.release(db, released.id, admin_id=boss.id, note="fine", notify=False)
    db.commit()
    db = guarded.db_session()
    db.execute(text("UPDATE users SET quarantined_utc = :now WHERE id = :u"), {"now": int(time.time()), "u": guarded.id})      # the signup guard's hold: kind 0
    db.execute(text("UPDATE users SET is_banned = :a, unban_utc = :until WHERE id = :u"), {"a": boss.id, "until": int(time.time()) + 3600, "u": suspended.id})
    db.commit()
    assert state(real_app, guarded)["quarantine_kind"] == 0 and state(real_app, guarded)["quarantined_utc"] > 0
    for member, expected in ((free, 0), (released, 0), (guarded, 0), (suspended, 1)):
        username, _ = sign_up_after(real_app, [member])
        assert evade_of(real_app, username)["ban_evade"] == expected, member.username
    username, _ = sign_up_after(real_app, [])
    assert evade_of(real_app, username)["ban_evade"] == 0


# --- banning an account the site holds ---------------------------------------------------------------------------------------------------------------

def ban(boss, member, days="0"):
    answer = post(boss.browser(), boss, f"/api/ban_user/{member.id}", days=days, reason="Posting spam links", message="That was spam.", rule="evasion")
    assert answer.status_code in (200, 302), text_of(answer)[:300]


def test_banning_a_held_account_for_good_settles_its_waiting_appeal_so_a_reversal_cannot_release_it_and_it_leaves_the_queue(real_app, make_member):
    member, boss, other = make_member(), admin_of(make_member), admin_of(make_member)
    hold_of(real_app, member)
    filed = appeal(real_app, member)
    waiting = count_for(real_app, 3)
    assert f'id="quarantine-{member.id}"' in text_of(get(boss.browser(), "/admin/quarantine"))
    assert f'action="/admin/appeals/{filed["id"]}"' in text_of(get(other.browser(), "/admin/appeals"))

    ban(boss, member)
    assert state(real_app, member)["is_banned"] and state(real_app, member)["quarantine_kind"] == 1 and state(real_app, member)["quarantined_utc"] > 0   # the hold is left as it is
    (settled,) = appeals_of(real_app, member)
    assert (settled["status"], settled["decided_by"], settled["reversal_entry_id"]) == ("dismissed", boss.id, None)
    assert "suspended" in settled["decision_note"] and "its own appeal" in settled["decision_note"]
    assert not entries(real_app, action="unquarantine_user", target_user_id=member.id)           # nothing was lifted

    assert count_for(real_app, 3) == waiting - 1
    assert f'id="quarantine-{member.id}"' not in text_of(get(boss.browser(), "/admin/quarantine"))
    assert f'action="/admin/appeals/{filed["id"]}"' not in text_of(get(other.browser(), "/admin/appeals"))
    # a second admin who opens the old appeal can no longer reverse it into a release of the banned account
    again = post(other.browser(), other, f"/admin/appeals/{filed['id']}", outcome="reversed", note="Let it out")
    assert "already%20decided" in again.headers["Location"]
    assert state(real_app, member)["quarantine_kind"] == 1 and state(real_app, member)["is_banned"]

    # the person is told once, by the ban's own notice: no outcome notice for the appeal
    told = notices_of(real_app, member.id)
    assert sum("permanently suspended" in body for body in told) == 1
    assert not [body for body in told if "appeal of the hold" in body]
    assert get(member.browser(), "/appeal/quarantine").status_code == 404                        # a ban has its own appeal


def test_the_csam_routes_hand_ban_settles_a_waiting_hold_appeal_too(real_app, make_member):
    """The closing lives in `User.ban` (every permanent ban goes through it: the admin's, an alt's, the site's own; the signup queue lists only the signup guard's holds, kind 0,
    so an automatic check's hold never reaches it) and in `quarantine_review.close_hold_appeal_for_ban`, which the CSAM route's hand ban (outside `User.ban`) calls: a waiting
    appeal is dismissed by the banning admin, once, the hold itself is left as it is, and an account that isn't held has nothing to close."""
    from ruqqus.helpers import quarantine_review
    _, db_session = real_app
    boss, handed, free = admin_of(make_member), make_member(), make_member()
    hold_of(real_app, handed)
    appeal(real_app, handed)
    db = db_session()
    db.rollback()
    assert quarantine_review.close_hold_appeal_for_ban(db, handed.id, boss.id) == 1
    db.commit()
    (settled,) = appeals_of(real_app, handed)
    assert (settled["status"], settled["decided_by"]) == ("dismissed", boss.id) and state(real_app, handed)["quarantined_utc"] > 0, "the hold itself is left as it is"
    assert not entries(real_app, action="unquarantine_user", target_user_id=handed.id)
    assert quarantine_review.close_hold_appeal_for_ban(db, handed.id, boss.id) == 0, "once each"
    assert quarantine_review.close_hold_appeal_for_ban(db, free.id, boss.id) == 0, "an account that isn't held has nothing to close"


def test_an_alt_banned_with_the_main_account_has_its_appeal_settled_too_and_another_account_is_left_alone(real_app, make_member):
    main, alt, bystander, boss = make_member(), make_member(), make_member(), admin_of(make_member)
    hold_of(real_app, main, "evasion", detail="its browser had been used by a suspended account", alts=[alt])
    hold_of(real_app, bystander)
    db = main.db_session()
    db.execute(text("INSERT INTO alts (user1, user2) VALUES (:a, :b)"), {"a": main.id, "b": alt.id})
    db.commit()
    theirs = [appeal(real_app, member) for member in (main, alt, bystander)][-1]
    ban(boss, main)
    assert state(real_app, alt)["is_banned"]                                                      # the existing route bans an alt with it, for good
    for member in (main, alt):
        (row,) = appeals_of(real_app, member)
        assert (row["status"], row["decided_by"], row["reversal_entry_id"]) == ("dismissed", boss.id, None), member.username
    assert appeals_of(real_app, bystander)[0]["status"] == "open" and appeals_of(real_app, bystander)[0]["id"] == theirs["id"]
    assert state(real_app, bystander)["quarantine_kind"] == 1 and not state(real_app, bystander)["is_banned"]


def test_a_suspension_for_a_time_leaves_the_hold_and_its_appeal_alone(real_app, make_member):
    member, boss = make_member(), admin_of(make_member)
    hold_of(real_app, member)
    filed = appeal(real_app, member)
    ban(boss, member, days="3")
    assert state(real_app, member)["is_banned"] and state(real_app, member)["quarantine_kind"] == 1
    (row,) = appeals_of(real_app, member)
    assert (row["id"], row["status"], row["decided_by"]) == (filed["id"], "open", None)         # the hold outlives it, and so does the appeal of it
    assert f'id="quarantine-{member.id}"' not in text_of(get(boss.browser(), "/admin/quarantine"))      # but a suspended account is not in the queue meanwhile


def test_an_account_that_is_not_held_is_banned_as_before(real_app, make_member):
    member, boss = make_member(), admin_of(make_member)
    ban(boss, member)
    assert state(real_app, member)["is_banned"] and not rows(real_app, "SELECT 1 FROM log_appeals WHERE user_id = :u", u=member.id)
    assert any("permanently suspended" in body for body in notices_of(real_app, member.id))


# --- reinstating an account the site holds -----------------------------------------------------------------------------------------------------------

def reinstate(boss, member):
    answer = post(boss.browser(), boss, f"/api/unban_user/{member.id}", reason="A mistake")
    assert answer.status_code in (200, 302), text_of(answer)[:300]


def test_reinstating_a_held_account_leaves_the_hold_in_the_queue_and_the_notice_says_it_is_still_held(real_app, make_member):
    member, boss = make_member(), admin_of(make_member)
    entry = hold_of(real_app, member)
    appeal(real_app, member)
    ban(boss, member)
    waiting = count_for(real_app, 3)
    reinstate(boss, member)
    held = state(real_app, member)
    assert not held["is_banned"] and held["quarantine_kind"] == 1 and held["quarantined_utc"] > 0   # the hold is a state of its own: an admin or an appeal lifts it
    assert not entries(real_app, action="unquarantine_user", target_user_id=member.id)
    assert count_for(real_app, 3) == waiting + 1
    assert f'id="quarantine-{member.id}"' in text_of(get(boss.browser(), "/admin/quarantine"))
    told = [body for body in notices_of(real_app, member.id) if "has been reinstated" in body]
    assert len(told) == 1
    assert "It is still on hold, so it can't post, comment or act on others for now." in told[0] and "A member of the staff will look at it." in told[0]
    assert "/help/terms" in told[0] and "/help/rules" in told[0]
    # it is still refused what acts on others, with the hold's own words
    browser = member.browser()
    refused = post(browser, member, "/api/vote/post/1/1")
    assert refused.status_code == 403 and "An automatic check put your account on hold" in text_of(refused)
    # the staff's entry is the one the hold had
    from ruqqus.helpers import auto_quarantine
    db = real_app[1]()
    db.rollback()
    assert auto_quarantine.current_entry(db, member.id)["id"] == entry


def test_reinstating_an_account_that_is_not_held_says_nothing_about_a_hold(real_app, make_member):
    member, boss = make_member(), admin_of(make_member)
    ban(boss, member)
    reinstate(boss, member)
    assert not state(real_app, member)["is_banned"] and state(real_app, member)["quarantined_utc"] == 0
    told = [body for body in notices_of(real_app, member.id) if "has been reinstated" in body]
    assert len(told) == 1 and "still on hold" not in told[0] and "suspended again" in told[0]


def test_reinstating_an_account_the_signup_guard_holds_says_it_is_still_on_hold_too(real_app, make_member):
    member, boss = make_member(), admin_of(make_member)
    db = member.db_session()
    db.execute(text("UPDATE users SET quarantined_utc = :now WHERE id = :u"), {"now": int(time.time()), "u": member.id})
    db.commit()
    ban(boss, member)
    reinstate(boss, member)
    assert state(real_app, member)["quarantined_utc"] > 0 and state(real_app, member)["quarantine_kind"] == 0
    told = [body for body in notices_of(real_app, member.id) if "has been reinstated" in body]
    assert len(told) == 1 and "It is still on hold" in told[0]
