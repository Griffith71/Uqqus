"""Calls in chats: the rules (ruqqus/helpers/calls.py), who is in a call (helpers/call_store.py on a Redis stand-in)
and the history (on SQLite). The promises: a request can't be called, a block or a bot ends it; a call has at most
AUDIO_MAX people and VIDEO_MAX sending video; a device not heard of for HEARTBEAT_TTL is out; a call ends once, and
its history row says only who started it, when, how long and how many."""
import json

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from fake_redis import FakeRedis
from ruqqus.helpers import calls as rules
from ruqqus.helpers import call_store as store

ROOM = "!abc"
NOW = 2_000_000_000


# --- rules ----------------------------------------------------------------------------------------------------

def test_who_may_call_in_a_one_to_one_chat():
    assert rules.pair_refusal("inbox", False, False, False) is None
    assert rules.pair_refusal("request", False, False, False) == rules.REQUEST_FIRST
    assert rules.pair_refusal("inbox", True, False, False) == rules.NOT_ALLOWED      # a block, either way
    assert rules.pair_refusal("inbox", False, True, False) == rules.NOT_ALLOWED      # a bot
    assert rules.pair_refusal("inbox", False, False, True) == rules.NOT_ALLOWED      # a deleted account


def test_how_many_can_join():
    assert rules.join_refusal(False, rules.AUDIO_MAX - 1, False, 0) is None
    assert rules.join_refusal(False, rules.AUDIO_MAX, False, 0) == rules.FULL
    assert rules.join_refusal(True, rules.AUDIO_MAX, False, 0) is None              # already in: a reload
    assert rules.join_refusal(False, 0, True, rules.STARTS_PER_HOUR) == rules.TOO_MANY_STARTS
    assert rules.join_refusal(False, 2, False, rules.STARTS_PER_HOUR) is None       # joining someone else's call


def test_who_sends_video():
    assert rules.video_allowed("video", 0, True)
    assert rules.video_allowed("video", rules.VIDEO_MAX - 1, True)
    assert not rules.video_allowed("video", rules.VIDEO_MAX, True)                  # the 5th joins on audio
    assert not rules.video_allowed("video", 0, False)                               # the relay's month at 80%
    assert not rules.video_allowed("audio", 0, True)
    assert rules.parse_kind("video") == "video" and rules.parse_kind("anything") == "audio"


def test_the_line_written_into_the_chat():
    assert rules.summary("audio", 9) == "Voice call · 9 s"
    assert rules.summary("video", 12 * 60 + 30) == "Video call · 12 min"
    assert rules.summary("audio", 2 * 3600 + 5 * 60) == "Voice call · 2 h 5 min"
    assert rules.summary("audio", -4) == "Voice call · 0 s"


# --- who is in a call ------------------------------------------------------------------------------------------

@pytest.fixture
def r():
    return FakeRedis()


def test_the_first_join_starts_the_call(r):
    call_id, started = store.join(r, ROOM, 1, "DEV1", "video", True, NOW)
    assert started and len(call_id) == 24
    again, started_again = store.join(r, ROOM, 2, "DEV2", "video", False, NOW + 1)
    assert again == call_id and not started_again
    meta = store.meta(r, ROOM)
    assert meta == {"call_id": call_id, "kind": "video", "started_by": 1, "started_utc": NOW}
    assert store.room_of(r, call_id) == ROOM
    assert [(e["user_id"], e["device_id"], e["video"]) for e in store.roster(r, ROOM, NOW + 2)] == [(1, "DEV1", True), (2, "DEV2", False)]


def test_a_device_not_heard_of_is_out(r):
    store.join(r, ROOM, 1, "DEV1", "audio", False, NOW)
    store.join(r, ROOM, 2, "DEV2", "audio", False, NOW)
    assert store.heartbeat(r, ROOM, 1, "DEV1", NOW + 20)
    later = NOW + rules.HEARTBEAT_TTL + 5
    assert [e["user_id"] for e in store.roster(r, ROOM, later)] == [1]        # 2 went quiet
    assert not store.heartbeat(r, ROOM, 2, "DEV2", later)
    assert not store.heartbeat(r, ROOM, 3, "DEV3", later)                    # never in it


