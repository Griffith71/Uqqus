import hmac
import html
import re
import secrets
import time
from os import environ
from flask import *
from sqlalchemy import or_, func, text

from ruqqus.helpers.wrappers import *
from ruqqus.helpers.get import *
from ruqqus.helpers.chat_permissions import can_message_directly, is_blocked
from ruqqus.helpers.chat_events import is_new_message
from ruqqus.helpers import broadcast_store, chat_group_store, chat_devices, guild_channel_store
from ruqqus.helpers import guild_channels as guild_channel_rules
from ruqqus.helpers.visibility import filter_users, post_hidden, user_hidden
from ruqqus.helpers.secret_box import encrypt_secret, decrypt_secret
from ruqqus.classes import *
import ruqqus.helpers.matrix_client as matrix
from ruqqus.__main__ import app, limiter, member_limit_key, r


def _ensure_provisioned(v):
    identity = g.db.query(ChatIdentity).filter_by(user_id=v.id).first()
    if identity:
        return identity.matrix_user_id
    # no record here: a Matrix account Synapse already has for this id belonged to someone else (a database restored
    # without Synapse's, audit D-07), so it is never handed over unless an admin says the two were restored together
    try:
        mxid = matrix.provision_user(v.id, adopt_existing=environ.get("MATRIX_ADOPT_EXISTING") == "1")
    except matrix.OrphanMatrixAccount as e:
        print(f"chat refused: {e}. Restore Synapse's database with the site's (scripts/backup.sh), or set "
              "MATRIX_ADOPT_EXISTING=1 if they belong together.", flush=True)
        abort(503)
    g.db.add(ChatIdentity(user_id=v.id, matrix_user_id=mxid))
    g.db.commit()
    chat_devices.sync_name(g.db, v, mxid)       # outside apps show the @handle (helpers/chat_devices.py)
    return mxid


def _mxid_for_user_id(user_id):
    identity = g.db.query(ChatIdentity).filter_by(user_id=user_id).first()
    if identity:
        return identity.matrix_user_id
    target = g.db.query(User).filter_by(id=user_id).first()
    return _ensure_provisioned(target)


def _user_id_for_mxid(mxid):
    identity = g.db.query(ChatIdentity).filter_by(matrix_user_id=mxid).first()
    return identity.user_id if identity else None


@app.route("/chat", methods=["GET"])
@auth_required
def chat_page(v):
    _ensure_provisioned(v)
    from ruqqus.helpers.chat_groups import MAX_MEMBERS
    return render_template("chat.html", v=v, chat_group_max=MAX_MEMBERS)


@app.route("/api/chat/token", methods=["POST"])
@auth_required
@validate_formkey
def chat_token(v):
    mxid = _ensure_provisioned(v)
    device_id = request.values.get("device_id") or None
    token_resp = matrix.login_as(mxid, device_id)
    return jsonify({
        "homeserver_url": app.config["MATRIX_PUBLIC_URL"],
        "user_id": token_resp["user_id"],
        "access_token": token_resp["access_token"],
        "device_id": token_resp["device_id"],
    })


_BLOB = re.compile(r"^[A-Za-z0-9_\-]{40,4096}$")


def _identity(v):
    identity = g.db.query(ChatIdentity).filter_by(user_id=v.id).first()
    if not identity:
        abort(404)
    return identity


@app.route("/api/chat/key", methods=["GET"])
@auth_required
def chat_key_get(v):
    """The account's LOCKED recovery key copies and the salt the browser derives its password key with (audit M-03).
    Nothing here can unlock anything without the member's password or recovery code, which never reach this route.
    `legacy` says an old server-readable copy still exists (the browser replaces it with locked copies, then it's
    cleared)."""
    _ensure_provisioned(v)
    identity = _identity(v)
    if not identity.key_salt:
        identity.key_salt = secrets.token_urlsafe(16)
        g.db.add(identity)
        g.db.commit()
    return jsonify({"salt": identity.key_salt, "password_blob": identity.key_blob_password,
                    "recovery_blob": identity.key_blob_recovery, "legacy": bool(identity.recovery_key_encrypted),
                    "ready": bool(identity.ready_utc)})


