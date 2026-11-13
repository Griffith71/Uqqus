"""Every credential event and the other chat sign-outs on the real app and a real Postgres, with the chat server faked
(helpers/chat_signouts.py, helpers/account_sessions.py `revoke`, helpers/chat_tokens.py): each writes the right queue row and revokes the
right site rows in its own transaction, the chat server is called only once that is committed, decision 2 spares exactly the devices of
the sign-in making the change, the events that spare nothing spare nothing, a chat server that is down leaves the row to be retried,
the interim of build steps 2 and 3 is over (every event reaches the site's own browsers), and a suspension changes nothing (the
owner's decision 3)."""
import secrets
import time

import gevent
import pyotp
import pytest
from sqlalchemy import text

from .test_child_accounts import made, post as child_post, switch  # noqa: F401  (made is a fixture)

BASE = "http://localhost"
HEADERS = {"User-Agent": "ci-signouts"}


def addr():
    return f"10.{secrets.randbelow(250)}.{secrets.randbelow(250)}.{secrets.randbelow(250) + 1}"


def get(client, path):
    return client.get(path, base_url=BASE, headers=HEADERS, environ_base={"REMOTE_ADDR": addr()})


def post(client, path, data, headers=None):
    return client.post(path, base_url=BASE, headers={**HEADERS, **(headers or {})}, data=data, environ_base={"REMOTE_ADDR": addr()})


def signed_in(client):
    return get(client, "/settings/security").status_code == 200


@pytest.fixture
def server(monkeypatch, real_app, pg_engine):
    """A chat server: `devices` is {mxid: {device_id: name}}. Each delete notes whether a sign-out row of that account was already
    committed (read through another connection): the chat server must only ever hear once the request's changes are durable."""
    import ruqqus.helpers.matrix_client as matrix
    state = {"devices": {}, "calls": [], "down": False, "committed": [], "refresh": {}}

    def committed(mxid):
        with pg_engine.connect() as conn:
            return conn.execute(text("SELECT count(*) FROM chat_device_signouts WHERE mxid = :m"), {"m": mxid}).scalar() > 0

    def list_devices(mxid):
        state["calls"].append(("list", mxid, None))
        if state["down"]:
            raise matrix.MatrixError("down")
        return [{"device_id": d, "name": n, "last_seen": None} for d, n in state["devices"].get(mxid, {}).items()]

    def delete_device(mxid, device_id):
        state["calls"].append(("delete", mxid, device_id))
        if state["down"]:
            raise matrix.MatrixError("down")
        state["committed"].append(committed(mxid))
        state["devices"].get(mxid, {}).pop(device_id, None)

    def login_as(mxid, device_id=None):                  # always with a refresh token (build step 5)
        state["calls"].append(("login", mxid, device_id))
        if state["down"]:
            raise matrix.MatrixError("down")
        state["devices"].setdefault(mxid, {})[device_id] = "Laba chat"
        return dict({"user_id": mxid, "device_id": device_id}, **pair(mxid, device_id))

    def pair(mxid, device_id):
        rt = "rt-" + secrets.token_hex(8)
        state["refresh"][rt] = (mxid, device_id)
        return {"access_token": "syt_" + secrets.token_hex(8), "refresh_token": rt, "expires_in_ms": 3600000}

    def refresh(refresh_token):                         # Synapse's /refresh: only while the device is there
        state["calls"].append(("refresh", None, None))
        if state["down"]:
            raise matrix.MatrixError("down")
        mxid, device_id = state["refresh"].pop(refresh_token, (None, None))
        if device_id is None or device_id not in state["devices"].get(mxid, {}):
            raise matrix.RefreshGone("gone")
        return pair(mxid, device_id)

    monkeypatch.setattr(matrix, "list_devices", list_devices)
    monkeypatch.setattr(matrix, "refresh", refresh)
    monkeypatch.setattr(matrix, "delete_device", delete_device)
    monkeypatch.setattr(matrix, "login_as", login_as)
    monkeypatch.setattr(matrix, "set_display_name", lambda *a, **k: None)
    return state


def with_chat(member_or_kid):
    db = member_or_kid.db_session()
    mxid = f"@ruqqus_{member_or_kid.id}:ruqqus.local"
    db.execute(text("INSERT INTO chat_identities (user_id, matrix_user_id, key_salt, key_blob_password, ready_utc) VALUES (:u, :m, 's', :b, :t) "
                    "ON CONFLICT DO NOTHING"), {"u": member_or_kid.id, "m": mxid, "b": "x" * 60, "t": int(time.time())})
    db.commit()
    return mxid


def mint(member, client, kind=None, device_id=None):
    form = {"formkey": member.formkey(client)}
    if kind:
        form["kind"] = kind
    if device_id:
        form["device_id"] = device_id
    answer = post(client, "/api/chat/token", form)
    assert answer.status_code == 200, answer.get_data(as_text=True)
    return answer.get_json()["device_id"]


def queue_rows(member_or_id):
    from ruqqus.__main__ import db_session
    db = db_session()
    db.rollback()
    uid = getattr(member_or_id, "id", member_or_id)
    return db.execute(text("SELECT scope, spared, reason, done_utc, device_ids, attempts FROM chat_device_signouts WHERE user_id = :u ORDER BY id"),
                      {"u": uid}).all()


