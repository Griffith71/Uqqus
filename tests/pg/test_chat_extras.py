"""Chat extras on the real app (CLAUDE.md "Chat extras"), the chat server faked: pinning and the disappearing-message timer
are written as room state through the bot, only by whoever may change them (either person of an accepted 1:1 chat, a
group's owner and admins; nobody outside it is told it exists); the webhook notes each message sent while the timer is on
and the cycle's step has the bot delete it when due; a chat file sent then stops being served; and "Seen" is a setting."""
import secrets
import time

import pytest
from sqlalchemy import text

from .test_characterization import fresh, post
from .test_push import _pair, _webhook


@pytest.fixture
def bot(monkeypatch):
    """The bot's room state and the events it can read, kept here."""
    import ruqqus.helpers.matrix_client as matrix
    state, events = {}, {}

    def set_room_state(room, kind, content, state_key=""):
        state[(room, kind)] = content
        return f"${secrets.token_hex(8)}"

    monkeypatch.setattr(matrix, "room_state", lambda room, kind, state_key="": dict(state.get((room, kind), {})))
    monkeypatch.setattr(matrix, "set_room_state", set_room_state)
    monkeypatch.setattr(matrix, "event", lambda room, eid: events.get(eid))
    return state, events


def _group(db, owner, member, member_role="member"):
    room = f"!g{secrets.token_hex(8)}"
    now = int(time.time())
    gid = db.execute(text("INSERT INTO chat_groups (matrix_room_id, created_by_id, created_utc) VALUES (:r, :o, :n) RETURNING id"),
                     {"r": room, "o": owner.id, "n": now}).scalar()
    for user, role in ((owner, "owner"), (member, member_role)):
        db.execute(text("INSERT INTO chat_group_members (group_id, user_id, role, status, joined_utc) VALUES (:g, :u, :role, 'joined', :n)"),
                   {"g": gid, "u": user.id, "role": role, "n": now})
    db.commit()
    return room


def test_either_person_pins_and_the_list_is_the_bots_room_state(make_member, bot):
    state, events = bot
    a, b, stranger = make_member(), make_member(), make_member()
    room = _pair(fresh(a), a, b)
    eid = "$" + secrets.token_hex(12)
    events[eid] = {"event_id": eid, "room_id": room, "type": "m.room.encrypted", "sender": f"@ruqqus_{a.id}:x"}
    here = b.browser()
    answer = post(here, "/api/chat/pin", {"room": room, "event_id": eid, "formkey": b.formkey(here)})
    assert answer.status_code == 200 and answer.get_json()["pinned"] == [eid]
    assert state[(room, "m.room.pinned_events")] == {"pinned": [eid]}
    assert post(here, "/api/chat/pin", {"room": room, "event_id": "$" + "9" * 20, "formkey": b.formkey(here)}).status_code == 404
    there = stranger.browser()
    assert post(there, "/api/chat/pin", {"room": room, "event_id": eid, "formkey": stranger.formkey(there)}).status_code == 404
    out = post(here, "/api/chat/pin", {"room": room, "event_id": eid, "pin": "0", "formkey": b.formkey(here)})
    assert out.status_code == 200 and state[(room, "m.room.pinned_events")] == {"pinned": []}


def test_a_request_and_a_groups_member_cant_change_it(make_member, bot):
    a, b = make_member(), make_member()
    db = fresh(a)
    room = _pair(db, a, b)
    db.execute(text("UPDATE chat_conversations SET status = 'request' WHERE matrix_room_id = :r"), {"r": room})
    db.commit()
    here = a.browser()
    assert post(here, "/api/chat/disappear", {"room": room, "seconds": "86400", "formkey": a.formkey(here)}).status_code == 403
    owner, member = make_member(), make_member()
    groom = _group(fresh(owner), owner, member)
    mine = member.browser()
    assert post(mine, "/api/chat/disappear", {"room": groom, "seconds": "86400", "formkey": member.formkey(mine)}).status_code == 403
    theirs = owner.browser()
    assert post(theirs, "/api/chat/disappear", {"room": groom, "seconds": "3600", "formkey": owner.formkey(theirs)}).status_code == 400
    assert post(theirs, "/api/chat/disappear", {"room": groom, "seconds": "86400", "formkey": owner.formkey(theirs)}).status_code == 200


