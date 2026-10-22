"""Guild channels: text and announcement channels inside a guild (rules in helpers/guild_channels.py, SQL in
helpers/guild_channel_store.py; the chat page lists them under Guilds and a guild's Chat tab frames the chat page).

Every channel is read through its guild's own visibility (`Board.can_view`, `board_hidden`, a banned guild is a 404) and
the channel's read ladder; a channel the viewer may not read is a 404, whatever the reason (only "join the guild" says
why). Messages are written only here: the site checks who may write, slow mode and the counts, rates the text with the
word filter and sends it as the writer with its signature, and the appservice webhook (routes/chat.py) removes anything
without a valid, unused one. Running channels is guild configuration (`config`); removing messages, working reports and
muting are content (`content`). Every change is a POST with the form key and is written to the guild's moderation log
(a removed message's text is not: the log names the channel and the writer).
"""
import html
import secrets
import time

from flask import *
from sqlalchemy import and_, or_, text

from ruqqus.helpers.wrappers import *
from ruqqus.helpers.get import *
from ruqqus.classes import *
import ruqqus.helpers.matrix_client as matrix
from ruqqus.__main__ import app, r, limiter

from ruqqus.helpers import guild_channels as rules
from ruqqus.helpers import guild_channel_store as store
from ruqqus.helpers import call_store
from ruqqus.helpers import guild_automod as automod
from ruqqus.helpers import guild_roles as role_rules
from ruqqus.helpers import guild_role_store as role_store
from ruqqus.helpers.base36 import base36encode
from ruqqus.helpers.alerts import send_notification
from ruqqus.helpers.visibility import board_hidden, text_hidden, user_hidden

_DAY = 86400
_BUSY = "Chat isn't answering right now. Try again in a moment."


def _error(message, status=400):
    return jsonify({"error": message}), status


def _now():
    return int(time.time())


def _mxid(user):
    from ruqqus.routes.chat import _ensure_provisioned
    return _ensure_provisioned(user)


def _severity(text):
    from ruqqus.helpers.word_filter_store import get_filter
    return get_filter(g.db).severity(text)


def _log(board, v, kind, note="", target_user_id=0):
    g.db.add(ModAction(kind=kind, user_id=v.id, board_id=board.id, note=html.escape(note)[:256],     # the log draws it as HTML
                       target_user_id=target_user_id))


# --- what a viewer may do ---------------------------------------------------------------------------------------------------

def _hidden(board, v, row):
    """The guild is hidden from this viewer by their word filter (the guild's guildmasters still see it)."""
    return bool(board_hidden(board, v)) and not row.mod


def _read_refusal(row, v, hidden):
    admin = v.admin_level >= 4
    return rules.read_refusal(
        row.read_level, can_view=rules.guild_visible(bool(row.is_private), bool(row.contributor), bool(row.mod), admin),
        hidden=hidden, bot=bool(v.is_bot), exiled=bool(row.exiled) and v.admin_level < 2, joined=bool(row.joined),
        contributor=bool(row.contributor), mod=bool(row.mod), admin=admin,
        role_read=role_rules.resolve(row.role_allow_read, row.role_deny_read))


def _write_refusal(row, v, read_result):
    return rules.write_refusal(
        row.write_level, read_result, suspended=bool(v.is_suspended), muted=bool(row.muted) and v.admin_level < 4,
        joined=bool(row.joined), contributor=bool(row.contributor), mod=bool(row.mod), content_mod=bool(row.content_mod),
        rules_pending=rules.rules_pending(row.rules_gate, row.rules, row.acked_key),
        role_write=role_rules.resolve(row.role_allow_write, row.role_deny_write))


def _can_remove(row):
    """May this person remove messages here: a guildmaster with the content permission, or a role that may."""
    return bool(row.content_mod or row.role_remove)


def _can_pin(row):
    return bool(row.content_mod or row.role_pin)


def _slow_exempt(row):
    return rules.slow_exempt(row.mod) or bool(row.role_slow_exempt)


def _messages_only(row):
    """A response when this channel holds no messages (voice, stage), else None."""
    if row.kind not in rules.MESSAGE_KINDS:
        return _error("This channel has no messages.", 409)
    return None


def _plaintext_only(row):
    """A response when this channel holds no messages the site can read: voice and stage have none, and an end-to-end
    encrypted channel's are read only by the people in its room."""
    refused = _messages_only(row)
    if refused:
        return refused
    if row.encrypted:
        return _error(rules.ENCRYPTED_REFUSAL, 409)
    return None


def _load(cid, v):
    """(channel row with the viewer's standing, its guild) when v may read it; otherwise a 404 (403 with "join the
    guild" for a member-level channel of a guild they are not in)."""
    row = store.one_for(g.db, cid, v.id)
    if row is None:
        abort(404)
    board = g.db.query(Board).filter_by(id=row.board_id).first()
    if board is None or (board.is_banned and v.admin_level < 3):
        abort(404)
    refusal = _read_refusal(row, v, _hidden(board, v, row))
    if refusal == rules.JOIN_FIRST:
        abort(make_response(jsonify({"error": refusal}), 403))
    if refusal:
        abort(404)
    return row, board


def _stage_card(row, v):
    """A stage channel's Space as this viewer may see it: {status, title, url, scheduled_utc}, or a status that says there
    is none (it was never set, it is gone, or they may not open its post: all read the same)."""
    if row.kind != rules.STAGE or not row.space_post_id:
        return None
    import html as _html
    from ruqqus.helpers import space_store
    from ruqqus.routes.spaces import _post_for
    space = space_store.for_posts(g.db, [row.space_post_id]).get(row.space_post_id)
    post = _post_for(space, v) if space is not None else None
    if post is None:
        return {"status": "none"}
    return {"status": space[3], "scheduled_utc": space[4], "title": _html.unescape(post.title or "")[:200], "url": f"/space/{space[0]}"}


def voice_people(row):
    """How many people are in a voice channel's call now."""
    if row.kind != rules.VOICE or r is None:
        return 0
    try:
        return len({e["user_id"] for e in call_store.roster(r, row.matrix_room_id, _now())})
    except Exception:
        return 0