def revoked(member_or_id):
    from ruqqus.__main__ import db_session
    db = db_session()
    db.rollback()
    uid = getattr(member_or_id, "id", member_or_id)
    return {d: r for d, r in db.execute(text("SELECT device_id, revoked_reason FROM chat_site_devices WHERE user_id = :u AND revoked_utc > 0"),
                                        {"u": uid})}


def settle(member_or_id):
    """The sign-outs run in greenlets after the request's commit: wait for them (a pending one that failed stays pending)."""
    for _ in range(40):
        gevent.sleep(0.05)
        if all(row[3] or row[5] for row in queue_rows(member_or_id)):
            return


def set_user(member, **values):
    db = member.db_session()
    db.execute(text("UPDATE users SET " + ", ".join(f"{k} = :{k}" for k in values) + " WHERE id = :u"), dict(values, u=member.id))
    db.commit()


def setup(make_member, server, app_device=True):
    """A member with chat on browser A (a chat and a call device) and browser B (a chat device), and an outside app."""
    member = make_member()
    mxid = with_chat(member)
    a, b = member.browser(), member.browser()
    devices = {"a_chat": mint(member, a), "a_call": mint(member, a, kind="call"), "b_chat": mint(member, b)}
    if app_device:
        server["devices"][mxid]["ELEMENTAPP1"] = "Element Web"
    return member, mxid, a, b, devices


# --- decision 2: these spare exactly the sign-in making the change -----------------------------------------------------------

def _password(member, a):
    return post(a, "/settings/security", {"old_password": member.password, "new_password": "Another-Passw0rd", "cnf_password": "Another-Passw0rd",
                                          "formkey": member.formkey(a)})


def _others(member, a):
    return post(a, "/settings/log_out_all_others", {"password": member.password, "formkey": member.formkey(a)})


def _mfa_on(member, a):
    secret = pyotp.random_base32()
    return post(a, "/settings/security", {"password": member.password, "2fa_secret": secret, "2fa_token": pyotp.TOTP(secret).now(),
                                          "formkey": member.formkey(a)})


def _mfa_off(member, a):
    secret = pyotp.random_base32()
    set_user(member, mfa_secret=secret)
    return post(a, "/settings/security", {"password": member.password, "2fa_remove": pyotp.TOTP(secret).at(time.time() + 30),
                                          "formkey": member.formkey(a)})


@pytest.mark.parametrize("event, reason", [(_password, "password"), (_others, "others"), (_mfa_on, "mfa"), (_mfa_off, "mfa")])
def test_these_events_spare_exactly_this_sign_ins_devices(make_member, server, event, reason):
    member, mxid, a, b, devices = setup(make_member, server)
    answer = event(member, a)
    assert answer.status_code in (200, 302), answer.status_code
    settle(member)
    [(scope, spared, why, done, _ids, _tries)] = queue_rows(member)
    assert (scope, why) == ("all", reason) and done > 0
    assert set(spared.split(",")) == {devices["a_chat"], devices["a_call"]}
    assert revoked(member) == {devices["b_chat"]: reason}
    assert set(server["devices"][mxid]) == {devices["a_chat"], devices["a_call"]}, "B's device and the app are gone"
    assert server["committed"] and all(server["committed"]), "the chat server heard only after the commit"
    assert signed_in(a) and not signed_in(b)


# --- these spare nothing ------------------------------------------------------------------------------------------------------

def test_a_reset_by_email_spares_nothing(make_member, server):
    from ruqqus.helpers.security import generate_hash
    member, mxid, a, b, devices = setup(make_member, server)
    now = int(time.time())
    token = generate_hash(f"{member.id}+{now}+reset+{member.user().login_nonce}")
    form = {"user_id": member.id, "time": now, "token": token, "password": "Reset-Passw0rd1", "confirm_password": "Reset-Passw0rd1"}
    assert post(member.app.test_client(), "/reset", form).status_code == 200
    settle(member)
    [(scope, spared, why, done, _ids, _tries)] = queue_rows(member)
    assert (scope, spared, why) == ("all", None, "reset") and done > 0
    assert set(revoked(member)) == set(devices.values()) and server["devices"][mxid] == {}
    assert server["committed"] and all(server["committed"]), "the chat server heard only after the commit"
    assert not signed_in(a) and not signed_in(b)


def test_the_recovery_code_spares_nothing(make_member, server):
    member, mxid, a, b, devices = setup(make_member, server)
    set_user(member, mfa_secret=pyotp.random_base32())
    code = member.user().mfa_removal_code.lower().replace(" ", "")
    visitor = member.app.test_client()
    environ = {"REMOTE_ADDR": addr()}
    first = visitor.post("/login", base_url=BASE, headers=HEADERS, environ_base=environ, data={"username": member.username, "password": member.password})
    page = first.get_data(as_text=True)
    import re
    form = {"username": member.username, "2fa_token": code, "redirect": "/", "time": re.search(r'name="time" value="(\d+)"', page).group(1),
            "hash": re.search(r'name="hash" value="([^"]+)"', page).group(1)}
    assert visitor.post("/login", base_url=BASE, headers=HEADERS, environ_base=environ, data=form).status_code == 302
    settle(member)
    [(scope, spared, why, done, _ids, _tries)] = queue_rows(member)
    assert (scope, spared, why) == ("all", None, "mfa_recovery")
    assert set(revoked(member)) == set(devices.values()) and server["devices"][mxid] == {}
    assert server["committed"] and all(server["committed"]), "the chat server heard only after the commit"
    assert signed_in(visitor) and not signed_in(a)


