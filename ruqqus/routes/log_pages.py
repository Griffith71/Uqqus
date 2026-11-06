"""The Log's pages (helpers/action_log.py has the rules and the promises).

`GET /log` is the PUBLIC log: what everyone may read, the same for a visitor, a member and an admin (an admin's other view is
`/admin/log`). Three tabs: Moderation (hides, removals, restores), Sanctions (exiles, chat mutes, suspensions, bans) and
Guild changes. For a hide or a removal it shows who (by role: an admin is the site), when, where, which rule and the written
reason; **never the title or the text** (the owner, 2026-10-31). A private or Circle guild is not named: its entries are the
guild's own (`audience` 1) and an open guild that went private is left out (`fetch(open_guilds_only=True)`).

`GET /log/<id>/copy` opens the private copy an entry kept of what it hid or removed, for the item's author, the guild's
guildmasters and admins only (`action_log.snapshot_visible`); anyone else gets the same 404 as for an entry that is not there.

`GET /admin/log` is the staff view: every entry an admin may read (level 3: public, guild and staff entries; level 4 also
the legal ones), filtered by type, action, rule, guild, who did it and who it was done to, newest first. The admin pages
stay in English. A guild's own log (`/+guild/mod/log`) reads the same table through `action_log.not_hidden_from`.

Not `log.py` / `action_log.py`: the star import of a routes package would put a module of that name over the helper."""
from flask import abort, g, redirect, render_template, request
from sqlalchemy import text
from sqlalchemy.orm import lazyload

from ruqqus.classes import Board, User
from ruqqus.classes.mod_logs import ACTIONTYPES
from ruqqus.helpers import action_log, log_view
from ruqqus.helpers.base36 import base36decode, base36encode
from ruqqus.helpers.get import get_comment, get_guild, get_post, get_user
from ruqqus.helpers.visibility import board_hidden, comment_hidden, hidden_notice, post_hidden, user_hidden
from ruqqus.helpers.wrappers import admin_level_required, auth_desired
from ruqqus.__main__ import app, limiter

PAGE = 50

app.jinja_env.globals.update(LOG_RULES=action_log.RULES, LOG_TYPES=action_log.TYPES)


def _noindex(html):
    """A Log page as a response nobody should index: the deleted-text rules to come make these pages a record, not content to
    be found by a search engine (the owner, 2026-11-05). `robots.txt` disallows them too, and neither stops a scraper."""
    response = app.make_response(html)
    response.headers["X-Robots-Tag"] = "noindex"
    return response


LOG_RATE = "60/minute"          # per address: the pages are a database read each and a scraper's target


def _names(db, table, column, ids):
    """{id: name} for these ids, one query."""
    ids = sorted({int(i) for i in ids if i})
    if not ids:
        return {}
    marks = {f"i{n}": i for n, i in enumerate(ids)}
    rows = db.execute(text(f"SELECT id, {column} FROM {table} WHERE id IN ({', '.join(':' + n for n in marks)})"), marks)
    return {r[0]: r[1] for r in rows}


def _id_of(lookup, name, **kw):
    """The id a typed name stands for, or None (a name that doesn't exist is 'nobody', not an error)."""
    name = (name or "").strip().lstrip("@+")
    if not name:
        return None
    found = lookup(name, graceful=True, **kw)
    return found.id if found is not None else -1          # -1 matches no row: an unknown name lists nothing


@app.route("/admin/log", methods=["GET"])
@admin_level_required(3)
def admin_log(v):
    level = action_log.staff_level(v.admin_level)
    args = request.args
    log_type = args.get("type") if args.get("type") in action_log.TYPES else None
    rule = action_log.parse_rule(args.get("rule"))
    raw_before = args.get("before") or ""
    before = int(raw_before) if raw_before.isascii() and raw_before.isdigit() and len(raw_before) < 16 else None
    rows, more = action_log.fetch(
        g.db, level=level, log_type=log_type, rule=rule or None, action=(args.get("action") or "")[:32] or None,
        board_id=_id_of(get_guild, args.get("guild")), actor_id=_id_of(get_user, args.get("actor")),
        target_user_id=_id_of(get_user, args.get("target")), before_id=before, limit=PAGE)

    users = _names(g.db, "users", "username", [r["actor_id"] for r in rows] + [r["target_user_id"] for r in rows])
    boards = _names(g.db, "boards", "name", [r["board_id"] for r in rows])
    return render_template("admin/log.html", v=v, entries=rows, more=more, users=users, boards=boards, level=level,
                           args={k: args.get(k, "") for k in ("type", "action", "rule", "guild", "actor", "target")},
                           next_before=rows[-1]["id"] if rows else None)


