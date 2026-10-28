"""Feedback about the site: a member's sheet ("Send feedback" in the account menu, partials/site_feedback_modal.html,
assets/js/site_feedback.js) and the admins' inbox at /admin/feedback. Rules and SQL in helpers/site_feedback.py, the
screenshot's checks in helpers/feedback_image.py. Nothing here is a report of a post or an account (routes/flagging.py,
routes/account_reports.py) or "Show fewer posts like this" (routes/post_feedback.py)."""
import re

from flask import Response, abort, g, jsonify, redirect, render_template, request

from ruqqus.classes import User
from ruqqus.helpers import feedback_image
from ruqqus.helpers import site_feedback as rules
from ruqqus.helpers.alerts import notify
from ruqqus.helpers.i18n_runtime import N_, tr
from ruqqus.helpers.media import rules as media_rules
from ruqqus.helpers.wrappers import admin_level_required, is_not_banned, validate_formkey
from ruqqus.__main__ import app, limiter, member_limit_key

# a sheet is a few hundred characters of text and at most one screenshot (the page sends it already shrunk)
REQUEST_MAX_BYTES = feedback_image.INPUT_MAX_BYTES + 256 * 1024
EXCERPT = 60
IMAGE_TYPES = {"webp": "image/webp", "jpg": "image/jpeg"}

# what a sender is told (stored in English, drawn in each reader's language: helpers/alerts.py notify)
CLOSED_TOLD = {
    ("fixed", False): N_('Your feedback was marked fixed: "%(excerpt)s"'),
    ("fixed", True): N_('Your feedback was marked fixed: "%(excerpt)s". Our response: %(response)s'),
    ("declined", False): N_('Thank you for your feedback: "%(excerpt)s". We are not going to act on it for now.'),
    ("declined", True): N_('Thank you for your feedback: "%(excerpt)s". We are not going to act on it for now. '
                           'Our response: %(response)s'),
}
RESPONDED = N_('Our response to your feedback "%(excerpt)s": %(response)s')


def _too_big():
    return jsonify({"error": tr("That is too big to send. The limit is %(size)s MB.",
                                size=feedback_image.INPUT_MAX_BYTES // (1024 * 1024))}), 413


@app.post("/api/feedback")
@limiter.limit("10/hour", key_func=member_limit_key)    # tries count, accepted or not; the day's cap bounds what is kept
@is_not_banned
@validate_formkey
def send_feedback(v):
    if (request.content_length or 0) > REQUEST_MAX_BYTES:
        return _too_big()
    request.max_content_length = REQUEST_MAX_BYTES
    kind, error = rules.parse_kind(request.form.get("kind"))
    if error:
        return jsonify({"error": error}), 400
    message, error = rules.parse_message(request.form.get("message"))
    if error:
        return jsonify({"error": error}), 400
    if rules.filed_today(g.db, v.id) >= rules.DAILY_LIMIT:
        return jsonify({"error": tr("You've sent a lot of feedback today. Try again tomorrow.")}), 429
    picture = None
    upload = request.files.get("image")
    if upload and upload.filename:
        picture, error = feedback_image.prepare(upload.stream.read(feedback_image.INPUT_MAX_BYTES + 1))
        if error:
            return jsonify({"error": error}), 400
    page, context = rules.parse_context(request.form.get("context"), request.headers.get("User-Agent"))
    item_id = rules.add(g.db, v.id, kind, message, page, context)
    if picture:
        rules.attach_image(g.db, item_id, *picture)
    g.db.commit()
    return jsonify({"message": tr("Thank you. If we write back, you will find it in your notifications.")})


def _back():
    """Where the admin page returns to: the inbox, with its filter, never anywhere else."""
    back = request.form.get("back", "")
    return back if back.startswith("/admin/feedback") else "/admin/feedback"


@app.get("/admin/feedback")
@admin_level_required(3)
def admin_feedback(v):
    status = request.args.get("status", "waiting")
    if status not in ("waiting", "all") + rules.STATUSES:
        status = "waiting"
    kind = request.args.get("kind", "")
    if kind not in rules.KINDS:
        kind = ""
    items = rules.items(g.db, None if status == "all" else status, kind or None)
    return render_template("admin/site_feedback.html", v=v, items=items, status=status, kind=kind,
                           kinds=rules.KINDS, statuses=rules.STATUSES)


@app.get("/admin/feedback/<int:feedback_id>/image")
@admin_level_required(3)
def admin_feedback_image(feedback_id, v):
    """A screenshot, for admins only: a member may have captured a private page. Typed by its own first bytes, never
    cached, and sandboxed so nothing in it could run."""
    found = rules.image(g.db, feedback_id)
    mime = IMAGE_TYPES.get(media_rules.sniff(found[1][:16])) if found else None
    if not mime:
        abort(404)
    response = Response(found[1], mimetype=mime)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = "sandbox; default-src 'none'"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def _excerpt(message):
    return " ".join(re.sub(r"[^\w\s.,!?'’:;-]", "", message or "").split())[:EXCERPT]


def tell_sender(item, status, response):
    """Best effort: the sender's notification when an item is closed or a new response is written."""
    closing = status in rules.CLOSED and item["status"] not in rules.CLOSED
    new_response = bool(response) and response != item["response"]
    if not (closing or new_response):
        return
    if status in rules.CLOSED:
        text = CLOSED_TOLD[(status, bool(response))]
    else:
        text = RESPONDED
    try:
        user = g.db.query(User).filter(User.id == item["user_id"], User.is_deleted == False).first()
        if user:
            notify(user, text, excerpt=_excerpt(item["message"]), response=response)
    except Exception:
        g.db.rollback()


@app.post("/admin/feedback/<int:feedback_id>/status")
@admin_level_required(3)
@validate_formkey
def admin_feedback_status(feedback_id, v):
    item = rules.get(g.db, feedback_id)
    if not item:
        abort(404)
    status = rules.parse_status(request.form.get("status"))
    if not status:
        abort(400)
    response = rules.parse_response(request.form.get("response"))
    # a response left empty keeps the earlier one; the sender is told only of what is new
    rules.set_status(g.db, feedback_id, status, response)
    g.db.commit()
    tell_sender(item, status, response)
    return redirect(_back())


def feedback_waiting():
    """The count beside Admin > Feedback on the admin page."""
    return rules.waiting_count(g.db)


app.jinja_env.globals.update(feedback_waiting=feedback_waiting)
