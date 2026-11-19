import hashlib
import hmac
import html
import re
import secrets
import time
from os import environ
from flask import *
from flask import session as flask_session
from sqlalchemy import or_, func, text

from ruqqus.helpers.wrappers import *
from ruqqus.helpers.get import *
from ruqqus.helpers.chat_permissions import can_message_directly, is_blocked
from ruqqus.helpers.chat_events import is_new_message
from ruqqus.helpers import push_rules, push_send
from ruqqus.helpers import age_declaration, broadcast_store, chat_group_store, chat_devices, chat_signouts, chat_store_keys, chat_tokens, guild_channel_store
from ruqqus.helpers import guild_channels as guild_channel_rules
from ruqqus.helpers.visibility import comment_hidden, filter_users, post_hidden, user_hidden
from ruqqus.helpers.i18n_runtime import msg, tr, trn, trp
from ruqqus.helpers.secret_box import encrypt_secret, decrypt_secret
from ruqqus.classes import *
import ruqqus.helpers.matrix_client as matrix
from ruqqus.__main__ import app, limiter, member_limit_key, r


PROVISION_LOCK = 7004               # pg_advisory_xact_lock(PROVISION_LOCK, user id): one first chat account at a time per member


def _ensure_provisioned(v):
    identity = g.db.query(ChatIdentity).filter_by(user_id=v.id).first()
    if identity:
        return identity.matrix_user_id
    # Two requests for a new member at once (their own page asking for a token while someone starts a chat with them) both find no record
    # and both register: the second is told the account exists and would be refused as an orphan, with a false alarm to every admin
    # (the review of 2026-11-06, C3). One at a time per member, and the record read again after waiting: the first one's commit releases
    # the lock, and the second then finds the record the first made.
    g.db.execute(text("SELECT pg_advisory_xact_lock(:k, :u)"), {"k": PROVISION_LOCK, "u": int(v.id)})
    identity = g.db.query(ChatIdentity).filter_by(user_id=v.id).first()
    if identity:
        return identity.matrix_user_id
    # no record here: a Matrix account Synapse already has for this id belonged to someone else (a database restored
    # without Synapse's, audit D-07), so it is never handed over unless an admin says the two were restored together
    try:
        mxid = matrix.provision_user(v.id, adopt_existing=environ.get("MATRIX_ADOPT_EXISTING") == "1")
    except matrix.OrphanMatrixAccount as e:
        print(f"chat refused: {e}. Restore Synapse's database with the site's (RESTORE.md), or give the account back "
              "at /admin/chat if it is the same person.", flush=True)
        _refuse_chat(v)
    g.db.add(ChatIdentity(user_id=v.id, matrix_user_id=mxid))
    g.db.commit()
    chat_devices.sync_name(g.db, v, mxid)       # outside apps show the @handle (helpers/chat_devices.py)
    return mxid


def _refuse_chat(user):
    """The chat server already has an account with this member's id and the site has no record of it (the two databases
    parted: helpers/chat_health.py). It is never handed over; the admins are told once, new ids are moved past the mark,
    and the member reads why instead of a bare 503."""
    from ruqqus.helpers import chat_health
    from ruqqus.helpers.alerts import send_to_many
    try:
        chat_health.run(g.db, r, fix=True)          # new sign-ups are moved past the mark at once
    except Exception as e:
        print(f"chat health check failed: {type(e).__name__}", flush=True)
    if chat_health.note_refused(r, user.id):
        try:
            admins = [x for (x,) in g.db.query(User.id).filter(User.admin_level >= 3, User.is_deleted == False).all()]
            send_to_many(admins, f"Chat was refused to @{user.username} (user id {user.id}): the chat server already has "
                                 "an account with that id and the site has no record of it. See [/admin/chat](/admin/chat).")
        except Exception as e:
            g.db.rollback()
            print(f"chat refusal notice failed: {type(e).__name__}", flush=True)
    own = getattr(g, "v", None) is not None and g.v.id == user.id
    sentence = (tr("Chat isn't available for your account right now. The site's admins have been told.") if own else
                tr("Chat with this account isn't available right now. The site's admins have been told."))
    if request.path.startswith("/api/") or request.is_json or request.accept_mimetypes.best == "application/json":
        abort(make_response(jsonify({"error": sentence}), 503))
    abort(make_response(render_template("message.html", v=getattr(g, "v", None), title=tr("Chat isn't available"),
                                        error=sentence), 503))


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
@not_qr_limited
@not_child_locked("chat")
@not_quarantined("chat")
def chat_page(v):
    try:
        mxid = _ensure_provisioned(v)
    except matrix.MatrixError as e:         # the chat server is down or answering badly: try again later, not a crash
        g.db.rollback()
        print(f"chat unavailable while setting up a chat account: {e}", flush=True)
        abort(503)
    from ruqqus.helpers.chat_groups import MAX_MEMBERS
    return render_template("chat.html", v=v, chat_group_max=MAX_MEMBERS, chat_mxid=mxid)