# --- the public Log -------------------------------------------------------------------------------------------------------------

def _people(ids):
    """{id: User} for these ids, one query and none of the relationships."""
    ids = sorted({int(i) for i in ids if i})
    if not ids:
        return {}
    return {u.id: u for u in g.db.query(User).options(lazyload("*")).filter(User.id.in_(ids)).all()}


def _guilds(ids):
    ids = sorted({int(i) for i in ids if i})
    if not ids:
        return {}
    return {b.id: b for b in g.db.query(Board).options(lazyload("*")).filter(Board.id.in_(ids)).all()}


def _rows(entries, v):
    """The public page's lines: the words, the rule, the reason and whether this reader may open a preserved copy. Entries
    whose guild or people the reader's own word filter hides are left out or drawn as 'a member'."""
    people = _people([e["actor_id"] for e in entries] + [e["target_user_id"] for e in entries])
    guilds = _guilds([e["board_id"] for e in entries])
    level = action_log.staff_level(v.admin_level if v else 0)
    copies = action_log.copies_for(g.db, [e["id"] for e in entries])
    mine = action_log.led_boards(v)
    site = app.config["SITE_NAME"]
    rows = []
    for e in entries:
        board = guilds.get(e["board_id"]) if e["board_id"] else None
        if board is not None and board_hidden(board, v):
            continue
        if board is not None and board.id in action_log.SITE_BOARDS:
            board = None                    # a profile post's board is the site's own, not a guild to name (L1)
        role = e["actor_role"]
        actor = people.get(e["actor_id"])
        if role != action_log.GUILDMASTER or e["actor_id"] is None:
            who = log_view.site_account(site)
        else:
            who = log_view.person(getattr(actor, "username", ""), deleted=actor is None or actor.is_deleted,
                                  hidden=user_hidden(actor, v))
        target = ""
        if e["target_user_id"]:
            # a person is the subject of an exile, a mute or a ban, even when an item is cited with it ("exiled user @x", not
            # "exiled #a1b2c"); the item is the subject only when there is no person
            user = people.get(e["target_user_id"])
            target = log_view.person(getattr(user, "username", ""), deleted=user is None or user.is_deleted,
                                     hidden=user_hidden(user, v))
        elif e["target_submission_id"]:
            target = log_view.item_link("post", base36encode(e["target_submission_id"]))
        elif e["target_comment_id"]:
            target = log_view.item_link("comment", base36encode(e["target_comment_id"]))
        copy = copies.get(e["id"])
        can_open = bool(copy and v is not None and not copy["scrubbed_utc"] and action_log.snapshot_visible(
            copy, viewer_id=v.id, level=level, guild_mod=copy["board_id"] in mine))
        kind = ACTIONTYPES.get(e["action"], {})
        rows.append({
            "id": e["id"], "created_utc": e["created_utc"], "guild": board.name if board is not None else "",
            "sentence": log_view.sentence(e["action"], ACTIONTYPES, who, target, board.name if board is not None else ""),
            "icon": kind.get("icon", "fa-gavel"), "color": kind.get("color", "bg-info"),
            "rule": e["rule_key"], "reason": e["reason"], "can_open": can_open, "type": e["log_type"]})      # not "copy": a dict has that method
    return rows


