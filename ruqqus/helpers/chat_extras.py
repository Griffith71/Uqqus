"""Pins and disappearing messages in 1:1 chats and groups (CLAUDE.md "Chat extras"). Rules here, plain `text()` SQL for who
stands where in a chat (tested on SQLite); the routes are routes/chat_extras_pages.py.

Both are room state the site's bot writes, since only the bot sends room state (synapse/modules/uqqus_rules.py): the
pinned messages are Matrix's own `m.room.pinned_events` (so an outside app shows them too), the timer is
`org.uqqus.disappear`. Who may change them: either person of a 1:1 chat both have in their Inbox (a request can't be
changed), a group's owner and admins. The site never reads a message: a pin names an event id, and a disappearing
message is only an event id and a time (helpers/chat_disappear.py)."""
import re

from sqlalchemy import text

from ruqqus.helpers.i18n_runtime import tr

PINNED = "m.room.pinned_events"
DISAPPEAR = "org.uqqus.disappear"
MAX_PINS = 10
DAY = 86400
CHOICES = (0, DAY, 7 * DAY)                  # off, a day, a week (the owner's choice)
EVENT_ID = re.compile(r"^\$[A-Za-z0-9_\-+/=:.]{8,255}$")


class Refused(Exception):
    pass


def standing(db, room_id, user_id):
    """('pair', status) or ('group', role, status) for this member in this chat, or None when they aren't in it."""
    pair = db.execute(text("SELECT user_a_id, user_b_id, status FROM chat_conversations WHERE matrix_room_id = :r"), {"r": room_id}).first()
    if pair is not None:
        return ("pair", pair[2]) if user_id in (pair[0], pair[1]) else None
    row = db.execute(text("""SELECT m.role, m.status FROM chat_group_members m JOIN chat_groups g ON g.id = m.group_id
                             WHERE g.matrix_room_id = :r AND g.deleted_utc = 0 AND m.user_id = :u"""),
                     {"r": room_id, "u": user_id}).first()
    return ("group", row[0], row[1]) if row is not None else None


def change_refusal(where):
    """Why this member can't pin or set the timer here (a translated sentence), or None. `where` from `standing`."""
    if where is None:
        return None                     # the route answers a 404 first: not saying a chat exists
    if where[0] == "pair":
        return None if where[1] == "inbox" else tr("You can change this once they've accepted your message request.")
    if where[2] != "joined" or where[1] not in ("owner", "admin"):
        return tr("Only a group's owner and admins can change this.")
    return None


def pins_after(current, event_id, pin):
    """The pinned list after pinning (newest first) or unpinning this message. Raises Refused when it is full."""
    kept = [e for e in (current or []) if isinstance(e, str) and EVENT_ID.match(e) and e != event_id]
    if not pin:
        return kept
    if len(kept) >= MAX_PINS:
        raise Refused(tr("A chat can have at most %(num)s pinned messages. Unpin one first.", num=MAX_PINS))
    return [event_id] + kept


def seconds_of(raw):
    """A timer from a form field: one of CHOICES, else None."""
    try:
        value = int(str(raw or "").strip())
    except ValueError:
        return None
    return value if value in CHOICES else None


def timer_of(db, room_id):
    """The chat's timer in seconds (0: off), or None when the room is neither a 1:1 chat nor a group."""
    for table in ("chat_conversations", "chat_groups"):
        found = db.execute(text(f"SELECT disappear_seconds FROM {table} WHERE matrix_room_id = :r"), {"r": room_id}).first()
        if found is not None:
            return int(found[0] or 0)
    return None


def set_timer(db, room_id, seconds):
    for table in ("chat_conversations", "chat_groups"):
        db.execute(text(f"UPDATE {table} SET disappear_seconds = :s WHERE matrix_room_id = :r"), {"s": seconds, "r": room_id})
