"""Co-authored posts (helpers/coauthors.py): inviting, answering an invitation, stepping off.

The author invites (from the composer, see routes/posts.py, or here), the invited account accepts or
declines on /coauthor/<post>, a co-author can step off and the author can take one off. Everything works
on the PRIMARY post: an id of a forwarded copy resolves to the post it was copied from."""
import time

from flask import abort, g, jsonify, redirect, render_template, request
from jinja2 import Undefined

from ruqqus.classes import PostCoauthor, Submission, User
from ruqqus.helpers import coauthors
from ruqqus.helpers.get import get_post, get_user
from ruqqus.helpers.visibility import post_hidden
from ruqqus.helpers.wrappers import auth_required, validate_formkey
from ruqqus.__main__ import app, cache


def _primary(pid):
    """The post an id stands for (a copy resolves to the post it was copied from); 404 for a post that
    is gone, removed or that nobody may see."""
    post = get_post(pid)
    primary = coauthors.primary_post_id(post)
    if primary != post.id:
        post = get_post(primary)
    if post.is_banned or post.deleted_utc or post.board.is_banned:
        abort(404)
    return post


def _row(post, user_id):
    return g.db.query(PostCoauthor).filter_by(post_id=post.id, user_id=user_id).first()


def _changed():
    coauthors.forget()
    from ruqqus.routes.front import frontlist
    cache.delete_memoized(frontlist)


def _author_only(post, v):
    if post.author_id != v.id:
        abort(403)


@app.post("/api/coauthor/<pid>/invite")
@auth_required
@validate_formkey
def coauthor_invite(pid, v):
    """The author invites accounts (`usernames`, separated by commas or spaces)."""
    post = _primary(pid)
    _author_only(post, v)
    try:
        names = coauthors.parse_names(request.form.get("usernames"))
    except coauthors.CoauthorError as error:
        return jsonify({"error": error.message}), 400
    if not names:
        return jsonify({"error": "Write a username."}), 400

    invited, errors = coauthors.invite_names(g.db, v, post, names)
    return jsonify({"invited": invited, "errors": errors}), (200 if invited or not errors else 400)


@app.get("/api/coauthor/<pid>/list")
@auth_required
def coauthor_list(pid, v):
    """Who co-authors the post. The author also sees the invitations still waiting; everyone else
    only those who accepted (the names are on the post anyway)."""
    post = _primary(pid)
    if post_hidden(post, v):
        abort(404)
    is_author = post.author_id == v.id
    rows = coauthors.rows_of(g.db, post.id)
    out = []
    for row in rows:
        user = g.db.query(User).filter_by(id=row.user_id).first()
        if not user or user.is_deleted:
            continue
        if row.status != coauthors.ACCEPTED and not is_author:
            continue
        out.append({"username": user.username, "profile_url": user.profile_url, "status": row.status})
    return jsonify({
        "is_author": is_author,
        "can_invite": is_author and not post.is_anonymous and len(rows) < coauthors.MAX_COAUTHORS,
        "limit": coauthors.MAX_COAUTHORS,
        "coauthors": out,
    })


@app.get("/coauthor/<pid>")
@auth_required
def coauthor_page(pid, v):
    """The invitation (accept or decline), or, once accepted, the way to step off."""
    post = _primary(pid)
    row = _row(post, v.id)
    if row is None or post.is_anonymous:
        abort(404)
    return render_template("coauthor_invite.html", v=v, post=post, row=row, accepted=(row.status == coauthors.ACCEPTED))


@app.post("/api/coauthor/<pid>/accept")
@auth_required
@validate_formkey
def coauthor_accept(pid, v):
    post = _primary(pid)
    row = _row(post, v.id)
    author = g.db.query(User).filter_by(id=post.author_id).first()
    if row is None or row.status != coauthors.PENDING:
        abort(404)
    if post.is_anonymous or (author and author.any_block_exists(v)):
        g.db.delete(row)         # the invitation can no longer stand
        g.db.commit()
        abort(404)
    row.status, row.accepted_utc = coauthors.ACCEPTED, int(time.time())
    g.db.add(row)
    g.db.commit()
    _changed()
    if author:
        coauthors.tell(author, f"@{v.username} accepted your invitation to co-author [your post]({post.permalink}).")
    return redirect(post.permalink)


@app.post("/api/coauthor/<pid>/decline")
@auth_required
@validate_formkey
def coauthor_decline(pid, v):
    post = _primary(pid)
    row = _row(post, v.id)
    if row is None or row.status != coauthors.PENDING:
        abort(404)
    g.db.delete(row)
    g.db.commit()
    return redirect("/")


@app.post("/api/coauthor/<pid>/leave")
@auth_required
@validate_formkey
def coauthor_leave(pid, v):
    """A co-author steps off: the post stays, without their name, and leaves their profile."""
    post = _primary(pid)
    row = _row(post, v.id)
    if row is None or row.status != coauthors.ACCEPTED:
        abort(404)
    g.db.delete(row)
    g.db.commit()
    _changed()
    author = g.db.query(User).filter_by(id=post.author_id).first()
    if author:
        coauthors.tell(author, f"@{v.username} stepped off as co-author of [your post]({post.permalink}).")
    return redirect(post.permalink)


@app.post("/api/coauthor/<pid>/remove")
@auth_required
@validate_formkey
def coauthor_remove(pid, v):
    """The author takes a co-author off, or cancels an invitation that was not answered."""
    post = _primary(pid)
    _author_only(post, v)
    name = (request.form.get("username") or "").strip().lstrip("@")
    user = get_user(name, graceful=True) if name else None
    row = _row(post, user.id) if user else None
    if row is None:
        return jsonify({"error": "That account is not invited."}), 404
    g.db.delete(row)
    g.db.commit()
    _changed()
    return jsonify({"message": f"@{user.username} removed."})


def _coauthors_of(post, v=None):
    return coauthors.accepted_of(post, None if isinstance(v, Undefined) else v)


def _is_coauthor(post, v=None):
    return coauthors.is_coauthor(post, None if isinstance(v, Undefined) else v)


def _may_invite(post, v=None):
    """Offer the Co-authors menu item: the author, on a post of their own profile that is not anonymous."""
    v = None if isinstance(v, Undefined) else v
    return bool(v and isinstance(post, Submission) and post.author_id == v.id and not post.is_anonymous
                and post.repost_id in (0, None))


app.jinja_env.globals.update(coauthors_of=_coauthors_of, is_coauthor=_is_coauthor, may_invite_coauthors=_may_invite)
