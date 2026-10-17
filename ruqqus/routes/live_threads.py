"""Live threads: going live, updates, ending (rules in helpers/live_threads.py, SQL in helpers/live_thread_store.py,
the panel partials/live_panel.html with assets/js/live_thread.js), and the livestream card's Jinja global
(helpers/livestreams.py).

A live thread belongs to the primary post (a forwarded copy shows the same one). Who may read its updates is who
may read the post (routes/live_comments.readable_post); who may write them is the author and accepted co-authors.
Every change is a POST with the form key. An update is pushed to readers by id only (`live.update` on the post's
topic): each reader asks for it, drawn for them (their word filter).
"""
import hashlib
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


def _render(body, primary):
    """Markdown as an update or the summary is shown: rendered and sanitized like a comment."""
    with CustomRenderer(post_id=primary.base36id) as renderer:
        body_md = renderer.render(mistletoe.Document(preprocess(body)))
    return sanitize(body_md, linkgen=True)


def _severity(body):
    from ruqqus.helpers.word_filter_store import get_filter
    return get_filter(g.db).severity(body)


def _topic(primary):
    return live_bus.topic_channel(f"post:{primary.base36id}")


def _update_json(row, post, v):
    """One update as a reader gets it: no name on an anonymous post, hidden when their word filter says so, whether it
    was crossed out, and whether this reader may cross it out or delete it (its writer, the post's author)."""
    author = g.db.query(User).filter_by(id=row[2]).first()
    hidden_name = post.is_anonymous or author is None
    hidden = text_hidden(row[5], v, owner_id=row[2])
    return {
        "id": row[0], "created_utc": row[6],
        "author": None if hidden_name else author.shown_name,
        "body_html": None if hidden else row[4],          # sanitized when it was written
        "hidden": hidden,
        "struck": bool(row[9]),
        "mine": v is not None and row[2] == v.id,
        "may_change": v is not None and live_rules.may_change_update(v.id, row[2], post.author_id),
    }


def _runner_names(primary):
    """The usernames of who runs the thread (the author, then accepted co-authors); none on an anonymous post."""
    if primary.is_anonymous:
        return []
    from ruqqus.helpers import coauthors
    author = g.db.query(User).filter_by(id=primary.author_id).first()
    names = [author.username] if author is not None and not author.is_deleted else []
    return names + [u.username for u in coauthors.accepted_of(primary) if u.username not in names]


def thread_json(primary, v, row=None):
    """The thread's state for the header: live or ended (and when), its pinned summary as this reader may see it (the
    words themselves only for who runs it, to edit), and who writes the updates."""
    row = row if row is not None else live_store.thread(g.db, primary.id)
    may = v is not None and live_rules.may_run(v.id, primary.author_id, _runners(primary))
    if row is None:
        return {"exists": False, "live": False, "may_run": may, "ends_utc": None, "ended_utc": None,
                "summary_html": None, "summary_hidden": False, "summary": "" if may else None,
                "runners": live_rules.runners_line(_runner_names(primary), primary.is_anonymous)}
    now = int(time.time())
    live = live_rules.is_live(row[0], row[1], row[2], now, row[4])
    summary_hidden = bool(row[5]) and text_hidden(row[7], v, owner_id=primary.author_id)
    return {
        "exists": True, "live": live, "may_run": may, "started_utc": row[0],
        "ends_utc": live_rules.ends_at(row[0], row[2], row[4]) if live else None,
        "ended_utc": None if live else live_rules.ended_at(row[0], row[1], row[2], row[4]),
        "summary_html": None if summary_hidden or not row[6] else row[6],     # sanitized when it was written
        "summary_hidden": summary_hidden,
        "summary": row[5] if may else None,
        "runners": live_rules.runners_line(_runner_names(primary), primary.is_anonymous),
    }


def _hours():
    try:
        return live_rules.clean_hours(request.values.get("hours")), None
    except live_rules.Refused as e:
        return None, _error(str(e))


