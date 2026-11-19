"""History > Manage: take your name off, or delete, many of your own posts and comments at once (the Log plan's slice D3c; rules `helpers/mass_edit.py`).

It does what the delete dialog does, item by item, by calling the same functions (`delete_post_as`, `unlink_post`, `delete_comment_as`,
`unlink_comment`), each in a savepoint of its own so one refusal never undoes the rest. **Never copy their logic here.** It needs the password
(and the two-factor code where the account has one), refuses a limited QR sign-in, is the site only (no `@api`: an API app or a bot key can't reach
it), and is limited to a hundred items a request and six requests an hour. A page of what could not be changed, and why, comes back.
"""
import html
import logging
import time

from flask import g, render_template, request
from sqlalchemy import or_

from ruqqus.__main__ import app, limiter, member_limit_key
from ruqqus.classes import Comment, Submission
from ruqqus.helpers import anonymity, mass_edit, mfa_backup_store
from ruqqus.helpers.base36 import base36decode
from ruqqus.helpers.get import get_comment, get_comments, get_post, get_posts
from ruqqus.helpers.i18n_runtime import msg
from ruqqus.helpers.wrappers import auth_required, is_not_banned, not_qr_limited, page_number, throttle_check_ip, validate_formkey

log = logging.getLogger(__name__)


def _own_posts(v, page):
    """The member's own live posts on their profile, newest first (a guild copy of the same post would list it twice; a Space and a post a moderator
    removed aren't theirs to change here)."""
    rows = g.db.query(Submission.id).filter(
        Submission.author_id == v.id, Submission.deleted_utc == 0, Submission.is_banned == False, Submission.is_space == False,
        or_(Submission.repost_id.is_(None), Submission.repost_id == 0),
    ).order_by(Submission.created_utc.desc(), Submission.id.desc()).offset(25 * (page - 1)).limit(26).all()
    return [r[0] for r in rows]


def _own_comments(v, page):
    rows = g.db.query(Comment.id).filter(
        Comment.author_id == v.id, Comment.deleted_utc == 0, Comment.is_banned == False,
    ).order_by(Comment.created_utc.desc(), Comment.id.desc()).offset(25 * (page - 1)).limit(26).all()
    return [r[0] for r in rows]


def _rows_for(v, content_type, page):
    """The page's rows, plain text only: `{"token", "kind", "title", "age", "anonymous", "url"}`. Posts and comments of one page, merged newest first."""
    post_ids = _own_posts(v, page) if content_type in ("all", "posts") else []
    comment_ids = _own_comments(v, page) if content_type in ("all", "comments") else []
    next_exists = len(post_ids) == 26 or len(comment_ids) == 26
    rows = []
    for post in get_posts(post_ids[:25], v=v) if post_ids else []:
        rows.append({"token": mass_edit.token(mass_edit.POST, post.base36id), "kind": mass_edit.POST, "created": post.created_utc,
                     "title": html.unescape(post.title or ""), "anonymous": bool(post.is_anonymous), "url": post.permalink})
    for comment in get_comments(comment_ids[:25], v=v) if comment_ids else []:
        words = " ".join((comment.body or "").split())
        rows.append({"token": mass_edit.token(mass_edit.COMMENT, comment.base36id), "kind": mass_edit.COMMENT, "created": comment.created_utc,
                     "title": words[:140] + ("…" if len(words) > 140 else ""), "anonymous": bool(comment.is_anonymous), "url": comment.permalink})
    rows.sort(key=lambda r: r["created"], reverse=True)
    return rows, next_exists


def _page(v, content_type, page, **extra):
    rows, next_exists = _rows_for(v, content_type, page)
    # `history_base`: this page is also drawn from the POST to /history/mass, whose address answers only POST: its Show links and pager go to /history/manage
    # (the second review, C67)
    return render_template("history_manage.html", v=v, active_tab="manage", content_type=content_type, rows=rows, page=page,
                           next_exists=next_exists, max_items=mass_edit.MAX_ITEMS, listing=[], mixed_listing=None, history_base="/history/manage", **extra)


def _content_type():
    content_type = request.args.get("type") or request.form.get("type") or "all"
    return content_type if content_type in ("all", "posts", "comments") else "all"


@app.route("/history/manage", methods=["GET"])
@auth_required
# which items are anonymous is what a limited sign-in isn't told
@not_qr_limited
def history_manage(v):
    """History > Manage: your own posts and comments, to tick."""
    return _page(v, _content_type(), page_number())


