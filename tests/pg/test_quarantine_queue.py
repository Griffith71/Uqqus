"""The staff queue and the person's appeal of an automatic quarantine, on the real app and Postgres (routes/quarantine_pages.py, helpers/quarantine_review.py, the
quarantine branch of routes/log_appeal_pages.py; the rules and SQL are tested on SQLite in tests/test_quarantine_queue.py, the hold itself in test_auto_quarantine.py).
The owner, 2026-11-17 and 18: the site's own checks only quarantine; a real admin decides what follows, and the person can appeal once per quarantine.

- the account finds its own entry server-side at /appeal/quarantine (an account that isn't held, a visitor and a limited QR sign-in get nothing), files once, and is
  shown the generic wording and never what was matched; ban-evasion detection and an alt's hold name no rule;
- the staff queue lists the hold with the check and the appeal, releases it with a note (the person told once, a waiting appeal settled as reversed, a public line that
  names nobody), or leaves it to the usual ban on the profile, after which the account leaves the queue; a non-admin is refused;
- an appeal is decided at /admin/appeals: reversed releases the hold with new linked entries and tells the person once, upheld leaves it;
- a release lifts the hold the admin was shown (the form posts the staff entry's id): a hold that was lifted and placed again since the page was drawn is refused, and
  the appeal of the new one stays open;
- a hold whose entry is legal-level (a link about minors) is a level-4 admin's, in the queue, in the count on /admin, in the release and in the appeal: a level-3 admin is
  shown nothing of it at all, no row, no name, no appeal badge."""
import re
import time

import pytest
from sqlalchemy import text

from pg.test_log_trail_routes import admin_of, entries, get, notices_of, post, rows


@pytest.fixture(autouse=True)
def no_stale_notices(real_app):
    """A worker (and so a test's thread) may reuse one session, and its waiting notices live on it: what an earlier test queued is not this one's to send."""
    real_app[1]().info.pop("auto_quarantine_notices", None)
    yield
    real_app[1]().info.pop("auto_quarantine_notices", None)


def publish_lines(real_app):
    """The cycle's step that writes the public lines (`auto_quarantine.publish_due`), run as if just past its delay."""
    from ruqqus.helpers import auto_quarantine
    db = real_app[1]()
    db.rollback()
    written = auto_quarantine.publish_due(db, now=int(time.time()) + auto_quarantine.PUBLISH_DELAY + 1)
    db.commit()
    return written


def hold_of(real_app, member, trigger="spam", detail="six similar posts in a minute", alts=()):
    from ruqqus.classes import User
    from ruqqus.helpers import auto_quarantine
    _, db_session = real_app
    db = db_session()
    db.rollback()
    entry = auto_quarantine.hold(db, db.get(User, member.id), trigger, detail=detail, alts=[db.get(User, a.id) for a in alts], notify=False)
    db.commit()
    assert entry
    return entry


def state(real_app, member):
    (row,) = rows(real_app, "SELECT quarantined_utc, quarantine_kind, is_banned FROM users WHERE id = :u", u=member.id)
    return row


def appeals_of(real_app, member):
    return rows(real_app, "SELECT id, entry_id, board_id, status, decided_by, decision_note, reversal_entry_id, reason FROM log_appeals WHERE user_id = :u ORDER BY id", u=member.id)


def text_of(answer):
    return answer.get_data(as_text=True)


def check_card(page):
    """The person's card about the check (`#quarantine-check`): the page around it also carries site-wide dialogs, whose menus list every rule."""
    start = page.index('id="quarantine-check"')
    return page[start:page.index("</div>\n  </div>", start)]


def card_of(page, member):
    """The queue's card for this account, from its anchor to the next card: the shared database holds other tests' held accounts too, so a page is never asserted whole."""
    start = page.index(f'id="quarantine-{member.id}"')
    end = page.find('class="card mb-2 quarantine-row"', start)
    return page[start:end if end != -1 else len(page)]


def count_for(real_app, level):
    """The number the queue reads for an admin of this level (what /admin prints beside Quarantine)."""
    from ruqqus.helpers import quarantine_review
    db = real_app[1]()
    db.rollback()
    return quarantine_review.queue_count(db, level)


