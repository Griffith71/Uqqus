"""Live threads: going live, updates, ending (rules in helpers/live_threads.py, SQL in helpers/live_thread_store.py,
the panel partials/live_panel.html with assets/js/live_thread.js), and the livestream card's Jinja global
(helpers/livestreams.py).

A live thread belongs to the primary post (a forwarded copy shows the same one). Who may read its updates is who
may read the post (routes/live_comments.readable_post); who may write them is the author and accepted co-authors.
Every change is a POST with the form key. An update is pushed to readers by id only (`live.update` on the post's
topic): each reader asks for it, drawn for them (their word filter).
"""
import time

import mistletoe
from flask import *

from ruqqus.helpers.wrappers import *
from ruqqus.helpers.get import *
from ruqqus.helpers.base36 import base36encode
from ruqqus.helpers.markdown import CustomRenderer, preprocess
from ruqqus.helpers.sanitize import sanitize
from ruqqus.classes import *
from ruqqus.__main__ import app, limiter, member_limit_key, r

from ruqqus.helpers import live_threads as live_rules
from ruqqus.helpers import live_thread_store as live_store
from ruqqus.helpers import live_view, livestream_status, live_bus, anonymity
from ruqqus.helpers.alerts import send_notification
from ruqqus.helpers.visibility import text_hidden

_DAY = 86400


def _error(message, status=400):
    return jsonify({"error": message}), status


def _primary(post):
    """The post a live thread belongs to (a guild copy's original)."""
    if post.repost_id:
        original = get_post(base36encode(post.repost_id), graceful=True)
        if original is not None:
            return original
    return post


def _runners(post):
    from ruqqus.helpers import coauthors
    return [u.id for u in coauthors.accepted_of(post)]


def _readable(pid, v):
    from ruqqus.routes.live_comments import readable_post
    post = readable_post(pid, v)
    if post is None:
        abort(404)
    return post, _primary(post)


def _update_json(row, post, v):
    """One update as a reader gets it: no name on an anonymous post, hidden when their word filter says so."""
    author = g.db.query(User).filter_by(id=row[2]).first()
    hidden_name = post.is_anonymous or author is None
    hidden = text_hidden(row[5], v, owner_id=row[2])
    return {
        "id": row[0], "created_utc": row[6],
        "author": None if hidden_name else author.shown_name,
        "body_html": None if hidden else row[4],          # sanitized when it was written
        "hidden": hidden,
        "mine": v is not None and row[2] == v.id,
    }


@app.route("/api/post/<pid>/live", methods=["POST"])
@is_not_banned
@validate_formkey
def live_toggle(pid, v):
    post, primary = _readable(pid, v)
    may = live_rules.may_run(v.id, primary.author_id, _runners(primary))
    now = int(time.time())
    row = live_store.thread(g.db, primary.id)
    live = bool(row) and live_rules.is_live(row[0], row[1], row[2], now)
    if request.values.get("on") == "0":
        if not may:
            return _error("Only the post's author and co-authors can end its live updates.", 403)
        live_store.end(g.db, primary.id, now)
        g.db.commit()
        live_bus.publish(r, live_bus.topic_channel(f"post:{primary.base36id}"), "live.ended", {})
        return jsonify({"live": False})
    refusal = live_rules.start_refusal(may, bool(primary.is_banned), bool(primary.deleted_utc), live)
    if refusal:
        return _error(refusal, 403 if refusal.startswith("Only") else 409)
    live_store.start(g.db, primary.id, v.id, now)
    g.db.commit()
    # the people who follow the post (helpers/post_follows.py) and may still read it
    from ruqqus.helpers import post_follows
    from ruqqus.routes.comments import _followers_who_may_read
    ids = post_follows.followers(g.db, primary.id, v.id, anonymous=bool(primary.is_anonymous))
    for user in g.db.query(User).filter(User.id.in_(_followers_who_may_read(primary, ids))).all() if ids else []:
        send_notification(user, f"A post you follow is live: [see the updates]({primary.permalink})")
    return jsonify({"live": True})


@app.route("/api/post/<pid>/live_updates", methods=["GET"])
@limiter.limit("120/minute", key_func=member_limit_key)
@auth_required
def live_updates(pid, v):
    post, primary = _readable(pid, v)
    try:
        after = int(request.args.get("after", "0") or 0)
    except ValueError:
        after = 0
    rows = live_store.updates(g.db, primary.id, after_id=after)
    row = live_store.thread(g.db, primary.id)
    live = bool(row) and live_rules.is_live(row[0], row[1], row[2], int(time.time()))
    return jsonify({"live": live, "updates": [_update_json(u, primary, v) for u in rows]})


@app.route("/api/post/<pid>/live_updates", methods=["POST"])
@is_not_banned
@validate_formkey
def live_update_add(pid, v):
    post, primary = _readable(pid, v)
    now = int(time.time())
    row = live_store.thread(g.db, primary.id)
    live = bool(row) and live_rules.is_live(row[0], row[1], row[2], now)
    try:
        body = live_rules.clean_update(request.values.get("body"))
    except live_rules.Refused as e:
        return _error(str(e))
    refusal = live_rules.update_refusal(live_rules.may_run(v.id, primary.author_id, _runners(primary)), live,
                                        live_store.updates_since(g.db, primary.id, now - _DAY))
    if refusal:
        return _error(refusal, 403 if refusal.startswith("Only") else 409)
    text = preprocess(body)
    with CustomRenderer(post_id=primary.base36id) as renderer:
        body_md = renderer.render(mistletoe.Document(text))
    body_html = sanitize(body_md, linkgen=True)
    from ruqqus.helpers.word_filter_store import get_filter
    severity = get_filter(g.db).severity(body)
    uid = live_store.add_update(g.db, primary.id, v.id, body, body_html, severity, now)
    g.db.commit()
    live_bus.publish(r, live_bus.topic_channel(f"post:{primary.base36id}"), "live.update", {"id": uid})
    return jsonify(_update_json(live_store.update(g.db, uid), primary, v))


@app.route("/api/live_updates/<int:uid>/delete", methods=["POST"])
@auth_required
@validate_formkey
def live_update_delete(uid, v):
    row = live_store.update(g.db, uid)
    if row is None or row[2] != v.id:
        abort(404)
    live_store.delete_update(g.db, uid, int(time.time()))
    g.db.commit()
    return "", 204


def updates_for_page(p, v):
    """The newest updates of a post's live thread, drawn for this reader (the post page renders them first; the
    script then only asks for newer ones). Nothing for a post that never went live."""
    if not live_view.live_of(p):
        return []
    primary = _primary(p)
    return [_update_json(u, primary, v) for u in live_store.updates(g.db, primary.id)]


app.jinja_env.globals.update(live_updates_of=updates_for_page, live_of=live_view.live_of, stream_of=lambda p: livestream_status.describe(p.url),
                             may_run_live=lambda p, v: bool(v) and live_rules.may_run(
                                 v.id, (p.author_id), _runners(p)) and not (p.repost_id),
                             author_hidden=lambda p, v: anonymity.identity_hidden(p, v))
