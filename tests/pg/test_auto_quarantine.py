"""What the site's own checks do to an account, on the real app and Postgres (helpers/auto_quarantine.py; the rules and the SQL are tested on SQLite in
tests/test_auto_quarantine.py). The owner, 2026-11-17: an automatic check only QUARANTINES; a real admin decides what follows.

- a held account can read the site and change its own settings and is refused what acts on others, with the automatic hold's own words and the review link
  (no email is offered: none releases it); the page shows its own banner, only to the account;
- the staff's entry names the account and the public line names nobody (a visitor reads it, with no name and no trigger); ban-evasion detection has no public line;
- verifying an email, approving or banning in the signup guard's queue never lifts it; an admin's release does, with a public line that names nobody, and the person is told;
- ban-evasion detection quarantines (the real wrapper), removes nothing the account wrote and does not refuse the request."""
import secrets
import time

import gevent
import pytest
from sqlalchemy import text

from pg.test_log_trail_routes import BASE, HEADERS, add_comment, add_post, address, admin_of, entries, get, guild, guildmaster, notices_of, post, rows


@pytest.fixture(autouse=True)
def no_stale_notices(real_app):
    """A worker (and so a test's thread) may reuse one session, and its waiting notices live on it: what an earlier test queued is not this one's to send."""
    real_app[1]().info.pop("auto_quarantine_notices", None)
    yield
    real_app[1]().info.pop("auto_quarantine_notices", None)


def hold_of(real_app, member, trigger="spam", detail="six similar posts in a minute", alts=(), notify=False):
    from ruqqus.classes import User
    from ruqqus.helpers import auto_quarantine
    _, db_session = real_app
    db = db_session()
    db.rollback()
    entry = auto_quarantine.hold(db, db.get(User, member.id), trigger, detail=detail, alts=[db.get(User, a.id) for a in alts], notify=notify)
    db.commit()
    return entry


def publish_lines(real_app):
    """The cycle's step that writes the public lines (`auto_quarantine.publish_due`), run as if just past the delay."""
    from ruqqus.helpers import auto_quarantine
    db = real_app[1]()
    db.rollback()
    written = auto_quarantine.publish_due(db, now=int(time.time()) + auto_quarantine.PUBLISH_DELAY + 1)
    db.commit()
    return written


def state(real_app, member):
    (row,) = rows(real_app, "SELECT quarantined_utc, quarantine_kind, is_banned FROM users WHERE id = :u", u=member.id)
    return row


def test_an_automatic_hold_refuses_what_acts_on_others_with_its_own_words_and_the_review_link(real_app, make_member):
    member = make_member()
    entry = hold_of(real_app, member)
    assert entry and state(real_app, member)["quarantine_kind"] == 1 and state(real_app, member)["quarantined_utc"] > 0 and not state(real_app, member)["is_banned"]
    browser = member.browser()
    key = member.formkey(browser)
    assert get(browser, "/").status_code == 200 and get(browser, "/settings/security").status_code == 200           # it can read and change its own settings

    refused = browser.post("/api/vote/post/1/1", base_url=BASE, headers=HEADERS, data={"formkey": key}, environ_base={"REMOTE_ADDR": address()})
    assert refused.status_code == 403 and "An automatic check put your account on hold" in refused.get_json()["error"]
    assert refused.get_json()["held"] is True, "the marker a script (the bot runner) reads, never the words"
    assert "email" not in refused.get_json()["error"].lower()
    page = browser.post("/gift_post/1", base_url=BASE, data={"formkey": key}, headers={**HEADERS, "Sec-Fetch-Mode": "navigate"}, environ_base={"REMOTE_ADDR": address()})
    assert page.status_code == 403 and b'href="/appeal/quarantine"' in page.data and b"Ask for a review" in page.data
    assert b'href="/settings/security"' not in page.data
    # what it tried is counted, as for the signup guard's hold
    from ruqqus.__main__ import r
    from ruqqus.helpers import signup_signals
    assert signup_signals.attempts(r, member.id).get("vote") == 1


def test_the_banner_is_the_automatic_one_and_only_the_account_sees_it(real_app, make_member):
    member, other = make_member(), make_member()
    hold_of(real_app, member)
    own = get(member.browser(), "/")
    assert b'id="held-notice"' in own.data and b"An automatic check put your account on hold." in own.data and b'href="/appeal/quarantine"' in own.data
    assert b"Verify an email to skip the wait" not in own.data
    assert b'id="held-notice"' not in get(other.browser(), "/").data
    # nothing public names it: not the profile, not a visitor's page
    visitor = real_app[0].test_client()
    profile = get(visitor, f"/@{member.username}")
    assert profile.status_code == 200 and b"An automatic check" not in profile.data and b'id="held-notice"' not in profile.data