def count_on_admin_page(browser):
    page = text_of(get(browser, "/admin"))
    found = re.search(r'href="/admin/quarantine">[^<]*</a>\s*\((\d+) waiting\)', page)
    assert found, "the admin page has no Quarantine line"
    return int(found.group(1))


def release(browser, admin, member, entry, note="An ordinary post, not spam"):
    return post(browser, admin, f"/admin/quarantine/{member.id}/release", entry=entry, note=note)


def appeal(real_app, member, reason="That was an ordinary post, not spam."):
    answer = post(member.browser(), member, "/appeal/quarantine", reason=reason)
    assert answer.status_code == 303, text_of(answer)[:300]
    (row,) = appeals_of(real_app, member)
    return row


# --- the person's page -------------------------------------------------------------------------------------------------------------------------------

def test_the_held_account_finds_its_own_entry_is_shown_the_generic_wording_and_appeals_once(real_app, make_member):
    member, other = make_member(), make_member()
    entry = hold_of(real_app, member, "spam", detail="a distinctive detail the person must never read")
    page = get(member.browser(), "/appeal/quarantine")
    body = text_of(page)
    assert page.status_code == 200 and page.headers["X-Robots-Tag"] == "noindex" and "private" in page.headers["Cache-Control"]
    assert "Your account is on hold" in body and "A member of the staff will look at it." in body
    assert "Automatic spam check" in body and "Evading and manipulating the site" in body
    assert "distinctive" not in body and "spam |" not in body and 'action="/appeal/quarantine"' in body
    assert get(other.browser(), "/appeal/quarantine").status_code == 404                           # not held by an automatic check
    assert get(real_app[0].test_client(), "/appeal/quarantine").status_code in (302, 401)          # a visitor signs in first

    sent = post(member.browser(), member, "/appeal/quarantine", reason="That was an ordinary post, not spam.")
    assert sent.status_code == 303 and sent.headers["Location"].endswith("/appeal/quarantine?sent=1")
    (filed,) = appeals_of(real_app, member)
    assert (filed["entry_id"], filed["board_id"], filed["status"]) == (entry, None, "open")             # the site's queue, the account's own entry
    after = text_of(get(member.browser(), "/appeal/quarantine?sent=1"))
    assert "Your appeal was sent." in after and "Your appeal is waiting to be read." in after and 'name="reason"' not in after

    again = post(member.browser(), member, "/appeal/quarantine", reason="Once more.")
    assert again.status_code == 303 and "error=" in again.headers["Location"]
    assert len(appeals_of(real_app, member)) == 1
    assert post(other.browser(), other, "/appeal/quarantine", reason="Not mine.").status_code == 404


def test_an_appeal_with_no_words_is_refused_and_the_one_appeal_is_still_there_to_make(real_app, make_member):
    member = make_member()
    hold_of(real_app, member)
    blank = post(member.browser(), member, "/appeal/quarantine", reason="   ")
    assert blank.status_code == 303 and "error=" in blank.headers["Location"] and not appeals_of(real_app, member)
    assert "Say why you are appealing." in text_of(get(member.browser(), blank.headers["Location"]))
    assert appeal(real_app, member)["status"] == "open"


def test_the_generic_appeal_route_is_not_a_second_way_in_and_names_nothing(real_app, make_member):
    member = make_member()
    entry = hold_of(real_app, member)
    browser = member.browser()
    assert get(browser, f"/log/{entry}/appeal").status_code == 404
    assert post(browser, member, f"/log/{entry}/appeal", reason="By the generic route.").status_code == 404
    assert not appeals_of(real_app, member)
    assert get(browser, f"/log/{entry}").status_code == 404                                         # the staff's entry is not the person's to open
    assert get(real_app[0].test_client(), f"/log/{entry}").status_code == 404