@app.route("/api/chat/token", methods=["POST"])
@auth_required
@not_qr_limited
@not_child_locked("messaging")
@not_quarantined("chat")
@validate_formkey
@limiter.limit("60/minute;600/hour", key_func=member_limit_key)
def chat_token(v):
    # no device is made without its row (helpers/chat_signouts.py): before the migration, chat waits instead (the page retries)
    if not chat_signouts.tables_ready(g.db):
        return _token_answer(503, {"retry": True})
    try:
        mxid = _ensure_provisioned(v)
    except matrix.MatrixError:
        # a first chat whose account the chat server couldn't make (down, an error, the id mark busy): nothing was handed out, try
        # again (OrphanMatrixAccount never gets here: _refuse_chat answers it with its own refusal)
        g.db.rollback()
        return _token_answer(503, {"retry": True})
    # the browser's own device, coming back after a log out, is taken up again only while it is this account's, not signed out,
    # and still on the chat server; anything else is a new device, whose new id tells the browser to start afresh (token.js).
    # Every device handed out is recorded, with its store key (helpers/chat_tokens.py, helpers/chat_store_keys.py),
    # and the account is read again before a device is handed out (a deletion or chat switched off while this waited)
    status, body = chat_tokens.issue(g.db, matrix, v.id, mxid, request.values, flask_session.get("session_id"),
                                     app.config["MATRIX_PUBLIC_URL"], site_name=app.config["SITE_NAME"],
                                     nonce=flask_session.get("login_nonce", 0), secret=chat_store_keys.secret_of(app.config),
                                     report_seconds=app.config["CHAT_TOKEN_REPORT_SECONDS"])
    return _token_answer(status, body)


@app.route("/api/chat/store_keys", methods=["POST"])
@auth_required
@not_qr_limited
@not_child_locked("messaging")
@not_quarantined("chat")
@validate_formkey
@limiter.limit("20/minute", key_func=member_limit_key)
def chat_store_keys_route(v):
    """The store keys of this account's own site devices, revoked or not (`device_ids`, up to 4): what a browser needs to open a store it
    kept from an older device of the account (the harvest, from build step 6). Any other id is left out and never told apart. No Matrix
    call, and nothing is made: an account with no chat yet has no devices."""
    if not chat_signouts.tables_ready(g.db):
        return _token_answer(503, {"retry": True})
    device_ids = chat_tokens.parse_store_key_ids(request.values.get("device_ids"))
    if device_ids is None:
        return _token_answer(400, {"bad_request": True})
    return _token_answer(200, chat_tokens.store_keys(g.db, v.id, chat_signouts.mxid_of(g.db, v.id), device_ids,
                                                     chat_store_keys.secret_of(app.config)))


def _token_answer(status, body):
    if body.get("retry"):
        body = dict(body, error=tr("Chat can't start right now. Try again in a moment."))
    elif body.get("bad_request"):
        body = {"error": tr("That request wasn't understood.")}
    elif body.get("signed_out"):
        body = {"error": tr("You're no longer signed in here. Sign in again to use chat."), "signed_out": True}
    elif body.get("held"):
        from ruqqus.helpers import quarantine
        body = {"error": msg(quarantine.HELD_AUTOMATIC if quarantine.is_automatic(getattr(g, "v", None)) else quarantine.HELD), "held": True}
    elif body.get("age_locked"):
        refused = age_declaration.PARENT_OFF if body.get("parent") else age_declaration.FEATURE_LOCKED
        body = {"error": msg(refused), "age_locked": True}
    resp = jsonify(body)
    resp.status_code = status
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/api/chat/formkey", methods=["GET"])
@limiter.limit("30/minute", key_func=member_limit_key)
@auth_required
def chat_formkey(v):
    """The form key of this cookie as it is now. A credential event that spares this sign-in (Log out everywhere, a password change,
    two-factor on or off) raises the account's nonce, which form keys are made from, so a chat page, frame, call window or Space page
    opened before it holds a key the form-key check now refuses: it asks here and tries again. A sign-in that ended is refused by
    @auth_required itself. Changes nothing; another site can't read the answer (same-origin JSON). The answer names the account's
    Matrix id, the same one the page was drawn with (chat_devices.mxid_of): a page drawn for another account that signed in here since
    keeps no key of this one's (assets/chat_src/src/site.js refreshFormkey), so it never posts as someone else."""
    resp = jsonify({"formkey": v.formkey, "mxid": chat_devices.mxid_of(g.db, v.id)})
    resp.headers["Cache-Control"] = "no-store"
    return resp


