"""Group chats: making a group, adding and removing people, roles, leaving, invitations (rules in
helpers/chat_groups.py, SQL in helpers/chat_group_store.py).

Every change is a POST with the form key. A group that is not yours to see is a 404, whatever the reason. The Matrix
side (the bot's invite, kick, the member's join or leave) goes first; the database is committed only once it went
through, so the two never disagree about who is in a group for long.
"""
import time

from flask import *
from sqlalchemy import or_

from ruqqus.helpers.wrappers import *
from ruqqus.helpers.get import *
from ruqqus.helpers.visibility import filter_users, text_hidden, user_hidden
from ruqqus.classes import *
import ruqqus.helpers.matrix_client as matrix
from ruqqus.__main__ import app

from ruqqus.helpers import chat_groups as group_rules
from ruqqus.helpers import chat_group_store as group_store
from ruqqus.helpers.chat_permissions import can_message_directly

_DAY = 86400


def _error(message, status=400):
    return jsonify({"error": message}), status


def _now():
    return int(time.time())


def _mxid(user_id):
    from ruqqus.routes.chat import _mxid_for_user_id
    return _mxid_for_user_id(user_id)


def _blocked_ids(v):
    """Everyone in a block with v, either way."""
    rows = g.db.query(UserBlock.user_id, UserBlock.target_id).filter(
        or_(UserBlock.user_id == v.id, UserBlock.target_id == v.id)).all()
    return {b if a == v.id else a for a, b in rows}


def _blocked_by_me(v):
    return {t for (t,) in g.db.query(UserBlock.target_id).filter(UserBlock.user_id == v.id).all()}


def _name_severity(name):
    if not name:
        return 0
    from ruqqus.helpers.word_filter_store import get_filter
    return get_filter(g.db).severity(name)


def _usernames():
    """Names from the form: `usernames` (commas or spaces) and repeated `username`, without @, at most 31."""
    raw = request.values.getlist("username") + [request.values.get("usernames", "")]
    names = []
    for chunk in raw:
        for name in chunk.replace(",", " ").split():
            name = name.strip().lstrip("@")
            if name and name.lower() not in (n.lower() for n in names):
                names.append(name)
    return names[:group_rules.MAX_MEMBERS - 1]


def _out_of_call(room_id, user_id):
    """Out of the group's call too, if it has one (the others' windows drop them at their next heartbeat)."""
    from ruqqus.__main__ import r
    from ruqqus.helpers import call_store
    if r is not None:
        call_store.leave_user(r, room_id, user_id)


def _membership(gid, v, joined_only=True):
    """(group row, my member row), or a 404."""
    group = group_store.group(g.db, gid)
    me = group_store.member(g.db, gid, v.id) if group else None
    if group is None or me is None or (joined_only and me[2] != group_rules.JOINED):
        abort(404)
    return group, me


def _add_one(group, target, v, now):
    """Invites one person (the room first), and lets them straight in when the two follow each other."""
    room_id = group[1]
    mxid = _mxid(target.id)
    matrix.invite_as_bot(room_id, mxid)
    direct = can_message_directly(v, target)
    if direct:
        matrix.join_room_as(room_id, mxid)
    group_store.add_member(g.db, group[0], target.id, group_rules.MEMBER,
                           group_rules.JOINED if direct else group_rules.INVITED, v.id, now)


def _refusals(v, role, names, existing_ids, size):
    """Each typed name to a user, or the first refusal. Nobody is added unless every name can be."""
    blocked = _blocked_ids(v)
    users = []
    for name in names:
        target = get_user(name, graceful=True)
        refusal = group_rules.add_refusal(v.id, role, target, target is not None and target.id in blocked,
                                          target is not None and target.id in existing_ids,
                                          size + len(users), typed_name=name)
        if refusal:
            return None, refusal
        users.append(target)
    return users, None


def _daily_adds_left(v, now):
    return group_rules.ADDS_PER_DAY - group_store.added_since(g.db, v.id, now - _DAY)


@app.route("/api/chat/groups", methods=["POST"])
@is_not_banned
@validate_formkey
def chat_group_create(v):
    if v.is_bot:
        abort(403)
    now = _now()
    try:
        name = group_rules.clean_name(request.values.get("name", ""))
    except group_rules.Refused as e:
        return _error(str(e))
    names = _usernames()
    if not names:
        return _error("Add at least one person.")
    if group_store.created_since(g.db, v.id, now - _DAY) >= group_rules.GROUPS_PER_DAY:
        return _error(f"You can start at most {group_rules.GROUPS_PER_DAY} groups a day.", 429)
    if _daily_adds_left(v, now) < len(names):
        return _error("You've added a lot of people to groups today. Try again tomorrow.", 429)

    users, refusal = _refusals(v, group_rules.OWNER, names, set(), 1)
    if refusal:
        return _error(refusal)

    from ruqqus.routes.chat import _ensure_provisioned
    owner_mxid = _ensure_provisioned(v)
    mxids = [owner_mxid] + [_mxid(u.id) for u in users]
    try:
        room_id = matrix.create_group_room(mxids)
        matrix.join_room_as(room_id, owner_mxid)
        gid = group_store.create(g.db, room_id, name, _name_severity(name), v.id, now)
        group = group_store.group(g.db, gid)
        for target in users:
            direct = can_message_directly(v, target)
            if direct:
                matrix.join_room_as(room_id, _mxid(target.id))
            group_store.add_member(g.db, gid, target.id, group_rules.MEMBER,
                                   group_rules.JOINED if direct else group_rules.INVITED, v.id, now)
    except matrix.MatrixError:
        g.db.rollback()
        return _error("Chat isn't answering right now. Try again in a moment.", 502)
    g.db.commit()
    return jsonify({"group_id": group[0], "room_id": room_id})