def test_a_parents_new_password_spares_nothing_and_leaves_the_parent_signed_in(made, server):
    mum, kid = made()
    mxid = with_chat(kid)
    db = kid.db_session()
    for device_id in ("UQKIDCHAT000", "UQKIDCALL000"):
        db.execute(text("INSERT INTO chat_site_devices (user_id, device_id, kind, created_ms, last_used_utc) VALUES (:u, :d, 'chat', 1, 1)"),
                   {"u": kid.id, "d": device_id})
    db.commit()
    server["devices"][mxid] = {"UQKIDCHAT000": "Laba chat", "UQKIDCALL000": "Laba chat", "APP0000001": "FluffyChat"}
    mine = mum.browser()
    done = child_post(mine, f"/settings/children/{kid.username}/password", formkey=mum.formkey(mine), parent_password=mum.password,
                      new_password="Another-Pass-2", new_password_confirm="Another-Pass-2")
    assert "msg=" in done.headers["Location"]
    settle(kid)
    assert [r[:3] for r in queue_rows(kid)] == [("all", None, "child_password")]
    assert server["devices"][mxid] == {} and set(revoked(kid)) == {"UQKIDCHAT000", "UQKIDCALL000"}
    assert server["committed"] and all(server["committed"]), "the chat server heard only after the commit"
    assert signed_in(mine) and queue_rows(mum) == [], "the parent's own sign-in and chat are untouched"


# --- the other sign-outs ------------------------------------------------------------------------------------------------------

def test_a_childs_chat_switched_off_and_stopping_managing_sign_out_every_device(made, server):
    """Every device goes: a child account has no chat."""
    for action in ("chat_off", "stop"):
        mum, kid = made()
        mxid = with_chat(kid)
        db = kid.db_session()
        db.execute(text("INSERT INTO chat_site_devices (user_id, device_id, kind, created_ms, last_used_utc) VALUES (:u, 'UQKID0000000', 'chat', 1, 1)"),
                   {"u": kid.id})
        db.commit()
        server["devices"][mxid] = {"UQKID0000000": "Laba chat", "APP0000001": "FluffyChat"}
        if action == "chat_off":
            assert switch(mum, kid, "chat", False).status_code == 200
            reason = "child_chat_off"
        else:
            mine = mum.browser()
            child_post(mine, f"/settings/children/{kid.username}/stop", formkey=mum.formkey(mine), parent_password=mum.password)
            reason = "child_stopped"
        settle(kid)
        assert [r[:3] for r in queue_rows(kid)] == [("all", None, reason)], action
        assert server["devices"][mxid] == {} and revoked(kid) == {"UQKID0000000": reason}, action
        assert server["committed"] and all(server["committed"]), f"{action}: the chat server heard only after the commit"


def test_an_account_newly_child_locked_is_signed_out_and_one_already_answered_is_not(make_member, server):
    member, mxid, a, b, devices = setup(make_member, server)
    set_user(member, age_declared=None)
    answer = post(a, "/settings/age_declaration", {"age_declaration": "1", "formkey": member.formkey(a)}, headers={"CF-IPCountry": "GB"})
    assert answer.status_code == 303
    assert member.user().age_declared == 2, "18 or over from the UK is stored as the locked answer"
    settle(member)
    [(scope, spared, why, done, _ids, _tries)] = queue_rows(member)
    assert (scope, spared, why) == ("all", None, "child_locked"), "every device: a child account has no chat"
    assert revoked(member) == {d: "child_locked" for d in devices.values()} and server["devices"][mxid] == {}

    adult = make_member()
    with_chat(adult)
    set_user(adult, age_declared=None)
    here = adult.browser()
    assert post(here, "/settings/age_declaration", {"age_declaration": "1", "formkey": adult.formkey(here)}, headers={"CF-IPCountry": "US"}).status_code == 303
    assert queue_rows(adult) == []


def test_an_admin_marking_an_account_a_child_signs_it_out_once(make_member, server):
    """admin_age_declaration: a newly locked account's every device goes (a child account has no chat); marking it again, or taking the
    lock off, signs nothing out."""
    member, mxid, a, b, devices = setup(make_member, server)
    set_user(member, age_declared=1, age_declared_utc=5)
    boss = make_member()
    set_user(boss, admin_level=3, age_declared=1, age_declared_utc=5)
    desk = boss.browser()

    def mark(answer):
        return post(desk, "/admin/age_declaration", {"formkey": boss.formkey(desk), "username": member.username, "answer": answer,
                                                     "reason": "ci"})
    assert mark("2").status_code == 302
    settle(member)
    [(scope, spared, why, done, _ids, _tries)] = queue_rows(member)
    assert (scope, spared, why) == ("all", None, "child_locked") and done > 0
    assert set(revoked(member)) == set(devices.values()) and server["devices"][mxid] == {}
    assert server["committed"] and all(server["committed"]), "the chat server heard only after the commit"
    assert mark("2").status_code == 302 and mark("1").status_code == 302
    assert len(queue_rows(member)) == 1, "already locked, then unlocked: nothing more to sign out"


