"""Spaces: the live page, joining and leaving, the stage, video, the board, live updates, reminders, reports and the
replay (rules in helpers/spaces.py, SQL in helpers/space_store.py, who is listening now in helpers/call_store.py under
`space:<id>`, the page's script assets/chat_src/src/space.js).

A Space is read through its post: whoever may not open the post gets a 404 here too (get_post, a Circle post,
a private guild, the word filter), and a block with the host or a co-host either way reads as "isn't available".
Every change is a POST with the form key; the host and co-hosts run it; admins (level 3) can end any Space.
"""
import os
import re
import time

import mistletoe
from sqlalchemy import or_
from flask import *

from ruqqus.helpers.wrappers import *
from ruqqus.helpers.get import *
from ruqqus.helpers.base36 import base36encode
from ruqqus.helpers.markdown import CustomRenderer
from ruqqus.helpers.sanitize import sanitize
from ruqqus.classes import *
import ruqqus.helpers.matrix_client as matrix
from ruqqus.__main__ import app, limiter, member_limit_key, r

from ruqqus.helpers import spaces as space_rules
from ruqqus.helpers import space_store, space_view, call_store, live_bus, turn_usage, turn_usage_store
from ruqqus.helpers import live_thread_store as live_store
from ruqqus.helpers.alerts import send_notification
from ruqqus.helpers.media import attach as media_attach, safety as media_safety
from ruqqus.helpers.visibility import post_hidden

_DEVICE = re.compile(r"^[A-Za-z0-9_.=-]{1,64}$")


def _error(message, status=400):
    return jsonify({"error": message}), status


class _Space:
    """A Space row with its post, as the routes need it."""

    def __init__(self, row, post):
        (self.id, self.post_id, self.host_id, self.status, self.scheduled_utc, self.started_utc, self.ended_utc,
         self.record, self.replay_asset_id, self.listener_peak, self.ended_by_admin, self.matrix_room_id,
         self.video, self.slow_seconds) = row
        self.post = post

    @property
    def room(self):
        return space_store.room(self.id)

    @property
    def topic(self):
        return live_bus.topic_channel(f"space:{self.id}")


def _post_for(space_row, v):
    post = get_post(base36encode(space_row[1]), v=v, graceful=True)
    if post is None or post.deleted_utc or post.is_banned or post.in_private_guild_for(v) or post_hidden(post, v):
        return None
    return post


def _blocked_with_hosts(space, v, roles):
    hosts = [space.host_id] + [uid for uid, (role, status) in roles.items() if role == space_rules.COHOST and status == "accepted"]
    if v.id in hosts:
        return False
    users = g.db.query(User).filter(User.id.in_(hosts)).all()
    return any(v.any_block_exists(u) for u in users)


def load_space(sid, v):
    """The Space when v may see its post, else a 404. Ends it lazily when its host has gone or it ran too long."""
    try:
        sid = int(sid)
    except (TypeError, ValueError):
        abort(404)
    row = space_store.get(g.db, sid)
    if row is None:
        abort(404)
    post = _post_for(row, v)
    if post is None:
        abort(404)
    space = _Space(row, post)
    _maybe_end(space)
    return space


def _maybe_end(space):
    if space.status != space_rules.LIVE or r is None:
        return
    now = int(time.time())
    host_seen = r.get(f"space:host:{space.id}")
    too_long = now - space.started_utc > space_rules.MAX_SECONDS
    gone = not host_seen and now - space.started_utc > space_rules.HOST_GONE_SECONDS
    if too_long or gone:
        _end(space, now)
        g.db.commit()


def _end(space, now, by_admin=False):
    if space_store.end(g.db, space.id, now, by_admin=by_admin):
        call_store.end(r, space.room)
        live_store.end(g.db, space.post_id, now)          # its live updates end with it
        live_bus.publish(r, space.topic, "space.ended", {"by_admin": by_admin})
        # a Space that was not recorded leaves nothing behind (the owner's choice): its post and every copy of it go
        if not space.record:
            _take_down(space, now)
    space.status = space_rules.ENDED


def _take_down(space, now):
    """Take a not-recorded Space's post and its guild copies down when it ends. It is not the author deleting it, so
    no history row is written and nothing appears in the public Deletion log (routes/deletion_log.py reads those rows);
    its board, its live updates and its reminders go too (a reported message waits for the admins). The caller
    commits."""
    posts = g.db.query(Submission).filter(or_(Submission.id == space.post_id, Submission.repost_id == space.post_id)).all()
    for post in posts:
        if not post.deleted_utc:
            post.deleted_utc = now
            g.db.add(post)
    space_store.clear_reminders(g.db, space.id)
    space_store.clear_board(g.db, space.id)
    live_store.erase(g.db, space.post_id, now)


def _relay_video_ok(now):
    allowance = turn_usage.allowance_bytes(os.environ.get("TURN_MONTHLY_GB"))
    relay, _spent = turn_usage_store.call_state(g.db, now, allowance)
    return turn_usage.video_allowed(relay)


