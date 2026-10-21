"""Broadcast channels: the rules (ruqqus/helpers/broadcasts.py) and the SQL (helpers/broadcast_store.py on SQLite).

The promises: an account has at most MAX_PER_OWNER channels and never as a bot; a channel for circles needs circles; a
block or not being in its circles reads the same ("isn't available"); only the owner and helpers write, only the owner
chooses helpers; a public channel's messages are counted per day; unread counts skip the writer and anyone who muted
the owner; a report is once per person and the queue never says who reported."""
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from ruqqus.helpers import broadcasts as rules
from ruqqus.helpers import broadcast_store as store

NOW = 2_000_000_000
OWNER, BOB, CAT, DAN = 1, 2, 3, 4


# --- rules ----------------------------------------------------------------------------------------------------

def test_names():
    assert rules.clean_name("  News   today ") == "News today"
    for bad in ("", "  ", "<b>", "@all"):
        with pytest.raises(rules.Refused):
            rules.clean_name(bad)


def test_who_can_make_a_channel():
    assert rules.make_refusal(False, 0, rules.PUBLIC, []) is None
    assert rules.make_refusal(False, 0, rules.CIRCLES, [5]) is None
    assert rules.make_refusal(True, 0, rules.PUBLIC, []) == "Bots can't have channels."
    assert rules.make_refusal(False, rules.MAX_PER_OWNER, rules.PUBLIC, []).startswith("You can have at most")
    assert rules.make_refusal(False, 0, rules.CIRCLES, []) == "Choose at least one of your circles."
    assert rules.make_refusal(False, 0, 3, []) == "Choose who can join."           # a Circle guild's audience is not one


def test_joining_never_says_why():
    assert rules.join_refusal(False, False, True, 10, rules.PUBLIC) is None
    for deleted, blocked, may_see in ((True, False, True), (False, True, True), (False, False, False)):
        assert rules.join_refusal(deleted, blocked, may_see, 0, rules.CIRCLES) == rules.UNAVAILABLE
    assert rules.join_refusal(False, False, True, rules.MAX_CIRCLE_MEMBERS, rules.CIRCLES) == "This channel is full."
    assert rules.join_refusal(False, False, True, rules.MAX_CIRCLE_MEMBERS, rules.PUBLIC) is None


def test_who_writes_and_who_chooses_helpers():
    assert rules.may_write(rules.OWNER) and rules.may_write(rules.HELPER) and not rules.may_write(rules.MEMBER)
    assert rules.helper_refusal(rules.OWNER, rules.MEMBER, 0, True) is None
    assert rules.helper_refusal(rules.OWNER, rules.HELPER, 0, False) is None
    assert rules.helper_refusal(rules.HELPER, rules.MEMBER, 0, True) == "Only the channel's owner chooses its helpers."
    assert rules.helper_refusal(rules.OWNER, None, 0, True) == "They need to join the channel first."
    assert rules.helper_refusal(rules.OWNER, rules.HELPER, 0, True) == "They're already a helper."
    assert rules.helper_refusal(rules.OWNER, rules.MEMBER, rules.MAX_HELPERS, True).startswith("A channel can have at most")


def test_messages():
    assert rules.clean_message("  hi  ") == "hi"
    with pytest.raises(rules.Refused):
        rules.clean_message("   ")
    with pytest.raises(rules.Refused):
        rules.clean_message("x" * (rules.MESSAGE_MAX + 1))
    assert rules.post_refusal(rules.OWNER, False, 0) is None
    assert rules.post_refusal(rules.MEMBER, False, 0) == "Only the channel's owner and helpers write in it."
    assert rules.post_refusal(rules.HELPER, True, 0) == "Your account is suspended."
    assert rules.post_refusal(rules.OWNER, False, rules.POSTS_PER_DAY).startswith("A channel can have at most")


# --- the SQL --------------------------------------------------------------------------------------------------

def _tables():
    from pathlib import Path
    sql = (Path(__file__).resolve().parent.parent / "scripts" / "migrations" / "2026-10-25_broadcasts.sql").read_text(encoding="utf-8")
    sql = "\n".join(line for line in sql.splitlines() if not line.strip().startswith("--"))
    for s in (s.strip() for s in sql.split(";") if "CREATE" in s):
        yield s.replace("public.", "").replace("IF NOT EXISTS ", "").replace("SERIAL PRIMARY KEY", "INTEGER PRIMARY KEY AUTOINCREMENT")


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        for statement in _tables():
            conn.execute(text(statement))
    with Session(engine) as session:
        yield session


def _channel(db, audience=rules.PUBLIC, circles=(), room="!news"):
    return store.create(db, OWNER, "News", 0, audience, room, list(circles), NOW)


