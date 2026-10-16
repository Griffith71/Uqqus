"""Voice and video calls in chats: the call window and the routes it talks to (rules in helpers/calls.py, who is in a
call in helpers/call_store.py, the browser side in assets/chat_src/src/call.js).

Every join and every heartbeat asks the chat's own rules again (a 1:1 chat both people have in their Inbox and no
block, or a group you have joined), so a block, leaving a group or being removed ends your part in its calls within
one heartbeat. A chat you are not in is a 404. The relay's monthly allowance is asked at every join
(helpers/turn_usage.py). The server only ever knows who is in a call, never what is said.
"""
import os
import re
import time

from flask import *

from ruqqus.helpers.wrappers import *
from ruqqus.helpers.get import *
from ruqqus.classes import *
import ruqqus.helpers.matrix_client as matrix
from ruqqus.__main__ import app, limiter, member_limit_key, r

from ruqqus.helpers import calls as call_rules
from ruqqus.helpers import call_store
from ruqqus.helpers import chat_groups as group_rules
from ruqqus.helpers import chat_group_store as group_store
from ruqqus.helpers import live_bus, turn_usage, turn_usage_store
from ruqqus.helpers.chat_permissions import is_blocked

_DEVICE = re.compile(r"^[A-Za-z0-9_.=-]{1,64}$")
# a room id: "!opaque:server", or from room version 12 (Synapse 1.162's default) "!opaque" alone
_ROOM = re.compile(r"^![A-Za-z0-9._~=+/-]{1,255}(:[A-Za-z0-9.:-]{1,255})?$")


def _error(message, status=400):
    return jsonify({"error": message}), status


def _person(u):
    return {"id": u.id, "mxid": matrix.mxid_for(u.id), "shown_name": u.shown_name, "username": u.username,
            "profile_url": u.profile_url}


def chat_room(v, room_id):
    """(room, refusal) for v in the chat whose Matrix room this is; (None, None) when v is not in it.
    room: {kind: pair|group, room_id, label, member_ids (who may be in its calls), other (pair)}."""
    if not room_id or not _ROOM.match(room_id):
        return None, None
    convo = g.db.query(ChatConversation).filter_by(matrix_room_id=room_id).first()
    if convo is not None:
        if v.id not in (convo.user_a_id, convo.user_b_id):
            return None, None
        other = convo.other_user(v.id)
        refusal = call_rules.pair_refusal(convo.status, is_blocked(v, other), bool(v.is_bot or other.is_bot),
                                          bool(other.is_deleted))
        return {"kind": "pair", "room_id": room_id, "label": other.shown_name, "member_ids": [v.id, other.id],
                "other": other}, refusal
    group = group_store.group_by_room(g.db, room_id)
    if group is not None:
        me = group_store.member(g.db, group[0], v.id)
        if me is None or me[2] != group_rules.JOINED:
            return None, None
        from ruqqus.routes.chat_groups import group_json
        info = group_json(group, me, v)
        joined = [m[0] for m in group_store.members(g.db, group[0]) if m[2] == group_rules.JOINED]
        return {"kind": "group", "room_id": room_id, "label": info["label"], "member_ids": joined, "other": None}, None
    return None, None


def _relay_state(now):
    allowance = turn_usage.allowance_bytes(os.environ.get("TURN_MONTHLY_GB"))
    state, spent = turn_usage_store.call_state(g.db, now, allowance)
    return state, spent, allowance


def _peers(roster, me_user, me_device):
    """The roster as the browsers need it (everyone but this device), with names."""
    others = [e for e in roster if not (e["user_id"] == me_user and e["device_id"] == me_device)]
    ids = {e["user_id"] for e in others}
    users = {u.id: u for u in g.db.query(User).filter(User.id.in_(ids)).all()} if ids else {}
    out = []
    for e in others:
        u = users.get(e["user_id"])
        if u is None:
            continue
        person = _person(u)
        out.append({"mxid": person["mxid"], "device_id": e["device_id"], "kind": e["kind"], "video": e["video"],
                    "shown_name": person["shown_name"], "profile_url": person["profile_url"], "user_id": u.id})
    return out


def _tell_roster(call_id):
    live_bus.publish(r, live_bus.topic_channel(f"call:{call_id}"), "call.roster", {})


