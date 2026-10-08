"""Who upvoted (helpers/voters.py): the routes, and the template helper.

The author of a post or comment can open a list of the upvoters they are mutual followers with. Nobody else can
(not the forwarder of a copy, not an admin), and downvoters are never part of any answer."""
from flask import abort, g, jsonify

from ruqqus.helpers import voters as voter_rules
from ruqqus.helpers.get import get_comment, get_post
from ruqqus.helpers.wrappers import auth_required
from ruqqus.__main__ import app


def _is_id(raw):
    """A base36 id (what get_post and get_comment decode), nothing else."""
    return bool(raw) and len(raw) <= 10 and raw.isascii() and raw.isalnum() and raw == raw.lower()


def _answer(kind, item, v):
    if item is None or item.is_banned or item.deleted_utc:
        abort(404)
    if item.author_id != v.id:
        return jsonify({"error": "Only the author can see who upvoted."}), 403

    people = voter_rules.friends_who_upvoted(g.db, kind, item.id, item.author_id, v)
    response = jsonify({
        "people": [{"username": u.username, "permalink": u.permalink, "avatar": u.profile_url} for u in people],
        "limit": voter_rules.LIMIT,
        "note": voter_rules.NOTE,
    })
    response.headers["Cache-Control"] = "private, no-store"
    return response


@app.route("/api/post/<pid>/voters", methods=["GET"])
@auth_required
def post_voters(pid, v):
    return _answer("post", get_post(pid, v=v, graceful=True) if _is_id(pid) else None, v)


@app.route("/api/comment/<cid>/voters", methods=["GET"])
@auth_required
def comment_voters(cid, v):
    return _answer("comment", get_comment(cid, v=v, graceful=True) if _is_id(cid) else None, v)


app.jinja_env.globals.update(may_see_voters=voter_rules.may_see)
