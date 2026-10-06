"""Publishes scheduled posts when their time comes.

Runs forever as its own supervisord program ([program:ruqqusscheduler]); run
it once by hand with

    PYTHONPATH=. python scripts/publish_scheduled.py --once

Every due draft is submitted through the real POST /api/vue/submit route, in
this process, as its author (a one-off session) from the IP and region saved
with the draft. So every posting rule - the posting-rate throttle, spam and
link checks, forwards, notifications, the word filter, bans - applies exactly
as if the author had pressed Post, and nothing here repeats submit_post.

Drafts are claimed with FOR UPDATE SKIP LOCKED, so more than one scheduler is
safe. A claim that never finishes (the process died mid-publish) is marked
failed rather than retried, because retrying could post twice.
"""
import secrets
import sys
import time
import traceback

from flask import g
from sqlalchemy import text

from ruqqus.__main__ import app, db_session
from ruqqus.classes import PostDraft, User
from ruqqus.helpers import post_drafts as pd
from ruqqus.helpers.alerts import send_notification
from ruqqus.helpers.base36 import base36decode
from ruqqus.helpers.security import generate_hash

POLL_SECONDS = 30
BATCH = 10
USER_AGENT = "Mozilla/5.0 (compatible; scheduled post)"


def log(message):
    print(f"[scheduler] {message}", flush=True)


def housekeeping(now):
    """Give up on interrupted publishes, and drop old published rows."""
    db = db_session()
    db.execute(text(
        "UPDATE post_drafts SET status = 'failed', "
        "error = 'Publishing was interrupted. Check your profile before posting this again.' "
        "WHERE status = 'publishing' AND claimed_utc < :cutoff"),
        {"cutoff": now - pd.STALE_PUBLISHING_SECONDS})
    db.execute(text("DELETE FROM post_drafts WHERE status = 'published' AND updated_utc < :cutoff"),
               {"cutoff": now - pd.KEEP_PUBLISHED_SECONDS})
    db.commit()


def claim_due(now):
    """Ids of the drafts whose time has come, now marked as being published."""
    db = db_session()
    rows = db.execute(text(
        "UPDATE post_drafts SET status = 'publishing', claimed_utc = :now, attempts = attempts + 1 "
        "WHERE id IN (SELECT id FROM post_drafts WHERE status = 'scheduled' AND publish_utc <= :now "
        "ORDER BY publish_utc LIMIT :batch FOR UPDATE SKIP LOCKED) RETURNING id"),
        {"now": now, "batch": BATCH}).fetchall()
    db.commit()
    return [row[0] for row in rows]


def _finish(draft_id, now, **changes):
    """Record the outcome. Always on a fresh query: the request that just ran
    closed the session the draft was loaded in."""
    db = db_session()
    draft = db.query(PostDraft).filter_by(id=draft_id).first()
    if not draft:
        return None
    for name, value in changes.items():
        setattr(draft, name, value)
    draft.updated_utc = now
    db.add(draft)
    db.commit()
    return draft


def _error_text(response):
    data = response.get_json(silent=True)
    if isinstance(data, dict) and data.get("error"):
        return str(data["error"])[:500]
    return f"The server answered {response.status_code}."


def _fail(draft_id, user, now, reason):
    draft = _finish(draft_id, now, status="failed", error=reason[:500], publish_utc=None)
    log(f"draft {draft_id} failed: {reason}")
    if user is None or draft is None:
        return
    # telling the author is best effort: it must never replace the real reason
    try:
        with app.test_request_context("/", environ_base={"REMOTE_ADDR": "127.0.0.1"}):
            g.db = db_session()
            g.v = user   # the markdown renderer reads the viewer (emoji)
            send_notification(user, pd.failure_notice(draft.title, reason))
            g.db.commit()   # send_notification leaves the last commit to the request teardown
    except Exception:
        traceback.print_exc()


def publish(draft_id, now):
    """Submit one claimed draft as its author. Returns what happened."""
    db = db_session()
    draft = db.query(PostDraft).filter_by(id=draft_id).first()
    if not draft or draft.status != "publishing":
        return "skipped"

    user = db.query(User).filter_by(id=draft.user_id, is_deleted=False).first()
    if not user:
        _fail(draft_id, None, now, "The account no longer exists.")
        return "failed"

    fields, attempts = draft.fields, draft.attempts
    ip, region = draft.creation_ip or "127.0.0.1", draft.creation_region
    user_id, nonce = user.id, user.login_nonce

    client = app.test_client()
    session_id = secrets.token_hex(16)
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["login_nonce"] = nonce
        session["session_id"] = session_id
    formkey = generate_hash(f"{session_id}+{user_id}+{nonce}")

    headers = {"User-Agent": USER_AGENT}
    if region:
        headers["cf-ipcountry"] = region

    response = client.post("/api/vue/submit", data=pd.publish_form(fields, formkey),
                           headers=headers, environ_base={"REMOTE_ADDR": ip})

    if response.status_code == 200:
        data = response.get_json(silent=True) or {}
        post_id = base36decode(data["id"]) if data.get("id") else None
        _finish(draft_id, now, status="published", published_post_id=post_id, error="", publish_utc=None)
        log(f"draft {draft_id} published as post {data.get('id')}")
        return "published"

    reason = _error_text(response)
    again = pd.retry_at(now, reason, attempts) if response.status_code == 429 else None
    if again:
        _finish(draft_id, now, status="scheduled", publish_utc=again, error="")
        log(f"draft {draft_id} waits for a posting cooldown, retrying at {again}")
        return "waiting"

    db = db_session()
    user = db.query(User).filter_by(id=user_id).first()
    _fail(draft_id, user, now, reason)
    return "failed"


def tick(now=None):
    """One pass: tidy up, claim what is due, publish it. Returns {id: outcome}."""
    now = int(now or time.time())
    housekeeping(now)
    results = {}
    for draft_id in claim_due(now):
        try:
            results[draft_id] = publish(draft_id, now)
        except Exception:
            traceback.print_exc()
            try:
                _fail(draft_id, None, now, "Something went wrong while publishing. Please try again.")
            except Exception:
                traceback.print_exc()
            results[draft_id] = "failed"
    return results


def main():
    if "--once" in sys.argv:
        print(tick())
        return
    log(f"started, checking every {POLL_SECONDS}s")
    while True:
        try:
            tick()
        except Exception:
            traceback.print_exc()
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
