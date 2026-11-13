"""Files in end-to-end encrypted chats on the real app (CLAUDE.md "Files in chats"), with the stand-in test storage
(helpers/media/dev.py): a member uploads encrypted bytes into a chat they are in, they are kept in their own storage
with that chat, only its members fetch them (never cached, always a download), a chat file is never part of a post
whoever pastes its address, and a chat they aren't in, a limited sign-in or too big a file is refused."""
import secrets
import time

import pytest
from sqlalchemy import text

from .test_characterization import fresh, post
from .test_comment_reply_render import a_post


@pytest.fixture
def storage(monkeypatch, tmp_path):
    monkeypatch.setenv("MEDIA_DEV_PROVIDER", "1")
    monkeypatch.setenv("MEDIA_DEV_DIR", str(tmp_path))


def _pair(db, a, b):
    room = f"!f{secrets.token_hex(8)}"
    now = int(time.time())
    db.execute(text("INSERT INTO chat_conversations (user_a_id, user_b_id, initiator_id, matrix_room_id, status, created_utc, "
                    "last_activity_utc) VALUES (:a, :b, :a, :r, 'inbox', :n, :n)"), {"a": a.id, "b": b.id, "r": room, "n": now})
    db.commit()
    return room


def _send(member, here, room, data):
    """The browser's steps: link the test storage, start the upload, send the bytes, finish. Answers the asset's json."""
    assert post(here, "/settings/media/dev/enable?json=1", {"formkey": member.formkey(here)}).status_code == 200
    begun = post(here, "/api/media/uploads", {"kind": "chat", "room": room, "filename": "file.bin", "size": str(len(data)),
                                               "formkey": member.formkey(here)})
    if begun.status_code != 200:
        return begun
    put = here.put(begun.get_json()["upload"]["url"], base_url="http://localhost", data=data)
    assert put.status_code in (200, 201, 204), put.get_data(as_text=True)[:200]
    return post(here, f"/api/media/uploads/{begun.get_json()['id']}/complete", {"formkey": member.formkey(here)})


def test_a_chat_file_is_fetched_by_its_chats_members_alone(make_member, storage):
    me, them, stranger = make_member(), make_member(), make_member()
    room = _pair(fresh(me), me, them)
    data = secrets.token_bytes(3000)                    # what the browser encrypted: bytes nobody here can read
    here = me.browser()
    done = _send(me, here, room, data)
    assert done.status_code == 200, done.get_data(as_text=True)[:300]
    path = done.get_json()["path"]
    assert path.endswith(".bin")
    kept = fresh(me).execute(text("SELECT kind, chat_room_id, status FROM media_assets WHERE user_id = :u ORDER BY id DESC LIMIT 1"),
                             {"u": me.id}).one()
    assert tuple(kept) == ("chat", room, "ready")

    got = them.browser().get(path, base_url="http://localhost")
    assert got.status_code == 200 and got.data == data
    assert got.headers["Content-Type"] == "application/octet-stream"
    assert got.headers["Cache-Control"] == "private, no-store" and got.headers["Content-Disposition"] == "attachment"
    assert me.browser().get(path, base_url="http://localhost").status_code == 200            # its sender
    assert stranger.browser().get(path, base_url="http://localhost").status_code == 404
    assert real_visitor(me).get(path, base_url="http://localhost").status_code == 404

    # pasted into a post, it stays a chat's file: never attached, never shown to anyone else
    post_id = a_post(me, here)
    answer = post(here, "/api/comment", {"parent_fullname": f"t2_{post_id}", "submission": post_id, "formkey": me.formkey(here),
                                         "body": f"look ![]({path})"})
    assert answer.status_code == 200
    attached = fresh(me).execute(text("SELECT submission_id, comment_id FROM media_assets WHERE chat_room_id = :r"), {"r": room}).one()
    assert tuple(attached) == (None, None)
    assert stranger.browser().get(path, base_url="http://localhost").status_code == 404


def test_a_chat_the_member_isnt_in_and_too_big_a_file_are_refused(make_member, storage):
    me, them, other = make_member(), make_member(), make_member()
    theirs = _pair(fresh(them), them, other)
    here = me.browser()
    refused = _send(me, here, theirs, b"x" * 10)
    assert refused.status_code == 404
    mine = _pair(fresh(me), me, them)
    from ruqqus.helpers.media import rules
    big = post(here, "/api/media/uploads", {"kind": "chat", "room": mine, "filename": "file.bin", "size": str(rules.CHAT_SIZE_MAX + 1),
                                            "formkey": me.formkey(here)})
    assert big.status_code == 413


def real_visitor(member):
    return member.app.test_client()