@app.route("/api/chat/key", methods=["POST"])
@auth_required
@validate_formkey
def chat_key_set(v):
    """Saves the locked copies the browser made (`password_blob`, `recovery_blob`: base64url, opaque here). Saving a
    password copy clears the old server-readable key for good."""
    identity = _identity(v)
    changed = False
    for field, column in (("password_blob", "key_blob_password"), ("recovery_blob", "key_blob_recovery")):
        value = request.values.get(field)
        if value is None:
            continue
        if not _BLOB.match(value):
            return jsonify({"error": "That key couldn't be saved."}), 400
        setattr(identity, column, value)
        changed = True
    if not changed:
        return jsonify({"error": "Nothing to save."}), 400
    if identity.key_blob_password:
        identity.recovery_key_encrypted = None          # the server can no longer read the key
    g.db.add(identity)
    g.db.commit()
    return "", 204


@app.route("/api/chat/recovery_key", methods=["GET"])
@auth_required
def chat_recovery_key_legacy(v):
    """The old server-readable copy, only while it still exists: the member's own browser reads it once to lock it
    with their password and a recovery code (POST /api/chat/key then clears it). Never anything for an account
    set up after audit M-03."""
    identity = g.db.query(ChatIdentity).filter_by(user_id=v.id).first()
    if not identity or not identity.recovery_key_encrypted:
        return jsonify({"recovery_key": None})
    return jsonify({"recovery_key": decrypt_secret(identity.recovery_key_encrypted)})


@app.route("/api/chat/check_password", methods=["POST"])
@limiter.limit("10/minute")
@auth_required
@validate_formkey
def chat_check_password(v):
    """Whether a typed password is the account's, before the browser locks the chat key with it (a typo would lock
    it with the wrong key). The password is checked like a login and kept nowhere."""
    return jsonify({"ok": bool(v.verifyPass(request.form.get("password", "")))})


@app.route("/api/chat/ready", methods=["POST"])
@auth_required
@validate_formkey
def chat_ready(v):
    """This member's chat has finished setting up encryption: messages waiting for them in other people's browsers
    can now be sent (audit M-04)."""
    identity = _identity(v)
    if not identity.ready_utc:
        identity.ready_utc = int(time.time())
        g.db.add(identity)
        g.db.commit()
    return "", 204


def _chat_ready(user_id):
    identity = g.db.query(ChatIdentity).filter_by(user_id=user_id).first()
    return bool(identity and identity.ready_utc)


def _started(room_id, status):
    """The answer to starting a chat: the chat page for a link, the room for the share sheet (?json=1)."""
    if request.values.get("json"):
        return jsonify({"room_id": room_id, "status": status})
    return redirect(f"/chat?room={room_id}")


@app.route("/api/chat/start", methods=["POST"])
@auth_required
@validate_formkey
def chat_start(v):
    target = get_user(request.values.get("username", ""), graceful=True)
    if not target or target.id == v.id:
        abort(400)
    if is_blocked(v, target):
        abort(403)
    if target.is_bot or v.is_bot:
        abort(403)                  # nobody reads a bot's chats (helpers/bots.py): no room is ever made with one

    a_id, b_id = min(v.id, target.id), max(v.id, target.id)
    existing = g.db.query(ChatConversation).filter_by(user_a_id=a_id, user_b_id=b_id).first()
    if existing:
        return _started(existing.matrix_room_id, existing.tab_for(v.id))

    v_mxid = _ensure_provisioned(v)
    t_mxid = _mxid_for_user_id(target.id)

    room_id = matrix.create_dm_room(v_mxid, t_mxid)
    allowed = can_message_directly(v, target)
    if allowed:
        matrix.join_room_as(room_id, t_mxid)

    convo = ChatConversation(
        user_a_id=a_id, user_b_id=b_id, initiator_id=v.id,
        matrix_room_id=room_id, status=("inbox" if allowed else "request"),
    )
    g.db.add(convo)
    try:
        g.db.commit()
    except Exception:
        # UNIQUE(user_a_id,user_b_id) race loser - someone else just
        # created this pair's row concurrently; use the winner's room.
        g.db.rollback()
        existing = g.db.query(ChatConversation).filter_by(user_a_id=a_id, user_b_id=b_id).first()
        if existing:
            return _started(existing.matrix_room_id, existing.tab_for(v.id))
        raise

    return _started(room_id, convo.tab_for(v.id))


