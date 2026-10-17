"""Guild roles: the guildmasters' page (`/+<guild>/mod/roles`) where they make, change, order and delete a guild's roles
and give them to members (rules in helpers/guild_roles.py, SQL in helpers/guild_role_store.py). What a role does in a
channel is set on the channel (routes/guild_channels.py: its edit form), and read where the channel is read.

Everything here is guild configuration (`config`), a POST with the form key and an entry in the guild's moderation log.
A role is only given to someone in the guild (joined, approved or a guildmaster), not exiled, not a bot; leaving the guild
or being exiled takes a person's roles (helpers/guild_channel_access.py).
"""
import html
import time

from flask import *

from ruqqus.helpers.wrappers import *
from ruqqus.helpers.get import *
from ruqqus.classes import *
from ruqqus.__main__ import app

from ruqqus.helpers import guild_roles as rules
from ruqqus.helpers import guild_role_store as store
from ruqqus.helpers import guild_channel_store as channel_store


def _now():
    return int(time.time())


def _severity(text):
    from ruqqus.helpers.word_filter_store import get_filter
    return get_filter(g.db).severity(text)


def _log(board, v, kind, note="", target_user_id=0):
    g.db.add(ModAction(kind=kind, user_id=v.id, board_id=board.id, note=html.escape(note)[:256], target_user_id=target_user_id))


def _page(board, v, error=None, notice=None, status=200):
    roles = store.roles_of(g.db, board.id)
    ids = [r.id for r in roles]
    swaps = {}
    for i, role_id in enumerate(ids):
        up, down = list(ids), list(ids)
        if i > 0:
            up[i - 1], up[i] = up[i], up[i - 1]
        if i < len(ids) - 1:
            down[i + 1], down[i] = down[i], down[i + 1]
        swaps[role_id] = {"up": ",".join(map(str, up)), "down": ",".join(map(str, down))}
    people = {}
    for role in roles:
        member_ids = store.members_of(g.db, role.id, rules.SHOWN_MEMBERS)
        users = {u.id: u for u in g.db.query(User).filter(User.id.in_(member_ids)).all()} if member_ids else {}
        people[role.id] = {"users": [users[i] for i in member_ids if i in users], "count": store.member_count(g.db, role.id)}
    return render_template("guild/roles.html", v=v, b=board, roles=roles, swaps=swaps, people=people, colours=rules.COLOURS,
                           perms=rules.PERMS, perm_labels=rules.PERM_LABELS, max_roles=rules.MAX_ROLES, name_max=rules.NAME_MAX,
                           shown=rules.SHOWN_MEMBERS, error=error, notice=notice), status


def _role_or_404(board, role_id):
    found = store.role(g.db, board.id, role_id)
    if found is None:
        abort(404)
    return found


@app.route("/+<guildname>/mod/roles", methods=["GET"])
@auth_required
@is_guildmaster("config")
def guild_roles_page(guildname, board, v):
    return _page(board, v)[0]


@app.route("/+<guildname>/mod/roles/new", methods=["POST"])
@auth_required
@is_guildmaster("config")
@validate_formkey
def guild_role_create(guildname, board, v):
    try:
        name = rules.clean_name(request.values.get("name"))
        colour = rules.clean_colour(request.values.get("colour"))
        perms = rules.clean_perms(lambda key: request.values.get(key) in ("1", "on", "true"))
    except rules.Refused as e:
        return _page(board, v, error=str(e), status=400)
    refusal = rules.make_refusal(store.role_count(g.db, board.id), store.name_taken(g.db, board.id, name))
    if refusal:
        return _page(board, v, error=refusal, status=409)
    store.make_role(g.db, board.id, name, _severity(name), colour, perms)
    _log(board, v, "role_create", name)
    g.db.commit()
    return _page(board, v, notice=f"The role {name} was made.")[0]


@app.route("/+<guildname>/mod/roles/<int:role_id>/edit", methods=["POST"])
@auth_required
@is_guildmaster("config")
@validate_formkey
def guild_role_edit(guildname, role_id, board, v):
    _role_or_404(board, role_id)
    try:
        name = rules.clean_name(request.values.get("name"))
        colour = rules.clean_colour(request.values.get("colour"))
        perms = rules.clean_perms(lambda key: request.values.get(key) in ("1", "on", "true"))
    except rules.Refused as e:
        return _page(board, v, error=str(e), status=400)
    refusal = rules.rename_refusal(store.name_taken(g.db, board.id, name, except_id=role_id))
    if refusal:
        return _page(board, v, error=refusal, status=409)
    store.update_role(g.db, board.id, role_id, name, _severity(name), colour, perms)
    _log(board, v, "role_edit", name)
    g.db.commit()
    return _page(board, v, notice=f"The role {name} was saved.")[0]


@app.route("/+<guildname>/mod/roles/<int:role_id>/delete", methods=["POST"])
@auth_required
@is_guildmaster("config")
@validate_formkey
def guild_role_delete(guildname, role_id, board, v):
    found = _role_or_404(board, role_id)
    store.delete_role(g.db, board.id, role_id)
    _log(board, v, "role_delete", found.name)
    g.db.commit()
    return _page(board, v, notice=f"The role {found.name} was deleted.")[0]


@app.route("/+<guildname>/mod/roles/order", methods=["POST"])
@auth_required
@is_guildmaster("config")
@validate_formkey
def guild_role_order(guildname, board, v):
    ids = []
    for part in (request.values.get("ids") or "").split(","):
        part = part.strip()
        if part.isascii() and part.isdigit() and int(part) not in ids:
            ids.append(int(part))
    store.move_roles(g.db, board.id, ids)
    g.db.commit()
    return redirect(f"/+{board.name}/mod/roles")


def _person(board):
    name = (request.values.get("username") or "").strip().lstrip("@")
    return get_user(name, graceful=True) if name else None


@app.route("/+<guildname>/mod/roles/<int:role_id>/add", methods=["POST"])
@auth_required
@is_guildmaster("config")
@validate_formkey
def guild_role_add(guildname, role_id, board, v):
    found = _role_or_404(board, role_id)
    target = _person(board)
    if target is None:
        return _page(board, v, error="There's no account with that name.", status=404)
    joined = channel_store.standing_sets(g.db, board.id, [target.id])
    in_guild = bool(joined["joined"] or joined["contributor"] or joined["mod"])
    refusal = rules.member_refusal(
        in_guild=in_guild, exiled=bool(joined["exiled"]) and target.admin_level < 2, bot=bool(target.is_bot),
        gone=bool(target.is_deleted or target.is_banned), count=store.member_count(g.db, role_id),
        already=store.has_role(g.db, role_id, target.id))
    if refusal:
        return _page(board, v, error=f"@{target.username}: {refusal}", status=409)
    store.add_member(g.db, role_id, target.id, v.id, _now())
    _log(board, v, "role_assign", found.name, target_user_id=target.id)
    g.db.commit()
    return _page(board, v, notice=f"@{target.username} now has the role {found.name}.")[0]


@app.route("/+<guildname>/mod/roles/<int:role_id>/remove", methods=["POST"])
@auth_required
@is_guildmaster("config")
@validate_formkey
def guild_role_remove(guildname, role_id, board, v):
    found = _role_or_404(board, role_id)
    target = _person(board)
    if target is None or not store.remove_member(g.db, role_id, target.id):
        return _page(board, v, error="They don't have this role.", status=404)
    _log(board, v, "role_unassign", found.name, target_user_id=target.id)
    g.db.commit()
    return _page(board, v, notice=f"@{target.username} no longer has the role {found.name}.")[0]
