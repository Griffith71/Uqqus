"""The token route v2 and /api/chat/store_keys on the real app and a real Postgres, with the chat server faked (chat plan phase 2, build
step 3: helpers/chat_tokens.py, helpers/chat_store_keys.py): the answer's shape and keys, what is recorded, the key-only start, renew,
boot and replace, who is refused before any chat-server call, a suspended account still served (decision 3), the store keys only of this
account's site devices, `no-store`, the rate limits, a row held past its lock timeout, and a mint racing a sign-out. The rules in detail
are tests/test_chat_tokens.py (SQLite)."""
import os
import subprocess
import sys
import time

import pytest
from sqlalchemy import text

from .test_chat_signout_routes import addr, post, with_chat

HEADERS = {"User-Agent": "ci-chat-token"}


@pytest.fixture
def server(monkeypatch, real_app):
    """A chat server: `devices` {mxid: {device_id: name}}, every call noted, /refresh with rotating tokens."""
    import ruqqus.helpers.matrix_client as matrix
    state = {"devices": {}, "calls": [], "refresh": {}, "n": 0}

    def pair(mxid, device_id):
        state["n"] += 1
        rt = f"rt-{state['n']}"
        state["refresh"][rt] = (mxid, device_id)
        return {"access_token": f"syt_{state['n']}", "refresh_token": rt, "expires_in_ms": 3600000}

    def list_devices(mxid):
        state["calls"].append(("list", mxid))
        return [{"device_id": d, "name": n, "last_seen": None} for d, n in state["devices"].get(mxid, {}).items()]

    def login_as(mxid, device_id=None):                  # always with a refresh token (build step 5)
        state["calls"].append(("login", device_id))
        state["devices"].setdefault(mxid, {})[device_id] = "Laba chat"
        return dict({"user_id": mxid, "device_id": device_id}, **pair(mxid, device_id))

    def refresh(refresh_token):
        state["calls"].append(("refresh", refresh_token))
        if refresh_token not in state["refresh"]:
            raise matrix.RefreshGone("gone")
        return pair(*state["refresh"][refresh_token])

    def delete_device(mxid, device_id):
        state["calls"].append(("delete", device_id))
        state["devices"].get(mxid, {}).pop(device_id, None)

    for name, fn in (("list_devices", list_devices), ("login_as", login_as), ("refresh", refresh), ("delete_device", delete_device)):
        monkeypatch.setattr(matrix, name, fn)
    monkeypatch.setattr(matrix, "set_display_name", lambda *a, **k: None)
    return state


@pytest.fixture(autouse=True)
def synapses_own_lifetime(real_app, monkeypatch):
    """Synapse's figure, as a live site reports it: the dev site may run with CHAT_TOKEN_REPORT_SECONDS for the renewal specs."""
    monkeypatch.setitem(real_app[0].config, "CHAT_TOKEN_REPORT_SECONDS", None)


def token(member, client, **form):
    return post(client, "/api/chat/token", dict(form, formkey=member.formkey(client)), headers=HEADERS)


def keys_of(member, client, ids):
    return post(client, "/api/chat/store_keys", {"formkey": member.formkey(client), "device_ids": ",".join(ids)}, headers=HEADERS)


def row(member, device_id):
    db = member.db_session()
    db.rollback()
    return db.execute(text("""SELECT kind, sign_in, created_ms, last_used_utc, refresh_sealed, revoked_utc, revoked_reason
                              FROM chat_site_devices WHERE user_id = :u AND device_id = :d"""), {"u": member.id, "d": device_id}).first()


def count_rows(member):
    db = member.db_session()
    db.rollback()
    return db.execute(text("SELECT count(*) FROM chat_site_devices WHERE user_id = :u"), {"u": member.id}).scalar()


def session_hash(client):
    from ruqqus.helpers import chat_signouts
    with client.session_transaction() as session:
        return chat_signouts.sign_in_hash(session["session_id"])


def secret(real_app):
    from ruqqus.helpers import chat_store_keys
    return chat_store_keys.secret_of(real_app[0].config)


def test_the_answer_its_keys_and_what_is_recorded(real_app, make_member, server):
    from ruqqus.helpers import chat_store_keys as k
    member = make_member()
    mxid = with_chat(member)
    client = member.browser()
    answer = token(member, client, kind="call")
    assert answer.status_code == 200 and answer.headers["Cache-Control"] == "no-store"
    body = answer.get_json()
    d = body["device_id"]
    assert k.is_site_made(d) and body["user_id"] == mxid and body["kind"] == "call" and body["new_device"] is True
    assert body["access_token"].startswith("syt_") and body["expires_in_ms"] == 3600000, "a token that ends, and says when"
    assert body["storage_key"] == k.b64(k.store_key(secret(real_app), mxid, d)) and len(body["storage_key"]) == 43
    assert body["waiting_key"] == k.b64(k.waiting_key(secret(real_app), mxid)) and body["kid"] == k.key_id(secret(real_app))
    assert not any("refresh" in key for key in body) and "rt-" not in answer.get_data(as_text=True)
    kind, sign_in, created_ms, _used, sealed, revoked_utc, _reason = row(member, d)
    assert (kind, sign_in, revoked_utc) == ("call", session_hash(client), 0) and created_ms > 0
    assert sealed and "rt-" not in sealed, "the refresh token kept here, sealed (secret_box), never in the clear"
    assert ("login", d) in server["calls"]


