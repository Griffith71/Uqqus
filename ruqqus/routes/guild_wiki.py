"""Guild wikis and pinned resources (rules in helpers/guild_wiki.py, SQL in helpers/guild_wiki_store.py).

Every page is read through the guild's own visibility (`visible_guild`: `Board.can_view`, `board_hidden`, a banned guild is a
404, a wiki that is off is a 404 for anyone but its guildmasters). Who edits a page is `guild_wiki.edit_refusal`;
making pages, restoring, page settings, deleting and the resources are for guildmasters (content permission for the
wiki, config for switching it on and for the resources). Every change is a POST with the form key and is written to
the guild's moderation log.
"""
import html
import time

import mistletoe
from flask import *

from ruqqus.helpers.wrappers import *
from ruqqus.helpers.get import *
from ruqqus.helpers.markdown import CustomRenderer, preprocess
from ruqqus.helpers.sanitize import sanitize
from ruqqus.classes import *
from ruqqus.__main__ import app

from ruqqus.helpers import guild_wiki as wiki_rules
from ruqqus.helpers import guild_wiki_store as wiki_store
from ruqqus.helpers.visibility import board_hidden, text_hidden

_DAY = 86400


def _error(message, status=400):
    return jsonify({"error": message}), status


def _render(body):
    with CustomRenderer() as renderer:
        body_md = renderer.render(mistletoe.Document(preprocess(body)))
    return sanitize(body_md, linkgen=True)


def _severity(body):
    from ruqqus.helpers.word_filter_store import get_filter
    return get_filter(g.db).severity(body)


def _log(board, v, kind, note=""):
    g.db.add(ModAction(kind=kind, user_id=v.id, board_id=board.id, note=html.escape(note)[:256]))   # drawn as HTML


def _content_gm(board, v):
    mod = board.has_mod(v) if v else None
    return bool(mod) and (mod.perm_content or mod.perm_full) and not v.is_suspended


def visible_guild(guildname, v, need_wiki=True):
    """The guild when `v` may see it (and its wiki is on, or `v` runs it), else a 404."""
    board = get_guild(guildname, v=v, graceful=True)
    if board is None or (board.is_banned and not (v and v.admin_level >= 3)) or not board.can_view(v):
        abort(404)
    if board_hidden(board, v) and not (v and board.has_mod(v)):
        abort(404)
    if need_wiki and not board.wiki_enabled and not board.has_mod(v):
        abort(404)
    return board


def _page_or_404(board, slug):
    try:
        slug = wiki_rules.clean_slug(slug)
    except wiki_rules.Refused:
        abort(404)
    return slug, wiki_store.page(g.db, board.id, slug)


def _member_days(board, v):
    sub = g.db.query(Subscription).filter_by(user_id=v.id, board_id=board.id, is_active=True).first()
    return None if sub is None else (int(time.time()) - (sub.created_utc or 0)) // _DAY


def edit_refusal_for(board, page, v):
    """Why `v` can't edit this page now (None: they can). `page` None: making a new page (guildmasters only)."""
    if v is None:
        return "Sign in to edit the wiki."
    gm = _content_gm(board, v)
    if page is None:
        if not gm:
            return "Only the guild's guildmasters can make new pages."
        return wiki_rules.page_refusal(wiki_store.page_count(g.db, board.id))
    founder = g.db.query(User).filter_by(id=board.creator_id).first()
    return wiki_rules.edit_refusal(
        level=page[4], locked=bool(page[7]), is_content_gm=gm, is_contributor=bool(board.has_contributor(v)),
        member_days=_member_days(board, v), exiled=bool(board.has_ban(v)), suspended=bool(v.is_suspended),
        bot=bool(v.is_bot), blocked_with_founder=bool(founder and founder.id != v.id and v.any_block_exists(founder)),
        edits_today=wiki_store.edits_since(g.db, board.id, v.id, int(time.time()) - _DAY))


