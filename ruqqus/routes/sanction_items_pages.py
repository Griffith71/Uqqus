"""The posts and comments of an account that a guildmaster may rely on when exiling it (the owner, 2026-11-14: an exile must cite what it is about).

`GET /mod/exile_items/<guild>?username=` answers the account's own NAMED, live posts and comments made in this guild, newest first, for the dialog's picker
(assets/js/decision_dialog.js; helpers/sanction_evidence.py says what can be cited). Only what anyone could already read: never an anonymous item (it would
pair an account with it), never a deleted or removed one, never one the guildmaster's own word filter hides. An account that doesn't exist, was deleted or has
nothing to cite answers the same empty list, so the page tells nothing about who is who."""
import html

from flask import g, jsonify, request
from sqlalchemy import or_

from ruqqus.__main__ import app, limiter, member_limit_key
from ruqqus.classes import CommentForwardRelationship, Comment, ForwardRelationship, Submission
from ruqqus.helpers import post_follows, sanction_evidence, visibility
from ruqqus.helpers.get import get_user
from ruqqus.helpers.wrappers import auth_required, is_guildmaster

LISTED = 25


def _snippet(text, size=110):
    text = " ".join((text or "").split())
    return text if len(text) <= size else text[:size - 1] + "…"


@app.route("/mod/exile_items/<guildname>", methods=["GET"])
@limiter.limit("30/minute", key_func=member_limit_key)
@auth_required
@is_guildmaster("access")
def mod_exile_items(guildname, board, v):
    """The account's citable items in this guild, as `{"items": [{"id": fullname, "kind", "text", "created_utc"}]}`."""
    user = get_user((request.args.get("username") or "").lstrip("@"), graceful=True)
    response = jsonify({"items": []})
    if user is None or user.is_deleted:
        response.headers["Cache-Control"] = "private, no-store"
        return response
    found = []
    not_removed = or_(Submission.is_banned == False, Submission.is_banned == None)
    # what they made straight in the guild, and the guild's copies of what they forwarded here (a copy keeps the ORIGINAL author's id: whose it is for an exile
    # is its forwarder, `post_follows.responsible_id`, as the exile route asks it)
    posts = g.db.query(Submission).filter(
        Submission.original_board_id == board.id, Submission.author_id == user.id, Submission.is_anonymous == False,
        Submission.deleted_utc == 0, not_removed, or_(Submission.repost_id == None, Submission.repost_id == 0),
    ).order_by(Submission.created_utc.desc()).limit(LISTED).all()
    forwarded = g.db.query(Submission).join(ForwardRelationship, ForwardRelationship.forward_submission_id == Submission.id).filter(
        ForwardRelationship.board_id == board.id, ForwardRelationship.forwarded_by_id == user.id, Submission.is_anonymous == False,
        Submission.deleted_utc == 0, not_removed).order_by(Submission.created_utc.desc()).limit(LISTED).all()
    from_comments = g.db.query(Submission).join(
        CommentForwardRelationship, CommentForwardRelationship.forwarded_submission_id == Submission.id).filter(
        CommentForwardRelationship.board_id == board.id, CommentForwardRelationship.forwarded_by_id == user.id, Submission.is_anonymous == False,
        Submission.deleted_utc == 0, not_removed).order_by(Submission.created_utc.desc()).limit(LISTED).all()
    for post in {p.id: p for p in [*posts, *forwarded, *from_comments]}.values():
        if sanction_evidence.usable(post, board.id, user.id, post_follows.responsible_id(g.db, post)) and not visibility.post_hidden(post, v):
            found.append((post.created_utc, {"id": post.fullname, "kind": "post", "text": _snippet(html.unescape(post.title or "")),
                                             "created_utc": post.created_utc}))
    comments = g.db.query(Comment).filter(
        Comment.original_board_id == board.id, Comment.author_id == user.id, Comment.is_anonymous == False,
        Comment.deleted_utc == 0, or_(Comment.is_banned == False, Comment.is_banned == None),
    ).order_by(Comment.created_utc.desc()).limit(LISTED).all()
    for comment in comments:
        if sanction_evidence.usable(comment, board.id, user.id, comment.author_id) and not visibility.comment_hidden(comment, v):
            found.append((comment.created_utc, {"id": comment.fullname, "kind": "comment", "text": _snippet(comment.body),
                                                "created_utc": comment.created_utc}))
    found.sort(key=lambda pair: pair[0], reverse=True)
    response = jsonify({"items": [item for _when, item in found[:LISTED]]})
    response.headers["Cache-Control"] = "private, no-store"
    return response
