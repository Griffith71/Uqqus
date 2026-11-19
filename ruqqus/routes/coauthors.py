"""Co-authored posts (helpers/coauthors.py): inviting, answering an invitation, stepping off.

The author invites (from the composer, see routes/posts.py, or here), the invited account accepts or
declines on /coauthor/<post>, a co-author can step off and the author can take one off. Everything works
on the PRIMARY post: an id of a forwarded copy resolves to the post it was copied from."""
import time

from flask import abort, g, jsonify, redirect, render_template, request
from jinja2 import Undefined

from ruqqus.classes import PostCoauthor, Submission, User
from ruqqus.helpers import age_declaration, anon_aliases, anonymity, coauthors, collab_view
from ruqqus.helpers.alerts import notify
from ruqqus.helpers.get import get_post, get_user
from ruqqus.helpers.i18n_runtime import N_, msg, tr
from ruqqus.helpers.post_fields import flag
from ruqqus.helpers.visibility import post_hidden
from ruqqus.helpers.wrappers import auth_required, not_qr_limited, not_quarantined, validate_formkey
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


COLLAB_REFUSAL = N_("This post's authors are decided in its shared draft.")


def _tell(user, message, **values):
    """A notification marked for translation (helpers/alerts.py notify), best effort as coauthors.tell: a failure to
    tell never undoes the answer."""
    try:
        notify(user, message, **values)
    except Exception:
        g.db.rollback()


def _collab(post):
    """A collaborative post: its authors come and go through its shared draft (routes/collab.py), never here."""
    from ruqqus.helpers import collab_store
    return collab_store.open_doc_for_post(g.db, post.id) is not None


@app.post("/api/coauthor/<pid>/invite")
@auth_required
@not_qr_limited
@not_quarantined("coauthor")
@validate_formkey
def coauthor_invite(pid, v):
    """The author invites accounts (`usernames`, separated by commas or spaces)."""
    post = _primary(pid)
    if _collab(post):
        return jsonify({"error": msg(COLLAB_REFUSAL)}), 409
    _author_only(post, v)
    if post.audience:
        return jsonify({"error": tr("A post for a Circle can't have co-authors.")}), 400
    try:
        names = coauthors.parse_names(request.form.get("usernames"))
    except coauthors.CoauthorError as error:
        return jsonify({"error": msg(error.message)}), 400
    if not names:
        return jsonify({"error": tr("Write a username.")}), 400

    invited, errors = coauthors.invite_names(g.db, v, post, names)
    return jsonify({"invited": invited, "errors": [msg(e) for e in errors]}), (200 if invited or not errors else 400)


@app.get("/api/coauthor/<pid>/list")
@auth_required
def coauthor_list(pid, v):
    """Who co-authors the post. The author also sees the invitations still waiting; everyone else
    only those who accepted (the names are on the post anyway)."""
    post = _primary(pid)
    if post_hidden(post, v):
        abort(404)
    is_author = anonymity.is_author(post, v)          # not a raw id comparison: a limited QR sign-in is a stranger to its own anonymous post (C16)
    rows = coauthors.rows_of(g.db, post.id)
    numbers = anon_aliases.numbers_cached(g.db, anon_aliases.scope_key(post)) if any(r.is_anonymous for r in rows) else {}
    out = []
    for row in rows:
        user = g.db.query(User).filter_by(id=row.user_id).first()
        if not user or (user.is_deleted and not row.is_anonymous):
            continue
        if row.status != coauthors.ACCEPTED and not is_author:
            continue
        # an anonymous credit is told to the post's author (who invited them), the co-author and admins only; everyone else is shown its alias
        if row.is_anonymous and not (is_author or coauthors.sees_credit(user, v)):
            number = numbers.get(user.id)
            out.append({"username": anonymity.alias_name(number) if number else anonymity.shown(), "profile_url": anonymity.DEFAULT_AVATAR,
                        "status": row.status, "alias": True})
            continue
        out.append({"username": user.username, "profile_url": user.profile_url, "status": row.status, "alias": False})
    return jsonify({
        "is_author": is_author,
        "can_invite": is_author and len(rows) < coauthors.MAX_COAUTHORS,
        "limit": coauthors.MAX_COAUTHORS,
        "coauthors": out,
    })