def _shown(rev, v):
    """A revision's HTML as this reader may see it: None and a notice when an admin took it down or their word filter
    hides it."""
    if rev is None:
        return None, None
    if rev[8] and not (v and v.admin_level >= 3):
        return None, "This revision was removed by the site's admins."
    if text_hidden(rev[5], v, owner_id=rev[2]):
        return None, "Hidden by your word filter."
    return rev[4], None


def _editor_names(rows):
    ids = {row[2] for row in rows}
    users = {u.id: u for u in g.db.query(User).filter(User.id.in_(ids)).all()} if ids else {}
    return {uid: ("Deleted account" if users.get(uid) is None or users[uid].is_deleted else users[uid].username)
            for uid in ids}


# --- reading ---------------------------------------------------------------------------------------------------------

@app.route("/+<guildname>/wiki", methods=["GET"])
@auth_desired
def guild_wiki_index(guildname, v):
    return _show_page(visible_guild(guildname, v), wiki_rules.INDEX, v)


@app.route("/+<guildname>/wiki/pages", methods=["GET"])
@auth_desired
def guild_wiki_pages(guildname, v):
    board = visible_guild(guildname, v)
    return render_template("guild/wiki.html", v=v, b=board, view="pages", pages=wiki_store.pages_of(g.db, board.id),
                           may_create=edit_refusal_for(board, None, v) is None, gm=_content_gm(board, v))


@app.route("/+<guildname>/wiki/<slug>", methods=["GET"])
@auth_desired
def guild_wiki_page(guildname, slug, v):
    return _show_page(visible_guild(guildname, v), slug, v)


def _show_page(board, slug, v):
    slug, page = _page_or_404(board, slug)
    if page is None:
        if edit_refusal_for(board, None, v) is None:
            return redirect(f"/+{board.name}/wiki/{slug}/edit")
        if slug == wiki_rules.INDEX:
            return render_template("guild/wiki.html", v=v, b=board, view="empty", pages=wiki_store.pages_of(g.db, board.id),
                                   may_create=False, gm=False)
        abort(404)
    rev = wiki_store.revision(g.db, page[0], page[5])
    body_html, notice = _shown(rev, v)
    return render_template("guild/wiki.html", v=v, b=board, view="page", page=page, rev=rev, body_html=body_html,
                           notice=notice, pages=wiki_store.pages_of(g.db, board.id), editor=_editor_names([rev]) if rev else {},
                           may_edit=edit_refusal_for(board, page, v) is None, gm=_content_gm(board, v),
                           levels=wiki_rules.LEVEL_LABELS)


@app.route("/+<guildname>/wiki/<slug>/history", methods=["GET"])
@auth_desired
def guild_wiki_history(guildname, slug, v):
    board = visible_guild(guildname, v)
    slug, page = _page_or_404(board, slug)
    if page is None:
        abort(404)
    rows = wiki_store.revisions(g.db, page[0])
    return render_template("guild/wiki_history.html", v=v, b=board, page=page, revisions=rows, editors=_editor_names(rows),
                           gm=_content_gm(board, v))


@app.route("/+<guildname>/wiki/<slug>/revision/<int:rid>", methods=["GET"])
@auth_desired
def guild_wiki_revision(guildname, slug, rid, v):
    board = visible_guild(guildname, v)
    slug, page = _page_or_404(board, slug)
    rev = wiki_store.revision(g.db, page[0], rid) if page else None
    if rev is None:
        abort(404)
    body_html, notice = _shown(rev, v)
    return render_template("guild/wiki.html", v=v, b=board, view="revision", page=page, rev=rev, body_html=body_html,
                           notice=notice, pages=wiki_store.pages_of(g.db, board.id), editor=_editor_names([rev]),
                           may_edit=False, gm=_content_gm(board, v), levels=wiki_rules.LEVEL_LABELS)


