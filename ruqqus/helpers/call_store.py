"""Who is in a call right now (Redis) and the calls that took place (`chat_calls`); rules in helpers/calls.py.

A call belongs to a chat's room. While anyone is in it, Redis holds:

- `call:meta:<room>`: the call's id (a random token: the topic its live updates go to and what every signalling
  message carries), its kind, who started it and when;
- `call:roster:<room>`: one field per device in it (`<user id>:<device id>`) with its kind, whether it sends video,
  and when it was last heard of; a device not heard of for HEARTBEAT_TTL seconds is out (`roster` drops it);
- `call:id:<call id>`: the room, so a live-updates topic can be checked (`room_of`);
- `call:ring:<room>`: in a 1:1 call nobody has answered yet, the person being called (RING_SECONDS).

Every key expires after the longest call, so a call nobody ends cleanly still disappears. `chat_calls` keeps one row
per call: room, kind, who started it, when, when it ended and how many were in it at most: never what was said.
The functions take the Redis client and the database session, so they are tested with stand-ins
(`tests/test_calls.py`). Nothing here commits.
"""

import json
import secrets

from sqlalchemy import text

from ruqqus.helpers import calls

_EXPIRE = calls.MAX_CALL_SECONDS + 600


def _meta_key(room_id):
    return f"call:meta:{room_id}"


def _roster_key(room_id):
    return f"call:roster:{room_id}"


def _ring_key(room_id):
    return f"call:ring:{room_id}"


def _id_key(call_id):
    return f"call:id:{call_id}"


def meta(r, room_id):
    data = r.hgetall(_meta_key(room_id)) or {}
    if not data.get("call_id"):
        return None
    return {"call_id": data["call_id"], "kind": data.get("kind", calls.AUDIO),
            "started_by": int(data.get("started_by", 0) or 0), "started_utc": int(data.get("started_utc", 0) or 0)}


def roster(r, room_id, now):
    """The devices in the call now, earliest first: [{user_id, device_id, kind, video, joined}]. Stale ones go."""
    raw = r.hgetall(_roster_key(room_id)) or {}
    out, stale = [], []
    for field, value in raw.items():
        try:
            entry = json.loads(value)
        except (TypeError, ValueError):
            stale.append(field)
            continue
        if now - int(entry.get("seen", 0)) > calls.HEARTBEAT_TTL:
            stale.append(field)
            continue
        out.append({"user_id": int(entry["user_id"]), "device_id": entry["device_id"], "kind": entry.get("kind", calls.AUDIO),
                    "video": bool(entry.get("video")), "joined": int(entry.get("joined", 0))})
    if stale:
        r.hdel(_roster_key(room_id), *stale)
    out.sort(key=lambda e: (e["joined"], e["user_id"], e["device_id"]))
    return out


def join(r, room_id, user_id, device_id, kind, video, now):
    """Puts a device in the call, starting it when there is none. Returns (call id, started)."""
    started = bool(r.hsetnx(_meta_key(room_id), "call_id", secrets.token_hex(12)))
    if started:
        r.hset(_meta_key(room_id), mapping={"kind": kind, "started_by": user_id, "started_utc": now})
    call_id = r.hget(_meta_key(room_id), "call_id")
    field = f"{user_id}:{device_id}"
    current = r.hget(_roster_key(room_id), field)
    joined = now
    if current:
        try:
            joined = int(json.loads(current).get("joined", now))
        except (TypeError, ValueError):
            joined = now
    r.hset(_roster_key(room_id), field, json.dumps({"user_id": user_id, "device_id": device_id, "kind": kind,
                                                    "video": bool(video), "joined": joined, "seen": now}))
    r.set(_id_key(call_id), room_id, ex=_EXPIRE)
    for key in (_meta_key(room_id), _roster_key(room_id)):
        r.expire(key, _EXPIRE)
    return call_id, started


