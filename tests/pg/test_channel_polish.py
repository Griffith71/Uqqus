"""Channels, the polish of 2026-11-13, on the real app (CLAUDE.md "Broadcast channels" and "Guild channels"), with the chat
server faked: the directory of public channels (most people first, never one you're in or across a block), a picture in a
public channel (the writer's own upload, public while its message is up, taken down with it by an admin, never used
twice), @everyone in a guild channel (a guildmaster's alone, once an hour, to the people in its room) and the guild's
channels in its JSON (a member's, never a visitor's)."""
import io
import secrets
import time

import pytest
from sqlalchemy import text

from .test_characterization import fresh, post


@pytest.fixture
def chat(monkeypatch, tmp_path):
    """The chat server faked: every message it is asked to send gets an event id, every removal is noted."""
    import ruqqus.helpers.matrix_client as matrix
    sent, redacted = [], []

    def send_as(room, mxid, content, txn):
        sent.append((room, mxid, content))
        return f"${secrets.token_hex(8)}"

    monkeypatch.setattr(matrix, "send_as", send_as)
    monkeypatch.setattr(matrix, "redact_as_bot", lambda room, event, txn, reason=None: redacted.append((room, event)))
    monkeypatch.setattr(matrix, "put_in_room", lambda room, mxid: None)
    monkeypatch.setenv("MEDIA_DEV_PROVIDER", "1")
    monkeypatch.setenv("MEDIA_DEV_DIR", str(tmp_path))
    return sent, redacted


def _identity(db, member):
    db.execute(text("INSERT INTO chat_identities (user_id, matrix_user_id, provisioned_utc) VALUES (:u, :m, :n) ON CONFLICT DO NOTHING"),
               {"u": member.id, "m": f"@ruqqus_{member.id}:test", "n": int(time.time())})


def _public_channel(db, owner, name=None):
    from ruqqus.helpers import broadcast_store, broadcasts
    bid = broadcast_store.create(db, owner.id, name or "ci_chan_" + secrets.token_hex(3), 0, broadcasts.PUBLIC,
                                 f"!b{secrets.token_hex(8)}:test", [], int(time.time()))
    db.commit()
    return bid


# --- the directory -----------------------------------------------------------------------------------------------------

def test_the_directory_lists_public_channels_by_size_never_yours_or_across_a_block(make_member):
    from ruqqus.helpers import broadcast_store, broadcasts
    me, big_owner, small_owner, blocker = make_member(), make_member(), make_member(), make_member()
    db = fresh(me)
    db.execute(text("UPDATE broadcasts SET deleted_utc = 1 WHERE name LIKE 'ci_pop_%' AND deleted_utc = 0"))   # an earlier run's
    big, small = _public_channel(db, big_owner, "ci_pop_big"), _public_channel(db, small_owner, "ci_pop_small")
    walled, mine = _public_channel(db, blocker, "ci_pop_walled"), _public_channel(db, big_owner, "ci_pop_mine")
    now = int(time.time())
    for _ in range(4):
        broadcast_store.add_member(db, big, make_member().id, broadcasts.MEMBER, now)
        broadcast_store.add_member(db, walled, make_member().id, broadcasts.MEMBER, now)
    broadcast_store.add_member(db, small, make_member().id, broadcasts.MEMBER, now)
    broadcast_store.add_member(db, mine, me.id, broadcasts.MEMBER, now)
    db.execute(text("INSERT INTO userblocks (user_id, target_id, created_utc) VALUES (:b, :m, :n)"), {"b": blocker.id, "m": me.id, "n": now})
    db.commit()
    got = me.browser().get("/api/broadcasts/popular", base_url="http://localhost")
    assert got.status_code == 200
    ids = [c["channel_id"] for c in got.get_json()["channels"]]
    assert big in ids and small in ids and ids.index(big) < ids.index(small)
    assert walled not in ids and mine not in ids


# --- pictures in a public channel ----------------------------------------------------------------------------------------

def _picture(member, here):
    from PIL import Image
    raw = io.BytesIO()
    Image.new("RGB", (40, 30), (35, 169, 242)).save(raw, "PNG")
    data = raw.getvalue()
    assert post(here, "/settings/media/dev/enable?json=1", {"formkey": member.formkey(here)}).status_code == 200
    begun = post(here, "/api/media/uploads", {"kind": "image", "filename": "a.png", "size": str(len(data)), "formkey": member.formkey(here)})
    assert begun.status_code == 200, begun.get_data(as_text=True)[:300]
    put = here.put(begun.get_json()["upload"]["url"], base_url="http://localhost", data=data)
    assert put.status_code in (200, 201, 204)
    done = post(here, f"/api/media/uploads/{begun.get_json()['id']}/complete", {"formkey": member.formkey(here)})
    assert done.status_code == 200, done.get_data(as_text=True)[:300]
    return begun.get_json()["id"], done.get_json()["path"]