def test_the_staff_entry_names_the_account_and_the_public_line_names_nobody(real_app, make_member):
    member, boss = make_member(), admin_of(make_member)
    entry = hold_of(real_app, member, "spam", detail="six similar posts in a minute")
    (staff,) = entries(real_app, id=entry)
    assert (staff["action"], staff["audience"], staff["target_user_id"], staff["actor_role"], staff["staff_note"]) == (
        "quarantine_user", 2, member.id, "system", "spam | six similar posts in a minute")
    assert not entries(real_app, action="automatic_quarantine", linked_entry_id=entry)            # hold() writes the staff entry only
    publish_lines(real_app)
    (line,) = entries(real_app, action="automatic_quarantine", linked_entry_id=entry)
    assert (line["audience"], line["target_user_id"], line["target_submission_id"], line["target_comment_id"], line["board_id"], line["reason"]) == (0, None, None, None, None, "Automatic spam check")
    assert line["linked_entry_id"] == entry

    visitor = real_app[0].test_client()
    page = get(visitor, f"/log/{line['id']}").get_data(as_text=True)                       # by id: a line dated to the hour sorts below the hour's other entries
    assert "put an account in quarantine" in page and "Automatic spam check" in page
    page += get(visitor, "/log?type=sanction").get_data(as_text=True)
    assert member.username not in page and "quarantine_user" not in page and "six similar posts" not in page
    staff_page = get(boss.browser(), f"/admin/log?action=quarantine_user&target={member.username}").get_data(as_text=True)
    assert "quarantine_user" in staff_page and "six similar posts in a minute" in staff_page and member.username in staff_page


def test_ban_evasion_detection_has_no_public_line_and_the_notice_names_no_rule(real_app, make_member):
    member = make_member()
    entry = hold_of(real_app, member, "evasion", detail="its browser had been used by a suspended account", notify=True)
    publish_lines(real_app)
    assert entry and not entries(real_app, action="automatic_quarantine", linked_entry_id=entry)            # a public line would teach how alts are caught
    (staff,) = entries(real_app, id=entry)
    assert staff["audience"] == 2 and staff["rule_key"] == "evasion"
    real_app[1]().info.pop("auto_quarantine_notices", None)


def test_verifying_an_email_never_lifts_an_automatic_hold_but_still_lifts_the_signup_guards(real_app, make_member):
    from ruqqus.helpers.security import generate_hash
    automatic, guarded = make_member(), make_member()
    hold_of(real_app, automatic)
    db = guarded.db_session()
    db.execute(text("UPDATE users SET quarantined_utc = :now WHERE id = :u"), {"now": int(time.time()), "u": guarded.id})
    db.execute(text("INSERT INTO signup_reviews (user_id, created_utc, score, reasons, network, name_shape, decision) VALUES (:u, :now, 60, 'fast', 'n', 's', '')"),
               {"u": guarded.id, "now": int(time.time())})
    db.commit()
    for member in (automatic, guarded):
        email = f"held{secrets.token_hex(3)}@example.com"
        now = int(time.time())
        link = f"/activate?email={email}&id={member.id}&time={now}&token={generate_hash(f'{email}+{member.id}+{now}')}"
        assert member.browser().get(link, base_url=BASE, headers=HEADERS).status_code == 200
    assert state(real_app, automatic)["quarantined_utc"] > 0 and state(real_app, automatic)["quarantine_kind"] == 1
    assert state(real_app, guarded)["quarantined_utc"] == 0


def test_the_signup_guards_queue_neither_lists_nor_clears_an_automatic_hold(real_app, make_member):
    member, boss = make_member(), admin_of(make_member)
    db = member.db_session()
    db.execute(text("UPDATE users SET quarantined_utc = :now WHERE id = :u"), {"now": int(time.time()), "u": member.id})
    db.execute(text("INSERT INTO signup_reviews (user_id, created_utc, score, reasons, network, name_shape, decision) VALUES (:u, :now, 60, 'fast', 'n', 's', '')"),
               {"u": member.id, "now": int(time.time())})
    db.commit()
    hold_of(real_app, member)                                                       # the guard's hold becomes an automatic one
    browser = boss.browser()
    assert f'id="signup-{member.id}"'.encode() not in get(browser, "/admin/signups").data
    answer = post(browser, boss, "/admin/signups/decide", uid=[str(member.id)], decision="approve")
    assert answer.status_code == 302
    assert state(real_app, member)["quarantined_utc"] > 0 and state(real_app, member)["quarantine_kind"] == 1


