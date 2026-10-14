"""Follow a post (helpers/post_follows.py): be told of the comments its author, or on a copy the person who
forwarded it, is told of. Nothing here ever names another follower."""
from flask import abort, g, jsonify

from ruqqus.helpers import post_follows as rules
from ruqqus.helpers.get import get_post
from ruqqus.helpers.visibility import post_hidden
from ruqqus.helpers.wrappers import is_not_banned, validate_formkey
from ruqqus.__main__ import app


def _is_post_id(pid):
    """A base36 post id (what get_post decodes), nothing else."""
    return bool(pid) and len(pid) <= 10 and pid.isascii() and pid.isalnum() and pid == pid.lower()


def _post_or_404(pid, v):
    """A post the caller may read: not removed or deleted, not hidden by their word filter, in a guild they
    can view (a Circle post the caller is not in is a 404 from get_post)."""
    post = get_post(pid, v=v, graceful=True) if _is_post_id(pid) else None
    if (not post or post.is_banned or post.deleted_utc or post.board.is_banned
            or post_hidden(post, v) or post.in_private_guild_for(v) or not post.board.can_view(v)):
        abort(404)
    return post


@app.route("/api/post/<pid>/follow", methods=["POST"])
@is_not_banned
@validate_formkey
def follow_post(pid, v):
    post = _post_or_404(pid, v)
    try:
        rules.follow(g.db, v.id, post)
    except rules.FollowError as error:
        return jsonify({"error": error.message}), error.status
    g.db.commit()
    return jsonify({"message": "You'll be notified of comments on this post."})


@app.route("/api/post/<pid>/unfollow", methods=["POST"])
@is_not_banned
@validate_formkey
def unfollow_post(pid, v):
    post = _post_or_404(pid, v)
    try:
        rules.unfollow(g.db, v.id, post)
    except rules.FollowError as error:
        return jsonify({"error": error.message}), error.status
    g.db.commit()
    return jsonify({"message": "You won't be notified of comments on this post."})
