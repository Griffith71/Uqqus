import time
from ruqqus.classes import *
from ruqqus.helpers.wrappers import *
from ruqqus.helpers.get import *
from ruqqus.helpers.base36 import *
from flask import (
    request, session, g, abort, jsonify, make_response, redirect, render_template
)
from ruqqus.helpers import community_notes
from ruqqus.__main__ import app


@app.route("/api/flag/post/<pid>", methods=["POST"])
@is_not_banned
@validate_formkey
def api_flag_post(pid, v):

    post = get_post(pid)

    kind = request.form.get("report_type")

    if kind == "admin":
        existing = g.db.query(Flag).filter_by(
            user_id=v.id, post_id=post.id).filter(
            Flag.created_utc >= post.edited_utc).first()

        if existing:
            return "", 409

        flag = Flag(post_id=post.id,
                    user_id=v.id,
                    created_utc=int(time.time())
                    )

    elif kind == "guild":
        existing = g.db.query(Report).filter_by(
            user_id=v.id, post_id=post.id).filter(
            Report.created_utc >= post.edited_utc).first()

        if existing:
            return "", 409

        flag = Report(post_id=post.id,
                      user_id=v.id,
                      created_utc=int(time.time())
                      )
    elif kind == "note":
        # "this may need context": a request for a community note, not a policy flag
        refusal = _note_refusal(v, post.author_id, post_id=community_notes.primary_post_id(post))
        if refusal:
            return refusal
        flag = NoteRequest(post_id=community_notes.primary_post_id(post),
                           user_id=v.id,
                           created_utc=int(time.time())
                           )

    else:
        return "", 422

    g.db.add(flag)

    return "", 204


def _note_refusal(v, author_id, post_id=None, comment_id=None):
    """An answer when this member may not ask for a note on this, else None. Not on your
    own post, once each, and not endlessly many a day (the admins read every request)."""
    if author_id == v.id:
        return jsonify({"error": "You can't ask for a community note on your own post."}), 409

    mine = g.db.query(NoteRequest).filter_by(user_id=v.id)
    target = NoteRequest.post_id == post_id if post_id is not None else NoteRequest.comment_id == comment_id
    if mine.filter(target).first():
        return jsonify({"error": "You already asked for a community note on this."}), 409

    today = int(time.time()) - 86400
    if mine.filter(NoteRequest.created_utc > today).count() >= community_notes.DAILY_REQUESTS:
        return jsonify({"error": "You have asked for a lot of community notes today. Try again tomorrow."}), 429
    return None


@app.route("/api/flag/comment/<cid>", methods=["POST"])
@is_not_banned
@validate_formkey
def api_flag_comment(cid, v):

    comment = get_comment(cid)
    kind = request.form.get("report_type", "admin")

    if kind == "note":
        refusal = _note_refusal(v, comment.author_id, comment_id=comment.id)
        if refusal:
            return refusal
        g.db.add(NoteRequest(comment_id=comment.id, user_id=v.id, created_utc=int(time.time())))
        return "", 204
    if kind != "admin":
        return "", 422

    existing = g.db.query(CommentFlag).filter_by(
        user_id=v.id, comment_id=comment.id).filter(
        CommentFlag.created_utc >= comment.edited_utc).first()

    if existing:
        return "", 409

    flag = CommentFlag(comment_id=comment.id,
                       user_id=v.id,
                       created_utc=int(time.time())
                       )

    g.db.add(flag)

    return "", 204