def _people(space, v, now):
    """Everyone in the Space now, with roles, for the page: (stage, listeners, peers for the mesh, my entry). Who sends
    video is the roster's word, and only while video is open in this Space (`space_rules.video_refusal`)."""
    roles = space_store.roles(g.db, space.id)
    roster = call_store.roster(r, space.room, now) if r is not None else []
    ids = {e["user_id"] for e in roster}
    video_note = space_rules.video_refusal(bool(space.video), len(ids), _relay_video_ok(now)) if space.video else space_rules.VIDEO_OFF
    video_open = video_note is None
    on_video = {e["user_id"] for e in roster if e["video"]} if video_open else set()
    users = {u.id: u for u in g.db.query(User).filter(User.id.in_(ids)).all()} if ids else {}
    hands = set(space_store.hands(g.db, space.id))
    stage, listeners, peers, seen = [], [], [], set()
    for e in roster:
        u = users.get(e["user_id"])
        if u is None:
            continue
        role = space_rules.role_of(u.id, space.host_id, roles)
        person = {"id": u.id, "username": u.username, "shown_name": u.shown_name, "profile_url": u.profile_url,
                  "role": role, "hand": u.id in hands, "invited": roles.get(u.id, (None, None))[1] == "invited",
                  "video": u.id in on_video and space_rules.on_stage(role)}
        if u.id != v.id or e["device_id"] != request.values.get("device_id"):
            peers.append({**person, "mxid": matrix.mxid_for(u.id), "device_id": e["device_id"],
                          "video": video_open and e["video"] and space_rules.on_stage(role)})
        if u.id in seen:
            continue
        seen.add(u.id)
        (stage if space_rules.on_stage(role) else listeners).append(person)
    order = {space_rules.HOST: 0, space_rules.COHOST: 1, space_rules.SPEAKER: 2}
    stage.sort(key=lambda p: order.get(p["role"], 3))
    my_role = space_rules.role_of(v.id, space.host_id, roles)
    me = {"role": my_role, "invited": roles.get(v.id, (None, None))[1] == "invited", "hand": v.id in hands,
          "may_run": space_rules.may_run(my_role) or v.admin_level >= 3, "video": v.id in on_video,
          "may_moderate": space_rules.may_moderate(my_role, v.admin_level >= 3)}
    video = {"allowed": bool(space.video), "open": video_open, "note": None if video_open else video_note,
             "senders": len({p["id"] for p in stage if p["video"]}), "max_senders": space_rules.VIDEO_STAGE_MAX,
             "max_people": space_rules.VIDEO_PEOPLE_MAX}
    return stage, listeners, peers, me, roles, video


def state_json(space, v):
    now = int(time.time())
    stage, listeners, peers, me, _, video = _people(space, v, now)
    may_run = me["may_run"]
    board = {"slow_seconds": int(space.slow_seconds or 0), "slow_label": space_rules.slow_label(int(space.slow_seconds or 0)),
             "open": space.status == space_rules.LIVE}
    inside = may_run or any(p["id"] == v.id for p in stage + listeners)
    if not inside:
        # someone who hasn't joined learns how many are in it, never who (audit L-01: the roster, with Matrix and
        # device ids, went to anyone who could open the post, a blocked account included)
        return {"id": space.id, "status": space.status, "record": bool(space.record), "room_id": None,
                "stage": [], "listeners": [], "peers": [], "me": me, "inside": False,
                "stage_count": len(stage), "listener_count": len(listeners), "hands": [],
                "max_stage": space_rules.MAX_STAGE, "max_listeners": space_rules.MAX_LISTENERS,
                "video": {"allowed": video["allowed"], "open": False, "note": None, "senders": 0,
                          "max_senders": video["max_senders"], "max_people": video["max_people"]}, "board": board}
    return {
        "inside": True, "stage_count": len(stage),
        "id": space.id, "status": space.status, "record": bool(space.record), "room_id": space.matrix_room_id,
        "stage": stage, "listeners": listeners, "peers": peers, "me": me,
        "listener_count": len(listeners),
        "hands": [p["id"] for p in listeners if p["hand"]] if may_run else [],
        "max_stage": space_rules.MAX_STAGE, "max_listeners": space_rules.MAX_LISTENERS,
        "video": video, "board": board,
    }


def _out_of_room(space, user_id):
    if not space.matrix_room_id:
        return
    try:
        matrix.kick_as_bot(space.matrix_room_id, matrix.mxid_for(user_id))
    except matrix.MatrixError:
        pass


def _tell(space):
    live_bus.publish(r, space.topic, "space.roster", {})


def _guard_space_topic(v, sid):
    try:
        row = space_store.get(g.db, int(sid))
    except (TypeError, ValueError):
        return False
    return row is not None and _post_for(row, v) is not None


live_bus.register_topic("space", _guard_space_topic)


def _target(space):
    """The member a host action is about (`username`), with their current role."""
    user = get_user(request.values.get("username", "").lstrip("@"), graceful=True)
    if user is None:
        abort(404)
    roles = space_store.roles(g.db, space.id)
    return user, space_rules.role_of(user.id, space.host_id, roles), roles


def _my_role(space, v):
    return space_rules.role_of(v.id, space.host_id, space_store.roles(g.db, space.id))


# --- the page ---------------------------------------------------------------------------------------------------

def _replay_path(space):
    """The replay's address on this site (`/media/...`), once the host's recording was saved."""
    if not space.replay_asset_id:
        return None
    from ruqqus.classes.media import MediaAsset
    asset = g.db.query(MediaAsset).filter_by(id=space.replay_asset_id).first()
    return asset.path if asset is not None else None


@app.route("/space/<sid>", methods=["GET"])
@auth_required
def space_page(sid, v):
    space = load_space(sid, v)
    host = g.db.query(User).filter_by(id=space.host_id).first()
    boot = {
        "formkey": v.formkey, "userId": v.id, "spaceId": space.id, "postId": space.post.base36id,
        "title": space.post.title, "permalink": space.post.permalink, "status": space.status,
        "isHost": v.id == space.host_id, "record": bool(space.record), "scheduledUtc": space.scheduled_utc,
        "host": {"shown_name": host.shown_name, "username": host.username, "profile_url": host.profile_url},
        "avatar": v.profile_url, "maxStage": space_rules.MAX_STAGE, "maxListeners": space_rules.MAX_LISTENERS,
        "isAdmin": v.admin_level >= 3, "video": bool(space.video), "boardMax": space_rules.BOARD_MAX,
        "slowChoices": [[n, space_rules.slow_label(n)] for n in space_rules.SLOW_CHOICES],
        "updateMax": 2000, "summaryMax": 1000, "replay": _replay_path(space),
    }
    resp = make_response(render_template("space.html", v=v, boot=boot))
    resp.headers["Cache-Control"] = "no-store"
    return resp


