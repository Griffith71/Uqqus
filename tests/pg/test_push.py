"""Push notifications on the real app (CLAUDE.md "Push notifications"): a browser's subscription is kept only for a known
push service with real keys, the chat server's webhook tells the other person of a 1:1 chat (never the words, never across a
block) and answers a transaction it has already handled at once (it used to count its messages twice), and a group member's
own choice decides what they are told of. The sending itself is caught here: nothing leaves the machine."""
import secrets
import time

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import text

from .test_characterization import fresh, post


@pytest.fixture
def push_on(monkeypatch):
    from ruqqus.helpers import push_send, web_push
    private, public = web_push.new_keys()
    monkeypatch.setenv("VAPID_PRIVATE_KEY", private)
    monkeypatch.setenv("VAPID_PUBLIC_KEY", public)
    sent = []
    monkeypatch.setattr(push_send, "send_later", lambda jobs, urgency="normal": sent.extend(jobs))
    return sent


def _browser_keys():
    from ruqqus.helpers import web_push
    ua = ec.generate_private_key(ec.SECP256R1())
    public = ua.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return web_push.b64u(public), web_push.b64u(secrets.token_bytes(16))


def _subscribe(member, endpoint=None):
    here = member.browser()
    p256dh, auth = _browser_keys()
    endpoint = endpoint or f"https://fcm.googleapis.com/fcm/send/{secrets.token_hex(8)}"
    return post(here, "/api/push/subscribe", {"endpoint": endpoint, "p256dh": p256dh, "auth": auth, "formkey": member.formkey(here)})


def _webhook(real_app, txn, events):
    app, _ = real_app
    token = app.config.get("MATRIX_HS_TOKEN") or ""
    return app.test_client().put(f"/matrix/appservice/_matrix/app/v1/transactions/{txn}", base_url="http://localhost",
                                 json={"events": events}, headers={"Authorization": f"Bearer {token}"})


def _pair(db, a, b):
    room = f"!p{secrets.token_hex(8)}"
    now = int(time.time())
    db.execute(text("INSERT INTO chat_conversations (user_a_id, user_b_id, initiator_id, matrix_room_id, status, created_utc, "
                    "last_activity_utc) VALUES (:a, :b, :a, :r, 'inbox', :n, :n)"), {"a": a.id, "b": b.id, "r": room, "n": now})
    for m in (a, b):
        db.execute(text("INSERT INTO chat_identities (user_id, matrix_user_id, provisioned_utc) VALUES (:u, :m, :n) ON CONFLICT DO NOTHING"),
                   {"u": m.id, "m": f"@ruqqus_{m.id}:ruqqus.local", "n": now})
    db.commit()
    return room


def test_only_a_real_subscription_on_a_known_push_service_is_kept(make_member, push_on):
    me = make_member()
    assert _subscribe(me).status_code == 200
    assert _subscribe(me, endpoint="https://evil.example/collect").status_code == 400
    here = me.browser()
    bad_keys = post(here, "/api/push/subscribe", {"endpoint": "https://fcm.googleapis.com/fcm/send/x", "p256dh": "AAAA", "auth": "BBBB",
                                                   "formkey": me.formkey(here)})
    assert bad_keys.status_code == 400
    assert fresh(me).execute(text("SELECT COUNT(*) FROM push_subscriptions WHERE user_id = :u"), {"u": me.id}).scalar() == 1