@app.get("/coauthor/<pid>")
@auth_required
@not_qr_limited
def coauthor_page(pid, v):
    """The invitation (accept or decline), or, once accepted, the way to step off."""
    post = _primary(pid)
    row = _row(post, v.id)
    if row is None:
        abort(404)
    return render_template("coauthor_invite.html", v=v, post=post, row=row, accepted=(row.status == coauthors.ACCEPTED))


@app.post("/api/coauthor/<pid>/accept")
@auth_required
@not_qr_limited
@not_quarantined("coauthor")
@validate_formkey
def coauthor_accept(pid, v):
    post = _primary(pid)
    row = _row(post, v.id)
    author = g.db.query(User).filter_by(id=post.author_id).first()
    if row is None or row.status != coauthors.PENDING:
        abort(404)
    if age_declaration.content_refusal(v):      # being on a public byline is posting
        abort(403)
    if author and author.any_block_exists(v):
        g.db.delete(row)         # the invitation can no longer stand
        g.db.commit()
        abort(404)
    row.status, row.accepted_utc = coauthors.ACCEPTED, int(time.time())
    # a co-author may accept without their name (admins can always see it): the credit is anonymous from the start, as on a post made anonymous
    if flag(request.form, "anonymous"):
        row.is_anonymous = True
    g.db.add(row)
    if row.is_anonymous:
        anon_aliases.give(g.db, anon_aliases.scope_key(post), v.id)       # their number in the post's thread (the Log plan's decision 23)
    g.db.commit()
    _changed()
    if author:
        _tell(author, N_("@%(name)s accepted your invitation to co-author [your post](%(link)s)."),
              name=v.username, link=post.permalink)
    return redirect(post.permalink)


@app.post("/api/coauthor/<pid>/hide_name")
@auth_required
@not_qr_limited
@validate_formkey
def coauthor_hide_name(pid, v):
    """A co-author takes their name off their credit, for good ("Remove my name", decision 21): only their credit becomes Anonymous (shown by its alias on the
    byline and the shared-post pages); the other authors stay named and the post is unchanged. Admins and the post's author still see who it is. It is the
    credit that is hidden, not what they wrote: it can't be undone, and they remain a co-author who can step off."""
    post = _primary(pid)
    row = _row(post, v.id)
    if row is None or row.status != coauthors.ACCEPTED:
        abort(404)
    if coauthors.hide_credit(g.db, post, v.id):
        g.db.commit()
        _changed()
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
    if _collab(post):
        return jsonify({"error": msg(COLLAB_REFUSAL)}), 409
    row = _row(post, v.id)
    if row is None or row.status != coauthors.ACCEPTED:
        abort(404)
    g.db.delete(row)
    g.db.commit()
    _changed()
    author = g.db.query(User).filter_by(id=post.author_id).first()
    if author:
        _tell(author, N_("@%(name)s stepped off as co-author of [your post](%(link)s)."), name=v.username, link=post.permalink)
    return redirect(post.permalink)


@app.post("/api/coauthor/<pid>/remove")
@auth_required
@not_quarantined("coauthor")
@validate_formkey
def coauthor_remove(pid, v):
    """The author takes a co-author off, or cancels an invitation that was not answered."""
    post = _primary(pid)
    if _collab(post):
        return jsonify({"error": msg(COLLAB_REFUSAL)}), 409
    _author_only(post, v)
    name = (request.form.get("username") or "").strip().lstrip("@")
    user = get_user(name, graceful=True) if name else None
    row = _row(post, user.id) if user else None
    if row is None:
        return jsonify({"error": tr("That account is not invited.")}), 404
    g.db.delete(row)
    g.db.commit()
    _changed()
    return jsonify({"message": tr("@%(name)s removed.", name=user.username)})


def _coauthors_of(post, v=None):
    """What a byline draws for the co-authors: accounts, and an anonymous credit's alias for whoever may not be told (helpers/coauthors.py credits_of)."""
    return coauthors.credits_of(post, None if isinstance(v, Undefined) else v)


def _is_coauthor(post, v=None):
    return coauthors.is_coauthor(post, None if isinstance(v, Undefined) else v)


def _may_invite(post, v=None):
    """Offer the Co-authors menu item: the author, on a post of their own profile that is not anonymous."""
    v = None if isinstance(v, Undefined) else v
    return bool(v and isinstance(post, Submission) and post.author_id == v.id
                and not post.audience and post.repost_id in (0, None) and not collab_view.is_collab(post))


app.jinja_env.globals.update(coauthors_of=_coauthors_of, is_coauthor=_is_coauthor, may_invite_coauthors=_may_invite)