# --- running it -------------------------------------------------------------------------------------------------

@app.route("/api/spaces/<sid>/start", methods=["POST"])
@is_not_banned
@validate_formkey
def space_start(sid, v):
    space = load_space(sid, v)
    if v.id != space.host_id:
        return _error("Only the host can start this Space.", 403)
    now = int(time.time())
    if space.status != space_rules.SCHEDULED:
        return _error(space_rules.OVER if space.status == space_rules.ENDED else "This Space has already started.", 409)
    try:
        room_id = matrix.create_space_room()
    except matrix.MatrixError:
        return _error("Spaces aren't answering right now. Try again in a moment.", 502)
    if not space_store.start(g.db, space.id, now):
        g.db.rollback()
        return _error("This Space has already started.", 409)
    space_store.set_room(g.db, space.id, room_id)
    g.db.commit()
    if r is not None:
        r.set(f"space:host:{space.id}", now, ex=space_rules.HOST_GONE_SECONDS)
    # its live updates (the live-thread machinery, keyed by its post) run while it is live, at most as long as it can
    live_store.start(g.db, space.post_id, v.id, now, now + space_rules.MAX_SECONDS)
    g.db.commit()
    live_bus.publish(r, space.topic, "space.started", {})
    # the people who asked to be reminded (send_notification commits by itself); never the title or the host's name
    reminded = set(space_store.reminder_ids(g.db, space.id))
    for uid in reminded:
        user = g.db.query(User).filter_by(id=uid).first()
        if user is not None and uid != v.id:
            send_notification(user, f"A Space you asked to be reminded of is live: [join it]({space.post.permalink})")
    # and, once, the people who follow its post (helpers/post_follows.py) and may still read it
    from ruqqus.helpers import post_follows
    from ruqqus.routes.comments import _followers_who_may_read
    ids = [uid for uid in post_follows.followers(g.db, space.post_id, v.id, anonymous=False) if uid not in reminded]
    for user in g.db.query(User).filter(User.id.in_(_followers_who_may_read(space.post, ids))).all() if ids else []:
        send_notification(user, f"A Space you follow is live: [join it]({space.post.permalink})")
    return jsonify({"status": space_rules.LIVE})


@app.route("/api/spaces/<sid>/join", methods=["POST"])
@auth_required
@validate_formkey
def space_join(sid, v):
    space = load_space(sid, v)
    device_id = request.values.get("device_id", "")
    if not _DEVICE.match(device_id) or r is None:
        abort(400)
    now = int(time.time())
    allowance = turn_usage.allowance_bytes(os.environ.get("TURN_MONTHLY_GB"))
    relay, spent = turn_usage_store.call_state(g.db, now, allowance)
    paused = turn_usage.refusal(relay, spent, allowance, now)
    if paused:
        return _error(paused, 503)
    roles = space_store.roles(g.db, space.id)
    role = space_rules.role_of(v.id, space.host_id, roles)
    roster = call_store.roster(r, space.room, now)
    if any(e["user_id"] == v.id and e["device_id"] != device_id for e in roster):
        return _error("You're already in this Space in another window.", 409)
    listeners = {e["user_id"] for e in roster
                 if not space_rules.on_stage(space_rules.role_of(e["user_id"], space.host_id, roles)) and e["user_id"] != v.id}
    refusal = space_rules.join_refusal(space.status, True, _blocked_with_hosts(space, v, roles),
                                       space_store.removed(g.db, space.id, v.id), len(listeners),
                                       space_rules.on_stage(role))
    if refusal:
        return _error(refusal, 403 if refusal == space_rules.UNAVAILABLE else 409)
    if space.matrix_room_id:
        try:
            from ruqqus.routes.chat import _ensure_provisioned
            matrix.put_in_room(space.matrix_room_id, _ensure_provisioned(v))
        except matrix.MatrixError:
            return _error("Spaces aren't answering right now. Try again in a moment.", 502)
    call_store.join(r, space.room, v.id, device_id, "audio", False, now)
    if v.id == space.host_id:
        r.set(f"space:host:{space.id}", now, ex=space_rules.HOST_GONE_SECONDS)
    space_store.note_listeners(g.db, space.id, len(listeners) + (0 if space_rules.on_stage(role) else 1))
    g.db.commit()
    _tell(space)
    return jsonify(state_json(space, v))


@app.route("/api/spaces/<sid>/heartbeat", methods=["POST"])
@limiter.limit("120/minute", key_func=member_limit_key)
@auth_required
@validate_formkey
def space_heartbeat(sid, v):
    space = load_space(sid, v)
    device_id = request.values.get("device_id", "")
    now = int(time.time())
    if space.status != space_rules.LIVE or r is None:
        return jsonify({"in_space": False, "status": space.status})
    if space_store.removed(g.db, space.id, v.id) or _blocked_with_hosts(space, v, space_store.roles(g.db, space.id)):
        call_store.leave_user(r, space.room, v.id)
        return jsonify({"in_space": False, "status": space.status, "reason": space_rules.UNAVAILABLE})
    if not _DEVICE.match(device_id) or not call_store.heartbeat(r, space.room, v.id, device_id, now):
        return jsonify({"in_space": False, "status": space.status})
    if v.id == space.host_id:
        r.set(f"space:host:{space.id}", now, ex=space_rules.HOST_GONE_SECONDS)
    return jsonify({"in_space": True, **state_json(space, v)})