def test_ban_evasion_detection_and_an_alts_hold_name_no_rule_to_the_person(real_app, make_member):
    evader, main, alt = make_member(), make_member(), make_member()
    hold_of(real_app, evader, "evasion", detail="its browser had been used by a suspended account")
    page = check_card(text_of(get(evader.browser(), "/appeal/quarantine")))
    assert "Automatic check" in page and "Evading and manipulating the site" not in page and "suspended account" not in page
    hold_of(real_app, main, "link_malicious", detail="a banned domain", alts=[alt])
    own = check_card(text_of(get(main.browser(), "/appeal/quarantine")))
    assert "Automatic link check" in own and "Criminal activity and prohibited conduct" in own
    held_alt = check_card(text_of(get(alt.browser(), "/appeal/quarantine")))
    assert "Automatic check" in held_alt and "Automatic link check" not in held_alt and "Criminal activity" not in held_alt
    assert main.username not in held_alt and "alt of" not in held_alt
    assert post(alt.browser(), alt, "/appeal/quarantine", reason="I am not who you think.").status_code == 303        # an alt may appeal its own hold


def test_a_banned_account_has_its_own_appeal_and_a_limited_sign_in_is_shown_nothing(real_app, make_member):
    banned, limited = make_member(), make_member()
    hold_of(real_app, banned)
    hold_of(real_app, limited)
    db = banned.db_session()
    db.execute(text("UPDATE users SET is_banned = 1 WHERE id = :u"), {"u": banned.id})
    db.commit()
    assert get(banned.browser(), "/appeal/quarantine").status_code == 404

    browser = limited.browser()
    with browser.session_transaction() as session:
        session["qr_limited"] = True
        session["qr_expires_utc"] = int(time.time()) + 3600
    assert 'id="quarantine-check"' not in text_of(get(browser, "/appeal/quarantine"))
    assert post(browser, limited, "/appeal/quarantine", reason="A relayed code is writing this.").status_code != 303
    assert not appeals_of(real_app, limited)


# --- the staff queue ---------------------------------------------------------------------------------------------------------------------------------

def test_the_queue_lists_the_hold_with_the_check_and_the_appeal_and_only_admins_open_it(real_app, make_member):
    member, boss = make_member(), admin_of(make_member)
    entry = hold_of(real_app, member, "spam", detail="six similar posts in a minute")
    page = text_of(get(boss.browser(), "/admin/quarantine"))
    assert f'id="quarantine-{member.id}"' in page and f"@{member.username}" in page
    assert "Spam guard (similar posts or comments, a bad link): six similar posts in a minute" in page
    assert f'action="/admin/quarantine/{member.id}/release"' in page and f'href="/@{member.username}"' in page and f"/log/{entry}" in page
    assert f'name="entry" value="{entry}"' in card_of(page, member)                                      # the hold this form was drawn for
    assert "appeal waiting" not in card_of(page, member)                                                  # this account's own card: other tests leave appeals in the queue
    appeal(real_app, member)
    assert "appeal waiting" in card_of(text_of(get(boss.browser(), "/admin/quarantine")), member)
    assert "/admin/quarantine" in text_of(get(boss.browser(), "/admin"))

    assert get(make_member().browser(), "/admin/quarantine").status_code == 403
    assert get(real_app[0].test_client(), "/admin/quarantine").status_code in (302, 401, 403)
    stranger = make_member()
    assert post(stranger.browser(), stranger, f"/admin/quarantine/{member.id}/release", note="Not an admin.").status_code in (403, 404)
    assert state(real_app, member)["quarantined_utc"] > 0


