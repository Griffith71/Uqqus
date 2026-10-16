"""Broadcast channels: making one, joining and leaving, helpers, a public channel's messages, reports, the list on a
profile (rules in helpers/broadcasts.py, SQL in helpers/broadcast_store.py; the chat page shows them under Channels).

Every change is a POST with the form key. A channel you may not see is a 404, whatever the reason; one in a block
with its owner, either way, says only "This channel isn't available." The Matrix side goes first and the database is
committed once it went through.
"""
import secrets
import time

from flask import *
from sqlalchemy import text

from ruqqus.helpers.wrappers import *
from ruqqus.helpers.get import *
from ruqqus.classes import *
import ruqqus.helpers.matrix_client as matrix
from ruqqus.__main__ import app

from ruqqus.helpers import broadcasts as channel_rules
from ruqqus.helpers import broadcast_store as channel_store
from ruqqus.helpers import circle_guard, circle_store
from ruqqus.helpers.visibility import text_hidden, user_hidden

_DAY = 86400


def _error(message, status=400):
    return jsonify({"error": message}), status


def _now():
    return int(time.time())


def _mxid(user):
    from ruqqus.routes.chat import _ensure_provisioned
    return _ensure_provisioned(user)


def _may_see(channel, v):
    """May v see (and join) this channel? Public: anyone signed in; for circles: the people in any of its circles now."""
    if channel[4] == channel_rules.PUBLIC:
        return True
    return circle_guard.may_see(g.db, channel_rules.CIRCLES, channel[1], v, circle_ids=channel_store.circle_ids(g.db, channel[0]))


def _blocked(channel, v):
    if v.id == channel[1]:
        return False
    owner = g.db.query(User).filter_by(id=channel[1]).first()
    return owner is None or bool(v.any_block_exists(owner))


def _channel(bid, v, member_only=True):
    """(channel row, my member row): a 404 when v may not see it (or is not in it, for member_only)."""
    channel = channel_store.get(g.db, bid)
    if channel is None:
        abort(404)
    me = channel_store.member(g.db, bid, v.id)
    if member_only and me is None:
        abort(404)
    if me is None and (not _may_see(channel, v) or _blocked(channel, v)):
        abort(404)
    return channel, me


def _writers(channel):
    ids = [channel[1]] + channel_store.helpers(g.db, channel[0])
    users = {u.id: u for u in g.db.query(User).filter(User.id.in_(ids)).all()}
    return [{"mxid": matrix.mxid_for(uid), "shown_name": users[uid].shown_name, "username": users[uid].username}
            for uid in ids if uid in users]


def channel_json(channel, me, v):
    owner = g.db.query(User).filter_by(id=channel[1]).first()
    hidden = text_hidden(channel[3], v)
    role = me[0] if me else None
    return {
        "kind": "channel",
        "key": f"b{channel[0]}",
        "channel_id": channel[0],
        "room_id": channel[5],
        "label": "A channel" if hidden else channel[2],
        "public": channel[4] == channel_rules.PUBLIC,
        "owner": {"id": owner.id, "username": owner.username, "shown_name": owner.shown_name,
                  "profile_url": owner.profile_url, "mxid": matrix.mxid_for(owner.id)} if owner else None,
        "role": role,
        "may_write": channel_rules.may_write(role),
        "size": channel_store.size(g.db, channel[0]),
        "unread_count": me[1] if me else 0,
        "last_activity_utc": channel[6],
        "status": "joined" if me else None,
        "writers": _writers(channel),
    }


@app.route("/api/broadcasts", methods=["POST"])
@is_not_banned
@validate_formkey
def broadcast_create(v):
    try:
        name = channel_rules.clean_name(request.values.get("name"))
    except channel_rules.Refused as e:
        return _error(str(e))
    audience = channel_rules.CIRCLES if request.values.get("audience") == "4" else channel_rules.PUBLIC
    circle_ids = []
    if audience == channel_rules.CIRCLES:
        circle_ids, error = circle_store.resolve(g.db, v.id, request.values.getlist("circles"))
        if error:
            return _error(error)
    refusal = channel_rules.make_refusal(bool(v.is_bot), channel_store.owned_count(g.db, v.id), audience, circle_ids)
    if refusal:
        return _error(refusal, 409 if "at most" in refusal else 400)
    from ruqqus.helpers.word_filter_store import get_filter
    try:
        room_id = matrix.create_channel_room(_mxid(v), encrypted=audience == channel_rules.CIRCLES)
    except matrix.MatrixError:
        return _error("Chat isn't answering right now. Try again in a moment.", 502)
    bid = channel_store.create(g.db, v.id, name, get_filter(g.db).severity(name), audience, room_id, circle_ids, _now())
    g.db.commit()
    return jsonify(channel_json(channel_store.get(g.db, bid), channel_store.member(g.db, bid, v.id), v))


