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
import html

from flask import abort, g, redirect, render_template, request
from markupsafe import Markup
from sqlalchemy import or_, text
from sqlalchemy.orm import lazyload

from ruqqus.classes import Board, Comment, Submission, User
from ruqqus.classes.mod_logs import ACTIONTYPES
from ruqqus.helpers import action_log, deletion, deletion_store, log_view, sensitive as sensitive_rules
from ruqqus.helpers import sanitize as sanitize_rules
from ruqqus.helpers import anonymity
from ruqqus.helpers.base36 import base36decode, base36encode
from ruqqus.helpers.get import get_comment, get_guild, get_post, get_user
from ruqqus.helpers.i18n_runtime import tr
from ruqqus.helpers.visibility import board_hidden, comment_hidden, hidden_notice, post_hidden, user_hidden, viewer_level
from ruqqus.helpers.wordfilter import is_hidden
from ruqqus.helpers.wrappers import admin_level_required, auth_desired, validate_formkey
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
                           next_before=rows[-1]["id"] if rows else None,
                           failures=action_log.failure_count(g.db), unmirrored=action_log.unmirrored_count(g.db),
                           msg=args.get("msg"))


@app.route("/admin/log/<int:entry_id>/redact", methods=["POST"])
@admin_level_required(4)
@validate_formkey
def admin_log_redact(entry_id, v):
    """Take an entry's reason out of the public's sight (level 4): the entry stays, its reason is read by staff only, and a new staff
    entry linked to it says who did this and why (a correction is a new entry; nothing is ever edited)."""
    why = action_log.clean_reason(request.form.get("why"), 300)
    if not why:
        return redirect("/admin/log?msg=Say+why+the+reason+is+being+hidden.", code=303)
    if g.db.execute(text("SELECT 1 FROM log_entries WHERE id = :i AND redacted_utc = 0"), {"i": entry_id}).first() is None:
        abort(404)
    action_log.correct(g.db, entry_id, v, why)
    g.db.commit()
    return redirect(f"/admin/log?msg=Entry+{entry_id}+is+hidden+from+the+public.", code=303)


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
    words = viewer_level(v)                 # the reader's own word filter: a reason is a person's words (the audit's M8)
    shows_sensitive = sensitive_rules.shows_sensitive(v)
    texts = action_log.deleted_texts(g.db, [e["id"] for e in entries if e["log_type"] == action_log.DELETION])
    post_languages, comment_languages = deletion_store.languages(
        g.db, [t["post_id"] for t in texts.values()], [t["comment_id"] for t in texts.values()])
    rows = []
    for e in entries:
        board = guilds.get(e["board_id"]) if e["board_id"] else None
        if board is not None and board_hidden(board, v):
            continue
        board_row = board
        if board is not None and board.id in action_log.SITE_BOARDS:
            board = None                    # a profile post's board is the site's own, not a guild to name (L1)
        role = e["actor_role"]
        actor = people.get(e["actor_id"])
        if role == action_log.AUTHOR:
            who = log_view.anonymous_actor() if e["log_type"] == action_log.DELETION else log_view.post_author()
        elif role != action_log.GUILDMASTER or e["actor_id"] is None:
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
            "rule": e["rule_key"], "reason": "" if is_hidden(words, e.get("reason_severity")) else e["reason"],
            "can_open": can_open, "type": e["log_type"],                                                          # not "copy": a dict has that method
            "until": e.get("until_utc") or 0})
        if e["log_type"] == action_log.DELETION:
            rows[-1]["deleted"] = _deleted_words(texts.get(e["id"]), board_row, words, shows_sensitive, post_languages, comment_languages)
        # the Report button is on an entry that shows a person's words (a deleted item's, or a reason): a signed-in member asks the admins to take
        # them down (`log_reports`, helpers/log_reports.py)
        rows[-1]["can_report"] = bool(v is not None and (rows[-1].get("deleted") or rows[-1]["reason"]))
    return rows