def test_todays_client_still_works_and_gets_the_new_fields(real_app, make_member, server):
    """Exactly what token.js posts today: formkey, then formkey and device_id."""
    member = make_member()
    with_chat(member)
    client = member.browser()
    first = post(client, "/api/chat/token", {"formkey": member.formkey(client)}, headers=HEADERS).get_json()
    again = post(client, "/api/chat/token", {"formkey": member.formkey(client), "device_id": first["device_id"]}, headers=HEADERS)
    body = again.get_json()
    assert again.status_code == 200 and body["device_id"] == first["device_id"] and body["access_token"]
    assert {"homeserver_url", "user_id", "access_token", "device_id", "storage_key", "waiting_key", "kid", "kind", "new_device"} <= set(body)
    assert body["kind"] == "chat" and body["new_device"] is False


def test_a_key_only_start_makes_no_chat_server_call_and_makes_the_device_this_sign_ins(real_app, make_member, server):
    member = make_member()
    with_chat(member)
    before = member.browser()
    d = token(member, before).get_json()["device_id"]
    after = member.browser()                                     # the cookie ran out; a new sign-in in the same browser
    server["calls"].clear()
    answer = token(member, after, mode="boot", want="key", device_id=d)
    body = answer.get_json()
    assert answer.status_code == 200 and body["access_token"] is None and body["device_id"] == d and body["storage_key"]
    assert server["calls"] == [], "nothing is asked of the chat server"
    assert row(member, d).sign_in == session_hash(after)
    listed = after.get("/api/chat/devices", base_url="http://localhost", headers=HEADERS, environ_base={"REMOTE_ADDR": addr()})
    assert [x["here"] for x in listed.get_json()["devices"] if x["device_id"] == d] == [True], "Settings > Chat apps: still this browser's"


def test_renew_goes_through_refresh(real_app, make_member, server):
    member = make_member()
    with_chat(member)
    client = member.browser()
    body = token(member, client).get_json()
    d = body["device_id"]
    assert body["expires_in_ms"] == 3600000
    sealed = row(member, d).refresh_sealed
    assert sealed and "rt-" not in sealed, "kept sealed (secret_box), never in the clear"
    server["calls"].clear()
    renewed = token(member, client, mode="renew", device_id=d)
    assert renewed.status_code == 200 and renewed.get_json()["device_id"] == d
    assert [c[0] for c in server["calls"]] == ["refresh"]
    assert row(member, d).refresh_sealed != sealed


def test_the_report_setting_caps_what_the_browser_is_told(real_app, make_member, server, monkeypatch):
    monkeypatch.setitem(real_app[0].config, "CHAT_TOKEN_REPORT_SECONDS", 90)
    member = make_member()
    with_chat(member)
    assert token(member, member.browser()).get_json()["expires_in_ms"] == 90000


def test_a_revoked_device_is_409_on_renew_and_a_new_device_with_previous_on_boot(real_app, make_member, server):
    from ruqqus.helpers import chat_signouts
    member = make_member()
    with_chat(member)
    client = member.browser()
    d = token(member, client).get_json()["device_id"]
    db = member.db_session()
    chat_signouts.revoke_rows(db, member.id, [d], "password")
    db.commit()
    server["calls"].clear()
    gone = token(member, client, mode="renew", device_id=d)
    assert gone.status_code == 409 and gone.get_json() == {"device_gone": True, "reason": "password"}
    assert gone.headers["Cache-Control"] == "no-store" and server["calls"] == [] and count_rows(member) == 1
    booted = token(member, client, mode="boot", device_id=d).get_json()
    assert booted["device_id"] != d and booted["previous"] == {"device_id": d, "reason": "password"} and count_rows(member) == 2


def test_replace_revokes_mints_nothing_and_answers_the_same_for_anyone(real_app, make_member, server):
    member, stranger = make_member(), make_member()
    with_chat(member)
    with_chat(stranger)
    client = member.browser()
    d = token(member, client).get_json()["device_id"]
    theirs = token(stranger, stranger.browser()).get_json()["device_id"]
    for named in (theirs, "UQNOBODY0000", d):
        answer = token(member, client, mode="replace", device_id=named, why="store_unopenable")
        assert (answer.status_code, answer.get_json()) == (200, {"replaced": True})
    assert count_rows(member) == 1 and row(member, d).revoked_reason == "store_unopenable"
    assert row(stranger, theirs).revoked_utc == 0


