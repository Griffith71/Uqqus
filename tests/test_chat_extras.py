"""Chat extras (CLAUDE.md "Chat extras"): pins and the disappearing-message timer in 1:1 chats and groups (who may change
them, the pinned list, the timer's choices; helpers/chat_extras.py), the messages waiting to disappear and their files
(helpers/chat_disappear.py, on SQLite from the migration), "Seen" turned off, screen sharing in calls, and the wiring."""
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from ruqqus.helpers import chat_disappear, chat_extras, device_access

ROOT = Path(__file__).resolve().parent.parent
NOW = 2_000_000_000


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


# --- the rules --------------------------------------------------------------------------------------------------------

def test_either_person_of_an_accepted_chat_and_a_groups_owner_and_admins_change_it():
    assert chat_extras.change_refusal(("pair", "inbox")) is None
    assert chat_extras.change_refusal(("pair", "request"))                       # a request can't be changed
    assert chat_extras.change_refusal(("group", "owner", "joined")) is None
    assert chat_extras.change_refusal(("group", "admin", "joined")) is None
    assert chat_extras.change_refusal(("group", "member", "joined"))
    assert chat_extras.change_refusal(("group", "admin", "invited"))            # not joined yet
    assert chat_extras.change_refusal(None) is None                             # the route answers a 404 first


def test_the_pinned_list_is_newest_first_once_each_and_never_more_than_ten():
    a, b = "$" + "a" * 20, "$" + "b" * 20
    assert chat_extras.pins_after([a], b, True) == [b, a]
    assert chat_extras.pins_after([b, a], b, True) == [b, a]                    # pinned again: moved to the top, not twice
    assert chat_extras.pins_after([b, a], b, False) == [a]
    assert chat_extras.pins_after(["junk", 5, a], b, True) == [b, a]            # what isn't an event id is dropped
    full = [f"${n:020d}" for n in range(chat_extras.MAX_PINS)]
    with pytest.raises(chat_extras.Refused):
        chat_extras.pins_after(full, b, True)
    assert chat_extras.pins_after(full, full[0], False) == full[1:]


def test_the_timer_is_off_a_day_or_a_week_and_nothing_else():
    assert [chat_extras.seconds_of(x) for x in ("0", "86400", "604800")] == [0, 86400, 604800]
    for bad in ("3600", "-1", "", None, "1e5", "86400.0"):
        assert chat_extras.seconds_of(bad) is None, bad


# --- the SQL ----------------------------------------------------------------------------------------------------------

@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE users (id integer PRIMARY KEY)"))
        conn.execute(text("CREATE TABLE chat_conversations (id integer PRIMARY KEY, user_a_id integer, user_b_id integer, "
                          "matrix_room_id text, status text)"))
        conn.execute(text("CREATE TABLE chat_groups (id integer PRIMARY KEY, matrix_room_id text, deleted_utc integer DEFAULT 0)"))
        conn.execute(text("CREATE TABLE chat_group_members (group_id integer, user_id integer, role text, status text)"))
        conn.execute(text("CREATE TABLE media_assets (id integer PRIMARY KEY, kind text, status text, updated_utc integer DEFAULT 0)"))
        sql = read("scripts", "migrations", "2026-12-29_chat_extras.sql")
        sql = "\n".join(line for line in sql.splitlines() if not line.strip().startswith("--"))
        for statement in (s.strip() for s in sql.split(";") if s.strip()):
            if "public.users" in statement:
                continue
            conn.execute(text(statement.replace("public.", "").replace("IF NOT EXISTS ", "")
                              .replace("SERIAL PRIMARY KEY", "INTEGER PRIMARY KEY AUTOINCREMENT")))
        conn.execute(text("INSERT INTO chat_conversations (id, user_a_id, user_b_id, matrix_room_id, status) VALUES (1, 1, 2, '!pair', 'inbox')"))
        conn.execute(text("INSERT INTO chat_groups (id, matrix_room_id) VALUES (7, '!group')"))
        conn.execute(text("INSERT INTO chat_group_members VALUES (7, 1, 'owner', 'joined'), (7, 3, 'member', 'joined')"))
    with Session(engine) as session:
        yield session


def test_who_stands_where_and_the_timer_is_kept_on_the_chat(db):
    assert chat_extras.standing(db, "!pair", 2) == ("pair", "inbox")
    assert chat_extras.standing(db, "!pair", 3) is None
    assert chat_extras.standing(db, "!group", 3) == ("group", "member", "joined")
    assert chat_extras.standing(db, "!nowhere", 1) is None
    assert chat_extras.timer_of(db, "!pair") == 0 and chat_extras.timer_of(db, "!nowhere") is None
    chat_extras.set_timer(db, "!group", 86400)
    assert chat_extras.timer_of(db, "!group") == 86400 and chat_extras.timer_of(db, "!pair") == 0


def test_a_message_is_noted_once_and_deleted_when_due_and_a_failure_is_tried_again(db):
    chat_disappear.note(db, "!pair", "$one", NOW, 86400)
    chat_disappear.note(db, "!pair", "$one", NOW, 86400)                       # the webhook saw the transaction again
    chat_disappear.note(db, "!pair", "$two", NOW, 86400)
    chat_disappear.note(db, "!pair", "$off", NOW, 0)                           # the timer off: nothing
    assert len(chat_disappear.due(db, NOW + 86400)) == 2 and chat_disappear.due(db, NOW + 86399) == []
    deleted = []

    def redact(room, event):
        if event == "$two":
            raise RuntimeError("chat server down")
        deleted.append((room, event))

    assert chat_disappear.run_due(db, NOW + 86400, redact) == (1, 1)
    assert deleted == [("!pair", "$one")]
    assert [r[2] for r in chat_disappear.due(db, NOW + 86400)] == ["$two"]       # tried again next time
    db.execute(text("UPDATE chat_expiring SET tries = :n"), {"n": chat_disappear.GIVE_UP - 1})
    assert chat_disappear.run_due(db, NOW + 86400, redact) == (0, 1)
    assert chat_disappear.due(db, NOW + 86400) == []                           # given up on