def _delete_rows(lines, v):
    """The lines for the times authors deleted their own posts and comments: who, when and which item, no words. Only the author's
    own page and the admins are ever given these (the routes below decide), so the author is named."""
    people = _people([l["actor_id"] for l in lines])
    rows = []
    for l in lines:
        actor = people.get(l["actor_id"])
        who = log_view.person(getattr(actor, "username", ""), deleted=actor is None or actor.is_deleted, hidden=user_hidden(actor, v))
        post = bool(l["target_submission_id"])
        target = (log_view.item_link("post", base36encode(l["target_submission_id"])) if post
                  else log_view.item_link("comment", base36encode(l["target_comment_id"])))
        kind = ACTIONTYPES["author_delete_post" if post else "author_delete_comment"]
        rows.append({"id": f"d{l['id']}", "created_utc": l["created_utc"], "guild": "", "type": "content", "rule": "", "reason": "",
                     "sentence": log_view.sentence("author_delete_post" if post else "author_delete_comment", ACTIONTYPES, who, target),
                     "icon": kind["icon"], "color": kind["color"], "can_open": False})
    return rows


ABOUT_MAX_PAGE = 40         # a page of these merges two lists, so how far back it reads is bounded: 1,000 lines


def _about_page(v, *, heading, back, scope, deletes, page):
    """One page of the log of an item or an account: the entries ABOUT it (the same public ones `/log` has, nothing more) and,
    when `deletes` is given (the author's own page, an admin's), the times its author deleted something."""
    page = min(page, ABOUT_MAX_PAGE)
    want = page * log_view.PER_PAGE
    entries, _ = action_log.fetch(g.db, level=0, scope=scope, limit=want)
    rows = _rows(entries, v)
    if deletes is not None:
        rows += _delete_rows(action_log.author_deletes(g.db, limit=want, **deletes), v)
    rows.sort(key=lambda r: (r["created_utc"], str(r["id"]).startswith("d"), int(str(r["id"]).lstrip("d"))), reverse=True)
    start = (page - 1) * log_view.PER_PAGE
    shown = rows[start:start + log_view.PER_PAGE]
    return _noindex(render_template("log_about.html", v=v, heading=heading, back=back, rows=shown, tab="content", page=page,
                                    more=len(rows) > start + log_view.PER_PAGE and page < ABOUT_MAX_PAGE))


def _may_see_deletes(v, author_id):
    """The author, and the admins: a self-delete is no decision and is not public (the owner, 2026-10-31)."""
    return bool(v and (v.admin_level >= 3 or (author_id is not None and v.id == author_id)))


@app.route("/post/<pid>/log", methods=["GET"])
@limiter.limit(LOG_RATE)
@auth_desired
def post_log(pid, v):
    """What was decided about a post, and about the guild copies of it: hides, removals, restores, hidden versions. A post named
    by id, never by title (a removed post's title is the author's text). Reachable by whoever may open the post's own page."""
    post = get_post(pid, v=v)
    if post.in_private_guild_for(v):
        abort(404)
    if post_hidden(post, v):
        return hidden_notice(v)["html"]()
    family = action_log.family_ids(g.db, post.id)
    deletes = ({"post_ids": family, "include_anonymous": True} if _may_see_deletes(v, post.author_id) else None)
    return _about_page(v, heading=("post", post.base36id), back=f"/post/{post.base36id}/history",
                       scope=action_log.about_scope(post_ids=family), deletes=deletes, page=log_view.parse_page(request.args.get("page")))


@app.route("/comment/<cid>/log", methods=["GET"])
@limiter.limit(LOG_RATE)
@auth_desired
def comment_log(cid, v):
    comment = get_comment(cid, v=v)
    if comment.post and comment.post.in_private_guild_for(v):
        abort(404)
    if comment_hidden(comment, v) or (comment.post and post_hidden(comment.post, v)):
        return hidden_notice(v)["html"]()
    deletes = ({"comment_ids": [comment.id], "include_anonymous": True} if _may_see_deletes(v, comment.author_id) else None)
    return _about_page(v, heading=("comment", comment.base36id), back=f"/comment/{comment.base36id}/history",
                       scope=action_log.about_scope(comment_ids=[comment.id]), deletes=deletes,
                       page=log_view.parse_page(request.args.get("page")))