@app.route("/api/chat/groups/<int:gid>", methods=["GET"])
@auth_required
def chat_group_info(gid, v):
    group, me = _membership(gid, v, joined_only=False)
    return jsonify(group_json(group, me, v, full=True))


@app.route("/api/chat/groups/<int:gid>/add", methods=["POST"])
@is_not_banned
@validate_formkey
def chat_group_add(gid, v):
    group, me = _membership(gid, v)
    now = _now()
    names = _usernames()
    if not names:
        return _error("Type who to add.")
    if _daily_adds_left(v, now) < len(names):
        return _error("You've added a lot of people to groups today. Try again tomorrow.", 429)
    existing = {m[0] for m in group_store.members(g.db, gid)}
    users, refusal = _refusals(v, me[1], names, existing, len(existing))
    if refusal:
        return _error(refusal, 403 if not group_rules.may_manage(me[1]) else 400)
    try:
        for target in users:
            _add_one(group, target, v, now)
    except matrix.MatrixError:
        g.db.rollback()
        return _error("Chat isn't answering right now. Try again in a moment.", 502)
    g.db.commit()
    return jsonify(group_json(group_store.group(g.db, gid), group_store.member(g.db, gid, v.id), v, full=True))


@app.route("/api/chat/groups/<int:gid>/remove", methods=["POST"])
@auth_required
@validate_formkey
def chat_group_remove(gid, v):
    group, me = _membership(gid, v)
    target = get_user(request.values.get("username", "").lstrip("@"), graceful=True)
    row = group_store.member(g.db, gid, target.id) if target else None
    refusal = group_rules.remove_refusal(v.id, me[1], target.id if target else None, row[1] if row else None)
    if refusal:
        return _error(refusal, 403 if row else 400)
    try:
        matrix.kick_as_bot(group[1], _mxid(target.id))
    except matrix.MatrixError:
        return _error("Chat isn't answering right now. Try again in a moment.", 502)
    _out_of_call(group[1], target.id)
    group_store.remove_member(g.db, gid, target.id)
    g.db.commit()
    return jsonify(group_json(group_store.group(g.db, gid), group_store.member(g.db, gid, v.id), v, full=True))


def _leave(group, v, now):
    """Out of the group, handing it on when the owner leaves, closing it when nobody is left."""
    gid = group[0]
    me = group_store.member(g.db, gid, v.id)
    matrix.leave_room_as(group[1], _mxid(v.id))
    _out_of_call(group[1], v.id)
    group_store.remove_member(g.db, gid, v.id)
    if me and me[1] == group_rules.OWNER:
        rest = group_store.members(g.db, gid)
        heir = group_rules.next_owner([(m[0], m[1], m[2], m[6]) for m in rest])
        if heir is not None:
            group_store.set_role(g.db, gid, heir, group_rules.OWNER)
        else:
            group_store.close(g.db, gid, now)        # nobody who joined is left; open invitations go with it
    elif not group_store.size(g.db, gid):
        group_store.close(g.db, gid, now)


@app.route("/api/chat/groups/<int:gid>/leave", methods=["POST"])
@auth_required
@validate_formkey
def chat_group_leave(gid, v):
    group, me = _membership(gid, v)
    try:
        _leave(group, v, _now())
    except matrix.MatrixError:
        g.db.rollback()
        return _error("Chat isn't answering right now. Try again in a moment.", 502)
    g.db.commit()
    return "", 204


@app.route("/api/chat/groups/<int:gid>/accept", methods=["POST"])
@auth_required
@validate_formkey
def chat_group_accept(gid, v):
    group, me = _membership(gid, v, joined_only=False)
    if me[2] != group_rules.INVITED:
        abort(404)
    try:
        matrix.join_room_as(group[1], _mxid(v.id))
    except matrix.MatrixError:
        return _error("Chat isn't answering right now. Try again in a moment.", 502)
    group_store.accept(g.db, gid, v.id, _now())
    g.db.commit()
    return "", 204


@app.route("/api/chat/groups/<int:gid>/decline", methods=["POST"])
@auth_required
@validate_formkey
def chat_group_decline(gid, v):
    group, me = _membership(gid, v, joined_only=False)
    if me[2] != group_rules.INVITED:
        abort(404)
    try:
        _leave(group, v, _now())
    except matrix.MatrixError:
        g.db.rollback()
        return _error("Chat isn't answering right now. Try again in a moment.", 502)
    g.db.commit()
    return "", 204