def channel_json(row, v):
    """One channel as the chat page and the guild's Chat tab draw it, for this viewer."""
    name_hidden = bool(text_hidden(row.word_severity, v)) and not row.mod
    now = _now()
    category_hidden = bool(row.category_name) and bool(text_hidden(row.category_severity or 0, v)) and not row.mod
    return {
        "kind": "guild_channel",
        "key": f"h{row.id}",
        "channel_id": row.id,
        "room_id": None if row.kind in rules.ROOMLESS else row.matrix_room_id,
        "guild": row.board_name,
        "label": "A channel" if name_hidden else row.name,
        "topic": "" if name_hidden else row.topic,
        "rules": "" if name_hidden else row.rules,
        "channel_kind": row.kind,
        "may_write": _write_refusal(row, v, None) is None,
        "needs_rules": bool(rules.rules_pending(row.rules_gate, row.rules, row.acked_key)) and not row.mod and not name_hidden,
        "category": None if not row.category_name else ("A category" if category_hidden else row.category_name),
        "category_id": row.category_id,
        "slow_mode_seconds": 0 if _slow_exempt(row) else row.slow_mode_seconds,
        "can_moderate": _can_remove(row),
        "can_remove": _can_remove(row),
        "can_pin": _can_pin(row),
        "muted": rules.is_muted(row.muted_until, now),
        "unread_count": 1 if store.unread(row, now) else 0,
        "mentions": int(row.mentions or 0),
        "encrypted": bool(row.encrypted),
        "voice_people": voice_people(row),
        "space": _stage_card(row, v),
        "last_activity_utc": row.last_activity_utc,
        "in_room": row.in_room_since is not None,
    }


def conversations_for(v, limit=200):
    """The channels of every guild v is in that v may read, a guild at a time (the chat page's Guilds tab)."""
    rows = store.joined_guilds_for(g.db, v.id)
    ids = {row.board_id for row in rows}
    boards = {b.id: b for b in g.db.query(Board).filter(Board.id.in_(ids)).all()} if ids else {}
    out = []
    for row in rows:
        board = boards.get(row.board_id)
        if board is None or _read_refusal(row, v, _hidden(board, v, row)):
            continue
        out.append(channel_json(row, v))
        if len(out) >= limit:
            break
    return out


def _visible_board(guildname, v):
    board = get_guild(guildname, v=v, graceful=True)
    if board is None or (board.is_banned and not (v and v.admin_level >= 3)) or not board.can_view(v):
        abort(404)
    return board


def has_chat(board, v):
    """Whether the guild has channels (the Chat tab): a count, never which channels."""
    return v is not None and board.can_view(v) and not board.is_banned and store.count(g.db, board.id) > 0


app.jinja_env.globals.update(guild_has_chat=has_chat)


# --- reading ------------------------------------------------------------------------------------------------------------------

@app.route("/api/guilds/<guildname>/channels", methods=["GET"])
@auth_required
def guild_channels_list(guildname, v):
    board = _visible_board(guildname, v)
    rows = store.of_board_for(g.db, board.id, v.id)
    return jsonify({"channels": [channel_json(row, v) for row in rows if not _read_refusal(row, v, _hidden(board, v, row))]})


@app.route("/+<guildname>/chat", methods=["GET"])
@auth_desired
def guild_chat(guildname, v):
    board = _visible_board(guildname, v)
    if v is None:
        return render_template("guild/chat.html", v=v, b=board, tab="chat", state="signin", channels=[], selected=None)
    rows = store.of_board_for(g.db, board.id, v.id)
    channels, need_join = [], False
    for row in rows:
        refusal = _read_refusal(row, v, _hidden(board, v, row))
        if refusal == rules.JOIN_FIRST:
            need_join = True
        elif not refusal:
            channels.append(channel_json(row, v))
    wanted = request.args.get("c", type=int)
    selected = next((c for c in channels if c["channel_id"] == wanted), channels[0] if channels else None)
    state = "ok" if channels else ("join" if need_join else "none")
    sections = {}                                  # the channels without a category first, then each category in order
    for c in channels:
        sections.setdefault(c["category_id"] or 0, {"name": c["category"], "channels": []})["channels"].append(c)
    return render_template("guild/chat.html", v=v, b=board, tab="chat", state=state, channels=channels, selected=selected,
                           sections=list(sections.values()), gm=bool(board.has_mod(v, "config")))


# --- joining, writing, reading ----------------------------------------------------------------------------------------------

def _sync_writers(row):
    """An announcement room's writers (power level 50): the guildmasters with the content permission who are in it."""
    try:
        matrix.set_writers(row.matrix_room_id,
                           [matrix.mxid_for(uid) for uid in store.writer_ids(g.db, row.id, row.board_id)])
    except matrix.MatrixError:
        app.logger.warning(f"guild channel {row.id}: writers not synced")      # the sync step does it within minutes


@app.route("/api/guilds/channels/<int:cid>/join", methods=["POST"])
@auth_required
@validate_formkey
def guild_channel_join(cid, v):
    """Put the viewer in the channel's room (they open a channel to join it, never all at once when a guild is joined)."""
    row, board = _load(cid, v)
    refused = _messages_only(row)
    if refused:
        return refused
    if not store.in_room(g.db, cid, v.id):
        refusal = rules.full_refusal(store.size(g.db, cid), bool(row.encrypted))
        if refusal:
            return _error(refusal, 409)
        if row.encrypted and _write_refusal(row, v, None):
            # the site can't stop someone writing in an encrypted room, so only people who may write are in it
            return _error(_write_refusal(row, v, None), 403)
        try:
            matrix.put_in_room(row.matrix_room_id, _mxid(v))
        except matrix.MatrixError:
            return _error(_BUSY, 502)
        store.join(g.db, cid, v.id, _now())
        if row.kind == rules.ANNOUNCEMENT and (row.content_mod or row.role_allow_write):
            _sync_writers(row)
        g.db.commit()
    return jsonify(channel_json(store.one_for(g.db, cid, v.id), v))


def _mentioned(row, board, v, post_id, body, now):
    """Record who a message mentions (@name) and return [(person, notification text)] for those to be told. Only people
    who may read the channel, in no block with the writer, who haven't muted the writer or the channel; the mention
    still counts for them, but they are told once per writer and channel an hour."""
    told = []
    recorded = []
    for name in rules.parse_mentions(body):
        target = get_user(name, graceful=True)
        if target is None or target.id == v.id or target.is_deleted or target.is_banned or target.is_bot:
            continue
        if v.any_block_exists(target):
            continue
        standing = store.one_for(g.db, row.id, target.id)
        if standing is None or _read_refusal(standing, target, _hidden(board, target, standing)):
            continue
        if g.db.query(UserMute).filter_by(user_id=target.id, target_id=v.id).first() or rules.is_muted(standing.muted_until, now):
            continue
        recorded.append(target.id)
        if store.notice_due(r, row.id, v.id, target.id):
            told.append((target, rules.mention_text(v.username, row.name, board.name, f"{board.permalink}/chat?c={row.id}")))
    store.record_mentions(g.db, row.id, post_id, recorded, now)
    return told