def _deleted_words(copy, board, level, shows_sensitive, post_languages, comment_languages):
    """What a reader is shown of a deleted item's words (None: the line alone): its title, its link as plain text and its text with every link and
    picture taken out, under the reader's word filter, the sensitive rule and the guild's state now (`helpers/deletion.py text_view`)."""
    view = deletion.text_view(copy, board_banned=bool(board is not None and board.is_banned), level=level, shows_sensitive=shows_sensitive)
    if view == deletion.LINE_ONLY:
        return None
    html_ = log_view.without_links(log_view.without_media(copy["body_html"]))
    return {"view": view, "title": html.unescape(copy["title"] or ""), "url": copy["url"] or "",
            "body": Markup(sanitize_rules._clean_wo_links.clean(html_)),
            "lang": post_languages.get(copy["post_id"]) or comment_languages.get(copy["comment_id"]) or ""}


ABOUT_MAX_PAGE = 40         # a page of these merges two lists, so how far back it reads is bounded: 1,000 lines


def _about_page(v, *, heading, back, scope, page):
    """One page of the log of an item or an account: the entries ABOUT it (the same public ones `/log` has, nothing more). An author's own delete
    is one of them since slice D3a (as Anonymous, by the Deleted tab's rules): it is not a separate list for its author and the admins any more."""
    page = min(page, ABOUT_MAX_PAGE)
    want = page * log_view.PER_PAGE
    entries, more = action_log.fetch(g.db, level=0, scope=scope, limit=want)
    rows = _rows(entries, v)
    start = (page - 1) * log_view.PER_PAGE
    shown = rows[start:start + log_view.PER_PAGE]
    return _noindex(render_template("log_about.html", v=v, heading=heading, back=back, rows=shown, tab="content", page=page,
                                    more=len(rows) > start + log_view.PER_PAGE and page < ABOUT_MAX_PAGE))


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
    return _about_page(v, heading=("post", post.base36id), back=f"/post/{post.base36id}/history",
                       scope=action_log.about_scope(post_ids=family), page=log_view.parse_page(request.args.get("page")))


@app.route("/comment/<cid>/log", methods=["GET"])
@limiter.limit(LOG_RATE)
@auth_desired
def comment_log(cid, v):
    comment = get_comment(cid, v=v)
    if comment.post and comment.post.in_private_guild_for(v):
        abort(404)
    if comment_hidden(comment, v) or (comment.post and post_hidden(comment.post, v)):
        return hidden_notice(v)["html"]()
    return _about_page(v, heading=("comment", comment.base36id), back=f"/comment/{comment.base36id}/history",
                       scope=action_log.about_scope(comment_ids=[comment.id]), page=log_view.parse_page(request.args.get("page")))


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
    return _about_page(v, heading=("user", user.username), back=user.permalink,
                       scope=action_log.about_scope(user_id=user.id, include_anonymous=staff),
                       page=log_view.parse_page(request.args.get("page")))


def deleted_post_context(post, v):
    """What a comment's context line may say of its post when the post was deleted (the owner's decision 25): the title and a link to the Log's thread
    only under the Deleted tab's own rules for this reader; a post erased inside the grace window, a Circle post's or one the reader may not read says
    "a deleted post", with no title and no link. None when the post is not deleted (the line is as it always was)."""
    if post is None or not (post.deleted_utc or 0) > 0:
        return None
    memo = g.__dict__.setdefault("_deleted_context", {})
    key = (post.id, v.id if v is not None else 0)
    if key in memo:
        return memo[key]
    result = {"title": None, "link": None}
    entry_id = deletion_store.post_entry(g.db, [post.id, post.repost_id or 0])
    if entry_id is not None:
        level = action_log.staff_level(v.admin_level if v else 0)
        member = action_log.member_board_ids(g.db, v.id) if v else []
        entries, _ = action_log.fetch(g.db, level=level, member_boards=member, scope=("id = :one", {"one": int(entry_id)}), limit=1)
        if entries:
            result["link"] = f"/log/post/{base36encode(post.id)}"
            copy = action_log.deleted_texts(g.db, [entry_id]).get(entry_id)
            view = deletion.text_view(copy, board_banned=bool(post.board.is_banned), level=viewer_level(v),
                                      shows_sensitive=sensitive_rules.shows_sensitive(v))
            if view != deletion.LINE_ONLY:
                result["title"] = html.unescape(copy["title"] or "")
    memo[key] = result
    return result