@app.route("/api/chat/conversations", methods=["GET"])
@auth_required
def chat_conversations(v):
    status = request.args.get("status", "inbox")
    before = request.args.get("before", type=int)

    # guild channels (routes/guild_channels.py) are their own tab
    if status == "guild":
        from ruqqus.routes.guild_channels import conversations_for
        return jsonify({"conversations": conversations_for(v)})

    q = g.db.query(ChatConversation).filter(
        or_(ChatConversation.user_a_id == v.id, ChatConversation.user_b_id == v.id)
    )
    if before:
        q = q.filter(ChatConversation.last_activity_utc < before)
    rows = q.order_by(ChatConversation.last_activity_utc.desc()).limit(100).all()

    out = []
    for c in rows:
        if c.tab_for(v.id) != status:
            continue
        other = c.other_user(v.id)
        unread = g.db.query(ChatUnread).filter_by(conversation_id=c.id, user_id=v.id).first()
        out.append({
            "kind": "pair",
            "key": f"c{c.id}",
            "conversation_id": c.id,
            "room_id": c.matrix_room_id,
            "other_user": {
                "id": other.id,
                "username": other.username,
                "profile_url": other.profile_url,
                # messages to someone whose chat isn't set up yet wait in the sender's browser (M-04)
                "ready": _chat_ready(other.id),
            },
            "unread_count": unread.unread_count if unread else 0,
            "last_activity_utc": c.last_activity_utc,
            "is_initiator": c.initiator_id == v.id,
        })
        if len(out) >= 25:
            break

    # channels (routes/broadcasts.py) are their own tab
    if status == "channel":
        from ruqqus.routes.broadcasts import channel_json
        rows = broadcast_store.joined_by(g.db, v.id, limit=50)
        return jsonify({"conversations": [{**channel_json(row[:8], (row[8], row[9], 0), v),
                                           "last_activity_utc": row[10] or row[6]} for row in rows]})

    # groups (routes/chat_groups.py): joined ones are their own tab, Groups (the Inbox is 1:1 chats only, the owner
    # 2026-10-20), and invitations wait in Requests beside the 1:1 requests, merged by last activity
    from ruqqus.routes.chat_groups import group_json
    if status == "group":
        out = []
    group_tab = {"group": "joined", "request": "invited"}.get(status)
    for row in chat_group_store.groups_for(g.db, v.id, group_tab, limit=25) if group_tab else []:
        if before and row[6] >= before:
            continue
        group = row[:8]
        out.append(group_json(group, chat_group_store.member(g.db, group[0], v.id), v))
    out.sort(key=lambda item: item["last_activity_utc"] or 0, reverse=True)

    return jsonify({"conversations": out[:25]})


SHARE_TARGETS = 40
SHARE_RECENT = 25
POST_ID = re.compile(r"^[0-9a-z]{1,10}$")


@app.route("/api/chat/share_targets", methods=["GET"])
@auth_required
def chat_share_targets(v):
    """Who a post can be sent to from the share sheet: the chats you can already write in
    (newest first), then people you follow, narrowed by ?q=. Never someone you block or who
    blocks you, and never a chat still waiting for you to accept it."""
    q = (request.args.get("q") or "").strip().lstrip("@")[:40].lower()

    blocked = {b if a == v.id else a for a, b in g.db.query(UserBlock.user_id, UserBlock.target_id).filter(
        or_(UserBlock.user_id == v.id, UserBlock.target_id == v.id)).all()}

    out, seen = [], set()
    rows = g.db.query(ChatConversation).filter(
        or_(ChatConversation.user_a_id == v.id, ChatConversation.user_b_id == v.id)
    ).order_by(ChatConversation.last_activity_utc.desc()).limit(100).all()
    for convo in rows:
        other = convo.other_user(v.id)
        if other is None or other.id in blocked or other.is_deleted or user_hidden(other, v):
            continue
        if convo.tab_for(v.id) != "inbox" or (q and q not in other.username.lower()):
            continue
        out.append({"username": other.username, "display_name": other.display_name or "", "profile_url": other.profile_url, "room_id": convo.matrix_room_id, "recent": True})
        seen.add(other.id)
        if len(out) >= SHARE_RECENT:
            break

    # the groups you are in (only joined ones: an invitation can't be written in yet)
    from ruqqus.routes.chat_groups import group_json
    for row in chat_group_store.groups_for(g.db, v.id, "joined", limit=SHARE_RECENT):
        group = group_json(row[:8], chat_group_store.member(g.db, row[0], v.id), v)
        if q and q not in group["label"].lower():
            continue
        out.append({"kind": "group", "label": group["label"], "size": group["size"], "room_id": group["room_id"],
                    "recent": True, "last_activity_utc": group["last_activity_utc"]})

    follows = [i for i in v.following_ids if i not in seen and i not in blocked]
    if follows and len(out) < SHARE_TARGETS:
        # never a bot: a chat with one is refused (audit M-13)
        people = g.db.query(User).filter(User.id.in_(follows), User.is_deleted == False, User.is_banned == 0,
                                         User.is_bot.isnot(True))
        if q:
            people = people.filter(User.username.ilike("%" + q.replace("\\", "").replace("%", "\\%").replace("_", "\\_") + "%"))
        people = filter_users(people, v).order_by(User.username.asc()).limit(SHARE_TARGETS - len(out)).all()
        out += [{"username": u.username, "display_name": u.display_name or "", "profile_url": u.profile_url, "room_id": None, "recent": False} for u in people]

    return jsonify({"targets": out})