def heartbeat(r, room_id, user_id, device_id, now):
    """False when this device is no longer in the call (it timed out or was ended)."""
    field = f"{user_id}:{device_id}"
    current = r.hget(_roster_key(room_id), field)
    if not current:
        return False
    try:
        entry = json.loads(current)
    except (TypeError, ValueError):
        return False
    if now - int(entry.get("seen", 0)) > calls.HEARTBEAT_TTL:
        r.hdel(_roster_key(room_id), field)
        return False
    entry["seen"] = now
    r.hset(_roster_key(room_id), field, json.dumps(entry))
    return True


def set_video(r, room_id, user_id, device_id, on):
    """Whether this device sends video now (a Space's camera button). False when it is not in the call."""
    field = f"{user_id}:{device_id}"
    current = r.hget(_roster_key(room_id), field)
    if not current:
        return False
    try:
        entry = json.loads(current)
    except (TypeError, ValueError):
        return False
    entry["video"] = bool(on)
    r.hset(_roster_key(room_id), field, json.dumps(entry))
    return True


def refresh(r, room_id):
    """Keeps a room's keys alive while someone is in it (a voice channel goes on for as long as anyone is there; `join`
    only does it when somebody comes)."""
    info = meta(r, room_id)
    if info is None:
        return
    for key in (_meta_key(room_id), _roster_key(room_id), _id_key(info["call_id"])):
        r.expire(key, _EXPIRE)


def leave(r, room_id, user_id, device_id):
    r.hdel(_roster_key(room_id), f"{user_id}:{device_id}")


def leave_user(r, room_id, user_id):
    """Every device of one person out of the call (a block, leaving the group)."""
    raw = r.hgetall(_roster_key(room_id)) or {}
    fields = [f for f in raw if f.split(":", 1)[0] == str(user_id)]
    if fields:
        r.hdel(_roster_key(room_id), *fields)


def end(r, room_id):
    """The call is over: every key goes. Returns its meta (None if there was no call)."""
    info = meta(r, room_id)
    keys = [_meta_key(room_id), _roster_key(room_id), _ring_key(room_id)]
    if info:
        keys.append(_id_key(info["call_id"]))
    r.delete(*keys)
    return info


def room_of(r, call_id):
    return r.get(_id_key(call_id)) if call_id else None


def ring(r, room_id, callee_id):
    r.set(_ring_key(room_id), str(callee_id), ex=calls.RING_SECONDS)


def ringing(r, room_id):
    value = r.get(_ring_key(room_id))
    return int(value) if value else None


def stop_ringing(r, room_id):
    r.delete(_ring_key(room_id))


# --- the history -----------------------------------------------------------------------------------------------

def record_start(db, room_id, call_id, kind, started_by, now):
    db.execute(text("""INSERT INTO chat_calls (room_id, call_id, kind, started_by_id, started_utc, ended_utc, peak)
                       VALUES (:room, :call, :kind, :by, :now, 0, 1)"""),
               {"room": room_id, "call": call_id, "kind": kind, "by": started_by, "now": now})


def record_peak(db, call_id, size):
    db.execute(text("UPDATE chat_calls SET peak = :n WHERE call_id = :call AND peak < :n"), {"call": call_id, "n": size})


def record_end(db, call_id, now):
    """Marks the call ended once. Returns how long it lasted in seconds (None if it was already ended)."""
    row = db.execute(text("SELECT started_utc, ended_utc FROM chat_calls WHERE call_id = :call"), {"call": call_id}).fetchone()
    if row is None or row[1]:
        return None
    db.execute(text("UPDATE chat_calls SET ended_utc = :now WHERE call_id = :call AND ended_utc = 0"),
               {"call": call_id, "now": now})
    return max(0, now - row[0])


def starts_since(db, user_id, since):
    return db.execute(text("SELECT COUNT(*) FROM chat_calls WHERE started_by_id = :u AND started_utc >= :since"),
                      {"u": user_id, "since": since}).scalar() or 0