def may_unlink_post(post):
    """Offer "Remove my name" in a post's delete dialog? Only where the route will do it (`deletion.unlink_refusal`): not a Circle post, a tipped one, one
    written with others, a Space, a bot's, or one that is already anonymous. The cheap checks only (the route asks the database too)."""
    from ruqqus.helpers import coauthors, collab_view, comment_permission as cperm
    return not (post.is_anonymous or post.audience or getattr(post.board, "is_circle", False) or (post.tip_coins or 0) > 0 or post.is_space
                or cperm.is_space(post) or post.is_bot or collab_view.is_collab(post) or coauthors.accepted_of(post, None))


def may_unlink_comment(comment):
    """The same for a comment: not on a Circle post, not on a Space, not a bot's, not already anonymous (a tip is checked by the route)."""
    post = comment.post
    return not (comment.is_anonymous or comment.is_bot or post is None or post.audience or getattr(post.board, "is_circle", False) or post.is_space)


app.jinja_env.globals.update(deleted_post_context=deleted_post_context, may_unlink_post=may_unlink_post, may_unlink_comment=may_unlink_comment)


THREAD_PER_PAGE = 25            # top-level comments a page of a deleted post's thread
THREAD_MAX = 2000               # comments read for one page: a thread is a record, not a feed


def _comment_line(kind):
    """The line a comment is drawn as when its words are not shown: every one is a plain sentence, never its text or its author."""
    return {"removed": tr("A comment removed by the site's admins."),
            "hidden": tr("A comment hidden by a guildmaster."),
            "filtered": tr("A comment hidden by your word filter."),
            "blocked": tr("A comment by an account you blocked or that blocked you."),
            "deleted": tr("A comment its author deleted. What it said isn't shown here.")}[kind]