def test_a_message_tells_the_other_person_never_the_words_and_a_repeated_transaction_counts_once(make_member, real_app, push_on):
    if not real_app[0].config.get("MATRIX_HS_TOKEN"):
        pytest.skip("no appservice token here")
    me, them = make_member(), make_member()
    room = _pair(fresh(me), me, them)
    assert _subscribe(them).status_code == 200
    assert _subscribe(me).status_code == 200
    event = {"type": "m.room.encrypted", "room_id": room, "sender": f"@ruqqus_{me.id}:ruqqus.local", "event_id": "$" + secrets.token_hex(8),
             "content": {"algorithm": "m.megolm.v1.aes-sha2", "ciphertext": "secret words"}}
    txn = secrets.token_hex(6)
    assert _webhook(real_app, txn, [event]).status_code == 200
    assert len(push_on) == 1
    sub, payload = push_on[0]
    assert payload["body"].startswith("New message from") and "secret words" not in str(payload)
    assert payload["url"] == f"/chat?room={room}"
    unread = lambda: fresh(me).execute(text("SELECT unread_count FROM chat_unread u JOIN chat_conversations c ON c.id = u.conversation_id "
                                             "WHERE c.matrix_room_id = :r AND u.user_id = :t"), {"r": room, "t": them.id}).scalar()
    assert unread() == 1
    assert _webhook(real_app, txn, [event]).status_code == 200                 # the chat server sending it again
    assert unread() == 1 and len(push_on) == 1

    # a block either way: nobody is told
    db = fresh(me)
    db.execute(text("INSERT INTO userblocks (user_id, target_id, created_utc) VALUES (:a, :b, :n)"), {"a": them.id, "b": me.id, "n": int(time.time())})
    db.commit()
    assert _webhook(real_app, secrets.token_hex(6), [{**event, "event_id": "$" + secrets.token_hex(8)}]).status_code == 200
    assert len(push_on) == 1


def test_a_group_member_is_told_by_their_own_choice(make_member, real_app, push_on):
    if not real_app[0].config.get("MATRIX_HS_TOKEN"):
        pytest.skip("no appservice token here")
    me, every, calls, nothing = make_member(), make_member(), make_member(), make_member()
    db = fresh(me)
    now = int(time.time())
    room = f"!g{secrets.token_hex(8)}"
    gid = db.execute(text("INSERT INTO chat_groups (matrix_room_id, created_by_id, created_utc) VALUES (:r, :o, :n) RETURNING id"),
                     {"r": room, "o": me.id, "n": now}).scalar()
    for m in (me, every, calls, nothing):
        db.execute(text("INSERT INTO chat_group_members (group_id, user_id, role, status, invited_by_id, invited_utc) VALUES (:g, :u, 'member', 'joined', :o, :n)"),
                   {"g": gid, "u": m.id, "o": me.id, "n": now})
        db.execute(text("INSERT INTO chat_identities (user_id, matrix_user_id, provisioned_utc) VALUES (:u, :m, :n) ON CONFLICT DO NOTHING"),
                   {"u": m.id, "m": f"@ruqqus_{m.id}:ruqqus.local", "n": now})
    db.commit()
    for m, mode in ((every, 0), (calls, 1), (nothing, 2)):
        assert _subscribe(m).status_code == 200
        here = m.browser()
        assert post(here, f"/api/chat/groups/{gid}/push", {"mode": str(mode), "formkey": m.formkey(here)}).status_code == 200
    event = {"type": "m.room.encrypted", "room_id": room, "sender": f"@ruqqus_{me.id}:ruqqus.local", "event_id": "$" + secrets.token_hex(8),
             "content": {"algorithm": "m.megolm.v1.aes-sha2", "ciphertext": "x"}}
    assert _webhook(real_app, secrets.token_hex(6), [event]).status_code == 200
    told = {row[0] for row in fresh(me).execute(text("SELECT user_id FROM push_subscriptions WHERE endpoint = ANY(:e)"),
                                                {"e": [s["endpoint"] for s, _ in push_on]}).fetchall()}
    assert told == {every.id}


def test_the_service_worker_is_served_from_the_sites_root(real_app):
    app, _ = real_app
    answer = app.test_client().get("/sw.js", base_url="http://localhost")
    assert answer.status_code == 200 and answer.headers["Service-Worker-Allowed"] == "/"
    assert answer.headers["Content-Type"].startswith("application/javascript") and b"showNotification" in answer.data
    assert b"addEventListener(\"fetch\"" not in answer.data                    # it never sees a page
