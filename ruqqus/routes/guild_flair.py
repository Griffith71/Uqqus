"""Guild flair: the guildmasters' list (`/+<guild>/mod/flair`), the flair on a guild's copy of a post, and the list the
Forward dialog reads (rules in helpers/guild_flair.py, SQL in helpers/guild_flair_store.py).

Making, renaming, recolouring, reordering and deleting flairs and requiring one is guild configuration (`config`
permission); setting the flair on a post is content (`content`), or the person who forwarded that copy. Every change
is a POST with the form key and is written to the guild's moderation log. Flair names are rated by the word filter;
a reader whose filter hides one does not see it.
"""
import html
import time

from flask import *

from ruqqus.helpers.wrappers import *
from ruqqus.helpers.get import *
from ruqqus.classes import *
from ruqqus.__main__ import app, cache

from ruqqus.helpers import guild_flair as flair_rules
from ruqqus.helpers import guild_flair_store as flair_store
from ruqqus.helpers.visibility import text_hidden


def _error(message, status=400):
    return jsonify({"error": message}), status


def _severity(name):
    from ruqqus.helpers.word_filter_store import get_filter
    return get_filter(g.db).severity(name)


def _log(board, v, kind, note="", post=None):
    g.db.add(ModAction(kind=kind, user_id=v.id, board_id=board.id, note=html.escape(note)[:256],     # the log draws it as HTML
                       target_submission_id=post.id if post is not None else 0))


def _forget_listing(board):
    cache.delete_memoized(Board.idlist, board)


def flair_json(row, v=None):
    return {"id": row[0], "name": row[2], "colour": row[3]} if not text_hidden(row[5], v) else None


@app.route("/+<guildname>/mod/flair", methods=["GET"])
@auth_required
@is_guildmaster("config")
def board_flair_settings(guildname, board, v):
    return render_template("guild/flair.html", v=v, b=board, flairs=flair_store.flairs_of(g.db, board.id),
                           colours=flair_rules.COLOURS, max_flairs=flair_rules.MAX_FLAIRS, name_max=flair_rules.NAME_MAX)


@app.route("/mod/<bid>/flair/add", methods=["POST"])
@auth_required
@is_guildmaster("config")
@validate_formkey
def mod_flair_add(bid, board, v):
    try:
        name = flair_rules.clean_name(request.values.get("name"))
        colour = flair_rules.clean_colour(request.values.get("colour"))
    except flair_rules.Refused as e:
        return _error(str(e))
    refusal = flair_rules.make_refusal(len(flair_store.ids_of(g.db, board.id)))
    if refusal:
        return _error(refusal, 409)
    new_id = flair_store.add(g.db, board.id, name, colour, _severity(name), int(time.time()))
    if new_id is None:
        return _error(f"+{board.name} has a flair called {name} already.", 409)
    _log(board, v, "flair_create", name)
    g.db.commit()
    return jsonify({"id": new_id, "name": name, "colour": colour})


@app.route("/mod/<bid>/flair/<int:fid>/edit", methods=["POST"])
@auth_required
@is_guildmaster("config")
@validate_formkey
def mod_flair_edit(bid, fid, board, v):
    if flair_store.flair(g.db, board.id, fid) is None:
        abort(404)
    try:
        name = flair_rules.clean_name(request.values.get("name"))
        colour = flair_rules.clean_colour(request.values.get("colour"))
    except flair_rules.Refused as e:
        return _error(str(e))
    if not flair_store.edit(g.db, board.id, fid, name, colour, _severity(name)):
        return _error(f"+{board.name} has a flair called {name} already.", 409)
    _log(board, v, "flair_edit", name)
    g.db.commit()
    return jsonify({"id": fid, "name": name, "colour": colour})


@app.route("/mod/<bid>/flair/<int:fid>/move", methods=["POST"])
@auth_required
@is_guildmaster("config")
@validate_formkey
def mod_flair_move(bid, fid, board, v):
    if flair_store.flair(g.db, board.id, fid) is None:
        abort(404)
    flair_store.move(g.db, board.id, fid, -1 if request.values.get("step") == "up" else 1)
    g.db.commit()
    return "", 204


@app.route("/mod/<bid>/flair/<int:fid>/delete", methods=["POST"])
@auth_required
@is_guildmaster("config")
@validate_formkey
def mod_flair_delete(bid, fid, board, v):
    row = flair_store.flair(g.db, board.id, fid)
    if row is None:
        abort(404)
    flair_store.delete(g.db, board.id, fid)
    _log(board, v, "flair_delete", row[2])
    g.db.commit()
    _forget_listing(board)
    return "", 204


