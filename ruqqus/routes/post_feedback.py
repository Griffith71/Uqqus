"""Show fewer posts like this (helpers/post_feedback.py): the member's own, private signal."""
from flask import g, jsonify, request

from ruqqus.helpers import post_feedback as rules
from ruqqus.helpers.wrappers import is_not_banned, validate_formkey
from ruqqus.routes.post_follows import _post_or_404
from ruqqus.__main__ import app


@app.route("/api/post/<pid>/fewer", methods=["POST"])
@is_not_banned
@validate_formkey
def post_fewer_like_this(pid, v):
    """Ask for fewer posts like this one; with `undo` take it back. Answering twice is the same answer."""
    post = _post_or_404(pid, v)
    undo = (request.values.get("undo") or "") in ("1", "true")
    try:
        if undo:
            rules.remove(g.db, v.id, post)
        else:
            rules.add(g.db, v.id, post)
    except rules.FeedbackError as error:
        return jsonify({"error": error.message}), error.status
    g.db.commit()
    rules.clear_cached_feeds(v)

    return jsonify({"message": "Back to normal." if undo else "We'll show fewer posts like this."})