@app.route("/api/spaces/<sid>/state", methods=["GET"])
@limiter.limit("120/minute", key_func=member_limit_key)
@auth_required
def space_state(sid, v):
    space = load_space(sid, v)
    return jsonify(state_json(space, v))


@app.route("/api/spaces/<sid>/leave", methods=["POST"])
@auth_required
@validate_formkey
def space_leave(sid, v):
    space = load_space(sid, v)
    if r is not None:
        call_store.leave(r, space.room, v.id, request.values.get("device_id", ""))
    _out_of_room(space, v.id)
    space_store.lower_hand(g.db, space.id, v.id)
    g.db.commit()
    _tell(space)
    return jsonify({"left": True})


@app.route("/api/spaces/<sid>/hand", methods=["POST"])
@auth_required
@validate_formkey
def space_hand(sid, v):
    space = load_space(sid, v)
    if space.status != space_rules.LIVE:
        return _error(space_rules.OVER, 409)
    if request.values.get("up") == "1":
        space_store.raise_hand(g.db, space.id, v.id, int(time.time()))
    else:
        space_store.lower_hand(g.db, space.id, v.id)
    g.db.commit()
    _tell(space)
    return jsonify(state_json(space, v))


@app.route("/api/spaces/<sid>/invite", methods=["POST"])
@auth_required
@validate_formkey
def space_invite(sid, v):
    space = load_space(sid, v)
    user, role, roles = _target(space)
    stage = 1 + sum(1 for (rl, st) in roles.values() if st == "accepted")
    refusal = space_rules.invite_refusal(_my_role(space, v), role, stage, roles.get(user.id, (None, None))[1] == "invited")
    if refusal:
        return _error(refusal, 403 if "Only" in refusal else 409)
    space_store.invite_speaker(g.db, space.id, user.id, int(time.time()))
    space_store.lower_hand(g.db, space.id, user.id)
    g.db.commit()
    live_bus.publish_to_user(r, user.id, "space.invited", {"space_id": space.id})
    _tell(space)
    return jsonify(state_json(space, v))


@app.route("/api/spaces/<sid>/accept", methods=["POST"])
@auth_required
@validate_formkey
def space_accept(sid, v):
    space = load_space(sid, v)
    roles = space_store.roles(g.db, space.id)
    stage = 1 + sum(1 for (rl, st) in roles.values() if st == "accepted")
    if stage >= space_rules.MAX_STAGE:
        return _error(space_rules.STAGE_FULL, 409)
    if not space_store.accept_speaker(g.db, space.id, v.id, int(time.time())):
        return _error("There's no invitation to speak.", 409)
    g.db.commit()
    _tell(space)
    return jsonify(state_json(space, v))


@app.route("/api/spaces/<sid>/listen", methods=["POST"])
@auth_required
@validate_formkey
def space_listen(sid, v):
    """Back to listening: yourself, or (the host and co-hosts) someone on stage. Also turns down an invitation."""
    space = load_space(sid, v)
    if request.values.get("username"):
        user, role, _ = _target(space)
        if user.id != v.id:
            mine = _my_role(space, v)
            if not space_rules.may_run(mine) or role == space_rules.HOST or (role == space_rules.COHOST and mine != space_rules.HOST):
                return _error("Only the host and co-hosts can move people off the stage.", 403)
    else:
        user = v
    if user.id == space.host_id:
        return _error("The host stays on stage.", 409)
    space_store.drop_role(g.db, space.id, user.id)
    g.db.commit()
    live_bus.publish_to_user(r, user.id, "space.listening", {"space_id": space.id})
    _tell(space)
    return jsonify(state_json(space, v))


@app.route("/api/spaces/<sid>/cohost", methods=["POST"])
@auth_required
@validate_formkey
def space_cohost(sid, v):
    space = load_space(sid, v)
    user, role, roles = _target(space)
    cohosts = sum(1 for (rl, st) in roles.values() if rl == space_rules.COHOST)
    refusal = space_rules.cohost_refusal(_my_role(space, v), role, cohosts)
    if refusal:
        return _error(refusal, 403)
    space_store.set_role(g.db, space.id, user.id, space_rules.SPEAKER if role == space_rules.COHOST else space_rules.COHOST,
                         int(time.time()))
    g.db.commit()
    _tell(space)
    return jsonify(state_json(space, v))


@app.route("/api/spaces/<sid>/mute", methods=["POST"])
@auth_required
@validate_formkey
def space_mute(sid, v):
    """Asks a speaker's window to mute their microphone (they can unmute; to stop them, move them off the stage)."""
    space = load_space(sid, v)
    user, role, _ = _target(space)
    if not space_rules.may_run(_my_role(space, v)) and v.admin_level < 3:
        return _error("Only the host and co-hosts can mute speakers.", 403)
    live_bus.publish_to_user(r, user.id, "space.mute", {"space_id": space.id})
    return jsonify({"ok": True})


@app.route("/api/spaces/<sid>/video", methods=["POST"])
@auth_required
@validate_formkey
def space_video(sid, v):
    """This window's camera on (`on=1`) or off. Only on stage, only while video is open in this Space, and at most
    VIDEO_STAGE_MAX people at once; turning it off is always allowed."""
    space = load_space(sid, v)
    device_id = request.values.get("device_id", "")
    if not _DEVICE.match(device_id) or r is None:
        abort(400)
    now = int(time.time())
    on = request.values.get("on") == "1"
    if on:
        roster = call_store.roster(r, space.room, now)
        roles = space_store.roles(g.db, space.id)
        role = space_rules.role_of(v.id, space.host_id, roles)
        others = {e["user_id"] for e in roster if e["video"] and e["user_id"] != v.id
                  and space_rules.on_stage(space_rules.role_of(e["user_id"], space.host_id, roles))}
        refusal = space_rules.camera_refusal(bool(space.video), len({e["user_id"] for e in roster}),
                                             _relay_video_ok(now), space_rules.on_stage(role), len(others))
        if space.status != space_rules.LIVE:
            refusal = space_rules.OVER
        if refusal:
            return _error(refusal, 409)
    if not call_store.set_video(r, space.room, v.id, device_id, on):
        return _error("You're not in this Space.", 409)
    _tell(space)
    return jsonify(state_json(space, v))