@app.route("/api/chat/groups/<int:gid>/rename", methods=["POST"])
@is_not_banned
@validate_formkey
def chat_group_rename(gid, v):
    group, me = _membership(gid, v)
    if not group_rules.may_manage(me[1]):
        return _error("Only the group's owner and admins can rename it.", 403)
    try:
        name = group_rules.clean_name(request.values.get("name", ""))
    except group_rules.Refused as e:
        return _error(str(e))
    group_store.rename(g.db, gid, name, _name_severity(name))
    g.db.commit()
    return jsonify(group_json(group_store.group(g.db, gid), group_store.member(g.db, gid, v.id), v, full=True))


@app.route("/api/chat/groups/<int:gid>/role", methods=["POST"])
@auth_required
@validate_formkey
def chat_group_role(gid, v):
    group, me = _membership(gid, v)
    target = get_user(request.values.get("username", "").lstrip("@"), graceful=True)
    row = group_store.member(g.db, gid, target.id) if target else None
    role = request.values.get("role", "")
    refusal = group_rules.role_refusal(me[1], target.id if target else None, row[1] if row else None, role, v.id)
    if refusal:
        return _error(refusal, 403)
    group_store.set_role(g.db, gid, target.id, role)
    g.db.commit()
    return jsonify(group_json(group_store.group(g.db, gid), group_store.member(g.db, gid, v.id), v, full=True))


@app.route("/api/chat/groups/<int:gid>/mark_read", methods=["POST"])
@auth_required
@validate_formkey
def chat_group_mark_read(gid, v):
    _membership(gid, v)
    group_store.mark_read(g.db, gid, v.id, _now())
    g.db.commit()
    return "", 204


@app.route("/api/chat/people", methods=["GET"])
@auth_required
def chat_people(v):
    """Who can be added to a group, narrowed by ?q=: people you follow and the people you chat with. Never
    someone in a block with you, a bot, a deleted or suspended account, or a name your word filter hides."""
    q = (request.args.get("q") or "").strip().lstrip("@")[:40]
    blocked = _blocked_ids(v)
    partners = set()
    for a, b in g.db.query(ChatConversation.user_a_id, ChatConversation.user_b_id).filter(
            or_(ChatConversation.user_a_id == v.id, ChatConversation.user_b_id == v.id)).limit(200).all():
        partners.add(b if a == v.id else a)
    ids = (set(v.following_ids) | partners) - blocked - {v.id}
    if not ids:
        return jsonify({"people": []})
    people = g.db.query(User).filter(User.id.in_(ids), User.is_deleted == False, User.is_banned == 0,
                                     User.is_bot == False)
    if q:
        like = "%" + q.replace("\\", "").replace("%", "\\%").replace("_", "\\_") + "%"
        people = people.filter(or_(User.username.ilike(like), User.display_name.ilike(like)))
    people = filter_users(people, v).order_by(User.username.asc()).limit(40).all()
    return jsonify({"people": [_person(u) for u in people]})


def _person(u):
    return {"id": u.id, "username": u.username, "display_name": u.display_name or "", "profile_url": u.profile_url}


def group_json(group, me, v, full=False):
    """A group as the chat list and the group sheet see it, for this viewer. Its name stays out when the viewer's
    word filter hides it; the people the viewer blocked are marked so their own client hides their messages."""
    gid = group[0]
    rows = group_store.members(g.db, gid)
    ids = [m[0] for m in rows]
    users = {u.id: u for u in g.db.query(User).filter(User.id.in_(ids)).all()} if ids else {}
    mine = _blocked_by_me(v)
    people = []
    for m in rows:
        u = users.get(m[0])
        if u is None:
            continue
        deleted = bool(u.is_deleted)
        person = {
            "id": u.id,
            "mxid": matrix.mxid_for(u.id),
            "username": "" if deleted else u.username,
            "display_name": "" if deleted or user_hidden(u, v) else (u.display_name or ""),
            "shown_name": "Deleted account" if deleted else (u.username if user_hidden(u, v) else u.shown_name),
            "profile_url": "" if deleted else u.profile_url,
            "role": m[1],
            "joined": m[2] == group_rules.JOINED,
            "blocked": u.id in mine,
        }
        people.append(person)
    name_hidden = text_hidden(group[3], v)
    others = [p["shown_name"] for p in people if p["id"] != v.id and p["joined"]]
    inviter = users.get(me[3]) if me and me[3] else None
    out = {
        "kind": "group",
        "key": f"g{gid}",
        "group_id": gid,
        "room_id": group[1],
        "label": group_rules.label(group[2], name_hidden, others, len(others)),
        "name": "" if name_hidden else group[2],
        "my_role": me[1] if me else None,
        "status": me[2] if me else None,
        "unread_count": me[4] if me else 0,
        "last_activity_utc": group[6],
        "size": len(people),
        "invited_by": _person(inviter) if inviter and me[2] == group_rules.INVITED else None,
        "can_manage": bool(me) and group_rules.may_manage(me[1]),
        "max_members": group_rules.MAX_MEMBERS,
    }
    out["members"] = people if full else people[:4]
    return out