def _finish(room_id, now, reason=None):
    """The call is over: Redis forgets it, the history row gets its end, everyone still listening is told.
    Returns the summary line for the chat (None when it never really took place)."""
    info = call_store.end(r, room_id)
    if not info:
        return None
    seconds = call_store.record_end(g.db, info["call_id"], now)
    live_bus.publish(r, live_bus.topic_channel(f"call:{info['call_id']}"), "call.ended", {"reason": reason or "ended"})
    if seconds is None:
        return None
    return call_rules.summary(info["kind"], seconds)


def _guard_call_topic(v, call_id):
    room_id = call_store.room_of(r, call_id)
    if not room_id:
        return False
    room, refusal = chat_room(v, room_id)
    return room is not None and refusal is None


live_bus.register_topic("call", _guard_call_topic)


@app.route("/call/<path:room_id>", methods=["GET"])
@auth_required
def call_window(room_id, v):
    room, refusal = chat_room(v, room_id)
    if room is None:
        abort(404)
    kind = call_rules.parse_kind(request.args.get("kind"))
    boot = {
        "formkey": v.formkey, "userId": v.id, "roomId": room_id, "kind": kind, "label": room["label"],
        "isGroup": room["kind"] == "group", "answer": bool(request.args.get("answer")), "avatar": v.profile_url,
        "refusal": refusal,
    }
    resp = make_response(render_template("call.html", v=v, kind=kind, boot=boot))
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _room_and_device(v):
    room_id = request.values.get("room", "")
    device_id = request.values.get("device_id", "")
    room, refusal = chat_room(v, room_id)
    if room is None:
        abort(404)
    if not _DEVICE.match(device_id):
        abort(400)
    return room, refusal, room_id, device_id


@app.route("/api/calls/join", methods=["POST"])
@auth_required
@validate_formkey
def call_join(v):
    room, refusal, room_id, device_id = _room_and_device(v)
    if refusal:
        return _error(refusal, 403)
    if r is None:
        return _error(turn_usage.UNAVAILABLE, 503)
    now = int(time.time())
    relay, spent, allowance = _relay_state(now)
    paused = turn_usage.refusal(relay, spent, allowance, now)
    if paused:
        return _error(paused, 503)

    roster = call_store.roster(r, room_id, now)
    if any(e["user_id"] == v.id and e["device_id"] != device_id for e in roster):
        return _error("You're already in this call in another window.", 409)
    already = any(e["user_id"] == v.id and e["device_id"] == device_id for e in roster)
    people = {e["user_id"] for e in roster if e["user_id"] != v.id}
    current = call_store.meta(r, room_id)
    starts = call_store.starts_since(g.db, v.id, now - 3600) if current is None else 0
    refusal = call_rules.join_refusal(already, len(people), current is None, starts)
    if refusal:
        return _error(refusal, 429 if refusal == call_rules.TOO_MANY_STARTS else 409)

    kind = call_rules.parse_kind(request.values.get("kind"))
    senders = sum(1 for e in roster if e["video"] and e["user_id"] != v.id)
    video = call_rules.video_allowed(kind, senders, turn_usage.video_allowed(relay))
    call_id, started = call_store.join(r, room_id, v.id, device_id, kind, video, now)
    if started:
        call_store.record_start(g.db, room_id, call_id, kind, v.id, now)
    roster = call_store.roster(r, room_id, now)
    call_store.record_peak(g.db, call_id, len({e["user_id"] for e in roster}))
    g.db.commit()

    caller = {"room_id": room_id, "call_id": call_id, "kind": kind, "from": v.shown_name,
              "from_username": v.username, "avatar": v.profile_url}
    if started and room["kind"] == "pair":
        call_store.ring(r, room_id, room["other"].id)
        live_bus.publish_to_user(r, room["other"].id, "call.ring", caller)
    elif started:
        for uid in room["member_ids"]:
            if uid != v.id:
                live_bus.publish_to_user(r, uid, "call.started", {**caller, "group": True})
    elif call_store.ringing(r, room_id) == v.id:
        # answered: the other tabs of this account stop ringing
        call_store.stop_ringing(r, room_id)
        live_bus.publish_to_user(r, v.id, "call.answered", {"room_id": room_id})
    _tell_roster(call_id)

    return jsonify({"call_id": call_id, "video": video, "kind": kind, "peers": _peers(roster, v.id, device_id),
                    "heartbeat_seconds": call_rules.HEARTBEAT_SECONDS, "ring_seconds": call_rules.RING_SECONDS,
                    "max_people": call_rules.AUDIO_MAX})