def test_a_channel_and_its_owner(db):
    bid = _channel(db, rules.CIRCLES, (7, 8))
    row = store.get(db, bid)
    assert (row[1], row[2], row[4], row[5]) == (OWNER, "News", rules.CIRCLES, "!news")
    assert store.circle_ids(db, bid) == (7, 8)
    assert store.member(db, bid, OWNER)[0] == "owner" and store.size(db, bid) == 1
    assert store.by_room(db, "!news")[0] == bid and store.owned_count(db, OWNER) == 1
    store.close(db, bid, NOW + 1)
    assert store.get(db, bid) is None and store.by_room(db, "!news") is None and store.owned_count(db, OWNER) == 0


def test_members_helpers_and_unread(db):
    bid = _channel(db)
    store.add_member(db, bid, BOB, rules.MEMBER, NOW)
    store.add_member(db, bid, CAT, rules.MEMBER, NOW)
    store.add_member(db, bid, CAT, rules.HELPER, NOW)        # twice is once: the first role stays
    assert store.member(db, bid, CAT)[0] == "member"
    store.set_role(db, bid, CAT, rules.HELPER)
    assert store.helpers(db, bid) == [CAT]
    store.note_message(db, bid, OWNER)
    store.note_message(db, bid, CAT, muted_owner_ids=[BOB])   # BOB muted the owner: not counted for him
    assert store.member(db, bid, BOB)[1] == 1 and store.member(db, bid, CAT)[1] == 1 and store.member(db, bid, OWNER)[1] == 1
    assert store.unread_total(db, BOB) == 1
    store.mark_read(db, bid, BOB, NOW + 5)
    assert store.unread_total(db, BOB) == 0
    store.remove_member(db, bid, BOB)
    assert store.member(db, bid, BOB) is None and sorted(store.member_ids(db, bid)) == [OWNER, CAT]
    assert [row[0] for row in store.joined_by(db, CAT)] == [bid]


def test_a_public_channels_messages_and_reports(db):
    bid = _channel(db)
    store.record_post(db, bid, OWNER, "$e1", "hello", 0, NOW)
    store.record_post(db, bid, OWNER, "$e2", "rude", 1, NOW + 10)
    assert store.posts_since(db, bid, NOW) == 2 and store.posts_since(db, bid, NOW + 5) == 1
    post = store.post_by_event(db, bid, "$e2")
    assert post[1:] == (OWNER, "rude", 0)
    assert store.post_by_event(db, bid, "$nope") is None
    assert store.report(db, bid, post[0], BOB, "rude", NOW) and not store.report(db, bid, post[0], BOB, "again", NOW)
    assert store.report(db, bid, post[0], CAT, "", NOW + 1)
    queue = store.open_reports(db)
    assert queue == [(bid, post[0], "rude", "$e2", 2, ["rude"])]       # never who
    store.remove_post(db, post[0], NOW + 20)
    store.resolve_reports(db, post[0], NOW + 20)
    assert store.post_by_event(db, bid, "$e2")[3] == NOW + 20 and store.open_reports(db) == []


def test_the_database_refuses_an_unknown_audience_or_role(db):
    with pytest.raises(Exception):
        store.create(db, OWNER, "Bad", 0, 3, "!bad", [], NOW)
    db.rollback()
    bid = _channel(db)
    with pytest.raises(Exception):
        store.add_member(db, bid, DAN, "king", NOW)


def test_channels_from_people_you_follow_are_public_ones_you_are_not_in(db):
    public = _channel(db, room="!pub")
    joined = _channel(db, room="!joined")
    store.add_member(db, joined, BOB, "member", NOW)
    _channel(db, rules.CIRCLES, (7,), room="!circle")                       # never: it is for circles
    closed = _channel(db, room="!closed")
    store.close(db, closed, NOW + 1)
    found = [row[0] for row in store.public_of_owners(db, [OWNER], BOB)]
    assert found == [public]
    assert store.public_of_owners(db, [], BOB) == [] and store.public_of_owners(db, [CAT], BOB) == []


def test_the_chat_search_finds_public_channels_you_are_not_in_by_name(db):
    news = _channel(db, room="!news1")                              # "News"
    joined = _channel(db, room="!news2")
    store.add_member(db, joined, BOB, "member", NOW)
    _channel(db, rules.CIRCLES, (7,), room="!news3")                 # for circles: never in a search
    db.execute(text("UPDATE broadcasts SET name = '100% real' WHERE matrix_room_id = '!news1'"))
    assert [r[0] for r in store.search_public(db, "real", BOB)] == [news]
    assert [r[0] for r in store.search_public(db, "100%", BOB)] == [news]
    assert store.search_public(db, "_", BOB) == []                   # a wildcard is only itself: no name holds "_"
    assert store.search_public(db, "NEWS", BOB) == []                 # the one left is joined or for circles
    assert store.like_pattern("a_b%") == "%a\_b\%%"