@app.route("/api/chat/post_preview/<pid>", methods=["GET"])
@auth_required
def chat_post_preview(pid, v):
    """The small card under a message that links to a post, made for the person reading it:
    the same visibility rules as opening the post, and never the author (so an anonymous post
    stays anonymous). When they may not see it the card says so instead."""
    if not POST_ID.match(pid):
        return jsonify({"ok": False, "notice": "This post does not exist."})
    post = get_post(pid, v=v, graceful=True)
    if post is None:
        return jsonify({"ok": False, "notice": "This post does not exist."})

    admin = v.admin_level >= 3
    if (post.is_banned or post.board.is_banned) and not admin:
        return jsonify({"ok": False, "notice": "This post was removed."})
    if post.deleted_utc and not admin:
        return jsonify({"ok": False, "notice": "This post was deleted."})
    if post_hidden(post, v):
        return jsonify({"ok": False, "notice": "Hidden by your word filter."})
    # the post page's own rule for private guilds, and a block either way hides it as a quote card does (audit
    # M-12); an anonymous post is never hidden for whose it is
    if post.in_private_guild_for(v):
        return jsonify({"ok": False, "notice": "Not available to you."})
    if not post.is_anonymous and post.author_id != v.id and post.author and v.any_block_exists(post.author):
        return jsonify({"ok": False, "notice": "Not available to you."})

    return jsonify({
        "ok": True,
        "title": html.unescape(post.title or "")[:200],
        "guild": None if post.is_profile_post else post.board.name,
        "url": post.permalink,
        "thumb": post.thumb_url if (post.has_thumb and not post.is_sensitive) else None,
        "comments": post.comment_count,
    })


@app.route("/api/chat/unread_count", methods=["GET"])
@auth_required
def chat_unread_count(v):
    # Polled by the navbar chat badge (assets/js/chat_badge.js). Read-only:
    # unlike /notifications it never marks anything as read.
    return jsonify({"unread": v.chat_unread_messages})


@app.route("/api/chat/conversations/<int:cid>/accept", methods=["POST"])
@auth_required
@validate_formkey
def chat_accept(v, cid):
    c = g.db.query(ChatConversation).filter_by(id=cid).first()
    if not c or v.id not in (c.user_a_id, c.user_b_id) or c.initiator_id == v.id:
        abort(404)
    c.status = "inbox"
    g.db.add(c)
    g.db.commit()
    return "", 204


@app.route("/api/chat/conversations/<int:cid>/decline", methods=["POST"])
@auth_required
@validate_formkey
def chat_decline(v, cid):
    c = g.db.query(ChatConversation).filter_by(id=cid).first()
    if not c or v.id not in (c.user_a_id, c.user_b_id) or c.initiator_id == v.id:
        abort(404)
    room_id, sender_id = c.matrix_room_id, c.initiator_id
    g.db.query(ChatUnread).filter_by(conversation_id=cid).delete()
    g.db.delete(c)
    g.db.commit()
    # the room goes with the request (audit M-10: it stayed, with the invitation in the decliner's chat client and
    # the sender still in it). Best effort: the request is already gone, and a new message makes a new room.
    if room_id:
        for leave in (lambda: matrix.leave_room_as(room_id, _mxid_for_user_id(v.id)),
                      lambda: matrix.kick_as_bot(room_id, _mxid_for_user_id(sender_id), "Message request declined")):
            try:
                leave()
            except Exception:
                pass
    return "", 204