@app.route("/api/calls/heartbeat", methods=["POST"])
@limiter.limit("120/minute", key_func=member_limit_key)
@auth_required
@validate_formkey
def call_heartbeat(v):
    room_id = request.values.get("room", "")
    device_id = request.values.get("device_id", "")
    room, refusal = chat_room(v, room_id)
    now = int(time.time())
    if room is None or refusal or not _DEVICE.match(device_id):
        # no longer allowed in this chat (a block, removed from the group): out of its call
        if r is not None and room_id:
            call_store.leave_user(r, room_id, v.id)
        return jsonify({"in_call": False, "reason": "You can't be in this call any more."})
    info = call_store.meta(r, room_id)
    if info is None or not call_store.heartbeat(r, room_id, v.id, device_id, now):
        return jsonify({"in_call": False})
    if now - info["started_utc"] > call_rules.MAX_CALL_SECONDS:
        _finish(room_id, now, "too_long")
        g.db.commit()
        return jsonify({"in_call": False, "reason": "Calls end after 4 hours."})
    return jsonify({"in_call": True, "peers": _peers(call_store.roster(r, room_id, now), v.id, device_id)})


@app.route("/api/calls/leave", methods=["POST"])
@auth_required
@validate_formkey
def call_leave(v):
    room_id = request.values.get("room", "")
    device_id = request.values.get("device_id", "")
    room, _ = chat_room(v, room_id)
    if room is None or r is None:
        abort(404)
    now = int(time.time())
    info = call_store.meta(r, room_id)
    if info is None:
        return jsonify({"ended": True, "summary": None})
    call_store.leave(r, room_id, v.id, device_id)
    left = call_store.roster(r, room_id, now)
    callee = call_store.ringing(r, room_id)
    if callee is not None and not left:
        # the caller gave up before anyone answered: the other side stops ringing
        call_store.stop_ringing(r, room_id)
        live_bus.publish_to_user(r, callee, "call.cancelled", {"room_id": room_id})
    if not left or (room["kind"] == "pair" and len({e["user_id"] for e in left}) < 2 and callee is None):
        summary = _finish(room_id, now)
        g.db.commit()
        return jsonify({"ended": True, "summary": summary})
    _tell_roster(info["call_id"])
    return jsonify({"ended": False, "summary": None})


@app.route("/api/calls/decline", methods=["POST"])
@auth_required
@validate_formkey
def call_decline(v):
    room_id = request.values.get("room", "")
    room, _ = chat_room(v, room_id)
    if room is None or r is None:
        abort(404)
    if room["kind"] != "pair" or call_store.ringing(r, room_id) != v.id:
        return "", 204
    call_store.stop_ringing(r, room_id)
    live_bus.publish_to_user(r, v.id, "call.answered", {"room_id": room_id})
    _finish(room_id, int(time.time()), "declined")
    g.db.commit()
    return "", 204


@app.route("/api/calls/status", methods=["GET"])
@limiter.limit("120/minute", key_func=member_limit_key)
@auth_required
def call_status(v):
    """Whether this chat has a call going now and how many are in it (the chat's "Join call" bar)."""
    room_id = request.args.get("room", "")
    room, refusal = chat_room(v, room_id)
    if room is None:
        abort(404)
    if r is None or refusal:
        return jsonify({"active": False, "can_call": False, "reason": refusal})
    now = int(time.time())
    info = call_store.meta(r, room_id)
    roster = call_store.roster(r, room_id, now) if info else []
    if info and not roster and call_store.ringing(r, room_id) is None:
        _finish(room_id, now)           # everyone timed out
        g.db.commit()
        info = None
    return jsonify({"active": bool(info and roster), "kind": info["kind"] if info else None,
                    "people": len({e["user_id"] for e in roster}), "can_call": True,
                    "in_call": any(e["user_id"] == v.id for e in roster)})