def test_a_file_sent_while_the_timer_is_on_stops_being_served_after_it(db):
    assert chat_disappear.file_expiry(NOW, 0) == 0
    assert chat_disappear.file_expiry(NOW, 86400) == NOW + 86400 + chat_disappear.FILE_GRACE
    db.execute(text("INSERT INTO media_assets (id, kind, status, expires_utc) VALUES "
                    "(1, 'chat', 'ready', :due), (2, 'chat', 'ready', :later), (3, 'chat', 'ready', 0), (4, 'image', 'ready', :due)"),
               {"due": NOW, "later": NOW + 10})
    chat_disappear.run_due(db, NOW, lambda room, event: None)
    states = dict(db.execute(text("SELECT id, status FROM media_assets")).fetchall())
    assert states == {1: "removed", 2: "ready", 3: "ready", 4: "ready"}


# --- screen sharing ---------------------------------------------------------------------------------------------------

def test_only_the_call_and_space_pages_may_capture_the_screen():
    assert "display-capture=(self)" in device_access.permissions_policy("/call/!abc")
    assert "display-capture=(self)" in device_access.permissions_policy("/space/12")
    for path in ("/chat", "/", "/settings/chat"):
        assert "display-capture=()" in device_access.permissions_policy(path), path


def test_a_screen_takes_the_cameras_place_and_the_camera_comes_back():
    media = read("ruqqus", "assets", "chat_src", "src", "rtc", "media.js")
    share = media.split("async shareScreen(", 1)[1].split("\n  stopScreen()", 1)[0]
    assert "if (!this.videoTrack) throw new MediaRefused" in share            # only someone already on video
    assert "getDisplayMedia" in share and "audio: false" in share
    assert 'this.changed("video", track)' in share and '"ended"' in share
    stop = media.split("\n  stopScreen()", 1)[1].split("\n  stop()", 1)[0]
    assert 'this.changed("video", this.camera)' in stop
    call = read("ruqqus", "assets", "chat_src", "src", "call.js")
    assert 'byId("call-screen")' in call and "shareScreen(" in call
    assert 'id="call-screen"' in read("ruqqus", "templates", "call.html")


# --- the wiring -------------------------------------------------------------------------------------------------------

def test_the_webhook_notes_what_disappears_and_the_cycle_deletes_it():
    chat = read("ruqqus", "routes", "chat.py")
    hook = chat.split("def matrix_appservice_transaction", 1)[1]
    assert "_note_disappearing(event)" in hook
    noting = chat.split("def _note_disappearing(event):", 1)[1].split("\n\n\n", 1)[0]
    assert '"state_key" in event' in noting and "chat_disappear.note(" in noting
    recomputes = read("scripts", "recomputes.py")
    assert "chat_disappear.run_due(" in recomputes and "redact_as_bot(" in recomputes
    assert "redaction_retention_period: 1d" in read("synapse", "homeserver.yaml")
    media = read("ruqqus", "routes", "media.py")
    assert "chat_disappear.file_expiry(" in media and "asset.expires_utc <= int(time.time())" in media


def test_the_routes_need_an_account_the_form_key_and_write_through_the_bot():
    routes = read("ruqqus", "routes", "chat_extras_pages.py")
    for name in ("chat_pin", "chat_disappear"):
        head = routes.split(f"def {name}(v):", 1)[0].rsplit("@app.route", 1)[1]
        for wrapper in ("@auth_required", "@not_qr_limited", '@not_child_locked("chat")', "@validate_formkey"):
            assert wrapper in head, (name, wrapper)
    assert "matrix.set_room_state(room_id, chat_extras.PINNED" in routes
    assert "matrix.set_room_state(room_id, chat_extras.DISAPPEAR" in routes
    assert "from .chat_extras_pages import *" in read("ruqqus", "routes", "__init__.py")


def test_seen_off_sends_a_private_receipt_and_no_typing_and_shows_neither():
    index = read("ruqqus", "assets", "chat_src", "src", "index.js")
    assert "boot.receipts === false ? ReceiptType.ReadPrivate : ReceiptType.Read" in index
    assert 'if (boot.receipts === false) return;                                                 // "Seen" off: never told' in index
    assert "const lastMine = boot.receipts === false ? null" in index
    assert "receipts: {{ 'false' if v.chat_receipts is false else 'true' }}" in read("ruqqus", "templates", "chat.html")
    assert 'action="/settings/chat/receipts"' in read("ruqqus", "templates", "settings_chat.html")


def test_a_forward_says_forwarded_never_who_and_a_disappearing_message_isnt_forwarded():
    extras = read("ruqqus", "assets", "chat_src", "src", "room_extras.js")
    forwarded = extras.split("export function forwardedText(content) {", 1)[1].split("\n}", 1)[0]
    assert '"org.uqqus.forwarded": true' in forwarded and "sender" not in forwarded and "m.relates_to" not in forwarded
    index = read("ruqqus", "assets", "chat_src", "src", "index.js")
    assert "!timerAt(room, event)" in index.split("forwardable:", 1)[1].split("forwarded:", 1)[0]
    assert "fileAgain(attachment)" in index                                     # a file is encrypted afresh for the new chat