@app.route("/api/chat/conversations/<int:cid>/mark_read", methods=["POST"])
@auth_required
@validate_formkey
def chat_mark_read(v, cid):
    c = g.db.query(ChatConversation).filter_by(id=cid).first()
    if not c or v.id not in (c.user_a_id, c.user_b_id) or c.tab_for(v.id) != "inbox":
        abort(404)
    g.db.execute(
        text("""INSERT INTO chat_unread (conversation_id, user_id, unread_count, last_read_utc)
                 VALUES (:cid, :uid, 0, :now)
                 ON CONFLICT (conversation_id, user_id)
                 DO UPDATE SET unread_count = 0, last_read_utc = :now"""),
        {"cid": cid, "uid": v.id, "now": int(time.time())},
    )
    g.db.commit()
    return "", 204


# --- Application Service webhook (Synapse -> Ruqqus), see
# synapse/ruqqus-appservice.yaml for the registration this answers to ---

def _guard_room_event(event):
    """What Matrix's own rules can't stop, removed as it happens (best effort, never failing the transaction):
    - someone joining or invited to a 1:1 room who isn't one of its two people (M-02, belt and braces: the rooms only
      let the bot invite);
    - in a public broadcast channel, a message the site didn't send (a writer's own client, around the word filter,
      the daily count and reports) or a reaction that is text rather than an emoji (M-06)."""
    from ruqqus.helpers import broadcasts as channel_rules
    try:
        room_id, kind, sender = event.get("room_id"), event.get("type"), event.get("sender") or ""
        bot = f"@{app.config['MATRIX_BOT_LOCALPART']}:{app.config['MATRIX_SERVER_NAME']}"
        if not room_id or sender == bot:
            return
        if kind == "m.room.member" and (event.get("content") or {}).get("membership") in ("join", "invite"):
            pair = g.db.query(ChatConversation).filter_by(matrix_room_id=room_id).first()
            target = event.get("state_key") or ""
            if pair is not None and target != bot and \
                    target not in (matrix.mxid_for(pair.user_a_id), matrix.mxid_for(pair.user_b_id)):
                matrix.kick_as_bot(room_id, target, "Not part of this chat")
            return
        guild_channel = guild_channel_store.by_room(g.db, room_id)
        if guild_channel is not None:
            _guard_guild_channel(guild_channel, event)
            return
        if kind not in ("m.room.message", "m.reaction"):
            return
        channel = broadcast_store.by_room(g.db, room_id)
        if channel is None or channel[4] != 0:          # only public channels: a channel for circles is encrypted
            return
        content = event.get("content") or {}
        if kind == "m.room.message" and not channel_rules.mark_ok(app.config["SECRET_KEY"], room_id, content):
            matrix.redact_as_bot(room_id, event.get("event_id"), secrets.token_hex(12), "Posted around the channel's rules")
        elif kind == "m.reaction" and not channel_rules.reaction_ok(((content.get("m.relates_to") or {}).get("key"))):
            matrix.redact_as_bot(room_id, event.get("event_id"), secrets.token_hex(12), "Reactions are emoji")
    except Exception as e:
        app.logger.warning(f"appservice guard: {e}")


def _guard_encrypted_guild_channel(channel, event, now, remove):
    """An end-to-end encrypted guild channel (private and Circle guilds): its people write from their own browsers, and
    the site can't read what they wrote, so it can only keep the room to what a client sends: encrypted messages, emoji
    reactions and taking back one's own. Nothing is signed or rated, and nothing is kept. What it can do is count: more
    encrypted events from one person than the guild's own limit allows a minute (on top of Synapse's per-session limit)
    are removed."""
    kind, sender, content = event.get("type"), event.get("sender") or "", event.get("content") or {}
    if kind == "m.room.redaction":
        return
    if kind == "m.reaction":
        if not guild_channel_rules.reaction_ok((content.get("m.relates_to") or {}).get("key")):
            remove("Reactions are emoji")
        return
    if kind != "m.room.encrypted":
        remove("Not allowed in an encrypted channel")
        return
    user_id = _user_id_for_mxid(sender)
    if user_id is None or guild_channel_store.burst_count(r, channel.id, user_id, now) > guild_channel_rules.PER_MINUTE:
        remove("Too many messages in a minute")
        return
    guild_channel_store.touch(g.db, channel.matrix_room_id, now, user_id)