def test_a_channels_picture_is_public_while_its_message_is_up_and_an_admin_takes_both_down(make_member, chat):
    sent, redacted = chat
    owner, admin = make_member(), make_member()
    db = fresh(owner)
    bid = _public_channel(db, owner)
    db.execute(text("UPDATE users SET admin_level = 3 WHERE id = :a"), {"a": admin.id})
    db.commit()
    here = owner.browser()
    asset_id, path = _picture(owner, here)
    answer = post(here, f"/api/broadcasts/{bid}/messages", {"media": asset_id, "body": "", "formkey": owner.formkey(here)})
    assert answer.status_code == 200, answer.get_data(as_text=True)[:300]
    content = sent[-1][2]
    assert content["msgtype"] == "m.image" and content["org.uqqus.picture"] == path and content["org.uqqus.picture_only"] is True

    visitor = owner.app.test_client()
    got = visitor.get(path, base_url="http://localhost")
    assert got.status_code == 200 and "public" in got.headers["Cache-Control"]

    again = post(here, f"/api/broadcasts/{bid}/messages", {"media": asset_id, "body": "twice", "formkey": owner.formkey(here)})
    assert again.status_code == 400                                         # one picture, one message

    pid = fresh(owner).execute(text("SELECT id FROM broadcast_posts WHERE broadcast_id = :b"), {"b": bid}).scalar()
    desk = admin.browser()
    assert post(desk, f"/admin/broadcast_reports/{pid}/remove", {"formkey": admin.formkey(desk)}).status_code == 302
    assert redacted and fresh(owner).execute(text("SELECT status FROM media_assets WHERE id = :i"),
                                             {"i": int(asset_id, 36)}).scalar() == "removed"
    assert visitor.get(path, base_url="http://localhost").status_code == 410          # removed: gone for good


def test_another_members_picture_is_refused(make_member, chat):
    owner, other = make_member(), make_member()
    bid = _public_channel(fresh(owner), owner)
    asset_id, _ = _picture(other, other.browser())
    here = owner.browser()
    assert post(here, f"/api/broadcasts/{bid}/messages", {"media": asset_id, "formkey": owner.formkey(here)}).status_code == 400


# --- @everyone in a guild channel ------------------------------------------------------------------------------------------

@pytest.fixture
def guild_with_channel(make_member):
    """A guild with a text channel its guildmaster, a writer and two members have opened. Its guildmaster steps down
    afterwards: every test account shares one address, and guilds led from it count towards one network's limit
    (helpers/guild_limits.py), which a later test would otherwise meet."""
    made = _guild_with_channel(make_member)
    board_id = made[0].id
    yield made
    db = fresh(made[2])
    db.execute(text("UPDATE mods SET invite_rescinded = true, accepted = false WHERE board_id = :b"), {"b": board_id})
    db.commit()


def _guild_with_channel(make_member):
    from ruqqus.classes import Board, ModRelationship, Subscription
    from ruqqus.helpers import guild_channel_store as store
    mod, writer, a, b = make_member(), make_member(), make_member(), make_member()
    db = fresh(mod)
    board = Board(name="ci_ev_" + secrets.token_hex(3), description="d", description_html="<p>d</p>", creator_id=mod.id, subcat_id=0)
    db.add(board)
    db.flush()
    db.add(ModRelationship(user_id=mod.id, board_id=board.id, accepted=True, invite_rescinded=False, perm_full=True))
    now = int(time.time())
    for m in (writer, a, b):
        db.add(Subscription(user_id=m.id, board_id=board.id, is_active=True, created_utc=now))
    cid = store.create(db, board.id, "text", "general", "", "", 0, "members", "members", 0, f"!g{secrets.token_hex(8)}:test", now)
    for at, m in enumerate((mod, writer, a, b)):
        _identity(db, m)
        store.join(db, cid, m.id, now - 100 + at)
    db.commit()
    return board, cid, mod, writer, a, b


def _notices(member):
    return fresh(member).execute(text("SELECT COUNT(*) FROM notifications WHERE user_id = :u"), {"u": member.id}).scalar()


def test_everyone_is_a_guildmasters_once_an_hour_and_tells_the_people_in_the_room(guild_with_channel, chat):
    board, cid, mod, writer, a, b = guild_with_channel
    before = {m.id: _notices(m) for m in (mod, writer, a, b)}

    plain = writer.browser()
    assert post(plain, f"/api/guilds/channels/{cid}/messages", {"body": "hi @everyone", "formkey": writer.formkey(plain)}).status_code == 200
    assert _notices(a) == before[a.id], "from anyone else it is only text"

    desk = mod.browser()
    assert post(desk, f"/api/guilds/channels/{cid}/messages", {"body": "Meeting now @everyone", "formkey": mod.formkey(desk)}).status_code == 200
    for m in (writer, a, b):
        assert _notices(m) == before[m.id] + 1
    assert _notices(mod) == before[mod.id]
    mentioned = fresh(mod).execute(text("SELECT COUNT(*) FROM guild_channel_mentions WHERE channel_id = :c"), {"c": cid}).scalar()
    assert mentioned == 3

    assert post(desk, f"/api/guilds/channels/{cid}/messages", {"body": "again @everyone", "formkey": mod.formkey(desk)}).status_code == 200
    assert _notices(a) == before[a.id] + 1, "once an hour a channel"


# --- the guild's channels in its JSON --------------------------------------------------------------------------------------

def test_the_guild_json_lists_the_channels_a_member_may_read_and_none_for_a_visitor(guild_with_channel, real_app, chat):
    from flask import g
    from ruqqus.classes import Board, User
    board, cid, mod, writer, a, b = guild_with_channel
    app, db_session = real_app
    with app.test_request_context("/"):
        g.db = db_session()
        found = g.db.query(Board).filter_by(id=board.id).one()
        g.v = g.db.query(User).filter_by(id=a.id).one()
        assert found.json["channels"] == [{"id": cid, "label": "general", "kind": "text", "url": f"/+{board.name}/chat?c={cid}"}]
        g.v = None
        assert found.json["channels"] == []