@app.route("/api/broadcasts/<int:bid>", methods=["GET"])
@auth_required
def broadcast_info(bid, v):
    channel, me = _channel(bid, v, member_only=False)
    return jsonify(channel_json(channel, me, v))


@app.route("/api/broadcasts/<int:bid>/join", methods=["POST"])
@auth_required
@validate_formkey
def broadcast_join(bid, v):
    channel = channel_store.get(g.db, bid)
    if channel is None:
        abort(404)
    if channel_store.member(g.db, bid, v.id):
        return jsonify(channel_json(channel, channel_store.member(g.db, bid, v.id), v))
    refusal = channel_rules.join_refusal(False, _blocked(channel, v), _may_see(channel, v), channel_store.size(g.db, bid), channel[4])
    if refusal:
        return _error(refusal, 403 if refusal == channel_rules.UNAVAILABLE else 409)
    try:
        matrix.put_in_room(channel[5], _mxid(v))
    except matrix.MatrixError:
        return _error("Chat isn't answering right now. Try again in a moment.", 502)
    channel_store.add_member(g.db, bid, v.id, channel_rules.MEMBER, _now())
    g.db.commit()
    return jsonify(channel_json(channel, channel_store.member(g.db, bid, v.id), v))


@app.route("/api/broadcasts/<int:bid>/leave", methods=["POST"])
@auth_required
@validate_formkey
def broadcast_leave(bid, v):
    channel, me = _channel(bid, v)
    if me[0] == channel_rules.OWNER:
        return _error("The owner deletes a channel instead of leaving it.", 409)
    try:
        matrix.leave_room_as(channel[5], matrix.mxid_for(v.id))
    except matrix.MatrixError:
        return _error("Chat isn't answering right now. Try again in a moment.", 502)
    was_helper = me[0] == channel_rules.HELPER
    channel_store.remove_member(g.db, bid, v.id)
    if was_helper:
        _sync_writers(channel)
    g.db.commit()
    return "", 204


def _sync_writers(channel):
    writers = [matrix.mxid_for(uid) for uid in [channel[1]] + channel_store.helpers(g.db, channel[0])]
    matrix.set_writers(channel[5], writers)


@app.route("/api/broadcasts/<int:bid>/helpers", methods=["POST"])
@auth_required
@validate_formkey
def broadcast_helpers(bid, v):
    channel, me = _channel(bid, v)
    target = get_user(request.values.get("username", "").lstrip("@"), graceful=True)
    target_row = channel_store.member(g.db, bid, target.id) if target else None
    adding = request.values.get("on") != "0"
    refusal = channel_rules.helper_refusal(me[0], target_row[0] if target_row else None,
                                           len(channel_store.helpers(g.db, bid)), adding)
    if refusal:
        return _error(refusal, 403 if refusal.startswith("Only") else 409)
    channel_store.set_role(g.db, bid, target.id, channel_rules.HELPER if adding else channel_rules.MEMBER)
    try:
        _sync_writers(channel)
    except matrix.MatrixError:
        g.db.rollback()
        return _error("Chat isn't answering right now. Try again in a moment.", 502)
    g.db.commit()
    return jsonify(channel_json(channel, channel_store.member(g.db, bid, v.id), v))


