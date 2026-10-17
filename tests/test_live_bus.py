"""Live updates (ruqqus/helpers/live_bus.py): channel names, who may hear a topic, the SSE framing and the worker's
one listener, without Redis or gevent. The promises: a member hears their own channel and only the topics a guard
allows (a topic nobody guards is closed), a slow tab loses its oldest events instead of holding up the others, and a
stream ends on its own so no worker holds a connection for ever."""
import json
import queue

import pytest

from ruqqus.helpers import live_bus


class Member:
    def __init__(self, id):
        self.id = id


@pytest.fixture(autouse=True)
def no_guards(monkeypatch):
    monkeypatch.setattr(live_bus, "_guards", {})


def test_channels():
    assert live_bus.user_channel(7) == "live:u:7"
    assert live_bus.user_channel("7") == "live:u:7"
    assert live_bus.topic_channel("space:12") == "live:t:space:12"
    for bad in ("", "space", "space:", "Space:1", "space:1:2", "space:a b", "u:7*", "space:" + "x" * 41):
        with pytest.raises(ValueError):
            live_bus.topic_channel(bad)


def test_a_topic_is_closed_until_it_has_a_guard():
    assert live_bus.allowed_topics(Member(1), "space:1,call:2") == []
    live_bus.register_topic("space", lambda v, key: key == "1")
    assert live_bus.allowed_topics(Member(1), "space:1,space:2,call:2") == ["space:1"]


def test_a_guard_that_fails_says_no():
    def broken(v, key):
        raise RuntimeError("database down")
    live_bus.register_topic("space", broken)
    assert live_bus.allowed_topics(Member(1), "space:1") == []


def test_topics_are_checked_cleaned_and_capped():
    seen = []
    live_bus.register_topic("post", lambda v, key: seen.append(key) or True)
    asked = ",".join(f"post:{n}" for n in range(30)) + ",post:1, post:2 ,POST:3,post:x y"
    allowed = live_bus.allowed_topics(Member(1), asked)
    assert allowed == [f"post:{n}" for n in range(live_bus.MAX_TOPICS)]
    assert live_bus.allowed_topics(Member(1), [" post:5 ", "post:5", None]) == ["post:5"]


def test_messages_and_frames():
    payload = live_bus.message("call.ring", {"room": "!a:b", "from": "Ann"})
    assert "\n" not in payload
    assert live_bus.sse_event(payload) == f"event: call.ring\ndata: {payload}\n\n"
    assert json.loads(payload) == {"kind": "call.ring", "data": {"room": "!a:b", "from": "Ann"}}
    assert json.loads(live_bus.message("x"))["data"] == {}
    for bad in ("", "Call", "call ring", "x" * 41, "call\nevent: evil"):
        with pytest.raises(ValueError):
            live_bus.message(bad)
    # a payload that is not ours is never framed (it would let a publisher write its own SSE lines)
    assert live_bus.sse_event("not json") is None
    assert live_bus.sse_event(json.dumps({"kind": "a\nevent: b", "data": {}})) is None
    assert live_bus.sse_event(json.dumps({"data": {}})) is None
    assert live_bus.sse_comment() == ": ka\n\n" and live_bus.sse_retry() == "retry: 5000\n\n"


def test_publishing_is_best_effort():
    class Redis:
        def __init__(self):
            self.sent = []

        def publish(self, channel, data):
            self.sent.append((channel, data))

    class Down:
        def publish(self, channel, data):
            raise ConnectionError("no redis")

    redis = Redis()
    assert live_bus.publish_to_user(redis, 3, "call.ring", {"a": 1})
    assert redis.sent == [("live:u:3", live_bus.message("call.ring", {"a": 1}))]
    assert not live_bus.publish(Down(), "live:u:3", "call.ring")
    assert not live_bus.publish(None, "live:u:3", "call.ring")
    assert not live_bus.publish(redis, "live:u:3", "Bad Kind")


def _hub():
    return live_bus.Hub(pubsub_factory=None, make_queue=lambda size: queue.Queue(maxsize=size), spawn=lambda fn: None)


