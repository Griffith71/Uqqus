"""Disappearing messages (CLAUDE.md "Chat extras"): while a 1:1 chat's or a group's timer is on, the appservice webhook
writes each new event's id and when it is due (`note`: never what it says, which the site can't read), and a step of
scripts/recomputes.py has the bot delete it when due (`run_due`, a Matrix redaction: every app then drops it, and
Synapse purges a redacted message's content a day later, `redaction_retention_period: 1d`). A chat file sent while the
timer was on stops being served at the same time (`media_assets.expires_utc`). Plain `text()` SQL, tested on SQLite."""
import logging

from sqlalchemy import text

log = logging.getLogger(__name__)

BATCH = 200                 # redactions a cycle at most
GIVE_UP = 50                # a redaction that failed this many times is dropped (the room or event is gone)
FILE_GRACE = 600            # a file's address outlives its message by this long: the message is sent just after the upload


def note(db, room_id, event_id, sent_utc, seconds):
    """Due `seconds` after it was sent. Safe to call twice (the webhook may see a transaction again)."""
    if not seconds or not event_id:
        return
    exists = db.execute(text("SELECT 1 FROM chat_expiring WHERE room_id = :r AND event_id = :e"), {"r": room_id, "e": event_id}).first()
    if exists is None:
        db.execute(text("INSERT INTO chat_expiring (room_id, event_id, expires_utc, tries) VALUES (:r, :e, :t, 0)"),
                   {"r": room_id, "e": event_id, "t": int(sent_utc) + int(seconds)})


def file_expiry(now, seconds):
    return int(now) + int(seconds) + FILE_GRACE if seconds else 0


def due(db, now, limit=BATCH):
    return [tuple(r) for r in db.execute(text("""SELECT id, room_id, event_id FROM chat_expiring WHERE expires_utc <= :n
                                                 ORDER BY expires_utc, id LIMIT :l"""), {"n": int(now), "l": int(limit)}).fetchall()]


def run_due(db, now, redact):
    """Deletes what is due through `redact(room_id, event_id)` (the bot), commits per batch. Answers (deleted, failed)."""
    done = failed = 0
    for row_id, room_id, event_id in due(db, now):
        try:
            redact(room_id, event_id)
        except Exception:
            failed += 1
            db.execute(text("UPDATE chat_expiring SET tries = tries + 1 WHERE id = :i"), {"i": row_id})
            db.execute(text("DELETE FROM chat_expiring WHERE id = :i AND tries >= :n"), {"i": row_id, "n": GIVE_UP})
            continue
        db.execute(text("DELETE FROM chat_expiring WHERE id = :i"), {"i": row_id})
        done += 1
    expired = db.execute(text("""UPDATE media_assets SET status = 'removed', updated_utc = :n
                                 WHERE kind = 'chat' AND status IN ('ready', 'pending') AND expires_utc > 0 AND expires_utc <= :n"""),
                         {"n": int(now)}).rowcount
    db.commit()
    if done or failed or expired:
        log.info("disappearing messages: %s deleted, %s to try again, %s files expired", done, failed, expired)
    return done, failed


def forget_room(db, room_id):
    """A room that is gone (a group closed) has nothing left to delete."""
    db.execute(text("DELETE FROM chat_expiring WHERE room_id = :r"), {"r": room_id})