@app.route("/api/broadcasts/<int:bid>/messages", methods=["POST"])
@is_not_banned
@validate_formkey
def broadcast_message(bid, v):
    """A public channel's message: rated, counted and sent by the site as its writer. A channel for circles is written
    by its writers' own browsers (end-to-end encrypted), never here."""
    channel, me = _channel(bid, v)
    if channel[4] != channel_rules.PUBLIC:
        return _error("This channel is written from the chat itself.", 409)
    try:
        body = channel_rules.clean_message(request.values.get("body"))
    except channel_rules.Refused as e:
        return _error(str(e))
    now = _now()
    refusal = channel_rules.post_refusal(me[0], bool(v.is_suspended), channel_store.posts_since(g.db, bid, now - _DAY))
    if refusal:
        return _error(refusal, 403 if refusal.startswith(("Only", "Your")) else 429)
    from ruqqus.helpers.word_filter_store import get_filter
    severity = get_filter(g.db).severity(body)
    try:
        event_id = matrix.send_as(channel[5], matrix.mxid_for(v.id),
                                  {"msgtype": "m.text", "body": body, channel_rules.SEVERITY_KEY: severity},
                                  secrets.token_hex(12))
    except matrix.MatrixError:
        return _error("Chat isn't answering right now. Try again in a moment.", 502)
    channel_store.record_post(g.db, bid, v.id, event_id, body, severity, now)
    g.db.commit()
    return jsonify({"event_id": event_id})


@app.route("/api/broadcasts/<int:bid>/mark_read", methods=["POST"])
@auth_required
@validate_formkey
def broadcast_mark_read(bid, v):
    _channel(bid, v)
    channel_store.mark_read(g.db, bid, v.id, _now())
    g.db.commit()
    return "", 204


@app.route("/api/broadcasts/<int:bid>/report", methods=["POST"])
@auth_required
@validate_formkey
def broadcast_report(bid, v):
    channel, me = _channel(bid, v)
    post = channel_store.post_by_event(g.db, bid, request.values.get("event_id", ""))
    if post is None or post[3]:
        abort(404)
    if post[1] == v.id:
        return _error("You can't report your own message.", 409)
    reason = " ".join(str(request.values.get("reason") or "").split())[:channel_rules.REPORT_MAX]
    if not channel_store.report(g.db, bid, post[0], v.id, reason, _now()):
        return _error("You've already reported this message.", 409)
    g.db.commit()
    return jsonify({"ok": True})


@app.route("/api/broadcasts/<int:bid>/delete", methods=["POST"])
@auth_required
@validate_formkey
def broadcast_delete(bid, v):
    channel, me = _channel(bid, v)
    if me[0] != channel_rules.OWNER:
        abort(403)
    channel_store.close(g.db, bid, _now())
    g.db.commit()
    return "", 204


@app.route("/@<username>/channels", methods=["GET"])
@auth_required
def profile_channels(username, v):
    """An account's channels that v may join: public ones, and those for circles v is in. Never one in a block."""
    owner = get_user(username, v=v, graceful=True)
    if owner is None or owner.is_deleted or user_hidden(owner, v) or (owner.id != v.id and v.any_block_exists(owner)):
        abort(404)
    channels = []
    for row in channel_store.of_owner(g.db, owner.id):
        me = channel_store.member(g.db, row[0], v.id)
        if me is None and not _may_see(row, v):
            continue
        channels.append(channel_json(row, me, v))
    return render_template("channels.html", v=v, owner=owner, channels=channels,
                           max_channels=channel_rules.MAX_PER_OWNER)


# --- admins ------------------------------------------------------------------------------------------------------

@app.route("/admin/broadcast_reports", methods=["GET"])
@admin_level_required(3)
def admin_broadcast_reports(v):
    reports = []
    for bid, pid, body, event_id, count, reasons in channel_store.open_reports(g.db):
        channel = channel_store.get(g.db, bid)
        reports.append({"channel": channel, "post_id": pid, "body": body, "count": count, "reasons": reasons})
    return render_template("admin/broadcast_reports.html", v=v, reports=reports)


@app.route("/admin/broadcast_reports/<int:pid>/<action>", methods=["POST"])
@admin_level_required(3)
@validate_formkey
def admin_broadcast_report_act(pid, action, v):
    row = g.db.execute(text("SELECT broadcast_id, event_id FROM broadcast_posts WHERE id = :p"), {"p": pid}).fetchone()
    if row is None or action not in ("remove", "dismiss"):
        abort(404)
    now = _now()
    if action == "remove":
        channel = channel_store.get(g.db, row[0])
        if channel is not None:
            try:
                matrix.redact_as_bot(channel[5], row[1], secrets.token_hex(12), "Removed by the site's admins")
            except matrix.MatrixError:
                return _error("Chat isn't answering right now. Try again in a moment.", 502)
        channel_store.remove_post(g.db, pid, now)
    channel_store.resolve_reports(g.db, pid, now)
    g.db.commit()
    return redirect("/admin/broadcast_reports")
