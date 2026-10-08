"""Polls (helpers/polls.py, helpers/poll_store.py): voting, and the template helpers.

A poll is voted on from wherever its post is shown: `<pid>` may be the primary post, a guild copy of it
(`repost_id`) or the post a repost points at, and every one of them votes the same poll."""
import time

from flask import abort, g, jsonify, redirect, render_template, request
from sqlalchemy.exc import IntegrityError

from ruqqus.classes import Submission
from ruqqus.classes.poll import Poll, PollOption, PollVote
from ruqqus.helpers import poll_store
from ruqqus.helpers import polls as rules
from ruqqus.helpers.coauthors import primary_post_id
from ruqqus.helpers.get import get_post
from ruqqus.helpers.visibility import post_hidden
from ruqqus.helpers.wrappers import is_not_banned, validate_formkey
from ruqqus.__main__ import app

_UNSET = object()


def _poll_of(post, viewer=None):
    """What to draw for this post's poll (None without one). A page of posts already has it
    (poll_store.attach in get_posts); a post loaded some other way is looked up on first use."""
    data = post.__dict__.get("poll_data", _UNSET)
    if data is _UNSET:
        data = poll_store.load_one(g.db, post, viewer)
        post.poll_data = data
    return data


app.jinja_env.globals.update(
    poll_of=_poll_of,
    poll_durations=rules.DURATIONS,
    poll_default_hours=rules.DEFAULT_HOURS,
    poll_max_options=rules.MAX_OPTIONS,
    poll_min_options=rules.MIN_OPTIONS,
)


def _is_post_id(pid):
    """A base36 post id (what get_post decodes), nothing else."""
    return bool(pid) and len(pid) <= 10 and pid.isascii() and pid.isalnum() and pid == pid.lower()


def _back(post):
    return redirect(post.permalink)


@app.route("/api/poll/<pid>/vote", methods=["POST"])
@is_not_banned
@validate_formkey
def poll_vote(pid, v):
    """Count the caller's vote. One vote each, final, before the poll closes, on a post the caller may see.
    With `back` (the plain form, no script) it redirects to the post; otherwise it answers with the poll as
    it should now be drawn."""
    post = get_post(pid, v=v, graceful=True) if _is_post_id(pid) else None
    if (not post or post.is_banned or post.deleted_utc or post.board.is_banned
            or post_hidden(post, v) or not post.board.can_view(v)):
        abort(404)

    primary_id = primary_post_id(post)
    poll = g.db.query(Poll).filter_by(post_id=primary_id).first()
    if not poll:
        abort(404)

    primary = post if primary_id == post.id else g.db.query(Submission).filter_by(id=primary_id).first()
    gone = primary is None or bool(primary.is_banned or primary.deleted_utc)

    raw = (request.values.get("option") or "").strip()
    option = None
    if raw.isascii() and raw.isdigit():
        option = g.db.query(PollOption).filter_by(id=int(raw), poll_id=poll.id).first()

    now = int(time.time())
    voted = g.db.query(PollVote).filter_by(poll_id=poll.id, user_id=v.id).first() is not None
    refusal = rules.vote_refusal(gone, rules.is_closed(poll.closes_utc, now), voted, option is not None)

    if not refusal:
        g.db.add(PollVote(poll_id=poll.id, option_id=option.id, user_id=v.id, created_utc=now))
        try:
            g.db.flush()
        except IntegrityError:
            g.db.rollback()
            refusal = "You already voted in this poll."

    if request.values.get("back"):
        return _back(post)
    if refusal:
        return jsonify({"error": refusal}), 410 if gone else 409 if refusal != "Pick one of the options." else 400

    post.poll_data = poll_store.load_one(g.db, post, v)
    return jsonify({"message": "Vote counted.",
                    "html": render_template("partials/poll_fragment.html", p=post, v=v)})
