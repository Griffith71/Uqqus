"""Comments arriving live on a post: a Space's discussion and a live thread (helpers/live_bus.py).

When a comment is made, `api_comment` publishes only its id to the post's topic (`post:<id>`); every page listening
then asks `GET /api/post/<id>/live_comments?after=<comment id>` for what is new, drawn **for that reader**: their word
filter, their blocks and mutes, the post's own visibility (a Circle post, a private guild), never a removed or
deleted comment, and the author through `anonymity.author_of` (an anonymous comment stays anonymous). So nothing
about a comment ever travels on the live channel itself.
"""
import re

from flask import *

from ruqqus.helpers.wrappers import *
from ruqqus.helpers.get import *
from ruqqus.helpers.base36 import base36decode, base36encode
from ruqqus.classes import *
from ruqqus.__main__ import app, limiter, member_limit_key, r

from ruqqus.helpers import anonymity, live_bus
from ruqqus.helpers.blocks import hide_blocked
from ruqqus.helpers.muting import hide_muted
from ruqqus.helpers.visibility import filter_comments, post_hidden

LIVE_PAGE = 50
_ID = re.compile(r"^[0-9a-z]{1,10}$")


def readable_post(pid, v):
    """The post when `v` may read it (the rules of opening it), else None."""
    if not pid or not _ID.match(pid):
        return None
    post = get_post(pid, v=v, graceful=True)
    if post is None or post.deleted_utc or post.is_banned or post_hidden(post, v) or post.in_private_guild_for(v):
        return None
    if post.board and post.board.is_banned:
        return None
    return post


def _guard_post_topic(v, pid):
    return readable_post(pid, v) is not None


live_bus.register_topic("post", _guard_post_topic)


def announce_comment(comment):
    """Called by api_comment once a comment is committed: its id, to the post's topic (and nothing else)."""
    live_bus.publish(r, live_bus.topic_channel(f"post:{base36encode(comment.parent_submission)}"), "comment.new",
                     {"id": comment.base36id})


def comment_json(c, v):
    author = anonymity.author_of(c, v)
    hidden = anonymity.identity_hidden(c, v)
    return {
        "id": c.base36id,
        "parent": base36encode(c.parent_comment_id) if c.parent_comment_id else None,
        "author": "Anonymous" if hidden else author.shown_name,
        "username": None if hidden else author.username,
        "avatar": anonymity.DEFAULT_AVATAR if hidden else author.profile_url,
        "body_html": c.body_html or "",           # sanitized when it was written
        "created_utc": c.created_utc,
        "mine": v is not None and c.author_id == v.id,
    }


@app.route("/api/post/<pid>/live_comments", methods=["GET"])
@limiter.limit("120/minute", key_func=member_limit_key)
@auth_required
def live_comments(pid, v):
    post = readable_post(pid, v)
    if post is None:
        abort(404)
    after = request.args.get("after", "")
    q = g.db.query(Comment).filter(Comment.parent_submission == post.id, Comment.deleted_utc == 0,
                                   Comment.is_banned == False)
    if after and _ID.match(after):
        q = q.filter(Comment.id > base36decode(after))
    q = filter_comments(q, v)
    q = hide_blocked(q, v, Comment)
    q = hide_muted(q, v, Comment)
    newest_first = not after
    q = q.order_by(Comment.id.desc() if newest_first else Comment.id.asc()).limit(LIVE_PAGE)
    comments = q.all()
    if newest_first:
        comments.reverse()
    return jsonify({"comments": [comment_json(c, v) for c in comments],
                    "can_comment": bool(post) and not post.is_archived})
