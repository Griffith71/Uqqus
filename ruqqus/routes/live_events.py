"""`GET /api/live/events`: the Server-Sent Events stream of a signed-in member (helpers/live_bus.py).

The stream never reads the database: the member and the topics they may hear are settled before the first
frame, and the response is not tied to the request context, so the request's session is committed and closed
as for any other answer while the stream goes on.
"""

from flask import *

from ruqqus.helpers.wrappers import *
from ruqqus.helpers import live_bus
from ruqqus.__main__ import app, limiter, member_limit_key, r

_hub = None


def _make_hub():
    try:
        import gevent
        from gevent.queue import Queue

        spawn = gevent.spawn
    except ImportError:                  # not under gevent (scripts, tests): plain threads
        import threading
        from queue import Queue

        def spawn(fn):
            threading.Thread(target=fn, daemon=True).start()

    return live_bus.Hub(pubsub_factory=lambda: r.pubsub(ignore_subscribe_messages=True),
                        make_queue=lambda size: Queue(maxsize=size),
                        spawn=spawn)


def live_hub():
    global _hub
    if _hub is None:
        _hub = _make_hub()
    return _hub


@app.route("/api/live/events", methods=["GET"])
@limiter.limit("120/minute", key_func=member_limit_key)
@auth_required
def live_events(v):
    if r is None:
        abort(503)
    topics = live_bus.allowed_topics(v, request.args.get("topics", ""))
    channels = [live_bus.user_channel(v.id)] + [live_bus.topic_channel(t) for t in topics]

    h = live_hub()
    sub = h.subscribe(v.id, channels)
    if sub is None:
        return jsonify({"error": "Too many live streams are open. Close a tab and try again."}), 429

    def get(timeout):
        try:
            return sub.queue.get(timeout=timeout)
        except Exception:
            return None

    def frames():
        try:
            yield from live_bus.stream(sub, get)
        finally:
            h.unsubscribe(sub)

    resp = Response(frames(), mimetype="text/event-stream")
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Accel-Buffering"] = "no"
    return resp