def _guard_guild_channel(channel, event):
    """A guild channel's room (helpers/guild_channels.py): everything in it comes through the site. A message needs the
    site's signature for this sender, rating and text, and a nonce nobody has used; a reaction must be an emoji; a
    sticker, a poll, an edit or anything else a member's own client could send is removed. A member taking back their
    own message is recorded. A valid message is what moves the channel's last activity (so a stream of removed ones
    can't light up everyone's unread dot)."""
    room_id, kind, sender = event.get("room_id"), event.get("type"), event.get("sender") or ""
    content = event.get("content") or {}
    event_id = event.get("event_id")
    now = int(time.time())

    def remove(reason):
        matrix.redact_as_bot(room_id, event_id, secrets.token_hex(12), reason)

    if event.get("state_key") is not None:
        return                                                # state events: only the bot can send them
    if channel.encrypted:
        _guard_encrypted_guild_channel(channel, event, now, remove)
        return
    if kind == "m.room.redaction":
        target = event.get("redacts") or content.get("redacts")
        if target:
            guild_channel_store.remove_post(g.db, channel.id, target, now)
        return
    if kind == "m.reaction":
        if not guild_channel_rules.reaction_ok((content.get("m.relates_to") or {}).get("key")):
            remove("Reactions are emoji")
        return
    if kind != "m.room.message":
        remove("Not allowed in a channel")
        return
    nonce = content.get(guild_channel_rules.NONCE_KEY)
    if not guild_channel_rules.mark_ok(app.config["SECRET_KEY"], room_id, sender, content) or \
            not guild_channel_store.claim_nonce(r, nonce, event_id):
        remove("Written around the channel's rules")
        return
    guild_channel_store.touch(g.db, room_id, now, _user_id_for_mxid(sender))


@app.route("/matrix/appservice/_matrix/app/v1/transactions/<txn_id>", methods=["PUT"])
def matrix_appservice_transaction(txn_id):
    token = app.config.get("MATRIX_HS_TOKEN") or ""
    auth = request.headers.get("Authorization", "")
    # an unset token never matches "Bearer None", and the compare takes the same time whatever is sent (M-11)
    if not token or not hmac.compare_digest(auth.encode(), f"Bearer {token}".encode()):
        abort(403)

    body = request.get_json(silent=True, force=True) or {}
    for event in body.get("events", []):
        _guard_room_event(event)
        if event.get("type") not in ("m.room.message", "m.room.encrypted"):
            continue
        if not is_new_message(event):     # a reaction to, or an edit of, an earlier message
            continue
        room_id = event.get("room_id")
        sender_mxid = event.get("sender")
        if not room_id or not sender_mxid:
            continue

        sender_user_id = _user_id_for_mxid(sender_mxid)
        if sender_user_id is None:
            continue

        convo = g.db.query(ChatConversation).filter_by(matrix_room_id=room_id).first()
        if not convo:
            # a group's room: one more unread for each of its other joined members (helpers/chat_group_store.py)
            if not chat_group_store.note_message(g.db, room_id, sender_user_id, int(time.time())):
                # a broadcast channel's (helpers/broadcast_store.py): everyone but the writer and whoever muted the owner
                channel = broadcast_store.by_room(g.db, room_id)
                if channel is not None:
                    muted = [uid for (uid,) in g.db.query(UserMute.user_id).filter(UserMute.target_id == channel[1]).all()]
                    broadcast_store.note_message(g.db, channel[0], sender_user_id, muted)
            continue
        recipient_id = convo.other_id(sender_user_id)

        if convo.tab_for(recipient_id) == "inbox":
            g.db.execute(
                text("""INSERT INTO chat_unread (conversation_id, user_id, unread_count, last_read_utc)
                         VALUES (:cid, :uid, 1, 0)
                         ON CONFLICT (conversation_id, user_id)
                         DO UPDATE SET unread_count = chat_unread.unread_count + 1"""),
                {"cid": convo.id, "uid": recipient_id},
            )
        convo.last_activity_utc = int(time.time())
        g.db.add(convo)

    g.db.commit()
    return jsonify({})


@app.route("/matrix/appservice/_matrix/app/v1/users/<matrix_user_id>", methods=["GET"])
def matrix_appservice_query_user(matrix_user_id):
    # We always proactively register users (see _ensure_provisioned) -
    # nothing to lazily create on query.
    abort(404)


@app.route("/matrix/appservice/_matrix/app/v1/rooms/<room_alias>", methods=["GET"])
def matrix_appservice_query_room(room_alias):
    # We never use room aliases.
    abort(404)