def test_plain_log_out_signs_no_device_out(make_member, server):
    """Decision 1: Log out ends this browser's sign-in on the site and nothing of chat; the same account signing back in takes its device
    up again."""
    member, mxid, a, b, devices = setup(make_member, server)
    assert post(a, "/logout", {"formkey": member.formkey(a)}).status_code == 302
    settle(member)
    assert not signed_in(a) and signed_in(b)
    assert queue_rows(member) == [] and revoked(member) == {}
    assert [c for c in server["calls"] if c[0] == "delete"] == [] and set(devices.values()) <= set(server["devices"][mxid])


def _dies_after_queueing(monkeypatch):
    """The worker dies (a deploy, a dropped connection) right after the sign-out is written: whatever the route had committed by then stays,
    and the request's own transaction goes."""
    from ruqqus.helpers import chat_signouts
    real = chat_signouts.queue
    seen = []

    def queue(*a, **k):
        seen.append(real(*a, **k))
        raise RuntimeError("the worker died here")
    monkeypatch.setattr(chat_signouts, "queue", queue)
    return seen


def _answer(call):
    try:
        return call().status_code
    except RuntimeError:            # a test client that propagates the error
        return 500


@pytest.mark.parametrize("action", ["chat_off", "stop", "age"])
def test_a_change_and_its_chat_sign_out_are_committed_together_or_not_at_all(made, make_member, server, monkeypatch, action):
    """A child's chat switched off, a parent stopping managing and an account newly child-locked write their sign-out in their own transaction:
    a sign-out written in a second one after the change committed is lost for good when the worker dies in between (nothing queues it again,
    and an outside app's token never expires)."""
    if action == "age":
        member = make_member()
        with_chat(member)
        set_user(member, age_declared=None)
        here = member.browser()
        seen = _dies_after_queueing(monkeypatch)
        _answer(lambda: post(here, "/settings/age_declaration", {"age_declaration": "1", "formkey": member.formkey(here)},
                             headers={"CF-IPCountry": "GB"}))
        assert seen, "the sign-out was asked for"
        db = member.db_session()
        db.rollback()                   # what is committed, not what the request's own session still holds
        changed = db.execute(text("SELECT age_declared FROM users WHERE id = :u"), {"u": member.id}).scalar() is not None
        account = member
    else:
        mum, kid = made()
        with_chat(kid)
        assert switch(mum, kid, "chat", True).status_code == 200
        seen = _dies_after_queueing(monkeypatch)
        if action == "chat_off":
            _answer(lambda: switch(mum, kid, "chat", False))
            db = kid.db_session()
            db.rollback()
            changed = not db.execute(text("SELECT chat FROM child_controls WHERE child_id = :c"), {"c": kid.id}).scalar()
        else:
            mine = mum.browser()
            _answer(lambda: child_post(mine, f"/settings/children/{kid.username}/stop", formkey=mum.formkey(mine), parent_password=mum.password))
            db = kid.db_session()
            db.rollback()
            changed = db.execute(text("SELECT age_lock FROM users WHERE id = :u"), {"u": kid.id}).scalar() != 2
        assert seen, "the sign-out was asked for"
        account = kid
    queued = bool(queue_rows(account))
    assert changed == queued, f"{action}: the change {'committed' if changed else 'rolled back'} and its sign-out {'kept' if queued else 'lost'}"
    assert not changed, "the request died before its one commit: nothing of it is kept"


def test_deleting_an_account_signs_out_every_device_and_forgets_the_rows_but_not_the_queue(make_member, server):
    member, mxid, a, b, devices = setup(make_member, server)
    page = post(a, "/settings/delete_account", {"password": member.password, "formkey": member.formkey(a)})
    assert page.status_code == 200
    assert f'forgetChatDevices("{mxid}", [])' in page.get_data(as_text=True)
    settle(member)
    [(scope, spared, why, done, _ids, _tries)] = queue_rows(member)
    assert (scope, spared, why) == ("all", None, "deleted") and done > 0
    assert server["devices"][mxid] == {}
    assert server["committed"] and all(server["committed"]), "the chat server heard only after the commit"
    from ruqqus.__main__ import db_session
    db = db_session()
    db.rollback()
    assert db.execute(text("SELECT count(*) FROM chat_site_devices WHERE user_id = :u"), {"u": member.id}).scalar() == 0