def test_an_admin_releases_it_with_a_note_the_person_is_told_once_and_a_waiting_appeal_is_settled(real_app, make_member):
    member, boss = make_member(), admin_of(make_member)
    entry = hold_of(real_app, member)
    publish_lines(real_app)                                                                           # the hold's line, written by the cycle while the hold stands
    filed = appeal(real_app, member)
    browser = boss.browser()

    no_note = release(browser, boss, member, entry, note="  ")
    assert no_note.status_code == 303 and "Say%20why" in no_note.headers["Location"] and state(real_app, member)["quarantine_kind"] == 1

    lifted = release(browser, boss, member, entry)
    assert lifted.status_code == 303 and "Lifted" in lifted.headers["Location"]
    assert (state(real_app, member)["quarantined_utc"], state(real_app, member)["quarantine_kind"], state(real_app, member)["is_banned"]) == (0, 0, 0)

    (staff,) = entries(real_app, action="unquarantine_user", target_user_id=member.id)
    assert (staff["audience"], staff["actor_id"], staff["actor_role"], staff["linked_entry_id"]) == (2, boss.id, "admin", entry)
    assert "An ordinary post, not spam" in staff["reason"]
    assert entries(real_app, id=entry)[0]["staff_note"].startswith("spam |")                         # the original is never edited
    assert not entries(real_app, action="automatic_quarantine_lifted", linked_entry_id=staff["id"])   # the lifting's line comes later, from the cycle
    publish_lines(real_app)
    (public,) = entries(real_app, action="automatic_quarantine_lifted", linked_entry_id=staff["id"])
    assert (public["audience"], public["target_user_id"], public["board_id"], public["linked_entry_id"]) == (0, None, None, staff["id"])
    page = text_of(get(real_app[0].test_client(), f"/log/{public['id']}"))             # by id: a line dated to the hour sorts below the hour's other entries, off the first page
    assert "lifted a quarantine an automatic check had placed" in page and member.username not in page

    (settled,) = appeals_of(real_app, member)
    assert (settled["id"], settled["status"], settled["decided_by"], settled["decision_note"], settled["reversal_entry_id"]) == (
        filed["id"], "reversed", boss.id, "Lifted by an admin.", staff["id"])
    told = [body for body in notices_of(real_app, member.id) if "hold on your account was lifted" in body]
    assert len(told) == 1                                                                              # once: the release's own notice, not an appeal's besides
    assert not [body for body in notices_of(real_app, member.id) if "appeal of the hold" in body]
    assert get(member.browser(), "/appeal/quarantine").status_code == 404                              # not held any more
    assert "That%20account" in release(browser, boss, member, entry, note="Twice.").headers["Location"]
    assert "any%20more" in release(browser, boss, member, entry, note="Twice.").headers["Location"]       # lifted: an old form finds nothing to lift, whatever entry it names

    own = member.browser()
    answer = own.post("/api/vote/post/1/1", base_url="http://localhost", headers={"User-Agent": "ci-quarantine-queue"},
                      data={"formkey": member.formkey(own)}, environ_base={"REMOTE_ADDR": "10.1.2.3"})
    assert b"on hold" not in answer.data and b'id="held-notice"' not in get(own, "/").data