def test_with_the_timer_on_each_message_is_noted_deleted_when_due_and_its_file_stops(make_member, real_app, bot, monkeypatch):
    if not real_app[0].config.get("MATRIX_HS_TOKEN"):
        pytest.skip("no appservice token in this environment")
    state, _ = bot
    a, b = make_member(), make_member()
    room = _pair(fresh(a), a, b)
    here = a.browser()
    assert post(here, "/api/chat/disappear", {"room": room, "seconds": "86400", "formkey": a.formkey(here)}).status_code == 200
    assert state[(room, "org.uqqus.disappear")]["seconds"] == 86400
    sent = int(time.time()) - 86400 - 5                        # sent a day and a bit ago
    eid = "$" + secrets.token_hex(12)
    event = {"type": "m.room.encrypted", "room_id": room, "event_id": eid, "sender": f"@ruqqus_{a.id}:ruqqus.local",
             "origin_server_ts": sent * 1000, "content": {"algorithm": "m.megolm.v1.aes-sha2", "ciphertext": "x"}}
    assert _webhook(real_app, "t" + secrets.token_hex(6), [event]).status_code == 200
    db = fresh(a)
    assert db.execute(text("SELECT expires_utc FROM chat_expiring WHERE event_id = :e"), {"e": eid}).scalar() == sent + 86400
    # a chat file uploaded while the timer is on: served until its time, then not (before the step even runs)
    asset = db.execute(text("""INSERT INTO media_assets (user_id, account_id, provider, kind, token, ext, status, created_utc,
                                                         updated_utc, chat_room_id, expires_utc)
                               VALUES (:u, 0, 'dev', 'chat', :t, 'bin', 'ready', :n, :n, :r, :x) RETURNING id"""),
                       {"u": a.id, "t": secrets.token_urlsafe(18), "n": sent, "r": room, "x": sent + 60}).scalar()
    token = db.execute(text("SELECT token FROM media_assets WHERE id = :i"), {"i": asset}).scalar()
    db.commit()
    from ruqqus.helpers.base36 import base36encode
    assert b.browser().get(f"/media/{base36encode(asset)}/{token}.bin", base_url="http://localhost").status_code == 410
    from ruqqus.helpers import chat_disappear
    deleted = []
    done, failed = chat_disappear.run_due(fresh(a), int(time.time()), lambda r, e: deleted.append((r, e)))
    assert (room, eid) in deleted and failed == 0
    after = fresh(a)
    assert after.execute(text("SELECT COUNT(*) FROM chat_expiring WHERE event_id = :e"), {"e": eid}).scalar() == 0
    assert after.execute(text("SELECT status FROM media_assets WHERE id = :i"), {"i": asset}).scalar() == "removed"


def test_seen_is_a_setting_the_chat_page_is_told(make_member):
    member = make_member()
    here = member.browser()
    assert post(here, "/settings/chat/receipts", {"on": "0", "formkey": member.formkey(here)}).status_code == 302
    assert fresh(member).execute(text("SELECT chat_receipts FROM users WHERE id = :u"), {"u": member.id}).scalar() is False
    page = here.get("/settings/chat", base_url="http://localhost").get_data(as_text=True)
    assert "Turn on Seen and typing" in page
    assert post(here, "/settings/chat/receipts", {"on": "1", "formkey": member.formkey(here)}).status_code == 302
    assert fresh(member).execute(text("SELECT chat_receipts FROM users WHERE id = :u"), {"u": member.id}).scalar() is True