def test_signing_one_device_out_is_at_once_and_queued_when_the_chat_server_is_down(make_member, server):
    member, mxid, a, b, devices = setup(make_member, server)
    form = {"formkey": member.formkey(a), "device_id": devices["b_chat"]}
    assert post(a, "/api/chat/devices/sign_out", form).status_code == 204
    assert devices["b_chat"] not in server["devices"][mxid] and revoked(member) == {devices["b_chat"]: "signed_out_here"}

    listed = get(a, "/api/chat/devices").get_json()["devices"]
    assert {d["device_id"]: (d["site"], d["here"]) for d in listed} == {
        devices["a_chat"]: (True, True), devices["a_call"]: (True, True), "ELEMENTAPP1": (False, False)}

    server["down"] = True
    queued = post(a, "/api/chat/devices/sign_out", {"formkey": member.formkey(a), "device_id": devices["a_call"]})
    assert queued.status_code == 202 and queued.get_json()["pending"] is True
    assert revoked(member)[devices["a_call"]] == "signed_out_here"
    stranger = post(a, "/api/chat/devices/sign_out", {"formkey": member.formkey(a), "device_id": "ELEMENTAPP1"})
    assert stranger.status_code == 503, "an app's device can't be told from a stranger's without the server"
    server["down"] = False
    from ruqqus.__main__ import db_session
    from ruqqus.helpers import chat_signouts
    import ruqqus.helpers.matrix_client as matrix
    db = db_session()
    db.execute(text("UPDATE chat_device_signouts SET next_try_utc = 0 WHERE user_id = :u"), {"u": member.id})
    db.commit()
    chat_signouts.run_due(db, matrix)
    assert devices["a_call"] not in server["devices"][mxid]


def test_a_chat_server_that_is_down_leaves_the_row_and_the_revoked_device_is_never_served(make_member, server):
    member, mxid, a, b, devices = setup(make_member, server)
    server["down"] = True
    assert _password(member, a).status_code == 302
    settle(member)
    [(scope, _spared, why, done, _ids, tries)] = queue_rows(member)
    assert done == 0 and tries >= 1 and revoked(member) == {devices["b_chat"]: "password"}
    # browser B, signed in again, names its revoked device: never re-tokened, whatever the chat server says
    b2 = member.browser()
    refused = post(b2, "/api/chat/token", {"formkey": member.formkey(b2), "device_id": devices["b_chat"]})
    assert refused.status_code == 503 and refused.get_json()["retry"] is True
    server["down"] = False
    fresh = mint(member, b2, device_id=devices["b_chat"])
    assert fresh != devices["b_chat"]
    assert [c for c in server["calls"] if c[0] == "login" and c[2] == devices["b_chat"]] == [("login", mxid, devices["b_chat"])], "only the mint that made it"
    from ruqqus.__main__ import db_session
    from ruqqus.helpers import chat_signouts
    import ruqqus.helpers.matrix_client as matrix
    db = db_session()
    db.execute(text("UPDATE chat_device_signouts SET next_try_utc = 0 WHERE user_id = :u"), {"u": member.id})
    db.commit()
    # the password's sign-out is done; the mint that failed while the server was down waits for its device (Synapse may finish a
    # /login the site gave up on: chat_signouts.MINT_GRACE_SECONDS), then is done once the wait is over and the device never came
    assert chat_signouts.run_due(db, matrix) == {"done": 1, "waiting": 1}
    assert devices["b_chat"] not in server["devices"][mxid] and fresh in server["devices"][mxid], "made after the sign-out: kept"
    assert chat_signouts.run_due(db, matrix, now=time.time() + chat_signouts.MINT_GRACE_SECONDS) == {"done": 1}
    assert fresh in server["devices"][mxid]


# --- the interim is over (build step 4) ------------------------------------------------------------------------------------

def test_a_password_change_reaches_the_other_browsers_site_device_and_keeps_this_sign_ins(make_member, server):
    """The interim of build steps 2 and 3 signed out apps only; now the other browser's site device goes too, and this sign-in keeps
    its chat and call devices (decision 2). No row says `site`."""
    member, mxid, a, b, devices = setup(make_member, server)
    server["devices"][mxid]["OLDSITEDEV"] = ""                         # a site device from before rows were kept
    assert _password(member, a).status_code == 302
    settle(member)
    [(scope, spared, why, done, _ids, _tries)] = queue_rows(member)
    assert "site" not in (spared or "").split(",") and why == "password" and done > 0
    assert set(spared.split(",")) == {devices["a_chat"], devices["a_call"]}
    assert revoked(member) == {devices["b_chat"]: "password"}
    assert set(server["devices"][mxid]) == {devices["a_chat"], devices["a_call"]}, "B's device, the old one and the app went"
    # B's next token request is a new site-chosen device, never its revoked one, even with the chat server failing first
    b2 = member.browser()
    server["down"] = True
    assert post(b2, "/api/chat/token", {"formkey": member.formkey(b2), "device_id": devices["b_chat"]}).status_code == 503
    server["down"] = False
    again = mint(member, b2, device_id=devices["b_chat"])
    assert again != devices["b_chat"] and again.startswith("UQ")
    assert not [c for c in server["calls"] if c[0] == "login" and c[2] == devices["b_chat"]][1:], "only the mint that made it"


# --- decision 3 ---------------------------------------------------------------------------------------------------------------

def test_a_ban_queues_nothing_and_the_banned_account_is_still_served_chat(make_member, server):
    member, mxid, a, b, devices = setup(make_member, server)
    from flask import g
    with member.app.test_request_context("/"):
        g.db = member.db_session()
        user = g.db.query(type(member.user())).filter_by(id=member.id).first()
        user.ban(reason="ci: a suspension", days=3)          # User.ban, the one place a suspension is written
        g.db.commit()
    settle(member)
    assert member.user().is_suspended
    assert queue_rows(member) == [] and revoked(member) == {}
    assert mint(member, a, device_id=devices["a_chat"]) == devices["a_chat"]