@app.route("/api/spaces/<sid>/remove", methods=["POST"])
@auth_required
@validate_formkey
def space_remove(sid, v):
    space = load_space(sid, v)
    user, role, _ = _target(space)
    mine = space_rules.COHOST if v.admin_level >= 3 and _my_role(space, v) == space_rules.LISTENER else _my_role(space, v)
    refusal = space_rules.remove_refusal(mine, role, v.id, user.id)
    if refusal:
        return _error(refusal, 403)
    space_store.remove(g.db, space.id, user.id, v.id, int(time.time()))
    g.db.commit()
    if r is not None:
        call_store.leave_user(r, space.room, user.id)
    _out_of_room(space, user.id)
    live_bus.publish_to_user(r, user.id, "space.removed", {"space_id": space.id})
    live_bus.revoke(r, user.id, f"space:{space.id}")                     # their open pages stop hearing it (C-07)
    _tell(space)
    return jsonify(state_json(space, v))


@app.route("/api/spaces/<sid>/end", methods=["POST"])
@auth_required
@validate_formkey
def space_end(sid, v):
    space = load_space(sid, v)
    admin = v.admin_level >= 3
    if v.id != space.host_id and not admin:
        return _error("Only the host can end this Space.", 403)
    _end(space, int(time.time()), by_admin=admin and v.id != space.host_id)
    if admin and v.id != space.host_id:
        space_store.resolve_reports(g.db, space.id, int(time.time()))
    g.db.commit()
    return jsonify({"status": space_rules.ENDED})


@app.route("/api/spaces/<sid>/remind", methods=["POST"])
@auth_required
@validate_formkey
def space_remind(sid, v):
    space = load_space(sid, v)
    if space.status != space_rules.SCHEDULED:
        return _error("This Space isn't waiting to start.", 409)
    if request.values.get("on") == "0":
        space_store.forget_reminder(g.db, space.id, v.id)
    else:
        space_store.remind(g.db, space.id, v.id, int(time.time()))
    g.db.commit()
    return jsonify({"reminded": space_store.reminded(g.db, space.id, v.id)})


@app.route("/api/spaces/<sid>/report", methods=["POST"])
@auth_required
@validate_formkey
def space_report(sid, v):
    space = load_space(sid, v)
    if v.id == space.host_id:
        return _error("You can't report your own Space.", 409)
    if not space_store.report(g.db, space.id, v.id, space_rules.clean_reason(request.values.get("reason")), int(time.time())):
        return _error("You've already reported this Space.", 409)
    g.db.commit()
    return jsonify({"ok": True})


@app.route("/api/spaces/<sid>/replay", methods=["POST"])
@is_not_banned
@validate_formkey
def space_replay(sid, v):
    """The host's recording, already uploaded to their own linked storage (helpers/media), becomes the post's sound."""
    space = load_space(sid, v)
    if v.id != space.host_id or not space.record:
        abort(403)
    if space.status != space_rules.ENDED or space.replay_asset_id:
        return _error("This Space can't take a replay now.", 409)
    asset = media_attach.own_asset(g.db, v.id, request.values.get("media"), kinds=("audio",))
    if asset is None:
        return _error("That recording can no longer be used.", 400)
    post = space.post
    body = ((post.body or "").rstrip() + f"\n\n[audio]({asset.path})").strip()
    with CustomRenderer() as renderer:
        body_md = renderer.render(mistletoe.Document(body))
    body_html = sanitize(body_md, linkgen=True)
    # the post and its live guild copies get the sound, each rated again by the word filter as any edit is (audit
    # L-03: only the original changed, unrated)
    from ruqqus.helpers import poll_store
    from ruqqus.helpers.word_filter_store import apply_post_severity
    options = poll_store.option_text(g.db, post.id)
    copies = g.db.query(Submission).filter(Submission.repost_id == post.id, Submission.deleted_utc == 0).all()
    for target in [post] + copies:
        target.submission_aux.body = body
        target.submission_aux.body_html = body_html
        apply_post_severity(target, target.title, body_html, extra=options)
        g.db.add(target.submission_aux)
        g.db.add(target)
    space_store.set_replay(g.db, space.id, asset.id)
    attached = media_attach.sync(g.db, v.id, (body, post.url or ""), submission_id=post.id)
    g.db.commit()
    media_safety.scan_later(attached)
    return jsonify({"ok": True, "path": asset.path})


# --- the board: the Space's own discussion (its post takes no comments) -----------------------------------------------

def _hidden_authors(v):
    """The accounts whose messages this reader never sees: blocked either way, or muted by the reader."""
    from ruqqus.classes.userblock import UserBlock
    from ruqqus.classes.usermute import UserMute
    ids = {row[0] for row in g.db.query(UserBlock.target_id).filter(UserBlock.user_id == v.id)}
    ids |= {row[0] for row in g.db.query(UserBlock.user_id).filter(UserBlock.target_id == v.id)}
    ids |= {row[0] for row in g.db.query(UserMute.target_id).filter(UserMute.user_id == v.id)}
    return ids


