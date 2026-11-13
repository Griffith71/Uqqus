"""Reporting a message in a private chat on the real app (CLAUDE.md "Reporting in private chats"): only a member of the
chat reports, who sent each message and when come from the chat server (faked here) and never from the browser, a
message the server doesn't have is left out, the reported account is the reported message's sender, one report per
message, and admins read and close it."""
import json
import secrets
import time

import pytest
from sqlalchemy import text

from .test_characterization import fresh, post


def _mxid(member):
    return f"@ruqqus_{member.id}:ruqqus.local"


@pytest.fixture
def chat_server(monkeypatch):
    """The events the bot can see: {event_id: (room_id, sender mxid, sent ms)}."""
    import ruqqus.helpers.matrix_client as matrix
    events = {}

    def event(room_id, event_id):
        found = events.get(event_id)
        if not found or found[0] != room_id:
            return None
        return {"type": "m.room.encrypted", "room_id": room_id, "sender": found[1], "origin_server_ts": found[2]}

    monkeypatch.setattr(matrix, "event", event)
    return events


def _pair(db, a, b):
    room = f"!p{secrets.token_hex(8)}"
    now = int(time.time())
    db.execute(text("INSERT INTO chat_conversations (user_a_id, user_b_id, initiator_id, matrix_room_id, status, created_utc, "
                    "last_activity_utc) VALUES (:a, :b, :a, :r, 'inbox', :n, :n)"), {"a": a.id, "b": b.id, "r": room, "n": now})
    for m in (a, b):
        db.execute(text("INSERT INTO chat_identities (user_id, matrix_user_id, provisioned_utc) VALUES (:u, :m, :n) "
                        "ON CONFLICT DO NOTHING"), {"u": m.id, "m": _mxid(m), "n": now})
    db.commit()
    return room


def _report(member, room, event_id, messages, reason="spam"):
    here = member.browser()
    return post(here, "/api/chat/report", {"room": room, "event_id": event_id, "messages": json.dumps(messages), "reason": reason,
                                           "formkey": member.formkey(here)})


def test_a_member_reports_and_the_chat_server_says_who_and_when(make_member, chat_server):
    me, them, stranger = make_member(), make_member(), make_member()
    room = _pair(fresh(me), me, them)
    mine, bad, gone = "$" + secrets.token_hex(8), "$" + secrets.token_hex(8), "$" + secrets.token_hex(8)
    chat_server[mine] = (room, _mxid(me), 1_700_000_000_000)
    chat_server[bad] = (room, _mxid(them), 1_700_000_100_000)
    # the browser's copy: the words are its own; it can't say who wrote them (it names no sender at all)
    messages = [{"event_id": mine, "body": "hello"}, {"event_id": gone, "body": "deleted since"},
                {"event_id": bad, "body": "a nasty message"}]
    assert _report(stranger, room, bad, messages).status_code == 404                     # not in the chat
    answer = _report(me, room, bad, messages)
    assert answer.status_code == 200, answer.get_data(as_text=True)
    db = fresh(me)
    row = db.execute(text("SELECT id, reported_id, room_kind, reason, status FROM chat_reports WHERE reporter_id = :u"), {"u": me.id}).one()
    assert row[1:] == (them.id, "pair", "spam", "open")
    kept = db.execute(text("SELECT event_id, sender_id, sent_ms, body FROM chat_report_messages WHERE report_id = :r ORDER BY position"),
                      {"r": row[0]}).fetchall()
    assert [tuple(k) for k in kept] == [(mine, me.id, 1_700_000_000_000, "hello"), (bad, them.id, 1_700_000_100_000, "a nasty message")]

    again = _report(me, room, bad, messages)
    assert again.status_code == 409 and again.get_json()["error"] == "You've already reported this message."
    own = _report(me, room, mine, [{"event_id": mine, "body": "hello"}])
    assert own.status_code == 409 and own.get_json()["error"] == "You can't report your own message."
    missing = _report(me, room, gone, [{"event_id": gone, "body": "x"}])
    assert missing.status_code == 409 and missing.get_json()["error"] == "That message isn't in this chat any more."


def test_admins_read_it_and_close_it_once_and_the_page_never_says_who_reported(make_member, chat_server):
    me, them = make_member(), make_member()
    room = _pair(fresh(me), me, them)
    bad = "$" + secrets.token_hex(8)
    chat_server[bad] = (room, _mxid(them), 1_700_000_100_000)
    words = "a nasty message " + secrets.token_hex(6)
    assert _report(me, room, bad, [{"event_id": bad, "body": words}]).status_code == 200
    admin = make_member()
    db = fresh(admin)
    db.execute(text("UPDATE users SET admin_level = 3 WHERE id = :u"), {"u": admin.id})
    db.commit()
    rid = db.execute(text("SELECT id FROM chat_reports WHERE reporter_id = :u"), {"u": me.id}).scalar()
    there = admin.browser()
    page = there.get("/admin/chat_reports").get_data(as_text=True)
    assert words in page and f"@{them.username}" in page and "the words can't be checked" in page
    assert f"@{me.username}" not in page                                   # none of the reporter's messages here: nothing names them
    assert member_cant_open(me)
    closed = post(there, f"/admin/chat_reports/{rid}/dismiss", {"formkey": admin.formkey(there)})
    assert closed.status_code == 302
    assert fresh(admin).execute(text("SELECT status, resolved_by_id FROM chat_reports WHERE id = :r"), {"r": rid}).one() == ("dismissed", admin.id)
    assert words not in there.get("/admin/chat_reports").get_data(as_text=True)


def member_cant_open(member):
    return member.browser().get("/admin/chat_reports").status_code in (302, 401, 403, 404)