def test_a_release_lifts_only_the_hold_the_admin_was_shown_never_one_that_replaced_it(real_app, make_member):
    """The review: the form named no hold, so an admin with a stale page lifted whichever hold the account was on when the POST arrived, and settled its appeal as reversed."""
    member, first_admin, second_admin = make_member(), admin_of(make_member), admin_of(make_member)
    first = hold_of(real_app, member, "spam", detail="six similar posts in a minute")
    stale_page = text_of(get(second_admin.browser(), "/admin/quarantine"))
    assert f'name="entry" value="{first}"' in card_of(stale_page, member)

    # the first admin lifts it; another check holds the account again (a new hold, a new staff entry, its own appeal)
    assert "Lifted" in release(first_admin.browser(), first_admin, member, first).headers["Location"]
    second = hold_of(real_app, member, "link_malicious", detail="a banned domain")
    assert second != first and state(real_app, member)["quarantine_kind"] == 1
    filed = appeal(real_app, member)
    assert filed["entry_id"] == second
    lifts_before = len(entries(real_app, action="unquarantine_user", target_user_id=member.id))

    # the second admin presses Release on the row they were shown: it is not the hold the account is on now
    stale = release(second_admin.browser(), second_admin, member, first, note="I read the spam hold")
    assert stale.status_code == 303 and "changed%20after%20you%20opened" in stale.headers["Location"]
    assert "Look%20at%20it%20again" in stale.headers["Location"]
    assert state(real_app, member)["quarantine_kind"] == 1 and state(real_app, member)["quarantined_utc"] > 0
    assert len(entries(real_app, action="unquarantine_user", target_user_id=member.id)) == lifts_before        # nothing was lifted, nothing written
    assert appeals_of(real_app, member)[0]["status"] == "open"                                              # the appeal of the new hold was not settled
    assert len([body for body in notices_of(real_app, member.id) if "hold on your account was lifted" in body]) == 1                  # the first lift's, not a second for a refusal

    # a form that names no entry, or an entry 0, is a form that was not drawn for this hold
    for fields in ({}, {"entry": ""}, {"entry": "0"}, {"entry": "x"}):
        refused = post(second_admin.browser(), second_admin, f"/admin/quarantine/{member.id}/release", note="Without the hold it was drawn for.", **fields)
        assert "changed%20after%20you%20opened" in refused.headers["Location"], fields
    assert state(real_app, member)["quarantine_kind"] == 1 and appeals_of(real_app, member)[0]["status"] == "open"

    # the page drawn now names the new entry, and a release of THAT hold lifts it and settles its appeal
    fresh = card_of(text_of(get(second_admin.browser(), "/admin/quarantine")), member)
    assert f'name="entry" value="{second}"' in fresh and "Banned link: digitally malicious: a banned domain" in fresh and "appeal waiting" in fresh
    done = release(second_admin.browser(), second_admin, member, second, note="Looked at the second hold")
    assert "Lifted" in done.headers["Location"] and state(real_app, member)["quarantined_utc"] == 0
    (settled,) = appeals_of(real_app, member)
    assert (settled["status"], settled["decided_by"]) == ("reversed", second_admin.id)


def test_an_admin_cannot_release_themselves_and_a_hold_that_is_not_there_is_refused(real_app, make_member):
    boss, member = admin_of(make_member), make_member()
    assert post(boss.browser(), boss, f"/admin/quarantine/{boss.id}/release", note="Myself.").status_code == 404
    gone = post(boss.browser(), boss, f"/admin/quarantine/{member.id}/release", note="Nothing to lift.")
    assert gone.status_code == 303 and "any%20more" in gone.headers["Location"]
    assert not entries(real_app, action="unquarantine_user", target_user_id=member.id)


def test_an_admin_bans_the_usual_way_and_the_account_leaves_the_queue(real_app, make_member):
    member, boss = make_member(), admin_of(make_member)
    hold_of(real_app, member)
    assert f'id="quarantine-{member.id}"' in text_of(get(boss.browser(), "/admin/quarantine"))
    banned = post(boss.browser(), boss, f"/api/ban_user/{member.id}", days="0", reason="Posting spam links", message="That was spam.", rule="evasion")
    assert banned.status_code in (200, 302), text_of(banned)[:300]
    assert state(real_app, member)["is_banned"]
    assert f'id="quarantine-{member.id}"' not in text_of(get(boss.browser(), "/admin/quarantine"))
    assert entries(real_app, action="ban_user", target_user_id=member.id) and entries(real_app, action="ban_user", target_user_id=member.id)[-1]["actor_role"] == "admin"
    assert get(member.browser(), "/appeal/quarantine").status_code == 404                              # a ban has its own appeal


def test_the_queue_shows_an_alt_as_one_of_a_main_account(real_app, make_member):
    main, alt, boss = make_member(), make_member(), admin_of(make_member)
    entry = hold_of(real_app, main, "evasion", detail="its browser had been used by a suspended account", alts=[alt])
    page = text_of(get(boss.browser(), "/admin/quarantine"))
    assert f'id="quarantine-{main.id}"' in page and f'id="quarantine-{alt.id}"' in page
    assert f"Ban-evasion detection: an alt of @{main.username}" in page
    publish_lines(real_app)
    assert not entries(real_app, action="automatic_quarantine", linked_entry_id=entry)                  # evasion writes no public line, for the main account or its alt
    assert not entries(real_app, action="automatic_quarantine", linked_entry_id=entries(real_app, action="quarantine_user", target_user_id=alt.id)[0]["id"])


