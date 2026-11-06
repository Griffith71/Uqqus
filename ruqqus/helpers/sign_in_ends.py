"""Sign-ins that ended stay ended (chat plan, phase 2, step 1).

The site's cookie is Flask's signed cookie, refreshed on every answer, and a plain Log out never raised the account's
nonce: an answer of the page's own still on its way when `/logout` answered wrote the old cookie back, signed in, with
nothing said ("a log out can be undone", CLAUDE.md). This is a deny-list of ended sign-ins, by a SHA-256 of the cookie's
random `session_id` (never the id itself): Log out, a sign-in over an older one in the same browser, the QR sign-in's
password (a copy taken before it is dead) and closing your own account write one. Two checks read it:

* `wrappers.get_logged_in_user` refuses a cookie whose sign-in ended (the browser becomes a visitor);
* `__main__.after_request` writes no cookie for an answer whose sign-in ended while it ran (`g.skip_session_cookie`).

A deny-list needs nothing from the places that make sessions (the scheduler, the bots, the tests): a fresh random id is
never on it. Postgres and not Redis: a flush must not bring a sign-in back. Rows are kept `KEEP_DAYS` (helpers/retention.py
`ENDED_SIGN_IN_DAYS`), longer than the 365 days a cookie written back can last. Plain `text()` SQL, tested on SQLite (tests/test_sign_in_ends.py).
"""

import hashlib
import logging

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

KEEP_DAYS = 400
QUIET_SECONDS = 60          # how long after an end a refused cookie's answer writes no cookie (`refuse`)
REASONS = ("logout", "replaced", "qr_upgrade", "deleted")

_warned = []


def _missing_table(error):
    """The database saying the table doesn't exist (Postgres 42P01, or SQLite's words in the unit tests)."""
    original = getattr(error, "orig", None)
    if getattr(original, "pgcode", None) == "42P01":
        return True
    return "no such table" in str(original or error).lower()


def _warn_missing():
    if not _warned:
        _warned.append(True)
        logging.error("ended_sign_ins is missing: apply scripts/migrations/2026-12-19_ended_sign_ins.sql")


def _id(session_id):
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()


def end(db, session_id, user_id, reason, now):
    """Record that the sign-in with this cookie `session_id` has ended. Nothing without an id; writing it twice is fine. Runs in
    the caller's transaction, which the request's commit makes durable. A database that hasn't reached the migration yet keeps no
    row (logged once), so Log out, the QR password and closing an account still work while a deploy applies it."""
    if not session_id or not user_id:
        return
    try:
        with db.begin_nested():
            db.execute(text("""INSERT INTO ended_sign_ins (id, user_id, ended_utc, reason) VALUES (:id, :u, :t, :r)
                               ON CONFLICT (id) DO NOTHING"""),
                       {"id": _id(session_id), "u": int(user_id), "t": int(now), "r": reason[:16]})
    except DBAPIError as e:
        if not _missing_table(e):
            raise
        _warn_missing()             # Log out and the rest still work while a deploy applies the migration; this end isn't kept


def ended_at(db, session_id):
    """When the sign-in with this cookie `session_id` ended, or None if it hasn't. One primary-key lookup in a savepoint. A database
    that hasn't reached the migration yet answers None (logged once): the site must go on working while a deploy applies it. Any
    other error is raised, so the request fails rather than letting an ended sign-in through."""
    if not session_id:
        return None
    try:
        with db.begin_nested():
            row = db.execute(text("SELECT ended_utc FROM ended_sign_ins WHERE id = :id"), {"id": _id(session_id)}).first()
    except DBAPIError as e:
        if not _missing_table(e):
            raise
        _warn_missing()
        return None
    return int(row[0]) if row else None


def ended(db, session_id):
    """Has the sign-in with this cookie `session_id` ended? (`ended_at`)"""
    return ended_at(db, session_id) is not None


def refuse(session, db, now, flags):
    """Called once at the start of a request that opened the database (`__main__.before_request`), and again by
    `wrappers.get_logged_in_user`: a cookie whose sign-in ended is a visitor's, whichever route it asks. Returns whether it was.
    The sign-in leaves the session. For `QUIET_SECONDS` after the end the answer writes no cookie of its own (`flags`, Flask's `g`,
    gets `ended_sign_in_refused`, which `__main__._SessionInterface` reads): the browser may already hold the sign-in that replaced it
    (the QR password pressed twice, another tab's request), which a visitor's cookie arriving last would overwrite. A route that
    changes the session after this still writes it, without the sign-in. Later, the answer writes the visitor's cookie, so the
    browser stops sending the ended one."""
    sid = session.get("session_id")
    if not (session.get("user_id") and sid):
        return False
    at = ended_at(db, sid)
    if at is None:
        return False
    from ruqqus.helpers import session_cookie
    session_cookie.forget_sign_in(session)
    if now - at < QUIET_SECONDS:
        session.modified = False
        flags.ended_sign_in_refused = True
    return True