@app.route("/api/post/<pid>/live", methods=["POST"])
@is_not_banned
@validate_formkey
def live_toggle(pid, v):
    """Go live for a chosen length (`hours`), extend a running thread (`extend=1&hours=`), or end it (`on=0`)."""
    post, primary = _readable(pid, v)
    may = live_rules.may_run(v.id, primary.author_id, _runners(primary))
    now = int(time.time())
    row = live_store.thread(g.db, primary.id)
    live = bool(row) and live_rules.is_live(row[0], row[1], row[2], now, row[4])
    if request.values.get("on") == "0":
        if not may:
            return _error("Only the post's author and co-authors can end its live updates.", 403)
        live_store.end(g.db, primary.id, now)
        g.db.commit()
        live_bus.publish(r, _topic(primary), "live.ended", {})
        return jsonify({"live": False, "thread": thread_json(primary, v)})
    hours, error = _hours()
    if error:
        return error
    if request.values.get("extend") == "1":
        if not may:
            return _error("Only the post's author and co-authors can extend its live updates.", 403)
        if not live:
            return _error("This post isn't live.", 409)
        live_store.extend(g.db, primary.id, live_rules.extend_until(row[0], now, hours))
        g.db.commit()
        live_bus.publish(r, _topic(primary), "live.status", {})
        return jsonify({"live": True, "thread": thread_json(primary, v)})
    stopped = live_rules.ended_at(row[0], row[1], row[2], row[4]) if row and not live else 0
    refusal = live_rules.start_refusal(may, bool(primary.is_banned), bool(primary.deleted_utc), live, stopped, now)
    if refusal:
        return _error(refusal, 403 if refusal.startswith("Only") else 409)
    start_thread(primary, v, now, hours)
    g.db.commit()
    # the people who follow the post (helpers/post_follows.py) and may still read it
    from ruqqus.helpers import post_follows
    from ruqqus.routes.comments import _followers_who_may_read
    ids = post_follows.followers(g.db, primary.id, v.id, anonymous=bool(primary.is_anonymous))
    for user in g.db.query(User).filter(User.id.in_(_followers_who_may_read(primary, ids))).all() if ids else []:
        send_notification(user, f"A post you follow is live: [see the updates]({primary.permalink})")
    live_bus.publish(r, _topic(primary), "live.status", {})
    return jsonify({"live": True, "thread": thread_json(primary, v)})


def start_thread(primary, v, now, hours):
    """Live from now for `hours` (also called by submit_post for "Make this a live thread"). Nothing is committed."""
    live_store.start(g.db, primary.id, v.id, now, live_rules.extend_until(now, now, hours))


@app.route("/api/post/<pid>/live_updates", methods=["GET"])
@limiter.limit("120/minute", key_func=member_limit_key)
@auth_desired
def live_updates(pid, v):
    post, primary = _readable(pid, v)
    try:
        after = int(request.args.get("after", "0") or 0)
    except ValueError:
        after = 0
    rows = live_store.updates(g.db, primary.id, after_id=after)
    thread = thread_json(primary, v)
    return jsonify({"live": thread["live"], "thread": thread, "updates": [_update_json(u, primary, v) for u in rows]})


@app.route("/api/live_updates/<int:uid>", methods=["GET"])
@limiter.limit("120/minute", key_func=member_limit_key)
@auth_desired
def live_update_one(uid, v):
    """One update as this reader sees it now (after it was crossed out or deleted: `live.changed`)."""
    row = live_store.update(g.db, uid)
    if row is None:
        abort(404)
    post, primary = _readable(base36encode(row[1]), v)
    if row[8]:
        return jsonify({"id": uid, "gone": True})
    return jsonify(_update_json(row, primary, v))


@app.route("/api/post/<pid>/live_updates", methods=["POST"])
@is_not_banned
@validate_formkey
def live_update_add(pid, v):
    post, primary = _readable(pid, v)
    now = int(time.time())
    row = live_store.thread(g.db, primary.id)
    live = bool(row) and live_rules.is_live(row[0], row[1], row[2], now, row[4])
    try:
        body = live_rules.clean_update(request.values.get("body"))
    except live_rules.Refused as e:
        return _error(str(e))
    refusal = live_rules.update_refusal(live_rules.may_run(v.id, primary.author_id, _runners(primary)), live,
                                        live_store.updates_since(g.db, primary.id, now - _DAY))
    if refusal:
        return _error(refusal, 403 if refusal.startswith("Only") else 409)
    uid = live_store.add_update(g.db, primary.id, v.id, body, _render(body, primary), _severity(body), now)
    g.db.commit()
    live_bus.publish(r, _topic(primary), "live.update", {"id": uid})
    return jsonify(_update_json(live_store.update(g.db, uid), primary, v))


def _changeable(uid, v):
    """(update row, primary post) for an update this member may cross out or delete, else a 404."""
    row = live_store.update(g.db, uid)
    if row is None or row[8]:
        abort(404)
    post, primary = _readable(base36encode(row[1]), v)
    if not live_rules.may_change_update(v.id, row[2], primary.author_id):
        abort(404)
    return row, primary