@app.route("/@<username>/log", methods=["GET"])
@limiter.limit(LOG_RATE)
@auth_desired
def profile_log(username, v):
    """What was decided about an account and about what it wrote: suspensions and exiles, and every hide and removal of its posts
    and comments, **including a guild's copy of a post and a post that was never on its profile**. An anonymous post or comment is
    left out (it would say who wrote it) except for the admins. The profile's own refusals apply (gone, private, a block, a name the
    reader's filter hides: 404), so this never confirms more than the profile would."""
    from ruqqus.routes.profile_lists import _profile_or_404
    user = _profile_or_404(username, v)
    staff = bool(v and v.admin_level >= 3)
    deletes = {"actor_id": user.id, "include_anonymous": staff} if _may_see_deletes(v, user.id) else None
    return _about_page(v, heading=("user", user.username), back=user.permalink,
                       scope=action_log.about_scope(user_id=user.id, include_anonymous=staff), deletes=deletes,
                       page=log_view.parse_page(request.args.get("page")))


@app.route("/log", methods=["GET"])
@limiter.limit(LOG_RATE)
@auth_desired
def public_log(v):
    args = request.args
    tab = log_view.parse_tab(args.get("type"))
    rule = action_log.parse_rule(args.get("rule"))
    before = log_view.parse_cursor(args.get("before"))
    entries, more = action_log.fetch(
        g.db, level=0, log_type=tab, rule=rule or None, before_id=before, limit=log_view.PER_PAGE,
        board_id=_id_of(get_guild, args.get("guild")), actor_id=_id_of(get_user, args.get("by")),
        target_user_id=_id_of(get_user, args.get("to")))
    rows = _rows(entries, v)
    return _noindex(render_template(
        "log.html", v=v, tab=tab, rows=rows, more=more, next_before=entries[-1]["id"] if entries else None,
        tabs=log_view.TABS, tab_labels=log_view.TAB_LABELS, rule=rule,
        filters={k: (args.get(k) or "").strip().lstrip("@+")[:40] for k in ("guild", "by", "to")}))


@app.route("/log/<int:entry_id>/copy", methods=["GET"])
@auth_desired
def log_copy(entry_id, v):
    """The private copy an entry kept (what a guild hid, an admin removed or an author deleted): for its author, the guild's
    guildmasters and admins. Everyone else, and a copy that was never kept, get a 404, so the page confirms nothing."""
    if v is None:
        abort(404)
    level = action_log.staff_level(v.admin_level)
    entry, snapshot = action_log.entry_with_copy(g.db, entry_id, level, action_log.member_board_ids(g.db, v.id))
    if entry is None or snapshot is None:
        abort(404)
    allowed = action_log.snapshot_visible({**snapshot, "scrubbed_utc": 0}, viewer_id=v.id, level=level,
                                          guild_mod=snapshot["board_id"] in action_log.led_boards(v))
    if not allowed:
        abort(404)
    response = app.make_response(render_template(
        "log_copy.html", v=v, entry=entry, snapshot=snapshot, kept=not snapshot["scrubbed_utc"],
        media_free=log_view.without_media))
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Robots-Tag"] = "noindex"
    return response


@app.route("/log/deleted", methods=["GET"])
def deletion_log_retired():
    """The public deletion log is gone (the owner, 2026-10-31): what an author deletes is not published. Old links land on the Log."""
    return redirect("/log", code=301)


def _item_copy(v, post_id=None, comment_id=None):
    """The newest private copy of a post or comment, for whoever may open it; a 404 for everyone else, so the page says nothing
    about whether the item, or a copy of it, exists."""
    snapshot = action_log.snapshot_for(g.db, post_id=post_id, comment_id=comment_id, viewer=v)
    if v is None or not action_log.may_open_copy(snapshot, v):
        abort(404)
    response = app.make_response(render_template(
        "log_copy.html", v=v, entry=None, snapshot=snapshot, kept=True, media_free=log_view.without_media))
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Robots-Tag"] = "noindex"
    return response


@app.route("/post/<pid>/copy", methods=["GET"])
@auth_desired
def post_copy(pid, v):
    """The private copy kept of a post its author deleted, or that a guild's guildmasters hid: for the author, the guild's
    guildmasters and the admins, until the copy's 90 days are up."""
    try:
        post_id = base36decode(pid)
    except (ValueError, TypeError):
        abort(404)
    return _item_copy(v, post_id=post_id)


@app.route("/comment/<cid>/copy", methods=["GET"])
@auth_desired
def comment_copy(cid, v):
    try:
        comment_id = base36decode(cid)
    except (ValueError, TypeError):
        abort(404)
    return _item_copy(v, comment_id=comment_id)