@app.route("/+<guildname>/wiki/<slug>/diff", methods=["GET"])
@auth_desired
def guild_wiki_diff(guildname, slug, v):
    board = visible_guild(guildname, v)
    slug, page = _page_or_404(board, slug)
    if page is None:
        abort(404)
    a = wiki_store.revision(g.db, page[0], request.args.get("a", type=int) or 0)
    b = wiki_store.revision(g.db, page[0], request.args.get("b", type=int) or 0)
    if a is None or b is None:
        abort(404)
    # text an admin removed or the reader's filter hides is not shown in a diff either
    if _shown(a, v)[1] or _shown(b, v)[1]:
        lines, notice = [], "One of these revisions can't be shown to you."
    else:
        lines, notice = wiki_rules.diff(a[3], b[3]), None
    return render_template("guild/wiki_history.html", v=v, b=board, page=page, revisions=None, diff=lines,
                           older=a, newer=b, notice=notice, editors=_editor_names([a, b]), gm=_content_gm(board, v))


# --- editing ---------------------------------------------------------------------------------------------------------

@app.route("/+<guildname>/wiki/<slug>/edit", methods=["GET"])
@auth_required
def guild_wiki_edit(guildname, slug, v):
    board = visible_guild(guildname, v)
    slug, page = _page_or_404(board, slug)
    refusal = edit_refusal_for(board, page, v)
    if refusal:
        return render_template("guild/wiki.html", v=v, b=board, view="refused", refusal=refusal,
                               pages=wiki_store.pages_of(g.db, board.id), may_create=False, gm=False), 403
    rev = wiki_store.revision(g.db, page[0], page[5]) if page else None
    # the text to start from only when this editor may read it (bug hunt: a revision the admins removed, or one their
    # word filter hides, was put in the form, and saving it published it again)
    start_notice = _shown(rev, v)[1]
    if start_notice:
        rev = None
    return render_template("guild/wiki_edit.html", v=v, b=board, slug=slug, page=page, rev=rev, start_notice=start_notice,
                           wiki_max=wiki_rules.WIKI_MAX, title_max=wiki_rules.TITLE_MAX, reason_max=wiki_rules.REASON_MAX)


@app.route("/+<guildname>/wiki/<slug>/edit", methods=["POST"])
@is_not_banned
@validate_formkey
def guild_wiki_save(guildname, slug, v):
    board = visible_guild(guildname, v)
    slug, page = _page_or_404(board, slug)
    refusal = edit_refusal_for(board, page, v)
    if refusal:
        return _error(refusal, 403)
    try:
        title = wiki_rules.clean_title(request.values.get("title"), slug)
        body = wiki_rules.clean_body(request.values.get("body"))
    except wiki_rules.Refused as e:
        return _error(str(e))
    now = int(time.time())
    # the revision the editor started from: someone else saving in the meantime is an edit conflict, never a silent
    # overwrite (audit G-03). The typed text stays in the form; the page shows what changed.
    base = request.values.get("base_revision", type=int)
    if page is not None and base is not None and page[5] and base != page[5]:
        return _error("Someone saved this page while you were editing. Your text is still here: copy it, reload the "
                      "page to see their changes, and add yours again.", 409)
    if page is None:
        page = wiki_store.create_page(g.db, board.id, slug, title, wiki_rules.MODS, now)
    current = wiki_store.revision(g.db, page[0], page[5]) if page[5] else None
    if current is not None and current[3] == body and page[3] == title:
        return jsonify({"ok": True, "unchanged": True, "url": f"/+{board.name}/wiki/{slug}"})
    # the title is rated with the text (audit G-04): a reader's filter hides both
    severity = max(_severity(title), _severity(body) if body else 0)
    wiki_store.save(g.db, board.id, page[0], v.id, title, body, _render(body) if body else "", severity,
                    wiki_rules.clean_reason(request.values.get("reason")), now)
    wiki_store.set_title_severity(g.db, board.id, page[0], _severity(title))
    _log(board, v, "wiki_edit", slug)
    g.db.commit()
    return jsonify({"ok": True, "url": f"/+{board.name}/wiki/{slug}"})