@app.route("/api/live_updates/<int:uid>/strike", methods=["POST"])
@auth_required
@validate_formkey
def live_update_strike(uid, v):
    """Cross an update out (`on=1`, shown "Corrected" to everyone) or undo it (`on=0`)."""
    row, primary = _changeable(uid, v)
    live_store.strike(g.db, uid, request.values.get("on") != "0", int(time.time()))
    g.db.commit()
    live_bus.publish(r, _topic(primary), "live.changed", {"id": uid})
    return jsonify(_update_json(live_store.update(g.db, uid), primary, v))


@app.route("/api/live_updates/<int:uid>/delete", methods=["POST"])
@auth_required
@validate_formkey
def live_update_delete(uid, v):
    row, primary = _changeable(uid, v)
    live_store.delete_update(g.db, uid, int(time.time()))
    g.db.commit()
    live_bus.publish(r, _topic(primary), "live.changed", {"id": uid})
    return "", 204


@app.route("/api/post/<pid>/live_summary", methods=["POST"])
@is_not_banned
@validate_formkey
def live_summary(pid, v):
    """The pinned summary (Reddit's "resources"), kept up to date by who runs the thread; empty clears it."""
    post, primary = _readable(pid, v)
    if not live_rules.may_run(v.id, primary.author_id, _runners(primary)):
        return _error("Only the post's author and co-authors can edit the summary.", 403)
    if live_store.thread(g.db, primary.id) is None:
        return _error("This post has no live thread.", 409)
    try:
        body = live_rules.clean_summary(request.values.get("summary"))
    except live_rules.Refused as e:
        return _error(str(e))
    live_store.set_summary(g.db, primary.id, body, _render(body, primary) if body else "", _severity(body) if body else 0,
                           int(time.time()))
    g.db.commit()
    live_bus.publish(r, _topic(primary), "live.summary", {})
    return jsonify({"thread": thread_json(primary, v)})


@app.route("/api/post/<pid>/live_watch", methods=["POST"])
@limiter.limit("60/minute", key_func=member_limit_key)
@auth_desired
def live_watch(pid, v):
    """A reader's page says it is still watching (every 30 s while the tab is visible) and gets how many are: a count
    only, never who, kept only in Redis for two minutes (live:watch:<post>; the member's id, or for a visitor a salted
    hash of their address). It changes nothing anyone can read about anyone, so it needs no form key. Visitors count
    once per address (audit L-06: a made-up id per tab let one visitor count as hundreds); people sharing a network
    count as one visitor, which only ever lowers the number."""
    post, primary = _readable(pid, v)
    now = int(time.time())
    watching = 0
    if r is not None:
        address = request.remote_addr or ""
        salt = app.config.get("SECRET_KEY") or ""
        who = f"m{v.id}" if v else ("a" + hashlib.sha256(f"{salt}:{address}".encode()).hexdigest()[:24] if address else None)
        key = f"live:watch:{primary.id}"
        try:
            if who:
                r.zadd(key, {who: now})
            r.zremrangebyscore(key, "-inf", now - live_rules.WATCH_SECONDS)
            r.expire(key, 2 * live_rules.WATCH_SECONDS)
            watching = int(r.zcard(key) or 0)
        except Exception:
            watching = 0
    thread = thread_json(primary, v)
    return jsonify({"watching": watching, "live": thread["live"], "ends_utc": thread["ends_utc"],
                    "ended_utc": thread["ended_utc"]})


def updates_for_page(p, v):
    """The newest updates of a post's live thread, drawn for this reader (the post page renders them first; the
    script then only asks for newer ones). Nothing for a post that never went live."""
    if not live_view.live_of(p):
        return []
    primary = _primary(p)
    return [_update_json(u, primary, v) for u in live_store.updates(g.db, primary.id)]


def thread_for_page(p, v):
    """The thread's header state for the post page (`thread_json`), for any post its runner may start one on too."""
    return thread_json(_primary(p), v)


app.jinja_env.globals.update(live_updates_of=updates_for_page, live_thread_of=thread_for_page, live_of=live_view.live_of,
                             live_durations=live_rules.DURATIONS, live_default_hours=live_rules.DEFAULT_HOURS, stream_of=lambda p: livestream_status.describe(p.url),
                             may_run_live=lambda p, v: bool(v) and live_rules.may_run(
                                 v.id, (p.author_id), _runners(p)) and not (p.repost_id),
                             author_hidden=lambda p, v: anonymity.identity_hidden(p, v))
