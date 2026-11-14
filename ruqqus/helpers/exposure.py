"""Unfair exposure: trying to find out who is behind an anonymous post or comment (the owner, 2026-11-14: "if they are caught doing this, it's a bannable
offense for unfair exposure").

A guildmaster can exile or chat-mute "Anonymous #3" without learning whose it is, and every outcome answers the same (helpers/alias_actions.py). What the
site can't hide is a guess tested against a SUSPECT: exile or mute the suspect by name, and when it is the account already exiled or muted by alias the answer
is the same quiet 204 with no new row. Each such test is visible to the site, so the site notes it for the staff.

**A note is a signal, never a verdict**: a staff-only entry (`possible_probing`, written once per guildmaster, guild, account and day, with who made the alias
action) that the admins read at `/admin/log?action=possible_probing`. A guildmaster may have a good reason (they didn't know another guildmaster had acted
by alias), so nothing here punishes or tells anyone: an admin looks, and when it is probing they ban with the rule "Evading and manipulating the site", which
now says so, and a reason naming it. The note is never shown to the account it is about or to anyone below the staff.

Plain SQL, tested on SQLite (tests/test_exposure.py). Nothing here commits."""
import time

from sqlalchemy import text

from ruqqus.helpers import action_log

ACTION = "possible_probing"
DAY = 86400

# what was done, in the words the staff read (never drawn to the public)
EXILED_BY_NAME = "exiled by name an account that is exiled by alias"
MUTED_BY_NAME = "muted by name an account that is muted by alias"
ALIAS_AFTER_EXILE = "acted by alias on an account they had exiled by name"
ALIAS_AFTER_MUTE = "acted by alias on an account they had muted by name"


def note(db, board_id, actor_id, account_id, how, by_id=None, now=None):
    """The staff-only entry for one guildmaster's test of a guess, or None when this guildmaster already has one for this account in this guild today (so a
    run of the same test is one line). `by_id` is the guildmaster who made the alias action, when it was someone else. Best effort: the caller's action
    goes ahead whatever happens here."""
    if not (board_id and actor_id and account_id):
        return None
    now = int(now if now is not None else time.time())
    savepoint = db.begin_nested()
    try:
        seen = db.execute(text("SELECT 1 FROM log_entries WHERE action = :a AND actor_id = :u AND board_id = :b AND target_user_id = :t AND created_utc > :since LIMIT 1"),
                          {"a": ACTION, "u": int(actor_id), "b": int(board_id), "t": int(account_id), "since": now - DAY}).first()
        if seen:
            savepoint.rollback()
            return None
        who = f"@{_username(db, actor_id)}"
        by = f", the alias action was made by @{_username(db, by_id)}" if by_id and by_id != actor_id else ""
        made = action_log.record_quietly(db, ACTION, actor_id, role=action_log.GUILDMASTER, board_id=int(board_id), target_user_id=int(account_id),
                                         staff_note=f"{who} {how}{by}: a guess tested against a suspect, or a coincidence", floor=action_log.STAFF,
                                         created_utc=now)
        savepoint.commit()
        return made
    except Exception:
        savepoint.rollback()
        return None


def _username(db, user_id):
    row = db.execute(text("SELECT username FROM users WHERE id = :i"), {"i": int(user_id)}).first() if user_id else None
    return row[0] if row else str(user_id)


def recent(db, since_days=30, limit=100, now=None):
    """The notes of the last `since_days`, newest first: `[{id, created_utc, actor_id, board_id, target_user_id, staff_note}]`, for the staff page."""
    now = int(now if now is not None else time.time())
    rows = db.execute(text("SELECT id, created_utc, actor_id, board_id, target_user_id, staff_note FROM log_entries WHERE action = :a AND created_utc > :since "
                           "ORDER BY id DESC LIMIT :n"), {"a": ACTION, "since": now - int(since_days) * DAY, "n": int(limit)})
    return [dict(r._mapping) for r in rows]


def waiting(db, since_days=30, now=None):
    """How many notes the last `since_days` hold: the count beside the staff Log's link."""
    now = int(now if now is not None else time.time())
    return db.execute(text("SELECT COUNT(*) FROM log_entries WHERE action = :a AND created_utc > :since"),
                      {"a": ACTION, "since": now - int(since_days) * DAY}).scalar() or 0