@app.route("/+<guildname>/wiki/<slug>/restore/<int:rid>", methods=["POST"])
@is_not_banned
@validate_formkey
def guild_wiki_restore(guildname, slug, rid, v):
    """A guildmaster puts an old revision's text back: a new revision, so nothing in the history is lost."""
    board = visible_guild(guildname, v)
    if not _content_gm(board, v):
        abort(403)
    slug, page = _page_or_404(board, slug)
    old = wiki_store.revision(g.db, page[0], rid) if page else None
    if old is None or old[8]:
        abort(404)
    wiki_store.save(g.db, board.id, page[0], v.id, page[3], old[3], old[4], old[5],
                    f"Restored the revision of {time.strftime('%d %b %Y %H:%M UTC', time.gmtime(old[7]))}", int(time.time()))
    _log(board, v, "wiki_restore", slug)
    g.db.commit()
    return jsonify({"ok": True, "url": f"/+{board.name}/wiki/{slug}"})


@app.route("/+<guildname>/wiki/<slug>/settings", methods=["POST"])
@is_not_banned
@validate_formkey
def guild_wiki_settings(guildname, slug, v):
    board = visible_guild(guildname, v)
    if not _content_gm(board, v):
        abort(403)
    slug, page = _page_or_404(board, slug)
    if page is None:
        abort(404)
    try:
        level = wiki_rules.clean_level(request.values.get("edit_level"))
    except wiki_rules.Refused as e:
        return _error(str(e))
    wiki_store.set_settings(g.db, board.id, page[0], level, request.values.get("locked") == "true")
    _log(board, v, "wiki_settings", slug)
    g.db.commit()
    return jsonify({"ok": True})


@app.route("/+<guildname>/wiki/<slug>/delete", methods=["POST"])
@is_not_banned
@validate_formkey
def guild_wiki_delete(guildname, slug, v):
    board = visible_guild(guildname, v)
    if not _content_gm(board, v):
        abort(403)
    slug, page = _page_or_404(board, slug)
    if page is None:
        abort(404)
    if slug == wiki_rules.INDEX:
        return _error("The wiki's first page can be emptied but not deleted.", 409)
    wiki_store.delete_page(g.db, board.id, page[0])
    _log(board, v, "wiki_delete", slug)
    g.db.commit()
    return jsonify({"ok": True, "url": f"/+{board.name}/wiki"})


@app.route("/mod/<bid>/wiki/enable", methods=["POST"])
@auth_required
@is_guildmaster("config")
@validate_formkey
def mod_wiki_enable(bid, board, v):
    board.wiki_enabled = request.values.get("setting") == "true"
    g.db.add(board)
    _log(board, v, "wiki_toggle", "on" if board.wiki_enabled else "off")
    g.db.commit()
    return jsonify({"wiki_enabled": board.wiki_enabled})


@app.route("/admin/wiki/<int:page_id>/revision/<int:rid>/remove", methods=["POST"])
@admin_level_required(3)
@validate_formkey
def admin_wiki_remove_revision(page_id, rid, v):
    """An admin takes a revision's text down (it stays listed in the history with no text)."""
    wiki_store.remove_revision(g.db, page_id, rid, int(time.time()))
    g.db.commit()
    return jsonify({"ok": True})


# --- pinned resources ---------------------------------------------------------------------------------------------

@app.route("/+<guildname>/mod/resources", methods=["GET"])
@auth_required
@is_guildmaster("config")
def board_resources_settings(guildname, board, v):
    return render_template("guild/resources.html", v=v, b=board, resources=wiki_store.resources_of(g.db, board.id),
                           max_resources=wiki_rules.MAX_RESOURCES, label_max=wiki_rules.LABEL_MAX,
                           pages=wiki_store.pages_of(g.db, board.id))