# --- an appeal is decided at /admin/appeals -----------------------------------------------------------------------------------------------------------

def test_an_appeal_reversed_releases_the_hold_with_linked_entries_and_tells_the_person_once(real_app, make_member):
    member, boss = make_member(), admin_of(make_member)
    entry = hold_of(real_app, member, "spam", detail="six similar posts in a minute")
    publish_lines(real_app)
    filed = appeal(real_app, member)
    queue = text_of(get(boss.browser(), "/admin/appeals"))
    assert f'action="/admin/appeals/{filed["id"]}"' in queue and f"@{member.username}" in queue
    assert "put" in queue and "on hold" in queue and "What the automatic check saw: Spam guard" in queue and "That was an ordinary post, not spam." in queue

    undecided = post(boss.browser(), boss, f"/admin/appeals/{filed['id']}", outcome="", note="")
    assert "Choose%20an%20outcome" in undecided.headers["Location"] and state(real_app, member)["quarantine_kind"] == 1
    decided = post(boss.browser(), boss, f"/admin/appeals/{filed['id']}", outcome="reversed", note="We looked again, it was fine")
    assert "Decided" in decided.headers["Location"]
    assert (state(real_app, member)["quarantined_utc"], state(real_app, member)["quarantine_kind"], state(real_app, member)["is_banned"]) == (0, 0, 0)

    (lift,) = entries(real_app, action="unquarantine_user", target_user_id=member.id)
    assert (lift["linked_entry_id"], lift["audience"], lift["actor_id"], lift["actor_role"], lift["staff_note"]) == (entry, 2, boss.id, "admin", "lifted after an appeal")
    publish_lines(real_app)
    assert len(entries(real_app, action="automatic_quarantine_lifted", linked_entry_id=lift["id"])) == 1
    (settled,) = appeals_of(real_app, member)
    assert (settled["status"], settled["decided_by"], settled["reversal_entry_id"]) == ("reversed", boss.id, lift["id"])
    mine = [body for body in notices_of(real_app, member.id) if "appeal of the hold" in body]
    assert len(mine) == 1 and "accepted" in mine[0] and "We looked again, it was fine" in mine[0]
    assert "/log/" not in mine[0]                                                                      # the staff's entry is not the person's to open
    assert not [body for body in notices_of(real_app, member.id) if "The hold on your account was lifted." in body]
    assert f'action="/admin/appeals/{filed["id"]}"' not in text_of(get(boss.browser(), "/admin/appeals"))
    assert "That%20appeal%20was%20already" in post(boss.browser(), boss, f"/admin/appeals/{filed['id']}", outcome="upheld", note="Too late").headers["Location"]


def test_an_appeal_upheld_leaves_the_hold_and_says_so_and_it_cannot_be_made_again(real_app, make_member):
    member, boss = make_member(), admin_of(make_member)
    hold_of(real_app, member)
    filed = appeal(real_app, member)
    assert "Decided" in post(boss.browser(), boss, f"/admin/appeals/{filed['id']}", outcome="upheld", note="It was a spam burst").headers["Location"]
    assert state(real_app, member)["quarantine_kind"] == 1 and state(real_app, member)["quarantined_utc"] > 0
    told = [body for body in notices_of(real_app, member.id) if "appeal of the hold" in body]
    assert len(told) == 1 and "the hold stands" in told[0] and "It was a spam burst" in told[0] and "/log/" not in told[0]
    page = text_of(get(member.browser(), "/appeal/quarantine"))
    assert "Your appeal was looked at, and the decision stands." in page and "They wrote: It was a spam burst" in page and 'name="reason"' not in page
    again = post(member.browser(), member, "/appeal/quarantine", reason="Once more.")
    assert again.status_code == 303 and "error=" in again.headers["Location"]
    assert len(appeals_of(real_app, member)) == 1


