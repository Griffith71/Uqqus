"""A held account has no chat, on the real app and Postgres with the chat server faked (the owner, 2026-11-19: "hold should cut off chat entirely"; helpers/quarantine.py,
helpers/auto_quarantine.py `_cut_off_chat`, helpers/chat_tokens.py `_refusal`, routes/oidc_pages.py `_refusal`).

- the chat server's session (the token route) and every chat page and read refuse it, in the hold's own words (an automatic check's, or the signup guard's), and the chat
  server is never asked;
- a NEW hold signs the account's chat devices out of the chat server (a queue row `held`, worked after the commit or by the cycle), so an open tab or an outside app stops at
  once, and an alt's too;
- the token route reads the hold again under its lock (a request already past its wrappers when the hold landed);
- an admin and a bot account are never held, so they keep chat; lifting the hold gives it back;
- an outside chat app can't sign it in, and a code asked for just before the hold signs no app in;
- a call can't be joined, and no chat icon is drawn."""
import time

import pytest
from sqlalchemy import text

from .test_chat_signout_routes import get, mint, post, queue_rows, revoked, server, with_chat   # noqa: F401  (server is a fixture)
from .test_held_profile_pictures import hold_by_a_check, hold_by_the_signup_guard
from .test_oidc import SECRET, chat_ready, consent, oidc_on, params, pkce, token   # noqa: F401  (oidc_on is a fixture)

AUTOMATIC = "An automatic check put your account on hold"
SIGNUP = "waiting for a quick check"
NAVIGATE = {"Sec-Fetch-Mode": "navigate"}


def held_token(member, client):
    return post(client, "/api/chat/token", {"formkey": member.formkey(client)})


@pytest.mark.parametrize("how, words", [("check", AUTOMATIC), ("signup", SIGNUP)])
def test_a_held_account_is_given_no_chat_session_and_the_chat_server_is_never_asked(how, words, make_member, real_app, server):
    member = make_member()
    with_chat(member)
    if how == "check":
        hold_by_a_check(real_app, member)
    else:
        hold_by_the_signup_guard(member)
    client = member.browser()
    for path, form in (("/api/chat/token", {}), ("/api/chat/store_keys", {"device_ids": "UQ" + "A" * 8}), ("/api/chat/ready", {}), ("/api/chat/check_password", {}),
                       ("/api/chat/key", {"password_blob": "x" * 60}), ("/api/chat/key_reset/grant", {})):
        answer = post(client, path, dict(form, formkey=member.formkey(client)))
        assert answer.status_code == 403, path
        body = answer.get_json()
        assert body["held"] is True and words in body["error"], (path, body)
    assert server["calls"] == [], "the chat server is never asked for a held account"
    assert queue_rows(member) == [] or all(row.reason == "held" for row in queue_rows(member)), "nothing but the hold's own sign-out"


@pytest.mark.parametrize("path", ["/api/chat/key", "/api/chat/conversations", "/api/chat/share_targets", "/api/chat/search?q=ann", "/api/chat/people",
                                  "/api/broadcasts/popular", "/api/broadcasts/following", "/api/broadcasts/1", "/api/guilds/general/channels", "/api/guilds/channels/1/pins",
                                  "/api/guilds/channels/1/search?q=ann", "/api/guilds/channels/1/senders?ids=1", "/api/chat/recovery_key", "/api/calls/status", "/api/calls/history",
                                  "/api/chat/groups/1"])
def test_the_chats_reads_refuse_a_held_account(path, make_member, real_app):
    """Every path is a real route (a path that isn't would be a 404 and pass for nothing): the wrapper answers before the route looks anything up."""
    member = make_member()
    with_chat(member)
    hold_by_a_check(real_app, member)
    answer = get(member.browser(), path)
    assert answer.status_code == 403, (path, answer.status_code)
    assert answer.get_json()["held"] is True
    # the same path answers a member who isn't held with something else (a 404 for an id nobody has, an empty list, the data): the refusal is the hold's
    free = make_member()
    with_chat(free)
    other = get(free.browser(), path)
    assert not (other.status_code == 403 and (other.get_json(silent=True) or {}).get("held")), path


def test_the_chat_page_and_the_call_window_say_the_account_is_on_hold(make_member, real_app):
    member = make_member()
    with_chat(member)
    hold_by_a_check(real_app, member)
    client = member.browser()
    for path in ("/chat", "/call/%21room%3Aruqqus.local"):
        answer = client.get(path, base_url="http://localhost", headers={"User-Agent": "ci-held-chat", **NAVIGATE})
        assert answer.status_code == 403, path
        text_ = answer.get_data(as_text=True)
        assert "Your account is on hold" in text_ and "/appeal/quarantine" in text_, path