@app.route("/mod/<bid>/resources/add", methods=["POST"])
@auth_required
@is_guildmaster("config")
@validate_formkey
def mod_resource_add(bid, board, v):
    try:
        label = wiki_rules.clean_label(request.values.get("label"))
        kind, target = wiki_rules.clean_resource_target(request.values.get("kind"), request.values.get("target"))
    except wiki_rules.Refused as e:
        return _error(str(e))
    refusal = wiki_rules.resources_refusal(len(wiki_store.resources_of(g.db, board.id)))
    if refusal:
        return _error(refusal, 409)
    if kind == wiki_rules.LINK:
        from ruqqus.helpers.filters import filter_comment_html
        if filter_comment_html(f'<a href="{html.escape(target)}">x</a>'):
            return _error("That site can't be linked here.")
    wiki_store.add_resource(g.db, board.id, label, kind, target, label_severity=_severity(label))
    _log(board, v, "resources_edit", label)
    g.db.commit()
    return jsonify({"ok": True})


@app.route("/mod/<bid>/resources/<int:rid>/delete", methods=["POST"])
@auth_required
@is_guildmaster("config")
@validate_formkey
def mod_resource_delete(bid, rid, board, v):
    wiki_store.delete_resource(g.db, board.id, rid)
    _log(board, v, "resources_edit", "removed one")
    g.db.commit()
    return "", 204


@app.route("/mod/<bid>/resources/<int:rid>/move", methods=["POST"])
@auth_required
@is_guildmaster("config")
@validate_formkey
def mod_resource_move(bid, rid, board, v):
    wiki_store.move_resource(g.db, board.id, rid, -1 if request.values.get("step") == "up" else 1)
    g.db.commit()
    return "", 204


def resources_for(board, v):
    """The buttons under a guild's header, as this reader may follow them: a link, a wiki page (only while the wiki is
    on), a post (only one this reader may open, live). Each is {"label", "href", "kind", "external"}."""
    out = []
    for row in wiki_store.resources_of(g.db, board.id):
        kind, target = row[3], row[4]
        if text_hidden(row[6] or 0, v):           # a label the reader's word filter hides (audit G-04)
            continue
        if kind == wiki_rules.LINK:
            out.append({"label": row[2], "href": target, "kind": kind, "external": True})
        elif kind == wiki_rules.WIKI:
            if board.wiki_enabled and wiki_store.page(g.db, board.id, target):
                out.append({"label": row[2], "href": f"/+{board.name}/wiki/{target}", "kind": kind, "external": False})
        else:
            post = get_post(target, v=v, graceful=True)
            if post is not None and not post.deleted_utc and not post.is_banned and not post.in_private_guild_for(v):
                out.append({"label": row[2], "href": post.permalink, "kind": kind, "external": False})
    return out


def wiki_title(page, v):
    """A page's title as this reader may see it: "Hidden page" when their word filter hides it (audit G-04)."""
    return "Hidden page" if text_hidden(page[8] or 0, v) else page[3]


app.jinja_env.globals.update(guild_resources_for=resources_for, wiki_title=wiki_title)


@app.template_filter("timestamp_label")
def timestamp_label(utc):
    return time.strftime("%d %b %Y, %H:%M UTC", time.gmtime(int(utc or 0)))


@app.template_filter("age_label")
def age_label(utc):
    """"3 days ago" for a Unix time (what a model's `age_string` says, for a plain row)."""
    age = max(0, int(time.time()) - int(utc or 0))
    for size, unit in ((31536000, "year"), (2592000, "month"), (86400, "day"), (3600, "hour"), (60, "minute")):
        if age >= size:
            n = age // size
            return f"{n} {unit}{'s' if n != 1 else ''} ago"
    return "just now"