def test_the_hub_hands_each_message_to_the_streams_that_hear_its_channel():
    hub = _hub()
    mine = hub.subscribe(1, ["live:u:1", "live:t:space:9"])
    theirs = hub.subscribe(2, ["live:u:2"])
    hub.dispatch("live:u:1", "a")
    hub.dispatch("live:t:space:9", "b")
    hub.dispatch("live:u:3", "c")
    assert [mine.queue.get_nowait(), mine.queue.get_nowait()] == ["a", "b"]
    assert theirs.queue.empty()
    hub.unsubscribe(mine)
    hub.dispatch("live:u:1", "d")
    assert mine.queue.empty()


def test_a_revoke_takes_a_topic_from_the_members_open_streams():
    class Redis:
        def __init__(self):
            self.sent = []

        def publish(self, channel, data):
            self.sent.append((channel, data))

    redis = Redis()
    assert live_bus.revoke(redis, 1, "collab:4")
    assert not live_bus.revoke(redis, 1, "not a topic")
    (channel, payload), = redis.sent
    hub = _hub()
    mine = hub.subscribe(1, ["live:u:1", "live:t:collab:4", "live:t:post:2"])
    other = hub.subscribe(2, ["live:u:2", "live:t:collab:4"])
    hub.dispatch(channel, payload)
    assert mine.queue.empty()                                           # applied, never passed on to the page
    hub.dispatch("live:t:collab:4", "x")
    hub.dispatch("live:t:post:2", "y")
    assert mine.queue.get_nowait() == "y" and mine.queue.empty()
    assert other.queue.get_nowait() == "x"                              # someone else keeps hearing it
    hub.dispatch("live:t:collab:4", payload)                            # a revoke on a topic channel is just a message
    assert other.queue.get_nowait() == payload


def test_a_slow_tab_loses_its_oldest_events():
    hub = _hub()
    sub = hub.subscribe(1, ["live:u:1"])
    for n in range(live_bus.QUEUE_SIZE + 5):
        hub.dispatch("live:u:1", str(n))
    got = [sub.queue.get_nowait() for _ in range(live_bus.QUEUE_SIZE)]
    assert got[0] == "5" and got[-1] == str(live_bus.QUEUE_SIZE + 4)
    assert sub.dropped == 5


def test_streams_per_member_are_capped_by_ending_the_oldest():
    """Audit L-05: at the cap the member's oldest stream ends (a page already left) and the new one opens; refusing
    the new one left the page being looked at with no live updates at all."""
    hub = _hub()
    subs = [hub.subscribe(1, ["live:u:1"]) for _ in range(live_bus.MAX_STREAMS_PER_MEMBER)]
    assert all(subs)
    newest = hub.subscribe(1, ["live:u:1"])
    assert newest is not None
    assert subs[0].queue.get_nowait() is live_bus.CLOSE           # the oldest is told to end
    hub.dispatch("live:u:1", "x")
    assert subs[0].queue.empty() and newest.queue.get_nowait() == "x"
    assert hub.subscribe(2, ["live:u:2"]) is not None              # another member is not affected
    hub.unsubscribe(subs[1])
    hub.unsubscribe(subs[1])                                       # twice is harmless
    frames = list(live_bus.stream(subs[0], lambda t: live_bus.CLOSE))
    assert len(frames) == 2                                        # retry + open, then it ends


def test_the_listener_starts_once():
    started = []
    hub = live_bus.Hub(pubsub_factory=None, make_queue=lambda size: queue.Queue(maxsize=size), spawn=started.append)
    hub.subscribe(1, ["live:u:1"])
    hub.subscribe(2, ["live:u:2"])
    assert len(started) == 1


def test_a_stream_keeps_alive_and_ends_on_its_own():
    clock = [0.0]
    events = [None, live_bus.message("call.ring", {"n": 1}), "garbage", None]

    def get(timeout):
        assert timeout == live_bus.KEEPALIVE_SECONDS < 100        # under Cloudflare's idle timeout
        clock[0] += timeout
        return events.pop(0) if events else None

    frames = list(live_bus.stream(None, get, now=lambda: clock[0], max_seconds=100))
    assert frames[0] == live_bus.sse_retry() and frames[1] == ": open\n\n"
    assert frames[2] == ": ka\n\n"
    assert frames[3].startswith("event: call.ring\n")
    assert frames[4] == ": ka\n\n"                              # the garbage payload is not framed
    assert clock[0] >= 100 and len(frames) == 5
    assert live_bus.MAX_STREAM_SECONDS <= 600