_BLOB = re.compile(r"^[A-Za-z0-9_\-]{40,4096}$")


def _identity(v):
    identity = g.db.query(ChatIdentity).filter_by(user_id=v.id).first()
    if not identity:
        abort(404)
    return identity


@app.route("/api/chat/key", methods=["GET"])
@auth_required
@not_qr_limited
@not_child_locked("messaging")
@not_quarantined("chat")
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
                    "ready": bool(identity.ready_utc),
                    # Start over asks for the two-factor code too (routes/chat_key_reset_pages.py)
                    "mfa": bool(v.mfa_secret),
                    # whether the member said they kept the recovery code (the chat page's reminder: chat_extras.js)
                    "code_saved": bool(identity.code_saved_utc)})


@app.route("/api/chat/key", methods=["POST"])
@auth_required
@not_qr_limited
@not_child_locked("messaging")
@not_quarantined("chat")
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
            return jsonify({"error": tr("That key couldn't be saved.")}), 400
        setattr(identity, column, value)
        changed = True
    if not changed:
        return jsonify({"error": tr("Nothing to save.")}), 400
    if identity.key_blob_password:
        identity.recovery_key_encrypted = None          # the server can no longer read the key
    if request.values.get("recovery_blob") is not None:
        identity.code_saved_utc = 0                     # a new code: kept only once the member says so (/api/chat/code_saved)
    g.db.add(identity)
    g.db.commit()
    return "", 204


@app.route("/api/chat/code_saved", methods=["POST"])
@auth_required
@not_qr_limited
@not_child_locked("messaging")
@not_quarantined("chat")
@validate_formkey
def chat_code_saved(v):
    """The member says they kept their recovery code ("I've saved it" under a new code, or "I have it" on the chat page's reminder). Only
    a time is kept; the code itself never reaches the site."""
    identity = _identity(v)
    if not identity.key_blob_recovery:
        return jsonify({"error": tr("There's no recovery code to keep yet.")}), 409
    identity.code_saved_utc = int(time.time())
    g.db.add(identity)
    g.db.commit()
    return "", 204


@app.route("/api/chat/recovery_key", methods=["GET"])
@auth_required
@not_qr_limited
@not_child_locked("messaging")
@not_quarantined("chat")
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
@not_qr_limited
@not_child_locked("messaging")
@not_quarantined("chat")
@validate_formkey
def chat_check_password(v):
    """Whether a typed password is the account's, before the browser locks the chat key with it (a typo would lock
    it with the wrong key). The password is checked like a login and kept nowhere."""
    return jsonify({"ok": bool(v.verifyPass(request.form.get("password", "")))})


@app.route("/api/chat/ready", methods=["POST"])
@auth_required
@not_qr_limited
@not_child_locked("chat")
@not_quarantined("chat")
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
@not_qr_limited
@not_child_locked("chat")
@not_quarantined("chat")
@validate_formkey
def chat_start(v):
    target = get_user(request.values.get("username", ""), graceful=True)
    if not target or target.id == v.id:
        abort(400)
    if is_blocked(v, target):
        abort(403)
    if target.is_bot or v.is_bot:
        abort(403)                  # nobody reads a bot's chats (helpers/bots.py): no room is ever made with one
    if age_declaration.target_refusal(target, "chat"):
        abort(403)                  # nobody starts a chat with a child-locked account (helpers/age_declaration.py); the page doesn't say why

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
@not_qr_limited
@not_child_locked("chat")
@not_quarantined("chat")
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
                # the name as the rest of the site shows it (CLAUDE.md "Display names"), never the chat server's profile
                # name: the @handle alone when the viewer's word filter hides the name, "Deleted account" for a deleted one
                "display_name": "" if other.is_deleted or user_hidden(other, v) else (other.display_name or ""),
                "shown_name": tr("Deleted account") if other.is_deleted else (other.username if user_hidden(other, v) else other.shown_name),
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
@not_qr_limited
@not_child_locked("chat")
@not_quarantined("chat")
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
@not_child_locked("chat")
@not_quarantined("chat")
def chat_post_preview(pid, v):
    """The small card under a message that links to a post, made for the person reading it:
    the same visibility rules as opening the post, and never the author (so an anonymous post
    stays anonymous). When they may not see it the card says so instead."""
    if not POST_ID.match(pid):
        return jsonify({"ok": False, "notice": tr("This post does not exist.")})
    post = get_post(pid, v=v, graceful=True)
    if post is None:
        return jsonify({"ok": False, "notice": tr("This post does not exist.")})

    admin = v.admin_level >= 3
    if (post.is_banned or post.board.is_banned or (post.hidden_by_guild and not post._guild_hide_lifted_for(v))) and not admin:
        return jsonify({"ok": False, "notice": tr("This post was removed.")})
    if post.deleted_utc and not admin:
        return jsonify({"ok": False, "notice": tr("This post was deleted.")})
    if post_hidden(post, v):
        return jsonify({"ok": False, "notice": tr("Hidden by your word filter.")})
    # the post page's own rule for private guilds, and a block either way hides it as a quote card does (audit
    # M-12); an anonymous post is never hidden for whose it is
    if post.in_private_guild_for(v):
        return jsonify({"ok": False, "notice": tr("Not available to you.")})
    if not post.is_anonymous and post.author_id != v.id and post.author and v.any_block_exists(post.author):
        return jsonify({"ok": False, "notice": tr("Not available to you.")})

    return jsonify({
        "ok": True,
        "title": html.unescape(post.title or "")[:200],
        "guild": None if post.is_profile_post else post.board.name,
        "url": post.permalink,
        "thumb": post.thumb_url if (post.has_thumb and not post.is_sensitive) else None,
        "comments": post.comment_count,
    })