def _password_ok(v):
    """The password, and the two-factor or backup code where the account has one (as deleting the account asks)."""
    if not v.verifyPass(request.form.get("password", "")):
        return False
    if v.mfa_secret:
        code = request.form.get("twofactor", "")
        return bool(v.accept_2fa(code) or mfa_backup_store.spend(g.db, v.id, code, int(time.time())))
    return True


def _answer(result):
    """`(ok, why)` from what the single-item functions return: `("", 204)`, a `(response, status)` refusal, or None for a done comment delete."""
    if result is None:
        return True, ""
    status = result[1] if isinstance(result, tuple) and len(result) > 1 else 204
    if status in (200, 204):
        return True, ""
    try:
        return False, (result[0].get_json() or {}).get("error") or msg(mass_edit.FAILED)
    except Exception:
        return False, msg(mass_edit.FAILED)


def _snippet(text_, size=140):
    words = " ".join(html.unescape(text_ or "").split())
    return words[:size] + ("…" if len(words) > size else "")


def _change_post(post_id, action, v):
    """`(ok, why, what it was)`: the post's title for the list of what couldn't be changed."""
    from ruqqus.routes.posts import COLLAB_DELETE_REFUSAL, delete_post_as, unlink_post
    from ruqqus.helpers import collab_store
    post = get_post(post_id, v=v, graceful=True)
    if post is None or post.is_deleted or not anonymity.is_author(post, v):
        return False, msg(mass_edit.NOT_FOUND), ""
    label = _snippet(post.title)
    if action == mass_edit.ANONYMISE:
        return (*_answer(unlink_post(post, v)), label)
    if collab_store.open_doc_for_post(g.db, post.repost_id or post.id) is not None and not post.is_repost:
        return False, msg(COLLAB_DELETE_REFUSAL), label
    return (*_answer(delete_post_as(post, v)), label)


def _change_comment(comment_id, action, v):
    from ruqqus.routes.comments import delete_comment_as, unlink_comment
    comment = get_comment(comment_id, v=v, graceful=True)
    if comment is None or comment.deleted_utc > 0 or not anonymity.is_author(comment, v):
        return False, msg(mass_edit.NOT_FOUND), ""
    label = _snippet(comment.body)
    if action == mass_edit.ANONYMISE:
        return (*_answer(unlink_comment(comment, v)), label)
    return (*_answer(delete_comment_as(comment, v)), label)


@app.route("/history/mass", methods=["POST"])
@limiter.limit(mass_edit.PER_HOUR, key_func=member_limit_key)
@is_not_banned
# irreversible, and a relayed code's: the password first (the owner's decision 22)
@not_qr_limited
@validate_formkey
# a wrong password costs heat, as everywhere the password is asked
@throttle_check_ip("history_mass")
def history_mass(v):
    """Make anonymous or delete the ticked items, then say what happened to each."""
    content_type = _content_type()
    action = mass_edit.parse_action(request.form.get("action"))
    items, problem = mass_edit.parse_items(request.form.getlist("items"))
    ticked = [mass_edit.token(kind, base36id) for kind, base36id in items]
    error = None
    if action is None:
        error = msg(mass_edit.NO_ACTION)
    elif problem:
        error = msg(problem[0]) % problem[1]
    elif not mass_edit.count_matches(request.form.get("count"), items):
        error = msg(mass_edit.COUNT)
    elif not _password_ok(v):
        error = msg(mass_edit.PASSWORD)
    if error:
        return _page(v, content_type, page_number(), error=error, ticked=ticked), 400

    results = []
    for kind, base36id in items:
        savepoint = g.db.begin_nested()
        try:
            item_id = base36decode(base36id)
            ok, why, label = (_change_post if kind == mass_edit.POST else _change_comment)(item_id, action, v)
        except Exception:
            log.exception("history_mass: %s %s", kind, base36id)
            ok, why, label = False, msg(mass_edit.FAILED), ""
        savepoint.commit() if ok else savepoint.rollback()
        results.append({"ok": ok, "why": why, "kind": kind, "label": label, "token": mass_edit.token(kind, base36id)})
    g.db.commit()
    done, refused = mass_edit.summary(results)
    return _page(v, content_type, 1, results=results, done=done, refused=refused, action=action)