def _messages_json(space, v, rows):
    """Board messages drawn for this reader: never from anyone in a block with them or whom they muted, the text hidden
    when their word filter says so, a quote that says when its message is gone, and whether they may remove each."""
    from ruqqus.helpers.visibility import text_hidden
    hidden = _hidden_authors(v)
    rows = [row for row in rows if row[2] not in hidden]
    quotes = space_store.messages_by(g.db, {row[5] for row in rows if row[5]})
    ids = {row[2] for row in rows} | {q[2] for q in quotes.values()}
    users = {u.id: u for u in g.db.query(User).filter(User.id.in_(ids)).all()} if ids else {}
    roles = space_store.roles(g.db, space.id)
    may_mod = space_rules.may_moderate(space_rules.role_of(v.id, space.host_id, roles), v.admin_level >= 3)

    def name(uid):
        u = users.get(uid)
        return "Deleted account" if u is None or u.is_deleted else u.shown_name

    out = []
    for row in rows:
        u = users.get(row[2])
        text_off = text_hidden(row[4], v, owner_id=row[2])
        quote = None
        if row[5]:
            q = quotes.get(row[5])
            if q is None or q[7] or q[8] or q[2] in hidden:
                quote = {"id": row[5], "gone": True}
            else:
                q_off = text_hidden(q[4], v, owner_id=q[2])
                quote = {"id": q[0], "gone": False, "author": name(q[2]), "hidden": q_off,
                         "body": None if q_off else q[3][:200]}
        role = space_rules.role_of(row[2], space.host_id, roles)
        out.append({
            "id": row[0], "created_utc": row[6], "author": name(row[2]),
            "username": None if u is None or u.is_deleted else u.username,
            "avatar": None if u is None or u.is_deleted else u.profile_url,
            "role": role if space_rules.may_run(role) else None,
            "body": None if text_off else row[3], "hidden": text_off, "quote": quote,
            "mine": row[2] == v.id, "may_remove": may_mod and row[2] != v.id,
        })
    return out


def _board_json(space):
    slow = int(space.slow_seconds or 0)
    return {"open": space.status == space_rules.LIVE, "slow_seconds": slow, "slow_label": space_rules.slow_label(slow)}


@app.route("/api/spaces/<sid>/messages", methods=["GET"])
@limiter.limit("120/minute", key_func=member_limit_key)
@auth_required
def space_messages(sid, v):
    space = load_space(sid, v)
    try:
        after = int(request.args.get("after", "0") or 0)
    except ValueError:
        after = 0
    rows = space_store.messages(g.db, space.id, after_id=max(0, after))
    return jsonify({"messages": _messages_json(space, v, rows), "board": _board_json(space)})


@app.route("/api/spaces/<sid>/messages", methods=["POST"])
@limiter.limit("30/minute", key_func=member_limit_key)
@is_not_banned
@validate_formkey
def space_message_add(sid, v):
    """A message on the board: only from someone in the Space while it is live, rated by the word filter, slowed by slow
    mode (never the host and co-hosts) and capped per minute and per day. Pushed to readers by id only."""
    space = load_space(sid, v)
    try:
        body = space_rules.clean_message(request.values.get("body"))
    except space_rules.Refused as e:
        return _error(str(e))
    now = int(time.time())
    roles = space_store.roles(g.db, space.id)
    role = space_rules.role_of(v.id, space.host_id, roles)
    roster = call_store.roster(r, space.room, now) if r is not None else []
    inside = space_rules.may_run(role) or any(e["user_id"] == v.id for e in roster)
    refusal = space_rules.message_refusal(space.status, inside, bool(v.is_suspended),
                                          space_store.messages_since(g.db, v.id, now - 60),
                                          space_store.messages_since(g.db, v.id, now - 86400))
    if refusal:
        return _error(refusal, 429 if "fast" in refusal or "a day" in refusal else 409)
    quote_id = None
    raw_quote = str(request.values.get("quote", "") or "").strip()
    if raw_quote:
        if not (raw_quote.isascii() and raw_quote.isdigit() and space_store.quotable(g.db, space.id, int(raw_quote))):
            return _error("That message can't be quoted.", 409)
        quote_id = int(raw_quote)
    slow = int(space.slow_seconds or 0)
    if slow and r is not None and not space_rules.may_moderate(role, v.admin_level >= 3):
        key = f"space:slow:{space.id}:{v.id}"
        if not r.set(key, 1, nx=True, ex=slow):
            wait = max(1, int(r.ttl(key) or slow))
            return _error(f"Slow mode is on: you can write again in {wait} s.", 429)
    from ruqqus.helpers.word_filter_store import get_filter
    mid = space_store.add_message(g.db, space.id, v.id, body, get_filter(g.db).severity(body), quote_id, now)
    g.db.commit()
    live_bus.publish(r, space.topic, "space.message", {"id": mid})
    return jsonify({"messages": _messages_json(space, v, [space_store.message(g.db, mid)])})


def _message_or_404(mid, v):
    row = space_store.message(g.db, mid)
    if row is None or row[7] or row[8]:
        abort(404)
    return row, load_space(row[1], v)


@app.route("/api/space_messages/<int:mid>/delete", methods=["POST"])
@auth_required
@validate_formkey
def space_message_delete(mid, v):
    row, space = _message_or_404(mid, v)
    if not space_rules.may_delete_message(v.id, row[2]):
        abort(404)
    space_store.delete_message(g.db, mid, int(time.time()))
    g.db.commit()
    live_bus.publish(r, space.topic, "space.message_gone", {"id": mid})
    return "", 204


@app.route("/api/space_messages/<int:mid>/remove", methods=["POST"])
@auth_required
@validate_formkey
def space_message_remove(mid, v):
    """The host, a co-host or a site admin takes a message down."""
    row, space = _message_or_404(mid, v)
    if not space_rules.may_moderate(_my_role(space, v), v.admin_level >= 3):
        return _error("Only the host and co-hosts can remove messages.", 403)
    space_store.remove_message(g.db, mid, v.id, int(time.time()))
    g.db.commit()
    live_bus.publish(r, space.topic, "space.message_gone", {"id": mid})
    return "", 204


