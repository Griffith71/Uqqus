"""Post insights for Premium authors (helpers/insights.py, helpers/insights_store.py): the pages.

Read only by the ORIGINAL author of a post (a guild copy's id resolves to the post it copies), and only with
Premium. Everything shown is a count: nothing names a viewer, a voter or someone who voted in a poll. The
Premium check is `has_premium_no_renew`: unlike the `has_premium` property it never spends a coin to renew
as a side effect. (An account's usual auto-renew is unchanged: any page that reads `has_premium`, such as the
sidebar through `can_make_guild`, renews a lapsed Premium from the account's coins, as everywhere on the site.)"""
import time

from flask import abort, g, jsonify, render_template, request

from ruqqus.classes import Submission
from ruqqus.helpers import insights as rules
from ruqqus.helpers import insights_store
from ruqqus.helpers.coauthors import primary_post_id
from ruqqus.helpers.get import get_post
from ruqqus.helpers.wrappers import auth_required
from ruqqus.__main__ import app


def _mine(pid, v):
    """The primary post `pid` names, if v wrote it and it is live; otherwise a 404 (never a hint that it
    exists)."""
    if not pid or len(pid) > 10 or not (pid.isascii() and pid.isalnum() and pid == pid.lower()):
        abort(404)
    post = get_post(pid, v=v, graceful=True)
    if not post:
        abort(404)
    primary_id = primary_post_id(post)
    primary = post if primary_id == post.id else g.db.query(Submission).filter_by(id=primary_id).first()
    if primary is None or primary.author_id != v.id or primary.is_banned or primary.deleted_utc:
        abort(404)
    return primary


def _may_see(post, v):
    """For the menus: the author's own post, not a guild copy."""
    return bool(v) and post.author_id == v.id and not post.repost_id and not post.deleted_utc and not post.is_banned


app.jinja_env.globals.update(may_see_insights=_may_see)


@app.route("/post/<pid>/insights", methods=["GET"])
@auth_required
def post_insights(pid, v):
    primary = _mine(pid, v)
    if not v.has_premium_no_renew:
        return render_template("insights_locked.html", v=v, post=primary)

    days = rules.parse_days(request.args.get("days"))
    data = insights_store.report(g.db, primary, v, days, time.time())
    return render_template("insights.html", v=v, post=primary, data=data, days=days, ranges=rules.RANGES)


@app.route("/post/<pid>/insights.json", methods=["GET"])
@auth_required
def post_insights_json(pid, v):
    primary = _mine(pid, v)
    if not v.has_premium_no_renew:
        return jsonify({"error": "Post insights are for Premium accounts."}), 403

    days = rules.parse_days(request.args.get("days"))
    data = insights_store.report(g.db, primary, v, days, time.time())
    poll = data.pop("poll")
    data["poll"] = None if poll is None else {
        "total": poll["total"], "closed": poll["closed"],
        "options": [{"label": o["label"], "votes": o["votes"], "percent": o["percent"]} for o in poll["options"]],
    }
    return jsonify(data)


@app.route("/insights", methods=["GET"])
@auth_required
def insights_overview(v):
    if not v.has_premium_no_renew:
        return render_template("insights_locked.html", v=v, post=None)

    return render_template("insights_overview.html", v=v, rows=insights_store.overview(g.db, v, time.time()))