def test_a_call_heartbeat_is_refused_so_a_held_account_falls_out_of_the_roster(make_member, real_app):
    member = make_member()
    with_chat(member)
    hold_by_a_check(real_app, member)
    client = member.browser()
    answer = post(client, "/api/calls/heartbeat", {"formkey": member.formkey(client), "room_id": "!room:ruqqus.local", "device_id": "UQAAAAAAAA"})
    assert answer.status_code == 403 and answer.get_json()["held"] is True


def test_a_call_cannot_be_joined(make_member, real_app):
    member = make_member()
    with_chat(member)
    hold_by_a_check(real_app, member)
    client = member.browser()
    answer = post(client, "/api/calls/join", {"formkey": member.formkey(client), "room_id": "!room:ruqqus.local", "device_id": "UQAAAAAAAA"})
    assert answer.status_code == 403 and answer.get_json()["held"] is True


def test_a_new_hold_signs_the_accounts_chat_devices_out_and_an_alt_s_too(make_member, real_app, server):
    from ruqqus.classes import User
    from ruqqus.helpers import auto_quarantine, chat_signouts
    app, db_session = real_app
    member, alt = make_member(), make_member()
    mxid, alt_mxid = with_chat(member), with_chat(alt)
    chat, alt_chat = member.browser(), alt.browser()
    device, alt_device = mint(member, chat), mint(alt, alt_chat)
    assert server["devices"][mxid] == {device: "Laba chat"} and server["devices"][alt_mxid] == {alt_device: "Laba chat"}

    db = db_session()
    db.rollback()
    assert auto_quarantine.hold(db, db.get(User, member.id), "spam", detail="six similar posts in a minute", alts=[db.get(User, alt.id)], notify=False)
    db.commit()
    for account in (member, alt):
        rows = queue_rows(account)
        assert [(row.scope, row.reason) for row in rows] == [("all", "held")], "queued in the hold's own transaction"
    for account in (member, alt):
        chat_signouts.run_for_user(app, account.id)             # what the request's after-commit (or the cycle) does
    assert server["devices"][mxid] == {} and server["devices"][alt_mxid] == {}, "signed out of the chat server"
    assert revoked(member) == {device: "held"} and revoked(alt) == {alt_device: "held"}

    # and nothing more is handed out, here or by a request already past its wrappers (the mint reads the hold under its lock)
    assert held_token(member, chat).status_code == 403 and held_token(alt, alt_chat).status_code == 403


def test_the_mint_reads_the_hold_again_under_its_lock(make_member, real_app, server, monkeypatch):
    from ruqqus.helpers import chat_signouts
    member = make_member()
    with_chat(member)
    client = member.browser()
    real_lock = chat_signouts.lock_account

    def held_meanwhile(db, user_id):                         # the hold lands while the request waits for the lock
        hold_by_the_signup_guard(member)
        return real_lock(db, user_id)

    monkeypatch.setattr(chat_signouts, "lock_account", held_meanwhile)
    answer = held_token(member, client)
    assert answer.status_code == 403 and answer.get_json()["held"] is True
    assert member.db_session().execute(text("SELECT count(*) FROM chat_site_devices WHERE user_id = :u"), {"u": member.id}).scalar() == 0
    assert not any(call[0] == "login" for call in server["calls"])


def test_an_admin_and_a_bot_are_never_held_so_they_keep_chat(make_member, real_app, server):
    for column in ("admin_level = 3", "is_bot = true"):
        member = make_member()
        with_chat(member)
        db = member.db_session()
        db.execute(text(f"UPDATE users SET quarantined_utc = :now, {column} WHERE id = :u"), {"now": int(time.time()) - 5, "u": member.id})
        db.commit()
        client = member.browser()
        assert held_token(member, client).status_code == 200, column


def test_lifting_the_hold_gives_chat_back(make_member, real_app, server):
    from ruqqus.helpers import auto_quarantine
    member = make_member()
    with_chat(member)
    hold_by_a_check(real_app, member)
    client = member.browser()
    assert held_token(member, client).status_code == 403
    _, db_session = real_app
    db = db_session()
    assert auto_quarantine.release(db, member.id, admin_id=None, notify=False)
    db.commit()
    answer = held_token(member, client)
    assert answer.status_code == 200 and answer.get_json()["device_id"].startswith("UQ")


def test_no_chat_icon_is_drawn_for_a_held_account(make_member, real_app):
    member = make_member()
    client = member.browser()
    page = get(client, "/").get_data(as_text=True)
    assert 'data-side-panel="chat"' in page, "a member who isn't held has it"
    hold_by_a_check(real_app, member)
    page = get(client, "/").get_data(as_text=True)
    assert 'data-side-panel="chat"' not in page