@app.route("/api/chat/comment_preview/<cid>", methods=["GET"])
@auth_required
@not_child_locked("chat")
@not_quarantined("chat")
def chat_comment_preview(cid, v):
    """The card under a message that links to a comment, made for the person reading it, by the rules of opening the
    comment (get_comment: a Circle post's comments, a block by its author) and its post (the post card's rules), with
    neither author ever named (an anonymous comment or post stays anonymous). The comment's words, and the title of
    the post it is on."""
    unavailable = jsonify({"ok": False, "notice": tr("Not available to you.")})
    if not POST_ID.match(cid):
        return unavailable
    comment = get_comment(cid, v=v, graceful=True)
    if comment is None:
        return unavailable
    post = comment.post
    admin = v.admin_level >= 3
    # a comment a guild hid, or its post's author hid, shows "removed" in its thread and says nothing of what it said here either (the second review, C28)
    if (comment.is_banned or post.is_banned or post.board.is_banned or comment._text_hidden_for(v)
            or (post.hidden_by_guild and not post._guild_hide_lifted_for(v))) and not admin:
        return jsonify({"ok": False, "notice": tr("This post was removed.")})
    if (comment.deleted_utc or post.deleted_utc) and not admin:
        return jsonify({"ok": False, "notice": tr("This post was deleted.")})
    if comment_hidden(comment, v) or post_hidden(post, v):
        return jsonify({"ok": False, "notice": tr("Hidden by your word filter.")})
    if post.in_private_guild_for(v):
        return unavailable
    # a block either way, with either author, as the post card says it; never for an anonymous item (it would say whose it is)
    for item in (comment, post):
        if not item.is_anonymous and item.author_id != v.id and item.author and v.any_block_exists(item.author):
            return unavailable

    return jsonify({
        "ok": True,
        "text": " ".join((comment.body or "").split())[:280],
        "title": html.unescape(post.title or "")[:200],
        "guild": None if post.is_profile_post else post.board.name,
        "url": comment.permalink,
    })


@app.route("/api/chat/unread_count", methods=["GET"])
@auth_required
def chat_unread_count(v):
    # Polled by the navbar chat badge (assets/js/chat_badge.js). Read-only:
    # unlike /notifications it never marks anything as read.
    return jsonify({"unread": v.chat_unread_messages})


@app.route("/api/chat/conversations/<int:cid>/accept", methods=["POST"])
@auth_required
@not_qr_limited
@not_child_locked("chat")
@not_quarantined("chat")
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
@not_qr_limited
@not_child_locked("chat")
@not_quarantined("chat")
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
@not_qr_limited
@not_child_locked("chat")
@not_quarantined("chat")
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
        if event.get("state_key") is not None or kind == "m.room.redaction":
            return                      # state is the bot's alone; taking back one's own message is allowed
        channel = broadcast_store.by_room(g.db, room_id)
        if channel is None or channel[4] != 0:          # only public channels: a channel for circles is encrypted
            return
        content = event.get("content") or {}
        # everything else a writer's own Matrix app could send (stickers, polls, encrypted events...) went around the
        # word filter, the daily count and reports (bug hunt): only a signed message or an emoji reaction stays
        reason = None
        if kind == "m.room.message":
            if not channel_rules.mark_ok(app.config["SECRET_KEY"], room_id, content):
                reason = "Posted around the channel's rules"
        elif kind == "m.reaction":
            if not channel_rules.reaction_ok(((content.get("m.relates_to") or {}).get("key"))):
                reason = "Reactions are emoji"
        else:
            reason = "Not allowed in a channel"
        if reason:
            matrix.redact_as_bot(room_id, event.get("event_id"), secrets.token_hex(12), reason)
            return True                 # removed: never counted as an unread message
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