def test_a_qr_limited_or_child_locked_account_is_refused_before_any_chat_server_call(real_app, make_member, server):
    limited = make_member()
    with_chat(limited)
    client = limited.browser()
    with client.session_transaction() as session:
        session["qr_limited"] = True
        session["qr_expires_utc"] = int(time.time()) + 3600
    for path, form in (("/api/chat/token", {}), ("/api/chat/store_keys", {"device_ids": "UQAAAAAAAAAA"})):
        answer = post(client, path, dict(form, formkey=limited.formkey(client)), headers=HEADERS)
        assert answer.status_code == 403 and answer.get_json()["need_password"] is True and "storage_key" not in answer.get_data(as_text=True)
    child = make_member()
    with_chat(child)
    db = child.db_session()
    db.execute(text("UPDATE users SET age_declared = 2, filter_level = 2, all_shows_sensitive = false WHERE id = :u"), {"u": child.id})
    db.commit()
    kid = child.browser()
    for path, form in (("/api/chat/token", {}), ("/api/chat/store_keys", {"device_ids": "UQAAAAAAAAAA"})):
        answer = post(kid, path, dict(form, formkey=child.formkey(kid)), headers=HEADERS)
        assert answer.status_code == 403 and answer.get_json()["age_locked"] is True and "storage_key" not in answer.get_data(as_text=True)
    assert server["calls"] == [] and count_rows(limited) == 0 and count_rows(child) == 0


def test_a_suspended_account_is_still_served_as_today(real_app, make_member, server):
    member = make_member()
    with_chat(member)
    db = member.db_session()
    db.execute(text("UPDATE users SET is_banned = 1, unban_utc = :t, ban_reason = 'ci' WHERE id = :u"),
               {"u": member.id, "t": int(time.time()) + 86400})
    db.commit()
    client = member.browser()
    answer = token(member, client)
    assert answer.status_code == 200 and answer.get_json()["storage_key"]
    assert keys_of(member, client, [answer.get_json()["device_id"]]).status_code == 200


def test_store_keys_serves_only_this_accounts_site_devices(real_app, make_member, server):
    from ruqqus.helpers import chat_signouts, chat_store_keys as k
    member, stranger = make_member(), make_member()
    mxid = with_chat(member)
    with_chat(stranger)
    client = member.browser()
    mine = token(member, client).get_json()["device_id"]
    gone = token(member, client, kind="call").get_json()["device_id"]
    db = member.db_session()
    chat_signouts.revoke_rows(db, member.id, [gone], "password")
    db.commit()
    theirs = token(stranger, stranger.browser()).get_json()["device_id"]
    server["calls"].clear()
    answer = keys_of(member, client, [mine, gone, theirs, "ELEMENTAPP"])
    assert answer.status_code == 200 and answer.headers["Cache-Control"] == "no-store"
    got = answer.get_json()["keys"]
    assert set(got) == {mine, gone}, "revoked too (the harvest), never another account's or an app's"
    assert got[mine] == {"key": k.b64(k.store_key(secret(real_app), mxid, mine)), "kid": k.key_id(secret(real_app))}
    assert server["calls"] == []
    many = [f"{n:x}" for n in range(60_000)]          # ~340 KB url-encoded: refused unread, not parsed for seconds in the worker
    for bad in (["A", "B", "C", "D", "E"], ["a b"], many):
        started = time.time()
        refused = keys_of(member, client, bad)
        assert refused.status_code == 400 and refused.headers["Cache-Control"] == "no-store"
        assert time.time() - started < 2, "a long list is refused before it is read"
    nobody = make_member()
    assert keys_of(nobody, nobody.browser(), [mine]).get_json() == {"keys": {}}, "no chat yet: nothing, and no chat account made"
    assert server["calls"] == []


def test_every_answer_is_no_store(real_app, make_member, server):
    member = make_member()
    with_chat(member)
    client = member.browser()
    for form in ({"kind": "video"}, {"mode": "renew", "device_id": "UQNOBODY0000"}, {"mode": "replace", "device_id": "X", "why": "key_changed"}, {}):
        assert token(member, client, **form).headers["Cache-Control"] == "no-store", form