def test_an_outside_chat_app_cannot_sign_a_held_account_in(oidc_on, make_member, real_app):
    app, _ = oidc_on
    member = make_member()
    chat_ready(member)
    client = member.browser()
    verifier, challenge = pkce()
    # a code asked for just before the hold ...
    answer = consent(member, client, app, challenge)
    assert answer.status_code == 302
    from urllib.parse import parse_qs, urlsplit
    code = parse_qs(urlsplit(answer.headers["Location"]).query)["code"][0]
    hold_by_a_check(real_app, member)
    # ... signs no app in, and a new one is refused with the app's plain refusal, which says nothing of why
    assert token(app.test_client(), app, code, verifier).get_json() == {"error": "invalid_grant"}
    refused = consent(member, client, app, challenge)
    assert refused.status_code == 403 and AUTOMATIC not in refused.get_data(as_text=True)


def test_a_held_account_is_not_pushed_chat_messages_or_rung_for_a_call_and_nothing_tells_the_sender(make_member, real_app, monkeypatch):
    """`push_send.prepare` leaves a held recipient out (a subscription it made before the hold stays on file, and delivers nothing), and a call neither rings nor tells it."""
    from ruqqus.__main__ import app
    from ruqqus.classes import User
    from ruqqus.helpers import push_rules, push_send, web_push
    _, db_session = real_app
    sender, held, free = make_member(), make_member(), make_member()
    db = db_session()
    monkeypatch.setattr(web_push, "configured", lambda: ("private", "public"))
    for member in (held, free):
        db.execute(text("INSERT INTO push_subscriptions (user_id, endpoint, p256dh, auth, created_utc) VALUES (:u, :e, 'k', 'a', :now) ON CONFLICT DO NOTHING"),
                   {"u": member.id, "e": f"https://fcm.googleapis.com/fcm/send/{member.id}", "now": int(time.time())})
    db.commit()
    hold_by_a_check(real_app, held)
    with app.test_request_context("/"):
        jobs = push_send.prepare(db, [held.id, free.id], push_rules.MESSAGE, db.get(User, sender.id), "!room:ruqqus.local")
    assert [sub["endpoint"] for sub, _payload in jobs] == [f"https://fcm.googleapis.com/fcm/send/{free.id}"], "only the member who isn't held is pushed to"
    with app.test_request_context("/"):
        everyone = push_send.prepare(db, [free.id], push_rules.MESSAGE, db.get(User, sender.id), "!room:ruqqus.local")
    assert len(everyone) == 1, "the same call for a member who isn't held gives a job: the hold is what left the other out"


def test_the_held_chat_backstop_queues_a_sign_out_for_a_hold_that_never_went_through_hold(make_member, real_app, server):
    """The signup guard's hold (kind 0) and an account that was already held when this shipped are written without `auto_quarantine.hold`: the cycle's backstop signs their
    live chat devices out once, never an admin's or a free account's, and the next cycle finds nothing."""
    from ruqqus.helpers import auto_quarantine, chat_signouts
    app, db_session = real_app
    signup_held, old_held, admin, free = make_member(), make_member(), make_member(), make_member()
    for member in (signup_held, old_held, admin, free):
        with_chat(member)
    devices = {member.id: mint(member, member.browser()) for member in (signup_held, old_held, admin, free)}
    hold_by_the_signup_guard(signup_held)
    db = db_session()
    db.execute(text("UPDATE users SET quarantined_utc = :now, quarantine_kind = 1 WHERE id = :u"), {"now": int(time.time()) - 5, "u": old_held.id})
    db.execute(text("UPDATE users SET quarantined_utc = :now, admin_level = 3 WHERE id = :u"), {"now": int(time.time()) - 5, "u": admin.id})
    db.commit()
    done = auto_quarantine.cut_off_held_chats(db)
    db.commit()
    assert done >= 2
    for member in (signup_held, old_held):
        assert [(row.scope, row.reason) for row in queue_rows(member)] == [("all", "held")], member.id
        chat_signouts.run_for_user(app, member.id)
        assert revoked(member) == {devices[member.id]: "held"}
    for member in (admin, free):
        assert queue_rows(member) == [] and revoked(member) == {}, "an admin and a free account keep chat"
    db = db_session()
    db.rollback()
    assert auto_quarantine.cut_off_held_chats(db) is not None
    db.commit()
    for member in (signup_held, old_held):
        assert [(row.scope, row.reason) for row in queue_rows(member)] == [("all", "held")], "the devices were revoked: the next cycle queues nothing more"