def test_an_admin_lifts_it_and_the_person_is_told_and_the_public_line_names_nobody(real_app, make_member):
    from ruqqus.helpers import auto_quarantine
    _, db_session = real_app
    member, boss = make_member(), admin_of(make_member)
    held_entry = hold_of(real_app, member)
    publish_lines(real_app)                                                         # the hold's line, written by the cycle while the hold stands
    assert entries(real_app, action="automatic_quarantine", linked_entry_id=held_entry)
    db = db_session()
    db.rollback()
    lifted = auto_quarantine.release(db, member.id, admin_id=boss.id, note="an ordinary post, not spam")
    db.commit()
    auto_quarantine.send_pending(db)
    assert not entries(real_app, action="automatic_quarantine_lifted", linked_entry_id=lifted)    # the lifting's line comes an hour later too
    auto_quarantine.publish_due(db, now=int(time.time()) + auto_quarantine.PUBLISH_DELAY + 1)
    db.commit()
    assert lifted and lifted is not True
    assert state(real_app, member)["quarantined_utc"] == 0 and state(real_app, member)["quarantine_kind"] == 0
    (staff,) = entries(real_app, id=lifted)
    assert (staff["action"], staff["audience"], staff["actor_id"], staff["actor_role"], staff["target_user_id"]) == ("unquarantine_user", 2, boss.id, "admin", member.id)
    (public,) = entries(real_app, action="automatic_quarantine_lifted", linked_entry_id=lifted)
    assert (public["audience"], public["target_user_id"], public["board_id"]) == (0, None, None)
    page = get(real_app[0].test_client(), f"/log/{public['id']}").get_data(as_text=True)
    assert "lifted a quarantine an automatic check had placed" in page and member.username not in page
    assert any("hold on your account was lifted" in body for body in notices_of(real_app, member.id))
    # and it can act again: the held refusal is gone (the vote itself may fail for another reason)
    browser = member.browser()
    answer = browser.post("/api/vote/post/1/1", base_url=BASE, headers=HEADERS, data={"formkey": member.formkey(browser)}, environ_base={"REMOTE_ADDR": address()})
    assert b"on hold" not in answer.data and b'id="held-notice"' not in get(browser, "/").data


def test_the_person_is_told_when_it_is_placed_with_the_rule_the_generic_reason_and_the_review_link(real_app, make_member):
    from ruqqus.helpers import auto_quarantine
    _, db_session = real_app
    member = make_member()
    db = db_session()
    db.rollback()
    from ruqqus.classes import User
    entry = auto_quarantine.hold(db, db.get(User, member.id), "spam", detail="a distinctive detail")
    db.commit()
    assert auto_quarantine.send_pending(db) == 1
    told = [body for body in notices_of(real_app, member.id) if "put your account on hold" in body]
    assert len(told) == 1
    assert "Evading and manipulating the site: Automatic spam check" in told[0] and "/appeal/quarantine" in told[0]
    assert "spam |" not in told[0] and "distinctive" not in told[0]                                              # never what was matched
    assert rows(real_app, "SELECT notice_sent FROM log_entries WHERE id = :e", e=entry)[0]["notice_sent"] is True
    assert auto_quarantine.send_pending(db) == 0                                  # once


def test_ban_evasion_detection_quarantines_removes_nothing_and_does_not_refuse_the_request(real_app, make_member, monkeypatch):
    from ruqqus.helpers import wrappers
    member = make_member()
    db = member.db_session()
    db.execute(text("UPDATE users SET ban_evade = 5 WHERE id = :u"), {"u": member.id})
    db.commit()
    monkeypatch.setattr(wrappers.random, "randint", lambda a, b: 0)                 # the roll always hits
    answer = get(member.browser(), "/")
    assert answer.status_code == 200
    held = state(real_app, member)
    assert held["quarantined_utc"] > 0 and held["quarantine_kind"] == 1 and not held["is_banned"]
    (staff,) = entries(real_app, action="quarantine_user", target_user_id=member.id)
    assert staff["audience"] == 2 and staff["staff_note"].startswith("evasion | ")
    publish_lines(real_app)
    assert not entries(real_app, action="automatic_quarantine", linked_entry_id=staff["id"]) and not entries(real_app, action="ban_user", target_user_id=member.id)
    # the notice goes after the request's commit, in a greenlet of its own
    for _ in range(50):
        if any("put your account on hold" in body for body in notices_of(real_app, member.id)):
            break
        gevent.sleep(0.1)
    told = [body for body in notices_of(real_app, member.id) if "put your account on hold" in body]
    assert len(told) == 1 and "Evading and manipulating" not in told[0] and "Automatic check" in told[0]
    # a second request counts nothing and writes nothing more
    monkeypatch.setattr(wrappers.random, "randint", lambda a, b: 0)
    assert get(member.browser(), "/").status_code == 200
    assert len(entries(real_app, action="quarantine_user", target_user_id=member.id)) == 1