def test_a_rejoin_keeps_its_place_and_leaving_takes_every_device(r):
    store.join(r, ROOM, 1, "DEV1", "audio", False, NOW)
    store.join(r, ROOM, 2, "DEV2", "audio", False, NOW + 5)
    store.join(r, ROOM, 1, "DEV1", "audio", False, NOW + 9)                 # a reload
    assert [e["user_id"] for e in store.roster(r, ROOM, NOW + 10)] == [1, 2]
    store.join(r, ROOM, 1, "DEV9", "audio", False, NOW + 10)
    store.leave_user(r, ROOM, 1)
    assert [e["user_id"] for e in store.roster(r, ROOM, NOW + 11)] == [2]
    store.leave(r, ROOM, 2, "DEV2")
    assert store.roster(r, ROOM, NOW + 12) == []


def test_a_garbled_entry_is_dropped(r):
    store.join(r, ROOM, 1, "DEV1", "audio", False, NOW)
    r.hset(f"call:roster:{ROOM}", "9:X", "not json")
    assert [e["user_id"] for e in store.roster(r, ROOM, NOW)] == [1]
    assert "9:X" not in r.hgetall(f"call:roster:{ROOM}")


def test_ringing_and_ending(r):
    call_id, _ = store.join(r, ROOM, 1, "DEV1", "audio", False, NOW)
    store.ring(r, ROOM, 2)
    assert store.ringing(r, ROOM) == 2
    store.stop_ringing(r, ROOM)
    assert store.ringing(r, ROOM) is None
    info = store.end(r, ROOM)
    assert info["call_id"] == call_id
    assert store.meta(r, ROOM) is None and store.roster(r, ROOM, NOW) == [] and store.room_of(r, call_id) is None
    assert store.end(r, ROOM) is None


def test_a_roster_entry_holds_no_more_than_it_needs(r):
    store.join(r, ROOM, 1, "DEV1", "video", True, NOW)
    entry = json.loads(r.hget(f"call:roster:{ROOM}", "1:DEV1"))
    assert set(entry) == {"user_id", "device_id", "kind", "video", "joined", "seen"}


# --- the history -----------------------------------------------------------------------------------------------

def _table():
    from pathlib import Path
    sql = (Path(__file__).resolve().parent.parent / "scripts" / "migrations" / "2026-10-23_chat_calls.sql").read_text(encoding="utf-8")
    sql = "\n".join(line for line in sql.splitlines() if not line.strip().startswith("--"))
    for s in (s.strip() for s in sql.split(";") if "CREATE" in s):
        yield s.replace("public.", "").replace("IF NOT EXISTS ", "").replace("SERIAL PRIMARY KEY", "INTEGER PRIMARY KEY AUTOINCREMENT")


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        for statement in _table():
            conn.execute(text(statement))
    with Session(engine) as session:
        yield session


def test_a_call_ends_once_and_keeps_only_counts(db):
    store.record_start(db, ROOM, "c1", "video", 1, NOW)
    store.record_peak(db, "c1", 3)
    store.record_peak(db, "c1", 2)                                          # the peak never goes down
    assert store.record_end(db, "c1", NOW + 600) == 600
    assert store.record_end(db, "c1", NOW + 900) is None                    # already ended
    assert store.record_end(db, "nope", NOW) is None
    row = db.execute(text("SELECT kind, started_by_id, started_utc, ended_utc, peak FROM chat_calls WHERE call_id = 'c1'")).fetchone()
    assert tuple(row) == ("video", 1, NOW, NOW + 600, 3)
    columns = [c[1] for c in db.execute(text("PRAGMA table_info(chat_calls)")).fetchall()]
    assert columns == ["id", "room_id", "call_id", "kind", "started_by_id", "started_utc", "ended_utc", "peak"]


def test_starts_are_counted_for_the_hourly_limit(db):
    for n in range(3):
        store.record_start(db, ROOM, f"c{n}", "audio", 1, NOW + n)
    store.record_start(db, ROOM, "other", "audio", 2, NOW)
    assert store.starts_since(db, 1, NOW) == 3 and store.starts_since(db, 1, NOW + 2) == 1