@app.route("/log/post/<pid>", methods=["GET"])
@limiter.limit(LOG_RATE)
@auth_desired
def log_thread(pid, v):
    """What a deleted post said and what was said under it (the owner's decision 25): the post as Anonymous, by the Deleted tab's rules, and its
    comments as a read-only tree. Open only where the post's deletion is in the Log for this reader (a delete inside the grace window leaves no
    entry, and a Circle post's or a private guild's is for its audience only): the same 404 as for a post that never existed. No vote, comment, tip or
    share control and no ad: it is a record."""
    post = get_post(pid, v=v, graceful=True)
    if post is None:
        abort(404)
    if not (post.deleted_utc or 0) > 0:
        return redirect(post.permalink)                     # not deleted: its own page is the page
    primary_id = post.repost_id or post.id
    entry_id = deletion_store.post_entry(g.db, [post.id, primary_id])
    if entry_id is None:
        abort(404)
    level = action_log.staff_level(v.admin_level if v else 0)
    member = action_log.member_board_ids(g.db, v.id) if v else []
    entries, _ = action_log.fetch(g.db, level=level, member_boards=member, scope=("id = :one", {"one": int(entry_id)}), limit=1)
    if not entries:
        abort(404)
    head = _rows(entries, v)
    if not head:
        abort(404)

    # the deleted copies the thread has a tab for: this post's own, and the guilds it was forwarded to that are open to the reader
    copies = g.db.query(Submission).filter(or_(Submission.id == primary_id, Submission.repost_id == primary_id),
                                           Submission.deleted_utc > 0).all()
    tabs = [{"id": base36encode(c.id), "name": None if c.board_id in action_log.SITE_BOARDS else c.board.name, "current": c.id == post.id}
            for c in copies if not board_hidden(c.board, v) and not c.board.is_private and not c.board.is_banned]
    tabs = tabs if len(tabs) > 1 else []

    page = log_view.parse_page(request.args.get("page"))
    rows = g.db.query(Comment).options(lazyload("*")).filter(Comment.parent_submission == post.id).order_by(Comment.created_utc.asc(), Comment.id.asc()) \
        .limit(THREAD_MAX).all()
    children, top = {}, []
    for c in rows:
        if c.parent_comment_id:
            children.setdefault(c.parent_comment_id, []).append(c)
        else:
            top.append(c)
    shown_top = top[(page - 1) * THREAD_PER_PAGE:page * THREAD_PER_PAGE]
    in_page = []

    def walk(c, depth):
        in_page.append(c)
        return {"c": c, "depth": depth, "children": [walk(k, depth + 1) for k in children.get(c.id, ())] if depth < 8 else []}

    tree = [walk(c, 0) for c in shown_top]
    people = _people([c.author_id for c in in_page])
    deleted_ids = [c.id for c in in_page if (c.deleted_utc or 0) > 0]
    entry_of = deletion_store.comment_entries(g.db, deleted_ids)
    visible_entries, _ = action_log.fetch(g.db, level=level, member_boards=member, limit=len(entry_of) or 1,
                                          scope=(f"id IN ({', '.join(str(int(i)) for i in entry_of.values()) or '0'})", {}))
    visible_ids = {e["id"] for e in visible_entries}
    texts = action_log.deleted_texts(g.db, list(visible_ids))
    words, shows = viewer_level(v), sensitive_rules.shows_sensitive(v)
    post_languages, comment_languages = deletion_store.languages(g.db, (), [t["comment_id"] for t in texts.values()])

    def draw(node):
        c = node["c"]
        out = {"id": base36encode(c.id), "depth": node["depth"], "created_utc": c.created_utc, "kind": "live", "line": "", "who": "", "words": None,
               "children": [draw(k) for k in node["children"]]}
        if c.purged_utc or c.is_banned:
            out["kind"], out["line"] = "line", _comment_line("removed")
        elif c.hidden_by_guild or c.author_hidden:
            out["kind"], out["line"] = "line", _comment_line("hidden")
        elif (c.deleted_utc or 0) > 0:
            entry = entry_of.get(c.id)
            copy = texts.get(entry) if entry in visible_ids else None
            board = c.post.board if c.post else None
            shown = _deleted_words(copy, board, words, shows, post_languages, comment_languages) if copy else None
            out["kind"], out["who"] = ("deleted" if shown else "line"), log_view.anonymous_actor()
            out["words"] = shown
            out["line"] = "" if shown else _comment_line("deleted")
        elif comment_hidden(c, v):
            out["kind"], out["line"] = "line", _comment_line("filtered")
        elif v is not None and not c.is_anonymous and v.any_block_exists(people.get(c.author_id) or c.author):
            out["kind"], out["line"] = "line", _comment_line("blocked")
        else:
            author = anonymity.author_of(c, v)
            out["who"] = (log_view.anonymous_actor() if c.is_anonymous and author is anonymity.ANONYMOUS else
                          log_view.person(author.username, deleted=bool(author.is_deleted), hidden=user_hidden(author, v)))
            out["words"] = {"view": "text", "title": "", "url": "", "body": Markup(c.body_html or ""), "lang": c.language_code or ""}
        return out

    return _noindex(render_template("log_thread.html", v=v, head=head, tree=[draw(n) for n in tree], tabs=tabs, page=page,
                                    more=len(top) > page * THREAD_PER_PAGE, post_id=post.base36id,
                                    total=len(top)))


@app.route("/log", methods=["GET"])
@limiter.limit(LOG_RATE)
@auth_desired
def public_log(v):
    args = request.args
    tab = log_view.parse_tab(args.get("type"))
    rule = action_log.parse_rule(args.get("rule"))
    before = log_view.parse_cursor(args.get("before"))
    what = log_view.parse_what(args.get("what")) if tab == log_view.DELETED else None
    # the Deleted tab has no "by" and no "to": every delete is Anonymous, so filtering by a person would list exactly one person's anonymous deletes (M11)
    by = None if tab == log_view.DELETED else _id_of(get_user, args.get("by"))
    to = None if tab == log_view.DELETED else _id_of(get_user, args.get("to"))
    entries, more = action_log.fetch(
        g.db, level=0, log_type=tab, rule=rule or None, before_id=before, limit=log_view.PER_PAGE, action=log_view.WHAT.get(what),
        board_id=_id_of(get_guild, args.get("guild")), actor_id=by, target_user_id=to)
    rows = _rows(entries, v)
    return _noindex(render_template(
        "log.html", v=v, tab=tab, rows=rows, more=more, next_before=entries[-1]["id"] if entries else None,
        tabs=log_view.TABS, tab_labels=log_view.TAB_LABELS, rule=rule, what=what,
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
    """The old deletion log's address: what authors delete is the Log's Deleted tab now (the owner, 2026-11-05)."""
    return redirect("/log?type=deletion", code=301)


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