def test_a_permanent_ban_and_an_unban_queue_nothing_either(make_member, server):
    member, mxid, a, b, devices = setup(make_member, server)
    from flask import g
    with member.app.test_request_context("/"):
        g.db = member.db_session()
        user = g.db.query(type(member.user())).filter_by(id=member.id).first()
        g.v = user
        user.ban(reason="ci: a permanent ban")               # days=0: permanent
        g.db.commit()
        assert user.is_banned and not user.unban_utc
        user.unban(reason="ci: reinstated")
        g.db.commit()
    settle(member)
    assert queue_rows(member) == [] and revoked(member) == {}
    assert [c for c in server["calls"] if c[0] == "delete"] == [], "no device deleted, apps included"
    assert set(server["devices"][mxid]) == set(devices.values()) | {"ELEMENTAPP1"}
    assert mint(member, b, device_id=devices["b_chat"]) == devices["b_chat"]


# --- the form key a page held before the change -------------------------------------------------------------------------------

def test_a_spared_sign_in_gets_a_fresh_form_key_and_keeps_its_device(make_member, server):
    member, mxid, a, b, devices = setup(make_member, server)
    stale = member.formkey(a)
    assert _others(member, a).status_code == 200
    settle(member)
    refused = post(a, "/api/chat/token", {"formkey": stale, "device_id": devices["a_chat"]})
    assert refused.status_code in (302, 401), "a stale form key: the page asks for the current one"
    fresh = get(a, "/api/chat/formkey")
    assert fresh.status_code == 200 and fresh.headers["Cache-Control"] == "no-store"
    key = fresh.get_json()["formkey"]
    assert key == member.formkey(a) and key != stale
    assert fresh.get_json()["mxid"] == mxid, "the page keeps a key only for the account it was drawn for (site.js refreshFormkey)"
    again = post(a, "/api/chat/token", {"formkey": key, "device_id": devices["a_chat"]})
    assert again.status_code == 200 and again.get_json()["device_id"] == devices["a_chat"], "spared: never a 503"
    assert get(member.app.test_client(), "/api/chat/formkey").status_code != 200
    assert get(b, "/api/chat/formkey").status_code != 200, "a sign-in that ended is refused by @auth_required"


# --- a token request already past its wrappers when a sign-out commits ------------------------------------------------------

def _while_it_waits(monkeypatch, pg_engine, change, params):
    """The token request has passed its wrappers and is waiting on the account's mint lock; what it waits for commits first."""
    from ruqqus.helpers import chat_signouts
    real = chat_signouts.lock_account
    fired = []

    def lock_account(db, user_id):
        if not fired:
            fired.append(True)
            with pg_engine.connect() as conn:
                conn.execute(text(change), params)
        return real(db, user_id)
    monkeypatch.setattr(chat_signouts, "lock_account", lock_account)
    return fired


def site_rows(member_or_id):
    from ruqqus.__main__ import db_session
    db = db_session()
    db.rollback()
    return db.execute(text("SELECT count(*) FROM chat_site_devices WHERE user_id = :u"), {"u": getattr(member_or_id, "id", member_or_id)}).scalar()


@pytest.mark.parametrize("change", ["UPDATE users SET login_nonce = login_nonce + 1 WHERE id = :u",      # Log out everywhere, a password...
                                    "UPDATE users SET is_deleted = true WHERE id = :u"])                 # the account deleted
def test_a_token_request_that_waited_for_a_sign_out_mints_no_device(make_member, server, pg_engine, monkeypatch, change):
    member = make_member()
    with_chat(member)
    here = member.browser()
    form = {"formkey": member.formkey(here)}
    fired = _while_it_waits(monkeypatch, pg_engine, change, {"u": member.id})
    answer = post(here, "/api/chat/token", form)
    assert fired and answer.status_code == 401 and answer.get_json()["signed_out"] is True
    assert site_rows(member) == 0 and [c for c in server["calls"] if c[0] == "login"] == [], "no device on the chat server"


def test_a_childs_token_request_that_waited_for_chat_switched_off_mints_no_device(made, server, pg_engine, monkeypatch):
    mum, kid = made()
    with_chat(kid)
    assert switch(mum, kid, "chat", True).status_code == 200
    mine = kid.browser()
    assert post(mine, "/api/chat/token", {"formkey": kid.formkey(mine)}).status_code == 200, "chat on: a device"
    before = site_rows(kid)
    fired = _while_it_waits(monkeypatch, pg_engine, "UPDATE child_controls SET chat = false WHERE child_id = :u", {"u": kid.id})
    answer = post(mine, "/api/chat/token", {"formkey": kid.formkey(mine)})
    assert fired and answer.status_code == 403 and answer.get_json()["age_locked"] is True
    assert site_rows(kid) == before and len([c for c in server["calls"] if c[0] == "login"]) == 1