# --- search (the chat page's search box: assets/chat_src/src/chat_extras.js) ------------------------------------------
SEARCH_EACH = 20


@app.route("/api/chat/search", methods=["GET"])
@limiter.limit("30/minute", key_func=member_limit_key)
@auth_required
def chat_search(v):
    """Find chats (the owner, 2026-10-21): your own 1:1 chats, groups and channels; public broadcast channels you are
    not in; the channels of your guilds you may read; and guilds with chat that you may see. Groups are private and
    invite-only, so only your own are found. Never a chat request, never anyone in a block with you either way, never a
    name your word filter hides; each list is worked out by the rules its own tab and page use."""
    from ruqqus.routes.chat_groups import group_json
    from ruqqus.routes.broadcasts import channel_json, _blocked
    from ruqqus.routes.guild_channels import conversations_for
    from ruqqus.routes.search import listable_boards
    from ruqqus.helpers.visibility import text_hidden
    q = (request.args.get("q") or "").strip().lstrip("@+")[:40]
    found = {"chats": [], "channels": [], "guild_chats": [], "guilds": []}
    if len(q) < 2:
        return jsonify(found)
    needle = q.lower()

    def holds(*texts):
        return any(needle in str(t or "").lower() for t in texts)

    # your 1:1 chats (never a request), your groups, the channels you are in
    rows = g.db.query(ChatConversation).filter(
        or_(ChatConversation.user_a_id == v.id, ChatConversation.user_b_id == v.id)
    ).order_by(ChatConversation.last_activity_utc.desc()).limit(300).all()
    for convo in rows:
        if convo.tab_for(v.id) != "inbox":
            continue
        other = convo.other_user(v.id)
        if other is None or other.is_deleted or is_blocked(v, other) or user_hidden(other, v):
            continue
        if holds(other.username, other.shown_name):
            found["chats"].append({"kind": "pair", "key": f"c{convo.id}", "room_id": convo.matrix_room_id,
                                   "label": other.shown_name, "sub": f"@{other.username}", "avatar": other.profile_url})
    for row in chat_group_store.groups_for(g.db, v.id, "joined", limit=200):
        group = group_json(row[:8], chat_group_store.member(g.db, row[0], v.id), v)
        if holds(group["label"]):
            found["chats"].append({"kind": "group", "key": group["key"], "room_id": group["room_id"],
                                   "label": group["label"], "sub": f"Group · {group['size']} people", "avatar": None})
    for row in broadcast_store.joined_by(g.db, v.id, limit=100):
        channel = channel_json(row[:8], (row[8], row[9], 0), v)
        if holds(channel["label"]):
            found["chats"].append({"kind": "channel", "key": channel["key"], "room_id": channel["room_id"],
                                   "label": channel["label"], "sub": "Channel", "avatar": None})
    found["chats"] = found["chats"][:SEARCH_EACH]

    # public channels you are not in (routes/broadcasts.py: never an owner in a block either way, a name your filter
    # hides is never matched)
    for row in broadcast_store.search_public(g.db, q, v.id, limit=SEARCH_EACH * 2):
        if text_hidden(row[3], v) or _blocked(row, v):
            continue
        found["channels"].append(channel_json(row, None, v))
        if len(found["channels"]) >= SEARCH_EACH:
            break

    # the channels of your guilds you may read (the Guilds tab's own list and rules)
    mine = conversations_for(v)
    found["guild_chats"] = [c for c in mine if holds(c["label"], c["guild"])][:SEARCH_EACH]

    # guilds with chat you may see and are not in yet: their Chat tab keeps its own rules
    in_guilds = {c["guild"].lower() for c in mine}
    boards = listable_boards(g.db.query(Board).filter(Board.name.ilike(broadcast_store.like_pattern(q), escape=chr(92))), v)
    for board in boards.order_by(Board.stored_subscriber_count.desc()).limit(SEARCH_EACH * 2).all():
        if board.name.lower() in in_guilds or not board.can_view(v) or board.is_banned:
            continue
        channels = guild_channel_store.count(g.db, board.id)
        if not channels:
            continue
        found["guilds"].append({"name": board.name, "url": f"/+{board.name}/chat", "channels": channels,
                                "avatar": board.profile_url})
        if len(found["guilds"]) >= SEARCH_EACH:
            break
    return jsonify(found)