@app.route("/api/space_messages/<int:mid>/report", methods=["POST"])
@auth_required
@validate_formkey
def space_message_report(mid, v):
    row, space = _message_or_404(mid, v)
    if row[2] == v.id:
        return _error("You can't report your own message.", 409)
    if not space_store.report_message(g.db, mid, v.id, space_rules.clean_reason(request.values.get("reason")), int(time.time())):
        return _error("You've already reported this message.", 409)
    g.db.commit()
    return jsonify({"ok": True})


@app.route("/api/spaces/<sid>/slow", methods=["POST"])
@auth_required
@validate_formkey
def space_slow(sid, v):
    space = load_space(sid, v)
    if not space_rules.may_moderate(_my_role(space, v), v.admin_level >= 3):
        return _error("Only the host and co-hosts can set slow mode.", 403)
    try:
        seconds = space_rules.clean_slow(request.values.get("seconds"))
    except space_rules.Refused as e:
        return _error(str(e))
    space_store.set_slow(g.db, space.id, seconds)
    g.db.commit()
    space.slow_seconds = seconds
    live_bus.publish(r, space.topic, "space.board", {})
    return jsonify({"board": _board_json(space)})


# --- live updates: the host and co-hosts write them above the board (helpers/live_thread_store.py, by its post) ---------

def _runs_updates(space, v):
    return space_rules.may_run(_my_role(space, v))


@app.route("/api/spaces/<sid>/updates", methods=["POST"])
@is_not_banned
@validate_formkey
def space_update_add(sid, v):
    """An update, pushed by id on the post's topic (`live.update`) and read through the live-thread routes, which ask
    for the post as anyone opening it would."""
    from ruqqus.helpers import live_threads as live_rules
    from ruqqus.routes import live_threads as live_routes
    space = load_space(sid, v)
    if not _runs_updates(space, v):
        return _error("Only the host and co-hosts can post updates.", 403)
    if space.status != space_rules.LIVE:
        return _error("Updates can be posted while the Space is live.", 409)
    try:
        body = live_rules.clean_update(request.values.get("body"))
    except live_rules.Refused as e:
        return _error(str(e))
    now = int(time.time())
    if live_store.updates_since(g.db, space.post_id, now - 86400) >= live_rules.UPDATES_PER_DAY:
        return _error(f"A Space can have at most {live_rules.UPDATES_PER_DAY} updates a day.", 429)
    if live_store.thread(g.db, space.post_id) is None:
        live_store.start(g.db, space.post_id, space.host_id, space.started_utc or now,
                         (space.started_utc or now) + space_rules.MAX_SECONDS)
    uid = live_store.add_update(g.db, space.post_id, v.id, body, live_routes._render(body, space.post),
                                live_routes._severity(body), now)
    g.db.commit()
    live_bus.publish(r, live_bus.topic_channel(f"post:{space.post.base36id}"), "live.update", {"id": uid})
    return jsonify(live_routes._update_json(live_store.update(g.db, uid), space.post, v))


@app.route("/api/spaces/<sid>/summary", methods=["GET"])
@auth_required
def space_summary_text(sid, v):
    """The summary's own words, for the host and co-hosts to edit."""
    space = load_space(sid, v)
    if not _runs_updates(space, v):
        abort(403)
    row = live_store.thread(g.db, space.post_id)
    return jsonify({"summary": (row[5] or "") if row is not None else ""})


@app.route("/api/spaces/<sid>/summary", methods=["POST"])
@is_not_banned
@validate_formkey
def space_summary(sid, v):
    """The pinned summary above the updates; empty clears it."""
    from ruqqus.helpers import live_threads as live_rules
    from ruqqus.routes import live_threads as live_routes
    space = load_space(sid, v)
    if not _runs_updates(space, v):
        return _error("Only the host and co-hosts can edit the summary.", 403)
    if space.status != space_rules.LIVE:
        return _error("The summary can be changed while the Space is live.", 409)
    try:
        body = live_rules.clean_summary(request.values.get("summary"))
    except live_rules.Refused as e:
        return _error(str(e))
    now = int(time.time())
    if live_store.thread(g.db, space.post_id) is None:
        live_store.start(g.db, space.post_id, space.host_id, space.started_utc or now,
                         (space.started_utc or now) + space_rules.MAX_SECONDS)
    live_store.set_summary(g.db, space.post_id, body, live_routes._render(body, space.post) if body else "",
                           live_routes._severity(body) if body else 0, now)
    g.db.commit()
    live_bus.publish(r, live_bus.topic_channel(f"post:{space.post.base36id}"), "live.summary", {})
    return jsonify({"thread": live_routes.thread_json(space.post, v)})


# --- the Spaces area: its own place, beside Chat (the owner's choice: a Space is not made from Create a post) --------

SPACE_TABS = (("live", "Live now"), ("upcoming", "Upcoming"), ("recordings", "Recordings"), ("mine", "Mine"))


def _visible_space_posts(rows, v):
    """(space row, post) for the rows whose post this viewer may open, as the Space page decides (`_post_for`)."""
    posts = {p.id: p for p in get_posts([row[1] for row in rows], v=v)} if rows else {}
    shown = []
    for row in rows:
        post = posts.get(row[1])
        if post is None or post.deleted_utc or post.is_banned or post.in_private_guild_for(v) or post_hidden(post, v):
            continue
        shown.append((row, post))
    return shown