def test_a_device_from_before_the_rows_is_committed_before_it_is_taken_up_and_never_when_a_pending_sign_out_covers_it(make_member, server,
                                                                                                                       pg_engine, monkeypatch):
    """A row only the take-up's transaction could see is one a sign-out queued meanwhile neither revokes nor waits for: the sign-out would
    delete the device and the take-up's login would make it again, with a live token nothing would ever sign out."""
    import ruqqus.helpers.matrix_client as matrix
    member = make_member()
    mxid = with_chat(member)
    old, other = "OLD" + secrets.token_hex(4).upper(), "OLD" + secrets.token_hex(4).upper()     # ids no earlier test gave another account
    server["devices"][mxid] = {old: "Laba chat", other: "Laba chat"}
    logging_in = matrix.login_as
    seen = {}

    def login_as(mxid_, device_id=None):
        with pg_engine.connect() as conn:
            seen[device_id] = conn.execute(text("SELECT created_ms, revoked_utc FROM chat_site_devices WHERE user_id = :u AND device_id = :d"),
                                           {"u": member.id, "d": device_id}).first()
        return logging_in(mxid_, device_id)
    monkeypatch.setattr(matrix, "login_as", login_as)
    here = member.browser()
    assert mint(member, here, device_id=old) == old
    assert tuple(seen[old]) == (0, 0), "committed before the login, under the mint lock"

    from ruqqus.__main__ import db_session
    from ruqqus.helpers import chat_signouts
    db = db_session()
    chat_signouts.queue(db, member.id, "child_chat_off")    # a sign-out of every device, queued outside a request: not run yet
    db.commit()
    fresh = mint(member, here, device_id=other)
    assert fresh != other and other not in seen, "covered by the pending sign-out: a new device, never the old one"


# --- Settings > Chat apps: the row first, then the chat server ----------------------------------------------------------------

def test_one_device_signed_out_has_its_row_revoked_and_committed_before_the_chat_server_deletes_it(make_member, server, pg_engine,
                                                                                                    monkeypatch):
    """Otherwise a token request in flight on the device (it holds the row) re-tokens it after the delete: Synapse makes it again."""
    import ruqqus.helpers.matrix_client as matrix
    member, mxid, a, b, devices = setup(make_member, server)
    server["devices"][mxid]["OLDSITEDEV"] = "Laba chat"                # a site device from before the rows
    deleting = matrix.delete_device
    seen = {}

    def delete_device(mxid_, device_id):
        with pg_engine.connect() as conn:
            seen[device_id] = conn.execute(text("SELECT revoked_reason FROM chat_site_devices WHERE user_id = :u AND device_id = :d AND revoked_utc > 0"),
                                           {"u": member.id, "d": device_id}).scalar()
        return deleting(mxid_, device_id)
    monkeypatch.setattr(matrix, "delete_device", delete_device)
    for device_id in (devices["b_chat"], "OLDSITEDEV"):
        assert post(a, "/api/chat/devices/sign_out", {"formkey": member.formkey(a), "device_id": device_id}).status_code == 204
    assert seen == {devices["b_chat"]: "signed_out_here", "OLDSITEDEV": "signed_out_here"}
    assert post(a, "/api/chat/devices/sign_out", {"formkey": member.formkey(a), "device_id": "ELEMENTAPP1"}).status_code == 204
    assert seen["ELEMENTAPP1"] is None, "an app's device gets no site row"
    assert "ELEMENTAPP1" not in revoked(member)


# --- a chat server that doesn't answer at all ---------------------------------------------------------------------------------

def test_a_chat_server_that_doesnt_answer_is_a_retry_and_a_queued_sign_out_never_a_500(make_member, monkeypatch):
    """matrix_client turns requests' own errors (refused, timed out) into MatrixError: the token route's cleanup runs (a timeout may come
    after Synapse made the device) and Settings > Chat apps queues, as for a server that answers with an error."""
    import types
    import requests
    import ruqqus.helpers.matrix_client as matrix

    def refused(*a, **k):
        raise requests.ConnectionError("refused")

    def timed_out(*a, **k):
        raise requests.ReadTimeout("slow")
    monkeypatch.setattr(matrix, "requests", types.SimpleNamespace(get=refused, post=timed_out, delete=refused))
    member = make_member()
    mxid = with_chat(member)
    db = member.db_session()
    db.execute(text("INSERT INTO chat_site_devices (user_id, device_id, kind, created_ms, last_used_utc) VALUES (:u, 'UQSITE000000', 'chat', 1, 1)"),
               {"u": member.id})
    db.commit()
    here = member.browser()
    answer = post(here, "/api/chat/token", {"formkey": member.formkey(here)})
    assert answer.status_code == 503 and answer.get_json()["retry"] is True
    made_now = {d: r for d, r in revoked(member).items() if d != "UQSITE000000"}
    assert list(made_now.values()) == ["mint_failed"]
    assert [(r[0], r[2], r[4]) for r in queue_rows(member)] == [("listed", "mint_failed", next(iter(made_now)))]
    with pytest.raises(matrix.Unreachable):
        matrix.list_devices(mxid)
    assert get(here, "/api/chat/devices").status_code == 503
    later = post(here, "/api/chat/devices/sign_out", {"formkey": member.formkey(here), "device_id": "UQSITE000000"})
    assert later.status_code == 202 and later.get_json()["pending"] is True
    assert revoked(member)["UQSITE000000"] == "signed_out_here"
    stranger = post(here, "/api/chat/devices/sign_out", {"formkey": member.formkey(here), "device_id": "ELEMENTAPP1"})
    assert stranger.status_code == 503