def _note_disappearing(event):
    """A message, reaction or edit sent in a 1:1 chat or group whose timer is on is noted with when it is due (its id and a
    time only: helpers/chat_disappear.py); the bot deletes it then. State events (the timer itself, pins) never disappear."""
    from ruqqus.helpers import chat_disappear, chat_extras
    if event.get("type") not in ("m.room.message", "m.room.encrypted", "m.reaction") or "state_key" in event:
        return
    room_id, event_id = event.get("room_id"), event.get("event_id")
    if not room_id or not event_id:
        return
    seconds = chat_extras.timer_of(g.db, room_id)
    if seconds:
        sent = int(event.get("origin_server_ts") or time.time() * 1000) // 1000
        chat_disappear.note(g.db, room_id, event_id, sent, seconds)


@app.route("/matrix/appservice/_matrix/app/v1/transactions/<txn_id>", methods=["PUT"])
def matrix_appservice_transaction(txn_id):
    token = app.config.get("MATRIX_HS_TOKEN") or ""
    auth = request.headers.get("Authorization", "")
    # an unset token never matches "Bearer None", and the compare takes the same time whatever is sent (M-11)
    if not token or not hmac.compare_digest(auth.encode(), f"Bearer {token}".encode()):
        abort(403)

    # the chat server sends a transaction again until it is answered: one already handled is answered at once (it used to
    # count its messages twice, and would send its push notifications twice)
    seen_key = f"as:txn:{hashlib.sha256(txn_id.encode()).hexdigest()}"
    if r is not None and r.get(seen_key):
        return jsonify({})

    body = request.get_json(silent=True, force=True) or {}
    pushes = []                     # (recipient ids, kind, sender id, room id): sent after the commit (helpers/push_send.py)
    for event in body.get("events", []):
        if _guard_room_event(event):
            continue                    # removed as it arrived (bug hunt: it still raised every member's unread count)
        _note_disappearing(event)
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
            if chat_group_store.note_message(g.db, room_id, sender_user_id, int(time.time())):
                pushes.append((chat_group_store.push_targets(g.db, room_id, sender_user_id, (push_rules.EVERY,)),
                               push_rules.MESSAGE, sender_user_id, room_id))
            else:
                # a broadcast channel's (helpers/broadcast_store.py): everyone but the writer and whoever muted the owner
                channel = broadcast_store.by_room(g.db, room_id)
                if channel is not None:
                    muted = [uid for (uid,) in g.db.query(UserMute.user_id).filter(UserMute.target_id == channel[1]).all()]
                    broadcast_store.note_message(g.db, channel[0], sender_user_id, muted)
            continue
        recipient_id = convo.other_id(sender_user_id)
        pushes.append(([recipient_id], push_rules.MESSAGE if convo.tab_for(recipient_id) == "inbox" else push_rules.REQUEST,
                       sender_user_id, room_id))

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

    jobs = []
    for recipients, kind, sender_id, room_id in pushes:
        sender = g.db.query(User).filter_by(id=sender_id).first()
        if sender is not None and recipients:
            jobs += push_send.prepare(g.db, recipients, kind, sender, room_id)
    g.db.commit()
    if r is not None:
        r.set(seen_key, 1, ex=2 * 86400)
    push_send.send_later(jobs, urgency="high")
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
@not_qr_limited
@not_child_locked("chat")
@not_quarantined("chat")
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
            # Translators: under a group chat's name in the chat search; the number of people in the group
            sub = trn("Group · %(num)s people", "Group · %(num)s people", group["size"])
            found["chats"].append({"kind": "group", "key": group["key"], "room_id": group["room_id"],
                                   "label": group["label"], "sub": sub, "avatar": None})
    for row in broadcast_store.joined_by(g.db, v.id, limit=100):
        channel = channel_json(row[:8], (row[8], row[9], 0), v)
        if holds(channel["label"]):
            found["chats"].append({"kind": "channel", "key": channel["key"], "room_id": channel["room_id"],
                                   "label": channel["label"], "sub": trp("chat kind", "Channel"),
                                   "avatar": None})
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