@app.route("/mod/<bid>/flair/require", methods=["POST"])
@auth_required
@is_guildmaster("config")
@validate_formkey
def mod_flair_require(bid, board, v):
    board.require_flair = request.values.get("setting") == "true"
    g.db.add(board)
    _log(board, v, "flair_settings", "required" if board.require_flair else "optional")
    g.db.commit()
    return jsonify({"require_flair": board.require_flair})


@app.route("/api/guild/<guildname>/flairs", methods=["GET"])
@auth_desired
def guild_flairs_api(guildname, v):
    """A guild's flairs, for the Forward dialog and the composer: what a reader of the guild may see."""
    board = get_guild(guildname, graceful=True)
    if board is None or board.is_banned or not board.can_view(v):
        abort(404)
    rows = [flair_json(row, v) for row in flair_store.flairs_of(g.db, board.id)]
    return jsonify({"guild": board.name, "require_flair": bool(board.require_flair), "flairs": [f for f in rows if f]})


def _forwarder_id(post):
    if "_forwarder_id" in post.__dict__:                              # attached for the page (helpers/forward_view.py)
        by = post.__dict__["_forwarder_id"]
    else:
        rel = g.db.query(ForwardRelationship.forwarded_by_id).filter_by(forward_submission_id=post.id).first()
        by = rel[0] if rel else None
    return by if by is not None else (post.author_id if post.board and post.board.is_circle else None)


@app.route("/api/post/<pid>/flair", methods=["POST"])
@auth_required
@validate_formkey
def post_flair_set(pid, v):
    """The flair on a guild's copy of a post (`flair`: one of the guild's ids; empty clears it): whoever forwarded the
    copy (in a Circle guild, its author) or a guildmaster with content permission."""
    post = get_post(pid, v=v, graceful=True)
    if post is None or post.deleted_utc or post.is_banned or post.in_private_guild_for(v):
        abort(404)
    board = post.board
    if board is None or board.name == PROFILE_BOARD_NAME or not (post.repost_id or board.is_circle):
        return _error("Only a guild's copy of a post has a flair.", 409)
    mod = board.has_mod(v)
    is_content_gm = bool(mod) and (mod.perm_content or mod.perm_full) and not v.is_suspended
    if not flair_rules.may_set(v.id, _forwarder_id(post), is_content_gm):
        return _error("Only whoever forwarded this post and the guild's guildmasters can change its flair.", 403)
    raw = request.values.get("flair", "")
    flair_id = flair_rules.parse_id(raw) if raw else None
    if raw and flair_id is None:
        return _error("That isn't a flair.")
    refusal = flair_rules.forward_refusal(board.require_flair and not is_content_gm, flair_id,
                                          flair_store.ids_of(g.db, board.id), board.name)
    if refusal:
        return _error(refusal, 409)
    flair_store.set_post_flair(g.db, post.id, flair_id)
    if is_content_gm and v.id != _forwarder_id(post):
        _log(board, v, "set_post_flair", "", post=post)
    g.db.commit()
    _forget_listing(board)
    row = flair_store.flair(g.db, board.id, flair_id) if flair_id else None
    return jsonify({"flair": flair_json(row, v) if row else None})


def flair_of(p):
    """A post's flair for the templates (attached for a page by get_posts, looked up on first use otherwise)."""
    if "guild_flair" not in p.__dict__:
        flair_store.attach(g.db, [p])
    found = p.__dict__.get("guild_flair")
    if found is None:
        return None
    v = g.get("v")
    return None if text_hidden(found["severity"], v) else found


def may_set_flair(p, v):
    """Whether the menu offers "Set flair" on this post to `v` (worked out once per card: the menu and the phone sheet
    both ask)."""
    key = f"_may_set_flair_{v.id if v else 0}"
    if key not in p.__dict__:
        p.__dict__[key] = _may_set_flair(p, v)
    return p.__dict__[key]


def _may_set_flair(p, v):
    if v is None or not getattr(p, "board", None) or p.board.name == PROFILE_BOARD_NAME:
        return False
    if not (p.repost_id or p.board.is_circle) or p.deleted_utc or p.is_banned:
        return False
    mod = p.board.has_mod(v)
    is_content_gm = bool(mod) and (mod.perm_content or mod.perm_full)
    return flair_rules.may_set(v.id, _forwarder_id(p), is_content_gm)


def flairs_for_guild(board, v):
    """A guild's flairs as a reader may see them ({"id", "name", "colour"}): the Circle-guild composer's picker."""
    if board is None:
        return []
    return [f for f in (flair_json(row, v) for row in flair_store.flairs_of(g.db, board.id)) if f]


app.jinja_env.globals.update(flair_of=flair_of, may_set_flair=may_set_flair, flairs_for_guild=flairs_for_guild,
                             flair_colours=flair_rules.COLOURS)
