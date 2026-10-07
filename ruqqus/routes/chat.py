import html
import re
import time
from flask import *
from sqlalchemy import or_, func, text

from ruqqus.helpers.wrappers import *
from ruqqus.helpers.get import *
from ruqqus.helpers.chat_permissions import can_message_directly, is_blocked
from ruqqus.helpers.chat_events import is_new_message
from ruqqus.helpers.visibility import filter_users, post_hidden, user_hidden
from ruqqus.helpers.secret_box import encrypt_secret, decrypt_secret
from ruqqus.classes import *
import ruqqus.helpers.matrix_client as matrix
from ruqqus.__main__ import app


def _ensure_provisioned(v):
    identity = g.db.query(ChatIdentity).filter_by(user_id=v.id).first()
    if identity:
        return identity.matrix_user_id
    mxid = matrix.provision_user(v.id)
    g.db.add(ChatIdentity(user_id=v.id, matrix_user_id=mxid))
    g.db.commit()
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
    return render_template("chat.html", v=v)


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


@app.route("/api/chat/recovery_key", methods=["GET"])
@auth_required
def chat_recovery_key_get(v):
    """Lets the chat bundle unlock E2EE automatically on any device with
    no user interaction - see crypto.js's ensureEncryptionReady(). A plain
    same-origin JSON read gated only by the normal session cookie, same as
    GET /api/chat/conversations; nothing here is a state change for CSRF
    to target."""
    identity = g.db.query(ChatIdentity).filter_by(user_id=v.id).first()
    if not identity or not identity.recovery_key_encrypted:
        return jsonify({"recovery_key": None})
    return jsonify({"recovery_key": decrypt_secret(identity.recovery_key_encrypted)})


@app.route("/api/chat/recovery_key", methods=["POST"])
@auth_required
@validate_formkey
def chat_recovery_key_set(v):
    """Called once by the bundle right after it generates a brand-new
    recovery key on first-ever E2EE setup for this account, so it never
    has to be shown to or saved by the user."""
    recovery_key = request.values.get("recovery_key", "")
    if not recovery_key:
        abort(400)
    identity = g.db.query(ChatIdentity).filter_by(user_id=v.id).first()
    if not identity:
        abort(404)
    identity.recovery_key_encrypted = encrypt_secret(recovery_key)
    g.db.add(identity)
    g.db.commit()
    return "", 204


@app.route("/api/chat/recovery_key/reveal", methods=["POST"])
@auth_required
@validate_formkey
def chat_recovery_key_reveal(v):
    """Manual export path for a human who wants to see/copy their actual
    key - gated behind re-entering the account password, same pattern as
    settings_log_out_others in ruqqus/routes/settings.py."""
    if not v.verifyPass(request.form.get("password", "")):
        return render_template("settings_security.html", v=v, error="Incorrect Password"), 401

    identity = g.db.query(ChatIdentity).filter_by(user_id=v.id).first()
    if not identity or not identity.recovery_key_encrypted:
        return render_template("settings_security.html", v=v, error="No recovery key has been generated yet."), 404

    return render_template("settings_security.html", v=v,
                           revealed_recovery_key=decrypt_secret(identity.recovery_key_encrypted))


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
            "conversation_id": c.id,
            "room_id": c.matrix_room_id,
            "other_user": {
                "id": other.id,
                "username": other.username,
                "profile_url": other.profile_url,
            },
            "unread_count": unread.unread_count if unread else 0,
            "last_activity_utc": c.last_activity_utc,
            "is_initiator": c.initiator_id == v.id,
        })
        if len(out) >= 25:
            break

    return jsonify({"conversations": out})


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
        out.append({"username": other.username, "profile_url": other.profile_url, "room_id": convo.matrix_room_id, "recent": True})
        seen.add(other.id)
        if len(out) >= SHARE_RECENT:
            break

    follows = [i for i in v.following_ids if i not in seen and i not in blocked]
    if follows and len(out) < SHARE_TARGETS:
        people = g.db.query(User).filter(User.id.in_(follows), User.is_deleted == False, User.is_banned == 0)
        if q:
            people = people.filter(User.username.ilike("%" + q.replace("\\", "").replace("%", "\\%").replace("_", "\\_") + "%"))
        people = filter_users(people, v).order_by(User.username.asc()).limit(SHARE_TARGETS - len(out)).all()
        out += [{"username": u.username, "profile_url": u.profile_url, "room_id": None, "recent": False} for u in people]

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
    if not post.post_public and not post.board.can_view(v) and post.author_id != v.id:
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
    g.db.query(ChatUnread).filter_by(conversation_id=cid).delete()
    g.db.delete(c)
    g.db.commit()
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

@app.route("/matrix/appservice/_matrix/app/v1/transactions/<txn_id>", methods=["PUT"])
def matrix_appservice_transaction(txn_id):
    auth = request.headers.get("Authorization", "")
    if auth != f"Bearer {app.config['MATRIX_HS_TOKEN']}":
        abort(403)

    body = request.get_json(silent=True, force=True) or {}
    for event in body.get("events", []):
        if event.get("type") not in ("m.room.message", "m.room.encrypted"):
            continue
        if not is_new_message(event):     # a reaction to, or an edit of, an earlier message
            continue
        room_id = event.get("room_id")
        sender_mxid = event.get("sender")
        if not room_id or not sender_mxid:
            continue

        convo = g.db.query(ChatConversation).filter_by(matrix_room_id=room_id).first()
        if not convo:
            continue

        sender_user_id = _user_id_for_mxid(sender_mxid)
        if sender_user_id is None:
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
