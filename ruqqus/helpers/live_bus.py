"""Live updates pushed to open pages (Server-Sent Events, `GET /api/live/events` in routes/live_events.py).

Anything that happens now and should show at once (an incoming call, who is in a call or a Space, a live
comment) is published here and reaches the member's open tabs within a second, without polling.

- **Channels**: every member has one (`user_channel(id)`), and a topic has one (`topic_channel("space:12")`).
  `publish` sends one event to one channel through Redis, so it reaches every worker.
- **A topic is only heard by someone allowed to**: a tab asks for topics by name and the route keeps only those
  whose guard (`register_topic(prefix, guard)`) says yes for that member. A prefix with no guard is refused, so
  a new kind of topic is closed until it has one. The member's own channel needs no guard.
- **Each worker listens once**: `Hub` holds one Redis pattern subscription (`live:*`) per worker process and hands
  each message to that worker's open streams in memory; a slow tab loses its oldest events rather than holding
  up the others (`QUEUE_SIZE`).
- **Nothing here reads the database.** An event carries only what its publisher put in it, so a publisher must
  never put in something a listener of that channel may not see.

The rules and the framing are pure and tested without Redis or gevent (`tests/test_live_bus.py`).
"""

import json
import re
import time

PREFIX = "live:"
PATTERN = PREFIX + "*"
QUEUE_SIZE = 50                  # events kept for one slow tab
KEEPALIVE_SECONDS = 25           # under Cloudflare's 100 s idle timeout
MAX_STREAM_SECONDS = 600         # a stream ends after 10 minutes; the browser's EventSource opens a new one
RETRY_MS = 5000                  # how long the browser waits before opening it again
MAX_STREAMS_PER_MEMBER = 6       # open tabs per member, per worker
MAX_TOPICS = 10                  # topics one tab may ask for

_KIND = re.compile(r"^[a-z][a-z0-9_.]{0,39}$")
_TOPIC = re.compile(r"^([a-z]{1,20}):([0-9a-z_]{1,40})$")

_guards = {}


def user_channel(user_id):
    return f"{PREFIX}u:{int(user_id)}"


def topic_channel(topic):
    if not _TOPIC.match(topic or ""):
        raise ValueError("not a topic name")
    return f"{PREFIX}t:{topic}"


def register_topic(prefix, guard):
    """guard(v, key) -> bool: may this member hear `<prefix>:<key>`? Asked once, when a stream opens."""
    _guards[prefix] = guard


def allowed_topics(v, asked):
    """The topics of `asked` (a comma-separated string or a list) that this member may hear, at most MAX_TOPICS."""
    if isinstance(asked, str):
        asked = asked.split(",")
    out = []
    for topic in asked or []:
        topic = (topic or "").strip()
        match = _TOPIC.match(topic)
        if not match or topic in out:
            continue
        guard = _guards.get(match.group(1))
        if guard is None:
            continue
        try:
            if not guard(v, match.group(2)):
                continue
        except Exception:
            continue                     # a guard that fails says no
        out.append(topic)
        if len(out) >= MAX_TOPICS:
            break
    return out


def message(kind, data=None):
    if not _KIND.match(kind or ""):
        raise ValueError("not an event kind")
    return json.dumps({"kind": kind, "data": data if data is not None else {}}, separators=(",", ":"))


def publish(redis, channel, kind, data=None):
    """Best effort: a live update that can't be sent is lost, never an error for the action that caused it."""
    if redis is None:
        return False
    try:
        redis.publish(channel, message(kind, data))
        return True
    except Exception:
        return False


def publish_to_user(redis, user_id, kind, data=None):
    return publish(redis, user_channel(user_id), kind, data)


def sse_event(payload):
    """One message as an SSE frame. The payload is our own JSON (no newlines), sent as one `data:` line."""
    try:
        kind = json.loads(payload)["kind"]
    except (ValueError, KeyError, TypeError):
        return None
    if not _KIND.match(kind or ""):
        return None
    return f"event: {kind}\ndata: {payload}\n\n"


def sse_comment(text="ka"):
    return f": {text}\n\n"


def sse_retry():
    return f"retry: {RETRY_MS}\n\n"


class Subscription:
    """One open stream: the channels it hears and its own small queue."""

    def __init__(self, channels, queue):
        self.channels = frozenset(channels)
        self.queue = queue
        self.dropped = 0

    def offer(self, payload):
        try:
            self.queue.put_nowait(payload)
        except Exception:                # full: drop the oldest, keep the newest
            try:
                self.queue.get_nowait()
                self.dropped += 1
                self.queue.put_nowait(payload)
            except Exception:
                pass


class Hub:
    """The worker's one listener. `make_queue` and `spawn` are gevent's in the app and the standard library's in
    tests; `pubsub_factory` returns a Redis PubSub (or a stand-in)."""

    def __init__(self, pubsub_factory, make_queue, spawn):
        self._pubsub_factory = pubsub_factory
        self._make_queue = make_queue
        self._spawn = spawn
        self._subs = set()
        self._per_member = {}
        self._started = False

    def subscribe(self, member_id, channels):
        """None when the member already has MAX_STREAMS_PER_MEMBER streams open on this worker."""
        if self._per_member.get(member_id, 0) >= MAX_STREAMS_PER_MEMBER:
            return None
        self._ensure_started()
        sub = Subscription(channels, self._make_queue(QUEUE_SIZE))
        sub.member_id = member_id
        self._subs.add(sub)
        self._per_member[member_id] = self._per_member.get(member_id, 0) + 1
        return sub

    def unsubscribe(self, sub):
        if sub in self._subs:
            self._subs.discard(sub)
            left = self._per_member.get(sub.member_id, 1) - 1
            if left > 0:
                self._per_member[sub.member_id] = left
            else:
                self._per_member.pop(sub.member_id, None)

    def dispatch(self, channel, payload):
        for sub in list(self._subs):
            if channel in sub.channels:
                sub.offer(payload)

    def _ensure_started(self):
        if not self._started:
            self._started = True
            self._spawn(self._listen)

    def _listen(self):
        # reconnects after any failure; a worker with no Redis simply never delivers
        while True:
            try:
                pubsub = self._pubsub_factory()
                pubsub.psubscribe(PATTERN)
                for item in pubsub.listen():
                    if item.get("type") == "pmessage":
                        self.dispatch(item.get("channel"), item.get("data"))
            except Exception:
                time.sleep(2)


def stream(sub, get, now=time.time, max_seconds=MAX_STREAM_SECONDS):
    """The frames of one stream. `get(timeout)` returns the next payload or None after `timeout` seconds."""
    started = now()
    yield sse_retry()
    yield sse_comment("open")
    while now() - started < max_seconds:
        payload = get(KEEPALIVE_SECONDS)
        if payload is None:
            yield sse_comment()
            continue
        frame = sse_event(payload)
        if frame:
            yield frame