def test_an_appeal_dismissed_is_told_without_a_link(real_app, make_member):
    member, boss = make_member(), admin_of(make_member)
    hold_of(real_app, member)
    filed = appeal(real_app, member)
    assert "Decided" in post(boss.browser(), boss, f"/admin/appeals/{filed['id']}", outcome="dismissed", note="Not an appeal").headers["Location"]
    told = [body for body in notices_of(real_app, member.id) if "appeal of the hold" in body]
    assert len(told) == 1 and "was not taken further" in told[0] and "/log/" not in told[0]
    assert state(real_app, member)["quarantine_kind"] == 1


def test_the_person_who_appealed_cannot_decide_their_own_appeal_if_they_are_later_made_an_admin(real_app, make_member):
    member = make_member()
    hold_of(real_app, member)
    filed = appeal(real_app, member)
    db = member.db_session()
    db.execute(text("UPDATE users SET admin_level = 3 WHERE id = :u"), {"u": member.id})
    db.commit()
    assert f'action="/admin/appeals/{filed["id"]}"' not in text_of(get(member.browser(), "/admin/appeals"))
    assert post(member.browser(), member, f"/admin/appeals/{filed['id']}", outcome="reversed", note="Letting myself out").status_code == 404
    assert appeals_of(real_app, member)[0]["status"] == "open"


# --- a hold whose entry is legal-level ---------------------------------------------------------------------------------------------------------------

def test_a_link_about_minors_is_a_level_4_admins_in_the_queue_the_count_the_release_and_the_appeal(real_app, make_member):
    """The review: level 3 was shown a masked row that named the account (and its appeal) and counted it on /admin, which tells it which account tripped the check about minors."""
    member, level3, level4 = make_member(), admin_of(make_member, 3), admin_of(make_member, 4)
    before3, before4 = count_for(real_app, 3), count_for(real_app, 4)
    on_page3, on_page4 = count_on_admin_page(level3.browser()), count_on_admin_page(level4.browser())
    assert (on_page3, on_page4) == (before3, before4)
    entry = hold_of(real_app, member, "link_minors", detail="the domain's own reason")
    filed = appeal(real_app, member)

    # level 3: nothing of the account anywhere in the queue, and the number does not move
    hidden = text_of(get(level3.browser(), "/admin/quarantine"))
    assert f'id="quarantine-{member.id}"' not in hidden and member.username not in hidden
    assert "level 4 admin" not in hidden and "the domain's own reason" not in hidden and "about minors" not in hidden
    assert f'/admin/quarantine/{member.id}/release' not in hidden and f'href="/log/{entry}"' not in hidden
    assert count_for(real_app, 3) == before3 and count_on_admin_page(level3.browser()) == before3
    assert f"Held ({before3})" in hidden
    assert post(level3.browser(), level3, f"/admin/quarantine/{member.id}/release", entry=entry, note="Trying.").status_code == 404
    assert f'action="/admin/appeals/{filed["id"]}"' not in text_of(get(level3.browser(), "/admin/appeals"))
    assert post(level3.browser(), level3, f"/admin/appeals/{filed['id']}", outcome="reversed", note="Trying.").status_code == 404
    assert state(real_app, member)["quarantine_kind"] == 1

    # level 4: the row, the check, the appeal badge, the form for this entry, and one more in the number
    full = text_of(get(level4.browser(), "/admin/quarantine"))
    card = card_of(full, member)
    assert "Banned link: about minors" in card and "the domain&#39;s own reason" in card and "appeal waiting" in card
    assert f'action="/admin/quarantine/{member.id}/release"' in card and f'name="entry" value="{entry}"' in card
    assert count_for(real_app, 4) == before4 + 1 and count_on_admin_page(level4.browser()) == before4 + 1 and f"Held ({before4 + 1})" in full
    assert f'action="/admin/appeals/{filed["id"]}"' in text_of(get(level4.browser(), "/admin/appeals"))
    assert "Decided" in post(level4.browser(), level4, f"/admin/appeals/{filed['id']}", outcome="reversed", note="Looked again").headers["Location"]
    assert state(real_app, member)["quarantined_utc"] == 0 and count_for(real_app, 4) == before4
    (lift,) = entries(real_app, action="unquarantine_user", target_user_id=member.id)
    assert lift["audience"] == 3                                                                      # as private as the hold it lifts
