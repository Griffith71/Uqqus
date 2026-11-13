"""A group of 500 on the real app (CLAUDE.md "Group chats"): the chat list draws it from its first members and its counts,
unread is one counter for the group, a full group takes nobody more, and one add names at most 50 people. Nothing here
reaches Matrix: every refusal comes before the bot is asked, and the list never asks it."""
import secrets
import time

from sqlalchemy import text

from .test_characterization import fresh, post


def _group_of(db, owner, members, strangers):
    """A group with `owner`, the real `members` (joined) and `strangers` more joined ids no account holds, added later."""
    now = int(time.time())
    gid = db.execute(text("INSERT INTO chat_groups (matrix_room_id, created_by_id, created_utc) VALUES (:r, :o, :n) RETURNING id"),
                     {"r": f"!g{secrets.token_hex(6)}:chat.localhost", "o": owner.id, "n": now}).scalar()
    rows = [(owner.id, "owner", now)] + [(m.id, "member", now + 1 + i) for i, m in enumerate(members)]
    rows += [(900_000_000 + i, "member", now + 100 + i) for i in range(strangers)]
    db.execute(text("INSERT INTO chat_group_members (group_id, user_id, role, status, invited_by_id, invited_utc) "
                    "VALUES (:g, :u, :r, 'joined', :o, :n)"),
               [{"g": gid, "u": u, "r": role, "o": owner.id, "n": when} for u, role, when in rows])
    db.commit()
    return gid


def _listed(member, gid):
    here = member.browser()
    answer = here.get("/api/chat/conversations?status=group")
    assert answer.status_code == 200
    return next(c for c in answer.get_json()["conversations"] if c["group_id"] == gid)


def test_a_group_of_500_is_listed_from_its_first_members_and_counts_its_unread_once(make_member):
    owner = make_member()
    members = [make_member() for _ in range(4)]
    db = fresh(owner)
    gid = _group_of(db, owner, members, 495)
    entry = _listed(owner, gid)
    assert entry["size"] == 500
    assert len(entry["members"]) == 4                                           # the list's few, never all 500
    names = [m.username for m in members[:3]]
    assert entry["label"] == f"{', '.join(names)} and 496 others"             # 499 others, three of them named
    assert entry["max_members"] == 500

    room = db.execute(text("SELECT matrix_room_id FROM chat_groups WHERE id = :g"), {"g": gid}).scalar()
    from ruqqus.helpers import chat_group_store
    for _ in range(3):
        assert chat_group_store.note_message(db, room, members[0].id, int(time.time()))
    db.commit()
    assert db.execute(text("SELECT message_seq FROM chat_groups WHERE id = :g"), {"g": gid}).scalar() == 3
    assert _listed(owner, gid)["unread_count"] == 3 and _listed(members[0], gid)["unread_count"] == 0


def test_a_full_group_takes_nobody_more_and_one_add_names_at_most_50(make_member):
    owner, newcomer = make_member(), make_member()
    db = fresh(owner)
    gid = _group_of(db, owner, [], 499)
    here = owner.browser()
    full = post(here, f"/api/chat/groups/{gid}/add", {"usernames": newcomer.username, "formkey": owner.formkey(here)})
    assert full.status_code == 400 and "at most 500 people" in full.get_json()["error"]

    smaller = _group_of(fresh(owner), owner, [], 10)
    names = " ".join(f"nobody_{i}" for i in range(51))
    many = post(here, f"/api/chat/groups/{smaller}/add", {"usernames": names, "formkey": owner.formkey(here)})
    assert many.status_code == 400 and "at most 50 people at a time" in many.get_json()["error"]


def test_a_read_that_waited_on_the_members_own_message_never_takes_the_count_back(make_member):
    """Seen by the end-to-end check: the writer's page marked the group read while the webhook counted the writer's own
    message. The read waited on the webhook's lock on the same row and then wrote the count from before it (its subquery
    reads the statement's snapshot), so the writer's own message read as news. A last-read number only moves forward."""
    import gevent
    from sqlalchemy.orm import Session
    from ruqqus.helpers import chat_group_store, guild_channel_store

    owner, other = make_member(), make_member()
    db = fresh(owner)
    gid = _group_of(db, owner, [other], 0)
    room = db.execute(text("SELECT matrix_room_id FROM chat_groups WHERE id = :g"), {"g": gid}).scalar()
    engine = db.get_bind()
    webhook, page = Session(bind=engine), Session(bind=engine)
    assert chat_group_store.note_message(webhook, room, owner.id, int(time.time()))      # counted, not committed yet
    reading = gevent.spawn(lambda: (chat_group_store.mark_read(page, gid, owner.id, int(time.time())), page.commit()))
    gevent.sleep(0.5)                                   # the read is waiting on the webhook's lock on the owner's row
    assert not reading.dead
    webhook.commit()
    reading.join(timeout=10)
    assert reading.successful()
    seq, read = fresh(owner).execute(text("SELECT g.message_seq, m.last_read_seq FROM chat_groups g JOIN chat_group_members m "
                                          "ON m.group_id = g.id WHERE g.id = :g AND m.user_id = :u"), {"g": gid, "u": owner.id}).one()
    assert (seq, read) == (1, 1)
    webhook.close()
    page.close()
    assert "CASE WHEN" in guild_channel_store.mark_read.__code__.co_consts[1]   # guild channels read the same way
