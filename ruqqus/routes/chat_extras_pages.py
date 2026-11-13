"""Pins and disappearing messages in 1:1 chats and groups (helpers/chat_extras.py, helpers/chat_disappear.py). Named `_pages`
so the star import of ruqqus.classes can't replace the helpers. Both write room state through the bot (only the bot sends
room state): the chat page itself draws what they mean."""
import time

from flask import *

from ruqqus.helpers.wrappers import *
from ruqqus.__main__ import app, limiter, member_limit_key

from ruqqus.helpers import chat_extras, chat_rooms
from ruqqus.helpers import matrix_client as matrix
from ruqqus.helpers.i18n_runtime import tr


def _error(message, status=400):
    return jsonify({"error": message}), status


def _room_for(v):
    """The room named in the form and this member's standing in it, or a 404 for a chat they aren't in."""
    room_id = str(request.values.get("room") or "")
    if not chat_rooms.ROOM_ID.match(room_id):
        abort(404)
    where = chat_extras.standing(g.db, room_id, v.id)
    if where is None:
        abort(404)
    refusal = chat_extras.change_refusal(where)
    if refusal:
        abort(make_response(_error(refusal, 403)))
    return room_id


@app.route("/api/chat/pin", methods=["POST"])
@limiter.limit("60/hour", key_func=member_limit_key)
@auth_required
@not_qr_limited
@not_child_locked("chat")
@validate_formkey
def chat_pin(v):
    """Pins or unpins a message: Matrix's own pinned list (`m.room.pinned_events`), newest first, at most MAX_PINS."""
    room_id = _room_for(v)
    event_id = str(request.values.get("event_id") or "")
    if not chat_extras.EVENT_ID.match(event_id):
        abort(404)
    pin = request.values.get("pin") != "0"
    try:
        if pin:
            found = matrix.event(room_id, event_id)
            if not found or found.get("room_id") not in (None, room_id) or "state_key" in found or found.get("unsigned", {}).get("redacted_because"):
                abort(404)
        current = matrix.room_state(room_id, chat_extras.PINNED).get("pinned")
        try:
            pinned = chat_extras.pins_after(current if isinstance(current, list) else [], event_id, pin)
        except chat_extras.Refused as e:
            return _error(str(e), 409)
        matrix.set_room_state(room_id, chat_extras.PINNED, {"pinned": pinned})
    except matrix.MatrixError:
        return _error(tr("Chat isn't answering right now. Try again in a moment."), 502)
    return jsonify({"pinned": pinned})


@app.route("/api/chat/disappear", methods=["POST"])
@limiter.limit("30/hour", key_func=member_limit_key)
@auth_required
@not_qr_limited
@not_child_locked("chat")
@validate_formkey
def chat_disappear(v):
    """Turns disappearing messages on (a day or a week) or off for everyone in the chat. Messages already sent keep the
    timer they were sent under: each is noted with its own due time when it is sent (helpers/chat_disappear.py)."""
    room_id = _room_for(v)
    seconds = chat_extras.seconds_of(request.values.get("seconds"))
    if seconds is None:
        return _error(tr("Choose how long messages last."))
    try:
        matrix.set_room_state(room_id, chat_extras.DISAPPEAR, {"seconds": seconds, "by": matrix.mxid_for(v.id)})
    except matrix.MatrixError:
        return _error(tr("Chat isn't answering right now. Try again in a moment."), 502)
    chat_extras.set_timer(g.db, room_id, seconds)
    g.db.commit()
    return jsonify({"seconds": seconds, "at": int(time.time())})