@pytest.mark.parametrize("failure", ["refused", "register_refused", "error", "busy"])
def test_a_first_chat_whose_account_the_chat_server_cant_make_is_a_retry_never_a_500(make_member, monkeypatch, failure):
    """A member with no chat account yet (a Space or a call page opened before /chat) is given one by the token route itself: the chat server
    not answering, answering with an error, or the id mark busy is the same 503 "try again" as for the device, and nothing is kept."""
    import types
    import requests
    import ruqqus.helpers.matrix_client as matrix

    def refused(*a, **k):
        raise requests.ConnectionError("refused")
    if failure in ("refused", "register_refused"):
        monkeypatch.setattr(matrix, "requests", types.SimpleNamespace(get=refused, post=refused, put=refused, delete=refused))
        if failure == "register_refused":           # the id mark written, the account itself not made
            monkeypatch.setattr(matrix, "note_chat_id", lambda user_id: None)
    elif failure == "error":
        monkeypatch.setattr(matrix, "read_id_mark", lambda: (_ for _ in ()).throw(matrix.MatrixError("read_id_mark failed: 500")))
    else:
        monkeypatch.setattr(matrix, "note_chat_id", lambda user_id: (_ for _ in ()).throw(matrix.MatrixError("note_chat_id: the mark is busy")))
    member = make_member()
    here = member.browser()
    answer = post(here, "/api/chat/token", {"formkey": member.formkey(here)})
    assert answer.status_code == 503, (failure, answer.status_code)
    assert answer.get_json()["retry"] is True and answer.headers["Cache-Control"] == "no-store"
    db = member.db_session()
    db.rollback()
    assert db.execute(text("SELECT count(*) FROM chat_identities WHERE user_id = :u"), {"u": member.id}).scalar() == 0
    assert site_rows(member) == 0


# --- the migration not applied yet (run last: it renames the tables and puts them back) ---------------------------------------

def test_without_the_tables_a_credential_change_goes_through_and_chat_waits(make_member, server, pg_engine, monkeypatch, caplog):
    import logging
    from ruqqus.helpers import chat_signouts
    member = make_member()
    with_chat(member)
    here = member.browser()
    nonce = member.user().login_nonce
    with pg_engine.connect() as conn:
        conn.execute(text("ALTER TABLE chat_device_signouts RENAME TO chat_device_signouts_away"))
    try:
        monkeypatch.setattr(chat_signouts, "_ready", [])
        with caplog.at_level(logging.ERROR):
            assert _password(member, here).status_code == 302
        assert member.user().login_nonce == nonce + 1 and member.user().verifyPass("Another-Passw0rd")
        assert f"chat sign-out not queued: migration missing (user {member.id}, password)" in caplog.text, "said, in the log"
        before = len(server["calls"])
        answer = post(here, "/api/chat/token", {"formkey": member.formkey(here)})
        assert answer.status_code == 503 and answer.get_json()["retry"] is True
        assert len(server["calls"]) == before, "no chat server call"
    finally:
        with pg_engine.connect() as conn:
            conn.execute(text("ALTER TABLE chat_device_signouts_away RENAME TO chat_device_signouts"))
        monkeypatch.setattr(chat_signouts, "_ready", [])


def test_without_the_tables_signing_one_device_out_is_never_a_500(make_member, server, pg_engine, monkeypatch):
    """The device is deleted at once and nothing is written; a chat server that fails can't be queued, so that is a 503, never "shortly"."""
    from ruqqus.helpers import chat_signouts
    member = make_member()
    mxid = with_chat(member)
    server["devices"][mxid] = {"UQSITE000000": "Laba chat", "ELEMENTAPP1": "Element Web"}
    here = member.browser()
    with pg_engine.connect() as conn:
        conn.execute(text("ALTER TABLE chat_site_devices RENAME TO chat_site_devices_away"))
    try:
        monkeypatch.setattr(chat_signouts, "_ready", [])
        for device_id in ("UQSITE000000", "ELEMENTAPP1"):
            answer = post(here, "/api/chat/devices/sign_out", {"formkey": member.formkey(here), "device_id": device_id})
            assert answer.status_code == 204, (device_id, answer.status_code)
        assert server["devices"][mxid] == {}
        server["devices"][mxid]["ELEMENTAPP2"] = "Element Web"
        import ruqqus.helpers.matrix_client as matrix

        def failing(*a, **k):
            raise matrix.MatrixError("down")
        monkeypatch.setattr(matrix, "delete_device", failing)
        failed = post(here, "/api/chat/devices/sign_out", {"formkey": member.formkey(here), "device_id": "ELEMENTAPP2"})
        assert failed.status_code == 503, "nothing could be kept, so nothing is promised"
    finally:
        with pg_engine.connect() as conn:
            conn.execute(text("ALTER TABLE chat_site_devices_away RENAME TO chat_site_devices"))
        monkeypatch.setattr(chat_signouts, "_ready", [])
