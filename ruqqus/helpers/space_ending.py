"""Ending a Space, from the routes (the host, an admin, lazily on a read) and from the background cycle
(scripts/recomputes.py `end_due`), so a board or a live thread ends at its time even when nobody opens it.

**A Space that is not recorded leaves nothing** (the owner's rule): its backing row and every guild copy are taken
down **without a history row** (it is not the host deleting it, so nothing appears in the public Deletion log), with
its reminders, its board (a reported message waits for the admins) and its live updates; the comments of a live thread
go with its row (they are only ever reached through it). A recorded Space keeps everything, read-only.

Plain SQL through the stores; nothing here commits.
"""
from sqlalchemy import text

from ruqqus.helpers import call_store, live_bus, space_store
from ruqqus.helpers import live_thread_store as live_store
from ruqqus.helpers import spaces as rules


def topic(space_id):
    return live_bus.topic_channel(f"space:{int(space_id)}")


def take_down(db, space_id, post_id, now):
    """A not-recorded Space disappears: its row and copies (no history row), reminders, board and updates."""
    db.execute(text("""UPDATE submissions SET deleted_utc = :now
                       WHERE (id = :p OR repost_id = :p) AND deleted_utc = 0"""), {"p": post_id, "now": now})
    # a live thread's comments go with it (they would still be listed on their writers' profiles)
    db.execute(text("""UPDATE comments SET deleted_utc = :now WHERE deleted_utc = 0 AND parent_submission IN
                       (SELECT id FROM submissions WHERE id = :p OR repost_id = :p)"""), {"p": post_id, "now": now})
    space_store.clear_reminders(db, space_id)
    space_store.clear_board(db, space_id)
    live_store.erase(db, post_id, now)


def end(db, r, row, now, by_admin=False):
    """Ends a Space (a row of space_store), once. True when this call ended it."""
    space_id, post_id, record = row[0], row[1], bool(row[7])
    if not space_store.end(db, space_id, now, by_admin=by_admin):
        return False
    if r is not None:
        call_store.end(r, space_store.room(space_id))
    live_store.end(db, post_id, now)                     # its live updates end with it
    live_bus.publish(r, topic(space_id), "space.ended", {"by_admin": by_admin})
    if not record:
        take_down(db, space_id, post_id, now)
    return True


def end_due(db, r, now):
    """Every live Space that has run its time (space_store.due): how many were ended."""
    ended = 0
    for row in space_store.due(db, now):
        if rules.is_due(row[space_store.KIND], row[3], row[5], row[space_store.ENDS], now) and end(db, r, row, now):
            ended += 1
    return ended