@app.route("/spaces", methods=["GET"])
@auth_desired
def spaces_home(v):
    tab = request.args.get("tab", "live")
    if tab not in dict(SPACE_TABS) or (tab == "mine" and not v):
        tab = "live"
    shown = _visible_space_posts(space_store.listing(g.db, tab, v.id if v else None), v)
    if tab == "live":
        # the busiest first: how many are in each now is the post's space card data (helpers/space_view.py)
        shown.sort(key=lambda pair: -((space_view.space_of(pair[1], v) or {}).get("people") or 0))
    return render_template("spaces_home.html", v=v, tab=tab, tabs=SPACE_TABS, items=shown)


@app.route("/spaces/new", methods=["GET"])
@auth_required
def spaces_new(v):
    """Setting up a Space. The form posts to the real POST /api/vue/submit (every posting rule, throttle and ban, and
    `space_store.create` in submit_post), so nothing about posting lives here."""
    return render_template("space_new.html", v=v, space_max_stage=space_rules.MAX_STAGE,
                           space_max_listeners=space_rules.MAX_LISTENERS, space_video_stage=space_rules.VIDEO_STAGE_MAX,
                           space_video_people=space_rules.VIDEO_PEOPLE_MAX)


@app.route("/space/post/<pid>", methods=["GET"])
@auth_required
def space_for_post(pid, v):
    """From a Space's post (or a guild copy of it) to the Space: where the setup page goes once it is made."""
    post = get_post(pid, v=v)
    rows = space_store.for_posts(g.db, [post.repost_id or post.id])
    row = rows.get(post.repost_id or post.id)
    if row is None or _post_for(row, v) is None:
        abort(404)
    return redirect(f"/space/{row[0]}")


def live_space_for(user, v):
    """Jinja global: the Space `user` is hosting now, for the live banner on their profile, or None. Never across a
    block either way, never one whose post the viewer may not open."""
    from jinja2 import Undefined
    v = None if isinstance(v, Undefined) else v
    row = space_store.live_for_host(g.db, user.id)
    if row is None:
        return None
    if v is not None and v.id != user.id and v.any_block_exists(user):
        return None
    post = _post_for(row, v)
    if post is None:
        return None
    people = len({e["user_id"] for e in call_store.roster(r, space_store.room(row[0]), int(time.time()))}) if r is not None else 0
    return {"id": row[0], "title": post.title, "people": people}


app.jinja_env.globals.update(live_space_for=live_space_for, space_tabs=SPACE_TABS)


# --- in feeds: the card under the post (partials/space_card.html; helpers/space_view.py) ----------------------------

app.jinja_env.globals.update(space_of=space_view.space_of, space_schedule_days=space_rules.SCHEDULE_DAYS)


@app.template_filter("space_time")
def space_time(ts):
    """A scheduled start for readers without script ("Sat 18 Oct, 18:30 UTC"); space_card.js shows local time."""
    import datetime
    return datetime.datetime.fromtimestamp(int(ts or 0), datetime.timezone.utc).strftime("%a %d %b, %H:%M UTC")


@app.template_filter("space_iso")
def space_iso(ts):
    import datetime
    return datetime.datetime.fromtimestamp(int(ts or 0), datetime.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


# --- admins ------------------------------------------------------------------------------------------------------

@app.route("/admin/space_reports", methods=["GET"])
@admin_level_required(3)
def admin_space_reports(v):
    reports = []
    for space_id, count, reasons in space_store.open_reports(g.db):
        row = space_store.get(g.db, space_id)
        if row is None:
            continue
        post = get_post(base36encode(row[1]), v=v, graceful=True)
        reports.append({"space_id": space_id, "count": count, "reasons": reasons, "status": row[3], "post": post})
    messages = []
    for row, count, reasons in space_store.open_message_reports(g.db):
        author = g.db.query(User).filter_by(id=row[2]).first()
        messages.append({"id": row[0], "space_id": row[1], "body": row[3], "count": count, "reasons": reasons,
                         "author": author, "removed": bool(row[8]), "deleted": bool(row[7])})
    return render_template("admin/space_reports.html", v=v, reports=reports, messages=messages)


@app.route("/admin/space_reports/<int:sid>/dismiss", methods=["POST"])
@admin_level_required(3)
@validate_formkey
def admin_space_report_dismiss(sid, v):
    space_store.resolve_reports(g.db, sid, int(time.time()))
    g.db.commit()
    return redirect("/admin/space_reports")


@app.route("/admin/space_reports/<int:sid>/end", methods=["POST"])
@admin_level_required(3)
@validate_formkey
def admin_space_report_end(sid, v):
    row = space_store.get(g.db, sid)
    if row is None:
        abort(404)
    post = get_post(base36encode(row[1]), v=v, graceful=True)
    _end(_Space(row, post), int(time.time()), by_admin=True)
    space_store.resolve_reports(g.db, sid, int(time.time()))
    g.db.commit()
    return redirect("/admin/space_reports")


@app.route("/admin/space_messages/<int:mid>/remove", methods=["POST"])
@admin_level_required(3)
@validate_formkey
def admin_space_message_remove(mid, v):
    row = space_store.message(g.db, mid)
    if row is None:
        abort(404)
    now = int(time.time())
    space_store.remove_message(g.db, mid, v.id, now)
    space_store.resolve_message_reports(g.db, mid, now)
    g.db.commit()
    live_bus.publish(r, live_bus.topic_channel(f"space:{row[1]}"), "space.message_gone", {"id": mid})
    return redirect("/admin/space_reports")


@app.route("/admin/space_messages/<int:mid>/dismiss", methods=["POST"])
@admin_level_required(3)
@validate_formkey
def admin_space_message_dismiss(mid, v):
    space_store.resolve_message_reports(g.db, mid, int(time.time()))
    g.db.commit()
    return redirect("/admin/space_reports")