@app.route("/api/guilds/channels/<int:cid>/messages", methods=["POST"])
@is_not_banned
@validate_formkey
def guild_channel_message(cid, v):
    """A message, rated and counted here and sent by the site as its writer with the site's signature."""
    row, board = _load(cid, v)
    refused = _plaintext_only(row)
    if refused:
        return refused
    try:
        body = rules.clean_message(request.values.get("body"))
    except rules.Refused as e:
        return _error(str(e))
    refusal = _write_refusal(row, v, None)
    if refusal:
        return _error(refusal, 403)
    settings = store.automod_of(g.db, board.id)
    if settings is not None and not row.mod:                       # the guild's own rules (guildmasters are never held to them)
        since = store.member_since(g.db, board.id, v.id)
        now_ = _now()
        refusal = automod.refusal(settings, account_days=(now_ - (v.created_utc or now_)) // _DAY,
                                  member_days=(now_ - since) // _DAY if since else 0, body=body, exempt=False)
        if refusal:
            return _error(refusal, 403)
    thread = ""
    if request.values.get("thread"):
        thread = rules.quoted_event_id(request.values.get("thread"))
        if not thread:
            return _error("That message can't have a thread.")
        refusal = rules.thread_refusal(store.post_by_event(g.db, cid, thread))
        if refusal:
            return _error(refusal, 409)
    now = _now()
    slow_key_set = False
    try:
        refusal = rules.message_refusal(store.burst_count(r, cid, v.id, now), store.posts_since(g.db, cid, v.id, now - _DAY))
        if refusal:
            return _error(refusal, 429)
        if not _slow_exempt(row) and row.slow_mode_seconds:
            wait = store.slow_wait(r, cid, v.id, row.slow_mode_seconds)
            if wait:
                return _error(rules.slow_message(wait), 429)
            slow_key_set = True
        nonce = store.new_nonce(r)
    except Exception:
        app.logger.warning("guild channel message: Redis unavailable", exc_info=True)
        return _error(_BUSY, 503)

    mxid = matrix.mxid_for(v.id)
    severity = _severity(body)
    content = {"msgtype": "m.text", "body": body, rules.SEVERITY_KEY: severity, rules.NONCE_KEY: nonce,
               rules.SITE_KEY: rules.site_mark(app.config["SECRET_KEY"], row.matrix_room_id, mxid, severity, nonce, body)}
    quote = rules.quoted_event_id(request.values.get("quote"))
    if thread:
        # a message in a thread (Matrix's m.thread): under its root, quoting the root unless it quotes another
        content["m.relates_to"] = {"rel_type": "m.thread", "event_id": thread, "is_falling_back": not quote,
                                   "m.in_reply_to": {"event_id": quote or thread}}
    elif quote:
        content["m.relates_to"] = {"m.in_reply_to": {"event_id": quote}}
    try:
        if not store.in_room(g.db, cid, v.id):
            if rules.full_refusal(store.size(g.db, cid)):
                return _error(rules.full_refusal(store.size(g.db, cid)), 409)
            matrix.put_in_room(row.matrix_room_id, _mxid(v))
            store.join(g.db, cid, v.id, now)
        event_id = matrix.send_as(row.matrix_room_id, mxid, content, secrets.token_hex(12))
    except matrix.MatrixError:
        if slow_key_set:
            store.slow_release(r, cid, v.id)
        return _error(_BUSY, 502)
    post_id = store.record_post(g.db, cid, v.id, event_id, body, severity, now, thread)
    store.mark_read(g.db, cid, v.id, now)
    told = _mentioned(row, board, v, post_id, body, now)
    g.db.commit()
    for target, text_ in told:                        # send_notification commits on its own
        send_notification(target, text_)
    return jsonify({"event_id": event_id})


@app.route("/api/guilds/channels/<int:cid>/mark_read", methods=["POST"])
@auth_required
@validate_formkey
def guild_channel_mark_read(cid, v):
    _load(cid, v)
    store.mark_read(g.db, cid, v.id, _now())
    store.clear_mentions(g.db, cid, v.id, _now())
    g.db.commit()
    return "", 204


def _sender_map(user_ids, v, board_id=None):
    """{user id: {id, shown_name, username, blocked, muted}} for these accounts, as this viewer may see them. Whom the
    viewer blocked or muted is marked for their own client; the other person is never told."""
    users = g.db.query(User).filter(User.id.in_(list(user_ids))).all() if user_ids else []
    blocked = {b if a == v.id else a for a, b in g.db.query(UserBlock.user_id, UserBlock.target_id).filter(
        or_(UserBlock.user_id == v.id, UserBlock.target_id == v.id)).all()}
    muted = {t for (t,) in g.db.query(UserMute.target_id).filter(UserMute.user_id == v.id, UserMute.target_id.in_(list(user_ids))).all()} \
        if user_ids else set()
    out = {}
    for u in users:
        gone = u.is_deleted or u.is_banned
        out[u.id] = {
            "id": u.id,
            "shown_name": "Deleted account" if u.is_deleted else ("A member" if user_hidden(u, v) else u.shown_name),
            "username": None if (gone or user_hidden(u, v)) else u.username,
            "blocked": u.id in blocked,
            "muted": u.id in muted,
        }
    if board_id is not None and out:                       # each person's first role, as a chip beside their name
        for uid, (name, colour, severity) in role_store.top_roles(g.db, board_id, list(out)).items():
            if uid in out and not text_hidden(severity or 0, v):
                out[uid]["role"] = {"name": name, "colour": colour if colour in role_rules.COLOURS else role_rules.DEFAULT_COLOUR}
    return out


@app.route("/api/guilds/channels/<int:cid>/senders", methods=["GET"])
@auth_required
def guild_channel_senders(cid, v):
    """Names for the people who wrote in a channel (?ids=1,2,3, at most 50): only people who wrote in this channel are
    ever answered, so this is no way to look an account up."""
    row, _board = _load(cid, v)
    ids = []
    for part in (request.args.get("ids") or "").split(",")[:50]:
        part = part.strip()
        if part.isascii() and part.isdigit():
            ids.append(int(part))
    known = store.members_among(g.db, cid, ids) if row.encrypted else store.authors_among(g.db, cid, ids)
    return jsonify({"senders": {str(uid): info for uid, info in _sender_map(known, v, row.board_id).items()}})


@app.route("/api/guilds/channels/<int:cid>/report", methods=["POST"])
@auth_required
@validate_formkey
def guild_channel_report(cid, v):
    row, board = _load(cid, v)
    if _plaintext_only(row):
        abort(404)
    post = store.post_by_event(g.db, cid, request.values.get("event_id", ""))
    if post is None or post[3]:
        abort(404)
    if post[1] == v.id:
        return _error("You can't report your own message.", 409)
    reason = " ".join(str(request.values.get("reason") or "").split())[:rules.REPORT_MAX]
    if not store.report(g.db, cid, post[0], v.id, reason, _now()):
        return _error("You've already reported this message.", 409)
    g.db.commit()
    return jsonify({"ok": True})


def _remove_message(row, board, v, event_id):
    """Take a message out of the room as the bot, keep the writer's name in the log (not the text). False when the
    channel has no such message."""
    if row.encrypted:
        return _remove_encrypted_message(row, board, v, event_id)
    post = store.post_by_event(g.db, row.id, event_id)
    if post is None:
        return False
    if not post[3]:
        try:
            matrix.redact_as_bot(row.matrix_room_id, event_id, secrets.token_hex(12), "Removed by a guildmaster")
        except matrix.MatrixError:
            abort(make_response(jsonify({"error": _BUSY}), 502))
        store.remove_post(g.db, row.id, event_id, _now())
        _log(board, v, "channel_message_remove", f"#{row.name}", target_user_id=post[1])
    store.resolve_reports(g.db, post[0], _now())
    return True


def _remove_encrypted_message(row, board, v, event_id):
    """An encrypted channel keeps no copy: Matrix says whose the event is, then the bot takes it out. Only a message
    written by a person in the channel (a made-up id is a 404)."""
    if rules.quoted_event_id(event_id) is None:
        return False
    try:
        found = matrix.event(row.matrix_room_id, event_id)
        # a person's message only: never the room's state (its encryption, power levels, creation) or anything else
        # (bug hunt: any event id was redacted, m.room.encryption included)
        if (found is None or "state_key" in found
                or found.get("type") not in ("m.room.encrypted", "m.room.message", "m.reaction")):
            return False
        sender = matrix.event_sender(row.matrix_room_id, event_id)
        if sender is None:
            return False
        matrix.redact_as_bot(row.matrix_room_id, event_id, secrets.token_hex(12), "Removed by a guildmaster")
    except matrix.MatrixError:
        abort(make_response(jsonify({"error": _BUSY}), 502))
    from ruqqus.routes.chat import _user_id_for_mxid
    _log(board, v, "channel_message_remove", f"#{row.name}", target_user_id=_user_id_for_mxid(sender) or 0)
    return True


@app.route("/api/guilds/channels/<int:cid>/remove", methods=["POST"])
@auth_required
@validate_formkey
def guild_channel_remove(cid, v):
    """A guildmaster with the content permission removes a message from the channel's page."""
    row, board = _load(cid, v)
    if not _can_remove(row) or v.is_suspended:
        abort(403)
    if not _remove_message(row, board, v, request.values.get("event_id", "")):
        abort(404)
    g.db.commit()
    return "", 204


# --- pins, a member's own mute, the rules to accept, search ----------------------------------------------------------------

def _visible_text(body, severity, v):
    """A message's text for this viewer: "" and hidden=True when their word filter hides it."""
    hidden = bool(text_hidden(severity or 0, v))
    return ("" if hidden else body), hidden


@app.route("/api/guilds/channels/<int:cid>/pin", methods=["POST"])
@auth_required
@validate_formkey
def guild_channel_pin(cid, v):
    """A guildmaster with the content permission pins a message (or takes the pin off with on=0): at most MAX_PINS."""
    row, board = _load(cid, v)
    refused = _plaintext_only(row)
    if refused:
        return refused
    if not _can_pin(row) or v.is_suspended:
        abort(403)
    post = store.post_by_event(g.db, cid, request.values.get("event_id", ""))
    if post is None or post[3]:
        abort(404)
    if request.values.get("on") == "0":
        if store.unpin(g.db, cid, post[0]):
            _log(board, v, "channel_unpin", f"#{row.name}", target_user_id=post[1])
    elif not store.is_pinned(g.db, cid, post[0]):
        if store.pin_count(g.db, cid) >= rules.MAX_PINS:
            return _error(f"A channel can have at most {rules.MAX_PINS} pinned messages.", 409)
        store.pin(g.db, cid, post[0], v.id, _now())
        _log(board, v, "channel_pin", f"#{row.name}", target_user_id=post[1])
    g.db.commit()
    return jsonify({"pinned": store.is_pinned(g.db, cid, post[0])})


@app.route("/api/guilds/channels/<int:cid>/pins", methods=["GET"])
@auth_required
def guild_channel_pins(cid, v):
    """The pinned messages, for this viewer (their word filter, their blocks and mutes)."""
    _load(cid, v)
    pins = store.pins_of(g.db, cid)
    names = _sender_map({p[1] for p in pins}, v)
    out = []
    for event_id, author_id, body, severity, written, pinned in pins:
        who = names.get(author_id) or {"id": author_id, "shown_name": "Someone who left", "username": None, "blocked": False, "muted": False}
        if who["muted"]:
            continue
        shown, hidden = _visible_text(body, severity, v)
        out.append({"event_id": event_id, "author": who, "body": shown, "hidden": hidden, "blocked": who["blocked"],
                    "written_utc": written, "pinned_utc": pinned})
    return jsonify({"pins": out})


@app.route("/api/guilds/channels/<int:cid>/mute", methods=["POST"])
@auth_required
@validate_formkey
def guild_channel_mute_for_me(cid, v):
    """Your own mute of a channel: no unread dot, no mention notifications, for hours (1, 8, 24), -1 until you turn it
    on, 0 to turn it on now. Nobody else is told."""
    _load(cid, v)
    try:
        until = rules.mute_until(request.values.get("hours"), _now())
    except rules.Refused as e:
        return _error(str(e))
    store.set_mute(g.db, cid, v.id, until)
    g.db.commit()
    return jsonify({"muted_until": until, "muted": rules.is_muted(until, _now())})


@app.route("/api/guilds/channels/<int:cid>/accept_rules", methods=["POST"])
@auth_required
@validate_formkey
def guild_channel_accept_rules(cid, v):
    """Accept the channel's rules as they are now (a channel that asks for it): change them and everyone is asked again."""
    row, board = _load(cid, v)
    if not (row.rules_gate and row.rules):
        return _error("This channel doesn't ask for its rules to be accepted.", 409)
    store.accept_rules(g.db, cid, v.id, rules.rules_key(row.rules), _now())
    g.db.commit()
    return "", 204


@app.route("/api/guilds/channels/<int:cid>/search", methods=["GET"])
@limiter.limit("30/minute")
@auth_required
def guild_channel_search(cid, v):
    """Messages in this channel with every word in them (?q=), newest first, among those whose text is still kept
    (KEEP_TEXT_DAYS). A rated message their word filter hides, a removed one, and anything by someone they muted or
    blocked is left out."""
    row, _board = _load(cid, v)
    if _plaintext_only(row):
        return jsonify({"results": [], "days": rules.KEEP_TEXT_DAYS})
    terms = rules.search_terms(request.args.get("q"))
    if not terms:
        return jsonify({"results": [], "days": rules.KEEP_TEXT_DAYS})
    found = store.search(g.db, cid, terms, _now() - rules.KEEP_TEXT_DAYS * _DAY)
    names = _sender_map({f[1] for f in found}, v)
    out = []
    for event_id, author_id, body, severity, written in found:
        who = names.get(author_id)
        if who is None or who["muted"] or who["blocked"] or text_hidden(severity or 0, v):
            continue
        out.append({"event_id": event_id, "author": who, "body": body[:300], "written_utc": written})
    return jsonify({"results": out, "days": rules.KEEP_TEXT_DAYS})


# --- the guildmasters' page ------------------------------------------------------------------------------------------------------

def _manage_page(board, v, error=None, notice=None, status=200):
    rows = store.of_board(g.db, board.id)
    muted_ids = store.muted_ids(g.db, board.id)
    muted = g.db.query(User).filter(User.id.in_(muted_ids)).all() if muted_ids else []
    reports = store.open_reports(g.db, board.id)
    ids = [row.id for row in rows]
    swaps = {}                                  # the order after moving a channel one place up or down
    for i, cid in enumerate(ids):
        up, down = list(ids), list(ids)
        if i > 0:
            up[i - 1], up[i] = up[i], up[i - 1]
        if i < len(ids) - 1:
            down[i + 1], down[i] = down[i], down[i + 1]
        swaps[cid] = {"up": ",".join(map(str, up)), "down": ",".join(map(str, down))}
    mod = board.has_mod(v)
    space_refs = {row.id: base36encode(row.space_post_id) for row in rows if row.space_post_id}
    feeds = {}
    for row in rows:
        if row.kind == rules.ANNOUNCEMENT:
            bots = {u.id: u for u in g.db.query(User).filter(User.id.in_([f[0] for f in store.feeds_of(g.db, row.id)])).all()} \
                if store.feeds_of(g.db, row.id) else {}
            feeds[row.id] = [bots[f[0]] for f in store.feeds_of(g.db, row.id) if f[0] in bots]
    roles = role_store.roles_of(g.db, board.id)
    overwrites = {row.id: role_store.overwrites_of(g.db, row.id) for row in rows} if roles else {}
    categories = store.categories_of(g.db, board.id)
    cat_ids = [c.id for c in categories]
    cat_swaps = {}
    for i, category_id in enumerate(cat_ids):
        up, down = list(cat_ids), list(cat_ids)
        if i > 0:
            up[i - 1], up[i] = up[i], up[i - 1]
        if i < len(cat_ids) - 1:
            down[i + 1], down[i] = down[i], down[i + 1]
        cat_swaps[category_id] = {"up": ",".join(map(str, up)), "down": ",".join(map(str, down))}
    return render_template("guild/channels.html", v=v, b=board, channels=rows, muted=muted, reports=reports, swaps=swaps,
                           categories=categories, cat_swaps=cat_swaps, space_refs=space_refs, roles=roles, overwrites=overwrites, feeds=feeds,
                           automod=store.automod_of(g.db, board.id), can_encrypt=bool(board.is_private or board.is_circle),
                           max_feeds=rules.MAX_FEEDS, max_words=automod.MAX_WORDS,
                           overwrite_labels=role_rules.OVERWRITE_LABELS, max_categories=rules.MAX_CATEGORIES, category_max=rules.CATEGORY_MAX,
                           kinds=rules.KIND_LABELS, levels=rules.LEVEL_LABELS,
                           slow_choices=[(seconds, rules.slow_label(seconds)) for seconds in rules.SLOW_CHOICES],
                           max_channels=rules.MAX_CHANNELS, name_max=rules.NAME_MAX, topic_max=rules.TOPIC_MAX,
                           rules_max=rules.RULES_MAX, can_configure=bool(mod and (mod.perm_config or mod.perm_full)),
                           can_moderate=bool(mod and (mod.perm_content or mod.perm_full)), error=error, notice=notice), status


@app.route("/+<guildname>/mod/channels", methods=["GET"])
@auth_required
def guild_channels_manage(guildname, v):
    board = _visible_board(guildname, v)
    mod = board.has_mod(v)
    if not mod or not (mod.perm_config or mod.perm_content or mod.perm_full):
        abort(404)
    return _manage_page(board, v)[0]


def _config_mod(board, v):
    mod = board.has_mod(v)
    return bool(mod) and (mod.perm_config or mod.perm_full) and not v.is_suspended


@app.route("/+<guildname>/mod/channels/new", methods=["POST"])
@auth_required
@is_guildmaster("config")
@validate_formkey
def guild_channel_create(guildname, board, v):
    try:
        name = rules.clean_name(request.values.get("name"))
        topic = rules.clean_topic(request.values.get("topic"))
        channel_rules = rules.clean_rules(request.values.get("rules"))
        kind, read_level, write_level, slow = rules.clean_settings(
            request.values.get("kind", rules.TEXT), request.values.get("read_level", rules.MEMBERS),
            request.values.get("write_level", rules.MEMBERS), request.values.get("slow_mode"))
    except rules.Refused as e:
        return _manage_page(board, v, error=str(e), status=400)
    refusal = rules.make_refusal(store.count(g.db, board.id), store.name_taken(g.db, board.id, name))
    if refusal:
        return _manage_page(board, v, error=refusal, status=409)
    category_id, gate, refusal = _category_and_gate(board, channel_rules)
    if refusal:
        return _manage_page(board, v, error=refusal, status=400)
    space_post_id, refusal = _space_post(request.values.get("space"), v) if kind == rules.STAGE else (None, None)
    if refusal:
        return _manage_page(board, v, error=refusal, status=400)
    if kind == rules.VOICE:
        gate = False                                  # nobody types in a voice channel, so there is nothing to accept
    encrypted = request.values.get("encrypted") in ("1", "on", "true")
    refusal = rules.encrypted_refusal(kind, encrypted, bool(board.is_private or board.is_circle), read_level, write_level, gate)
    if refusal:
        return _manage_page(board, v, error=refusal, status=400)
    try:
        room_id = _new_room(kind, v, encrypted)
    except matrix.MatrixError:
        return _manage_page(board, v, error=_BUSY, status=502)
    store.create(g.db, board.id, kind, name, topic, channel_rules, _severity(f"{name} {topic} {channel_rules}"),
                 read_level, write_level, slow, room_id, _now(), category_id, gate, space_post_id, encrypted)
    _log(board, v, "channel_create", f"#{name}")
    g.db.commit()
    return _manage_page(board, v, notice=f"#{name} was made.")[0]


def _new_room(kind, v, encrypted=False):
    """The Matrix room a new channel gets: messages for text and announcements, a silent encrypted one for a voice
    channel (how the browsers in its call learn each other's devices), none for a stage (a placeholder that is never
    used: the column is unique and not null)."""
    if kind == rules.STAGE:
        return f"stage:{secrets.token_hex(12)}"
    if kind == rules.VOICE:
        return matrix.create_space_room()
    announcement = kind == rules.ANNOUNCEMENT
    return matrix.create_guild_channel_room(announcement, [_mxid(v)] if announcement else (), encrypted)


def _space_post(value, v):
    """(the Space's post id, refusal) for a stage channel's form field: a Space this guildmaster can open."""
    from ruqqus.helpers import space_store
    try:
        ref = rules.clean_space_ref(value)
    except rules.Refused as e:
        return None, str(e)
    post = get_post(ref, v=v, graceful=True)
    if post is None or not space_store.for_posts(g.db, [post.id]):
        return None, "That post isn't a Space you can open."
    return post.id, None


def _overwrites_from_form(board):
    """{role id: (read, write)} from the edit form's ow_read_<id> / ow_write_<id> fields, or None when it has none (a
    form that doesn't mention roles leaves them as they are)."""
    if not any(key.startswith("ow_") for key in request.values):
        return None
    out = {}
    for role in role_store.roles_of(g.db, board.id):
        out[role.id] = (role_rules.parse_overwrite(request.values.get(f"ow_read_{role.id}")),
                        role_rules.parse_overwrite(request.values.get(f"ow_write_{role.id}")))
    return out


def _category_and_gate(board, channel_rules):
    """(category id or None, rules gate, refusal) from the form: a category of this guild's own, and whether the channel
    asks its rules to be accepted (it needs rules to be accepted)."""
    category_id = request.values.get("category", type=int)
    if category_id and store.category(g.db, board.id, category_id) is None:
        return None, False, "That category isn't in this guild."
    gate = request.values.get("rules_gate") in ("1", "on", "true")
    return (category_id or None), gate, rules.gate_refusal(gate, channel_rules)


def _own_channel(board, cid):
    row = store.get_in_board(g.db, board.id, cid)
    if row is None:
        abort(404)
    return row


@app.route("/+<guildname>/mod/channels/<int:cid>/edit", methods=["POST"])
@auth_required
@is_guildmaster("config")
@validate_formkey
def guild_channel_edit(guildname, cid, board, v):
    row = _own_channel(board, cid)
    try:
        name = rules.clean_name(request.values.get("name"))
        topic = rules.clean_topic(request.values.get("topic"))
        channel_rules = rules.clean_rules(request.values.get("rules"))
        _kind, read_level, write_level, slow = rules.clean_settings(
            row.kind, request.values.get("read_level", row.read_level), request.values.get("write_level", row.write_level),
            request.values.get("slow_mode", row.slow_mode_seconds))
    except rules.Refused as e:
        return _manage_page(board, v, error=str(e), status=400)
    refusal = rules.rename_refusal(store.name_taken(g.db, board.id, name, except_id=cid))
    if refusal:
        return _manage_page(board, v, error=refusal, status=409)
    category_id, gate, refusal = _category_and_gate(board, channel_rules)
    if refusal:
        return _manage_page(board, v, error=refusal, status=400)
    space_post_id = row.space_post_id
    if row.kind == rules.STAGE:
        space_post_id, refusal = _space_post(request.values.get("space"), v)
        if refusal:
            return _manage_page(board, v, error=refusal, status=400)
    if row.kind == rules.VOICE:
        gate = False
    refusal = rules.encrypted_refusal(row.kind, bool(row.encrypted), True, read_level, write_level, gate)
    if refusal:
        return _manage_page(board, v, error=refusal, status=400)
    try:
        overwrites = _overwrites_from_form(board) if row.kind != rules.STAGE else None
    except role_rules.Refused as e:
        return _manage_page(board, v, error=str(e), status=400)
    store.update(g.db, board.id, cid, name, topic, channel_rules, _severity(f"{name} {topic} {channel_rules}"),
                 read_level, write_level, slow, category_id, gate, space_post_id)
    if overwrites is not None:
        role_store.set_overwrites(g.db, board.id, cid, overwrites)
    _log(board, v, "channel_edit", f"#{name}")
    g.db.commit()
    if row.kind == rules.ANNOUNCEMENT:
        _sync_writers(row)                                  # a role that may now write in it, in its room
    return _manage_page(board, v, notice=f"#{name} was saved.")[0]


@app.route("/+<guildname>/mod/channels/<int:cid>/delete", methods=["POST"])
@auth_required
@is_guildmaster("config")
@validate_formkey
def guild_channel_delete(guildname, cid, board, v):
    row = _own_channel(board, cid)
    store.close(g.db, board.id, cid, _now())
    _log(board, v, "channel_delete", f"#{row.name}")
    g.db.commit()
    return _manage_page(board, v, notice=f"#{row.name} was deleted.")[0]


@app.route("/+<guildname>/mod/channels/order", methods=["POST"])
@auth_required
@is_guildmaster("config")
@validate_formkey
def guild_channel_order(guildname, board, v):
    ids = []
    for part in (request.values.get("ids") or "").split(","):
        part = part.strip()
        if part.isascii() and part.isdigit() and int(part) not in ids:
            ids.append(int(part))
    store.move(g.db, board.id, ids)
    g.db.commit()
    return redirect(f"/+{board.name}/mod/channels")


@app.route("/+<guildname>/mod/channels/mute", methods=["POST"])
@auth_required
@is_guildmaster("content")
@validate_formkey
def guild_channel_mute(guildname, board, v):
    """Muted in the guild's channels: they can read, not write. Not a guildmaster, not yourself."""
    target = get_user((request.values.get("username") or "").lstrip("@"), graceful=True)
    if target is None or target.is_deleted:
        return _manage_page(board, v, error="There's no account with that name.", status=404)
    if target.id == v.id or board.has_mod(target):
        return _manage_page(board, v, error="You can't mute a guildmaster, or yourself.", status=409)
    if not store.mute(g.db, board.id, target.id, v.id, _now()):
        return _manage_page(board, v, error=f"@{target.username} is already muted here.", status=409)
    _log(board, v, "chatban_user", "", target_user_id=target.id)
    for cid, room in store.encrypted_rooms_of(g.db, board.id, target.id):      # the site can't stop them writing there
        try:
            matrix.kick_as_bot(room, matrix.mxid_for(target.id), "Muted in the guild's channels")
            store.leave(g.db, cid, target.id)
        except matrix.MatrixError:
            pass                                                                # the sync step does it within minutes
    g.db.commit()
    return _manage_page(board, v, notice=f"@{target.username} was muted in +{board.name}'s channels.")[0]


@app.route("/+<guildname>/mod/channels/unmute", methods=["POST"])
@auth_required
@is_guildmaster("content")
@validate_formkey
def guild_channel_unmute(guildname, board, v):
    target = get_user((request.values.get("username") or "").lstrip("@"), graceful=True)
    if target is None or not store.unmute(g.db, board.id, target.id):
        return _manage_page(board, v, error="They aren't muted here.", status=404)
    _log(board, v, "unchatban_user", "", target_user_id=target.id)
    g.db.commit()
    return _manage_page(board, v, notice=f"@{target.username} can write in the channels again.")[0]


@app.route("/+<guildname>/mod/channels/reports/<int:pid>/<action>", methods=["POST"])
@auth_required
@is_guildmaster("content")
@validate_formkey
def guild_channel_report_act(guildname, pid, action, board, v):
    if action not in ("remove", "dismiss"):
        abort(404)
    found = store.post_in_board(g.db, board.id, pid)
    if found is None:
        abort(404)
    if action == "remove":
        row = _own_channel(board, found[1])
        _remove_message(row, board, v, found[2])
    else:
        store.resolve_reports(g.db, pid, _now())
    g.db.commit()
    return redirect(f"/+{board.name}/mod/channels")


@app.route("/+<guildname>/mod/channels/categories/new", methods=["POST"])
@auth_required
@is_guildmaster("config")
@validate_formkey
def guild_category_create(guildname, board, v):
    try:
        name = rules.clean_category(request.values.get("name"))
    except rules.Refused as e:
        return _manage_page(board, v, error=str(e), status=400)
    refusal = rules.category_refusal(store.category_count(g.db, board.id), store.category_taken(g.db, board.id, name))
    if refusal:
        return _manage_page(board, v, error=refusal, status=409)
    store.make_category(g.db, board.id, name, _severity(name))
    _log(board, v, "category_create", name)
    g.db.commit()
    return _manage_page(board, v, notice=f"The category {name} was made.")[0]


@app.route("/+<guildname>/mod/channels/categories/<int:category_id>/rename", methods=["POST"])
@auth_required
@is_guildmaster("config")
@validate_formkey
def guild_category_rename(guildname, category_id, board, v):
    if store.category(g.db, board.id, category_id) is None:
        abort(404)
    try:
        name = rules.clean_category(request.values.get("name"))
    except rules.Refused as e:
        return _manage_page(board, v, error=str(e), status=400)
    if store.category_taken(g.db, board.id, name, except_id=category_id):
        return _manage_page(board, v, error="The guild already has a category with that name.", status=409)
    store.rename_category(g.db, board.id, category_id, name, _severity(name))
    _log(board, v, "category_edit", name)
    g.db.commit()
    return _manage_page(board, v, notice=f"The category was renamed {name}.")[0]


@app.route("/+<guildname>/mod/channels/categories/<int:category_id>/delete", methods=["POST"])
@auth_required
@is_guildmaster("config")
@validate_formkey
def guild_category_delete(guildname, category_id, board, v):
    found = store.category(g.db, board.id, category_id)
    if found is None:
        abort(404)
    store.delete_category(g.db, board.id, category_id)
    _log(board, v, "category_delete", found[2])
    g.db.commit()
    return _manage_page(board, v, notice=f"The category {found[2]} was deleted; its channels stay.")[0]


@app.route("/+<guildname>/mod/channels/categories/order", methods=["POST"])
@auth_required
@is_guildmaster("config")
@validate_formkey
def guild_category_order(guildname, board, v):
    ids = []
    for part in (request.values.get("ids") or "").split(","):
        part = part.strip()
        if part.isascii() and part.isdigit() and int(part) not in ids:
            ids.append(int(part))
    store.move_categories(g.db, board.id, ids)
    g.db.commit()
    return redirect(f"/+{board.name}/mod/channels")


# --- voice channels: what the call routes ask of the channel (routes/calls.py) -----------------------------------------------

def voice_room(v, room_id):
    """(room, refusal) for a voice channel's Matrix room, in the shape routes/calls.py `chat_room` answers with:
    (None, None) when this is no voice channel or v may not see it (a 404), a refusal when they may see it but not speak
    in it. Asked at every join and every heartbeat, so being exiled or muted ends your part in the call."""
    channel = store.by_room(g.db, room_id)
    if channel is None or channel.kind != rules.VOICE:
        return None, None
    row = store.one_for(g.db, channel.id, v.id)
    board = g.db.query(Board).filter_by(id=channel.board_id).first()
    if row is None or board is None or (board.is_banned and v.admin_level < 3):
        return None, None
    if _read_refusal(row, v, _hidden(board, v, row)):
        return None, None
    return {"kind": "voice", "room_id": room_id, "label": rules.voice_label(row.name, row.board_name), "member_ids": [],
            "other": None, "channel": row}, _write_refusal(row, v, None)


def voice_blocked(v, user_ids):
    """Is v in a block, either way, with anyone in the call? They would hear each other, so they don't share a channel."""
    ids = [uid for uid in user_ids if uid != v.id]
    if not ids:
        return False
    return g.db.query(UserBlock).filter(or_(and_(UserBlock.user_id == v.id, UserBlock.target_id.in_(ids)),
                                            and_(UserBlock.target_id == v.id, UserBlock.user_id.in_(ids)))).first() is not None


def voice_joins_ok(v, now):
    """One person joins voice channels at most VOICE_JOINS_PER_HOUR times an hour (a window opening and closing in a loop)."""
    try:
        key = f"gc:voicejoin:{v.id}:{now // 3600}"
        n = int(r.incr(key))
        if n == 1:
            r.expire(key, 3700)
        return n <= rules.VOICE_JOINS_PER_HOUR
    except Exception:
        return False


def voice_enter(v, room):
    """Puts the person in the channel's silent room (how their browser learns the other devices); an error response or None."""
    row = room["channel"]
    try:
        if not store.in_room(g.db, row.id, v.id):
            matrix.put_in_room(row.matrix_room_id, _mxid(v))
            store.join(g.db, row.id, v.id, _now())
    except matrix.MatrixError:
        return _error(_BUSY, 502)
    return None


def voice_exit(v, room):
    """Out of the room once out of the call, so it holds only the people in it. Best effort: the sync step does the rest."""
    row = room["channel"]
    try:
        matrix.kick_as_bot(row.matrix_room_id, matrix.mxid_for(v.id), "Left the voice channel")
        store.leave(g.db, row.id, v.id)
    except Exception:
        app.logger.warning(f"voice channel {row.id}: not taken out of its room", exc_info=True)


def voice_who(room, roster, v):
    """Who is in the call, for the people who may see the channel: names and pictures, never a blocked account."""
    ids = {e["user_id"] for e in roster}
    users = g.db.query(User).filter(User.id.in_(ids)).all() if ids else []
    return [{"shown_name": u.shown_name, "profile_url": u.profile_url} for u in users
            if u.id == v.id or not v.any_block_exists(u)]


@app.route("/+<guildname>/mod/channels/automod", methods=["POST"])
@auth_required
@is_guildmaster("config")
@validate_formkey
def guild_automod_save(guildname, board, v):
    """The guild's own rules for what is written in its channels (helpers/guild_automod.py)."""
    try:
        account_days = automod.clean_days(request.values.get("min_account_days"), "The account age")
        member_days = automod.clean_days(request.values.get("min_member_days"), "The time in the guild")
        link_days = automod.clean_days(request.values.get("link_member_days"), "The time before links")
        words = automod.clean_words(request.values.get("blocked_words"))
    except automod.Refused as e:
        return _manage_page(board, v, error=str(e), status=400)
    store.save_automod(g.db, board.id, account_days, member_days, link_days, words)
    _log(board, v, "automod_settings", "")
    g.db.commit()
    return _manage_page(board, v, notice="The auto-moderation was saved.")[0]


@app.route("/+<guildname>/mod/channels/<int:cid>/feeds/add", methods=["POST"])
@auth_required
@is_guildmaster("config")
@validate_formkey
def guild_feed_add(guildname, cid, board, v):
    """An approved bot account whose new posts go into this announcement channel (helpers/guild_channel_feeds.py)."""
    row = _own_channel(board, cid)
    if row.kind != rules.ANNOUNCEMENT:
        return _manage_page(board, v, error="Only an announcement channel takes a bot's posts.", status=409)
    name = (request.values.get("username") or "").strip().lstrip("@")
    bot = get_user(name, graceful=True) if name else None
    approved = bool(bot) and g.db.execute(text("SELECT 1 FROM bots WHERE user_id = :u AND status = 'approved'"), {"u": bot.id}).fetchone()
    if bot is None or not bot.is_bot or bot.is_deleted or bot.is_banned or not approved:
        return _manage_page(board, v, error="That isn't a bot account that is running.", status=404)
    if len(store.feeds_of(g.db, cid)) >= rules.MAX_FEEDS:
        return _manage_page(board, v, error=f"A channel can have at most {rules.MAX_FEEDS} bots feeding it.", status=409)
    if not store.add_feed(g.db, cid, bot.id, v.id, _now(), store.latest_bot_post(g.db, bot.id)):
        return _manage_page(board, v, error="That bot already feeds this channel.", status=409)
    _log(board, v, "channel_feed_add", f"#{row.name}", target_user_id=bot.id)
    g.db.commit()
    return _manage_page(board, v, notice=f"@{bot.username}'s new posts will go into #{row.name}.")[0]


@app.route("/+<guildname>/mod/channels/<int:cid>/feeds/remove", methods=["POST"])
@auth_required
@is_guildmaster("config")
@validate_formkey
def guild_feed_remove(guildname, cid, board, v):
    row = _own_channel(board, cid)
    name = (request.values.get("username") or "").strip().lstrip("@")
    bot = get_user(name, graceful=True) if name else None
    if bot is None or not store.remove_feed(g.db, cid, bot.id):
        return _manage_page(board, v, error="That bot doesn't feed this channel.", status=404)
    _log(board, v, "channel_feed_remove", f"#{row.name}", target_user_id=bot.id)
    g.db.commit()
    return _manage_page(board, v, notice=f"@{bot.username} no longer feeds #{row.name}.")[0]


def guild_channels_for(board, v):
    """Jinja global: a guild's channels this viewer may read, for its About box (the Chat tab's own rule: a visitor
    sees none). Name, kind and the link into the Chat tab."""
    from jinja2 import Undefined
    v = None if isinstance(v, Undefined) else v
    if not has_chat(board, v):
        return []
    out = []
    for row in store.of_board_for(g.db, board.id, v.id):
        if _read_refusal(row, v, _hidden(board, v, row)):
            continue
        item = channel_json(row, v)
        out.append({"id": row.id, "label": item["label"], "kind": row.kind,
                    "url": f"/+{board.name}/chat?c={row.id}"})
    return out


app.jinja_env.globals.update(guild_channels_for=guild_channels_for)