def test_the_rate_limits(real_app, make_member, server):
    member = make_member()
    with_chat(member)
    client = member.browser()
    form = {"mode": "replace", "device_id": "UQNOBODY0000", "why": "key_changed"}     # nothing is made or asked of the chat server
    codes = [token(member, client, **form).status_code for _ in range(61)]
    assert codes[:60] == [200] * 60 and codes[60] == 429, "60 a minute per member, from any address"
    other = make_member()
    with_chat(other)
    browser = other.browser()
    codes = [keys_of(other, browser, ["UQNOBODY0000"]).status_code for _ in range(21)]
    assert codes[:20] == [200] * 20 and codes[20] == 429
    assert server["calls"] == []


HOLD_ROW = """
import sys, time, psycopg2
conn = psycopg2.connect(sys.argv[1])
conn.cursor().execute("SELECT 1 FROM chat_site_devices WHERE user_id = %s AND device_id = %s FOR UPDATE", (int(sys.argv[2]), sys.argv[3]))
print("held", flush=True)
time.sleep(float(sys.argv[4]))
conn.rollback()
"""


def test_a_row_held_past_its_lock_timeout_is_503_never_a_500(real_app, make_member, server, monkeypatch):
    from ruqqus.helpers import chat_tokens
    monkeypatch.setattr(chat_tokens, "ROW_LOCK_SECONDS", 1)
    member = make_member()
    with_chat(member)
    client = member.browser()
    d = token(member, client).get_json()["device_id"]
    holder = subprocess.Popen([sys.executable, "-I", "-c", HOLD_ROW, os.environ["PG_TEST_DATABASE_URL"], str(member.id), d, "4"],
                              stdout=subprocess.PIPE, text=True)
    assert holder.stdout.readline().strip() == "held"
    started = time.time()
    answer = token(member, client, mode="renew", device_id=d)
    waited = time.time() - started
    holder.wait()
    assert answer.status_code == 503 and answer.get_json()["retry"] is True and answer.headers["Cache-Control"] == "no-store"
    assert 0.8 <= waited < 3.5
    assert token(member, client, mode="renew", device_id=d).status_code == 200, "once let go, it is served"


HOLD_MINT_LOCK = """
import sys, time, psycopg2
conn = psycopg2.connect(sys.argv[1])
cur = conn.cursor()
cur.execute("SELECT pg_advisory_xact_lock(7003, %s)", (int(sys.argv[2]),))
print("held", flush=True)
time.sleep(float(sys.argv[4]))       # a sign-out that took the lock, then is slow: it stamps its time only now, as queue() does
at = int(time.time() * 1000)
cur.execute("UPDATE chat_site_devices SET revoked_utc = %s, revoked_reason = 'password' WHERE user_id = %s AND revoked_utc = 0 AND created_ms <= %s",
            (at // 1000, int(sys.argv[2]), at))
cur.execute("INSERT INTO chat_device_signouts (user_id, mxid, scope, spare_after_ms, reason, created_utc, next_try_utc) "
            "VALUES (%s, %s, 'all', %s, 'password', %s, %s) RETURNING id", (int(sys.argv[2]), sys.argv[3], at, at // 1000, at // 1000))
conn.commit()
print(cur.fetchone()[0], at, flush=True)
"""


def test_a_mint_racing_a_sign_out_waits_and_survives_it(real_app, make_member, server):
    """A sign-out holds the account's mint lock while it stamps spare_after_ms and revokes the rows made before it. A device being made
    meanwhile waits for it, so it is made after the sign-out (created_ms > spare_after_ms) and the sign-out never touches it. Without the
    lock the device would be stamped before the sign-out's time, revoked by it after it was handed out, and deleted by its run."""
    from ruqqus.helpers import chat_signouts
    import ruqqus.helpers.matrix_client as matrix
    member = make_member()
    mxid = with_chat(member)
    client = member.browser()
    old = token(member, client).get_json()["device_id"]
    holder = subprocess.Popen([sys.executable, "-I", "-c", HOLD_MINT_LOCK, os.environ["PG_TEST_DATABASE_URL"], str(member.id), mxid, "2"],
                              stdout=subprocess.PIPE, text=True)
    assert holder.stdout.readline().strip() == "held"
    started = time.time()
    answer = token(member, client, mode="boot")                   # a new page start with no device of its own
    waited = time.time() - started
    row_id, spare_after_ms = (int(x) for x in holder.stdout.readline().split())
    holder.wait()
    assert answer.status_code == 200 and waited >= 1.5, (answer.status_code, waited)
    new = answer.get_json()["device_id"]
    assert new != old and row(member, new).created_ms > spare_after_ms and row(member, new).revoked_utc == 0
    assert row(member, old).revoked_reason == "password"
    db = member.db_session()
    db.rollback()
    assert chat_signouts.run_one(db, matrix, row_id) == "done"
    assert new in server["devices"][mxid] and old not in server["devices"][mxid], "the old device went, the new one stays"
    assert token(member, client, mode="renew", device_id=new).status_code == 200
