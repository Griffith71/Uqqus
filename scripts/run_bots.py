"""Posts to bot accounts from the feeds they were approved with (helpers/bots.py, helpers/bot_runner.py).

Runs forever as its own supervisord program ([program:ruqqusbots]); run it once by hand with

    PYTHONPATH=. python scripts/run_bots.py --once

Every item is submitted through the real POST /api/vue/submit route, in this process, as the bot (a one-off session;
nobody ever logs into a bot), with the bot header and from the bot's own loopback address
(`bots.loopback_for`). So every posting rule - the throttle, spam and link checks, guild forwarding rules, the word
filter, bans and the bot rules in submit_post - applies exactly as for a person, and nothing here repeats submit_post.

A member's own account that posts automatically (helpers/bots.py SELF) goes the same way: the session is the member's
account, made here and never a sign-in (no sign-in history row, no new-sign-in email), and the bot header makes
submit_post mark the post automated and hold it to the bot rules (bots.is_automated).

Sources are claimed with FOR UPDATE SKIP LOCKED (bot_store.claim_due), so more than one runner is safe.
BOTS_ENABLED=0 makes every run do nothing.
"""
import secrets
import sys
import time
import traceback

import gevent
from flask import g

from ruqqus.__main__ import app, db_session
from ruqqus.classes import User
from ruqqus.helpers import bot_runner, bot_store, bots, feed_fetch
from ruqqus.helpers.alerts import send_notification
from ruqqus.helpers.base36 import base36decode
from ruqqus.helpers.security import generate_hash

POLL_SECONDS = 60
BATCH = 20
USER_AGENT = "Mozilla/5.0 (compatible; bot runner)"


def log(message):
    print(f"[bots] {message}", flush=True)


def owner_ok(owner_id):
    owner = db_session().query(User).filter_by(id=owner_id).first()
    return bool(owner and not owner.is_deleted and not owner.is_suspended)


def notify(user_id, text):
    """Best effort: telling the owner must never stop the run."""
    try:
        user = db_session().query(User).filter_by(id=user_id).first()
        if user is None:
            return
        with app.test_request_context("/", environ_base={"REMOTE_ADDR": "127.0.0.1"}):
            g.db = db_session()
            g.v = user
            send_notification(user, text)
            g.db.commit()
    except Exception:
        traceback.print_exc()


def submit(bot, item, guilds):
    """Post one item as the bot through the real route; what the answer means (bot_runner.classify)."""
    db = db_session()
    db.commit()                                   # the route commits this same session
    user = db.query(User).filter_by(id=bot.user_id, is_deleted=False).first()
    if user is None:
        return bot_runner.outcome("stop", "The bot's account no longer exists.")
    user_id, nonce = user.id, user.login_nonce

    client = app.test_client()
    session_id = secrets.token_hex(16)
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["login_nonce"] = nonce
        session["session_id"] = session_id
    form = {"title": item.title, "url": item.link, "body": "", "no_repost": "1",
            "formkey": generate_hash(f"{session_id}+{user_id}+{nonce}")}
    if guilds:
        form["forward_guilds"] = list(guilds)

    response = client.post("/api/vue/submit", data=form,
                           headers={"User-Agent": USER_AGENT, "X-User-Type": "Bot"},
                           environ_base={"REMOTE_ADDR": bots.loopback_for(user_id)})
    data = response.get_json(silent=True) or {}
    post_id = base36decode(data["id"]) if response.status_code == 200 and data.get("id") else None
    return bot_runner.classify(response.status_code, response.headers.get("Location", ""), data, bool(guilds), post_id)


def tick(now=None):
    """One pass: tidy up, claim what is due, read and post it. Returns {source id: what happened}."""
    if not bots.enabled():
        return {}
    now = int(now or time.time())
    db = db_session()
    bot_store.prune_seen(db, now)
    due = bot_store.claim_due(db, now, BATCH)
    db.commit()
    results = {}
    for source_id in due:
        try:
            results[source_id] = bot_runner.run_source(db_session(), source_id, now, feed_fetch.fetch, submit,
                                                       notify, owner_ok)
        except Exception:
            traceback.print_exc()
            db_session().rollback()
            results[source_id] = "error"
    for source_id, what in results.items():
        log(f"source {source_id}: {what}")
    return results


def tick_safely():
    """One tick of the forever loop. After an error the scoped session is thrown away: left in its failed
    transaction, every later tick would fail too until the job was restarted (bug hunt M5)."""
    try:
        return tick()
    except Exception:
        traceback.print_exc()
        try:
            db_session.remove()
        except Exception:
            traceback.print_exc()
        return None


def main():
    if "--once" in sys.argv:
        print(tick())
        # each post started background work (its link preview, a CSAM check: gevent greenlets the route spawns). The
        # forever loop's sleep lets it run; a one-off run must wait for it, or posts are left without a preview
        gevent.wait(timeout=120)
        return
    log(f"started, checking every {POLL_SECONDS}s")
    while True:
        tick_safely()
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