def test_an_admin_a_bot_and_a_banned_account_are_never_held_by_a_check(real_app, make_member):
    admin, bot, banned = admin_of(make_member), make_member(), make_member()
    db = bot.db_session()
    db.execute(text("UPDATE users SET is_bot = true WHERE id = :u"), {"u": bot.id})
    db.execute(text("UPDATE users SET is_banned = 1 WHERE id = :u"), {"u": banned.id})
    db.commit()
    for member in (admin, bot, banned):
        assert hold_of(real_app, member) is None
        assert state(real_app, member)["quarantined_utc"] == 0 and not entries(real_app, target_user_id=member.id)


# --- a quarantine stops what a suspension used to: every route that writes what others read, and a guildmaster's powers (the review of 2026-11-18) ---------------------

HELD_WORDS = "An automatic check put your account on hold"
# one route of every kind the owner's rule reaches, with ids that don't exist: the wrapper answers before the route looks anything up
CHANGING_ROUTES = (
    "/edit_comment/zzzzzz", "/author/hide_comment/zzzzzz", "/+ci_nowhere/wiki/index/edit", "/+ci_nowhere/events/new", "/api/spaces", "/api/spaces/zzzzzz/messages",
    "/api/spaces/zzzzzz/updates", "/api/post/zzzzzz/live_updates", "/api/post/zzzzzz/live_summary", "/api/guilds/channels/1/messages", "/api/guilds/channels/1/remove",
    "/api/broadcasts", "/api/broadcasts/1/messages", "/collab/new", "/api/collab/1/ops", "/api/collab/1/publish", "/api/coauthor/zzzzzz/invite", "/create_guild",
    "/siege_guild", "/curations/create", "/api/highlights", "/regions/nowhere/propose", "/api/circles", "/api/chat/groups/1/add", "/api/chat/report",
    "/api/stories/1/report", "/api/spaces/zzzzzz/report")


def test_a_held_account_is_refused_every_route_that_writes_what_others_read_and_a_free_one_is_not(real_app, make_member):
    member, other = make_member(), make_member()
    hold_of(real_app, member)
    held, free = member.browser(), other.browser()
    for path in CHANGING_ROUTES:
        refused = post(held, member, path)
        assert refused.status_code == 403 and HELD_WORDS in refused.get_data(as_text=True), (path, refused.status_code, refused.get_data(as_text=True)[:200])
        assert HELD_WORDS not in post(free, other, path).get_data(as_text=True), path


def test_a_held_account_cannot_rewrite_its_old_comments(real_app, make_member):
    member = make_member()
    board_id, _ = guild(real_app)
    post_id, _ = add_post(member, board_id)
    cid, c36 = add_comment(member, post_id, board_id, body="<p>Original words.</p>")
    hold_of(real_app, member)
    browser = member.browser()
    refused = post(browser, member, f"/edit_comment/{c36}", body="Visit my spam site", edit_summary="")
    assert refused.status_code == 403 and HELD_WORDS in refused.get_data(as_text=True)
    assert "Original words." in rows(real_app, "SELECT body FROM comments_aux WHERE id = :c", c=cid)[0]["body"]


def test_a_held_guildmaster_has_no_powers_but_still_reads_and_a_stranger_learns_nothing(real_app, make_member):
    board_id, name = guild(real_app)
    gm, author, stranger = guildmaster(make_member, board_id), make_member(), make_member()
    post_id, b36 = add_post(author, board_id)
    hold_of(real_app, gm)
    hold_of(real_app, stranger)
    refused = post(gm.browser(), gm, f"/mod/kick/{name}/{b36}", reason="Advertising", rule="good_faith")
    assert refused.status_code == 403 and HELD_WORDS in refused.get_data(as_text=True)
    assert not entries(real_app, board_id=board_id, target_submission_id=post_id)             # nothing was hidden, nothing was logged
    plain = post(stranger.browser(), stranger, f"/mod/kick/{name}/{b36}", reason="Advertising", rule="good_faith")
    assert plain.status_code == 403 and HELD_WORDS not in plain.get_data(as_text=True) and f"aren't a guildmaster of +{name}" in plain.get_data(as_text=True)
    assert get(gm.browser(), f"/+{name}").status_code == 200                                    # it can still read the guild
    # lifted, the same guildmaster hides it
    from ruqqus.helpers import auto_quarantine
    db = real_app[1]()
    db.rollback()
    assert auto_quarantine.release(db, gm.id, admin_id=admin_of(make_member).id, notify=False)
    db.commit()
    assert post(gm.browser(), gm, f"/mod/kick/{name}/{b36}", reason="Advertising", rule="good_faith").status_code == 200
