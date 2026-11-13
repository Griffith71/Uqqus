"""Sending push notifications (rules in helpers/push_rules.py, the encryption in helpers/web_push.py). Off unless the site
has its key pair (VAPID_PUBLIC_KEY, VAPID_PRIVATE_KEY).

`prepare` runs in the request that caused it (the appservice webhook, a call starting): it reads who is told, leaves out
anyone in a block with the sender either way or who muted them, and writes each one's notification in their own language.
`send_later` then sends them in a greenlet after the request's commit, so a slow push service never holds the request
up, and notes what each push service said in a session of its own (a browser that unsubscribed is forgotten).
"""
import logging
import os
import time

from sqlalchemy import or_

from ruqqus.helpers import push_rules, push_store, web_push
from ruqqus.helpers.i18n_runtime import force_locale, locale_for_user

log = logging.getLogger(__name__)


def enabled():
    return web_push.configured() is not None


def public_key():
    found = web_push.configured()
    return found[1] if found else None


def prepare(db, recipient_ids, kind, sender, room_id, video=False):
    """[(subscription, payload)] for these members, or [] when push is off. `sender` is a User."""
    from ruqqus.classes import User, UserBlock, UserMute
    from ruqqus.helpers.visibility import user_hidden
    if not enabled():
        return []
    ids = {int(u) for u in recipient_ids if u and int(u) != sender.id}
    if not ids:
        return []
    subs = push_store.of_users(db, ids)
    if not subs:
        return []
    ids = set(subs)
    blocked = set()
    for a, b in db.query(UserBlock.user_id, UserBlock.target_id).filter(
            or_(UserBlock.user_id == sender.id, UserBlock.target_id == sender.id)).all():
        blocked.add(b if a == sender.id else a)
    muted = {u for (u,) in db.query(UserMute.user_id).filter(UserMute.target_id == sender.id, UserMute.user_id.in_(list(ids))).all()}
    from flask import current_app
    site = current_app.config["SITE_NAME"]
    jobs = []
    for user in db.query(User).filter(User.id.in_(list(ids - blocked - muted))).all():
        if user.is_deleted:
            continue
        name = None if sender.is_deleted else (sender.username if user_hidden(sender, user) else sender.shown_name)
        with force_locale(locale_for_user(user)):
            body = push_rules.wording(kind, name, user.push_names is not False, video=video)
        payload = push_rules.payload(kind, body, site, room_id)
        jobs.extend((sub, payload) for sub in subs.get(user.id, []))
    return jobs


def send_later(jobs, urgency="normal"):
    """Sends them after this request, best effort: nothing here can fail the action that caused it."""
    if not jobs:
        return
    try:
        import gevent
        from flask import current_app
        # who sent it, as the push services ask (VAPID's subject): the site's own address, or VAPID_SUBJECT (a mailto:)
        subject = os.environ.get("VAPID_SUBJECT") or f"https://{current_app.config.get('SERVER_NAME') or 'localhost'}"
        gevent.spawn(_send, list(jobs), urgency, subject)
    except Exception:
        log.warning("push: could not start sending", exc_info=True)


def _send(jobs, urgency, subject, post=None, session=None):
    found = web_push.configured()
    if not found:
        return
    key, public = found
    outcomes = [(sub["id"], web_push.deliver(sub, payload, key, public, subject, urgency, post=post)) for sub, payload in jobs]
    try:
        if session is None:
            from ruqqus.__main__ import db_session
            session = db_session()
        now = int(time.time())
        for sub_id, outcome in outcomes:
            push_store.note(session, sub_id, outcome, now)
        session.commit()
    except Exception:
        log.warning("push: could not note what the push services said", exc_info=True)
    return outcomes
