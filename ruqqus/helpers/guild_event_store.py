"""Guild events in the database (rules in helpers/guild_events.py): `guild_events`, `guild_event_rsvps`. Plain `text()`
SQL with bound values only, tested on SQLite (tests/test_guild_events.py). Every function that reads or changes one
event takes the guild's id, so another guild's event is never reached. **Nothing here returns who answered an event**
except `reminder_user_ids` (the reminder job alone) and `answer_of` (the reader's own answer). Nothing here commits.
"""
from sqlalchemy import text

_EVENT = ("id, board_id, creator_id, title, details, details_html, word_severity, starts_utc, ends_utc, location_url, "
          "location_text, space_post_id, cancelled_utc, created_utc, reminded_utc")
FIELDS = ("title", "details", "details_html", "word_severity", "starts_utc", "ends_utc", "location_url",
          "location_text", "space_post_id")


def create(db, board_id, creator_id, fields, now):
    values = {k: fields[k] for k in FIELDS}
    db.execute(text("""INSERT INTO guild_events (board_id, creator_id, title, details, details_html, word_severity,
                         starts_utc, ends_utc, location_url, location_text, space_post_id, cancelled_utc, created_utc,
                         reminded_utc)
                       VALUES (:b, :c, :title, :details, :details_html, :word_severity, :starts_utc, :ends_utc,
                         :location_url, :location_text, :space_post_id, 0, :now, 0)"""),
               dict(values, b=board_id, c=creator_id, now=now))
    return db.execute(text("SELECT MAX(id) FROM guild_events WHERE board_id = :b AND creator_id = :c"),
                      {"b": board_id, "c": creator_id}).scalar()


def edit(db, board_id, event_id, fields):
    """Changes an event. A new start lets its reminder go out again."""
    values = {k: fields[k] for k in FIELDS}
    db.execute(text("""UPDATE guild_events SET title = :title, details = :details, details_html = :details_html,
                         word_severity = :word_severity, location_url = :location_url, location_text = :location_text,
                         space_post_id = :space_post_id, ends_utc = :ends_utc,
                         reminded_utc = CASE WHEN starts_utc = :starts_utc THEN reminded_utc ELSE 0 END,
                         starts_utc = :starts_utc
                       WHERE id = :e AND board_id = :b"""),
               dict(values, e=event_id, b=board_id))


def cancel(db, board_id, event_id, now):
    db.execute(text("UPDATE guild_events SET cancelled_utc = :now WHERE id = :e AND board_id = :b AND cancelled_utc = 0"),
               {"now": now, "e": event_id, "b": board_id})


def event(db, board_id, event_id):
    return db.execute(text(f"SELECT {_EVENT} FROM guild_events WHERE id = :e AND board_id = :b"),
                      {"e": event_id, "b": board_id}).fetchone()


def upcoming(db, board_id, now, limit=50):
    """Events not over yet (cancelled ones too, marked), soonest first."""
    return db.execute(text(f"""SELECT {_EVENT} FROM guild_events WHERE board_id = :b AND ends_utc >= :now
                               ORDER BY starts_utc, id LIMIT :n"""), {"b": board_id, "now": now, "n": limit}).fetchall()


def past(db, board_id, now, limit=10):
    return db.execute(text(f"""SELECT {_EVENT} FROM guild_events WHERE board_id = :b AND ends_utc < :now
                               ORDER BY starts_utc DESC, id DESC LIMIT :n"""), {"b": board_id, "now": now, "n": limit}).fetchall()


def between(db, board_id, start, end):
    """Events that overlap [start, end), for a month view."""
    return db.execute(text(f"""SELECT {_EVENT} FROM guild_events WHERE board_id = :b AND starts_utc < :end AND ends_utc >= :start
                               ORDER BY starts_utc, id LIMIT 500"""), {"b": board_id, "start": start, "end": end}).fetchall()


def next_event(db, board_id, now):
    """The next event not cancelled and not over, or None."""
    return db.execute(text(f"""SELECT {_EVENT} FROM guild_events WHERE board_id = :b AND ends_utc >= :now AND cancelled_utc = 0
                               ORDER BY starts_utc, id LIMIT 1"""), {"b": board_id, "now": now}).fetchone()


def made_since(db, board_id, since):
    return db.execute(text("SELECT COUNT(*) FROM guild_events WHERE board_id = :b AND created_utc >= :since"),
                      {"b": board_id, "since": since}).scalar() or 0


# --- answers: counts, and the reader's own -------------------------------------------------------------------------

def answer(db, event_id, user_id, status, now):
    """Sets (or with `status` None takes back) one person's answer."""
    db.execute(text("DELETE FROM guild_event_rsvps WHERE event_id = :e AND user_id = :u"), {"e": event_id, "u": user_id})
    if status:
        db.execute(text("INSERT INTO guild_event_rsvps (event_id, user_id, status, created_utc) VALUES (:e, :u, :s, :now)"),
                   {"e": event_id, "u": user_id, "s": status, "now": now})


def answer_of(db, event_id, user_id):
    if not user_id:
        return None
    return db.execute(text("SELECT status FROM guild_event_rsvps WHERE event_id = :e AND user_id = :u"),
                      {"e": event_id, "u": user_id}).scalar()


def counts(db, event_ids):
    """{event id: {"going": n, "interested": n}} — numbers only."""
    ids = [int(i) for i in event_ids]
    out = {i: {"going": 0, "interested": 0} for i in ids}
    if not ids:
        return out
    params = {f"e{n}": i for n, i in enumerate(ids)}
    marks = ", ".join(f":e{n}" for n in range(len(ids)))
    for event_id, status, n in db.execute(text(f"""SELECT event_id, status, COUNT(*) FROM guild_event_rsvps
                                                   WHERE event_id IN ({marks}) GROUP BY event_id, status"""), params):
        if status in out[event_id]:
            out[event_id][status] = n
    return out


# --- reminders -------------------------------------------------------------------------------------------------------

def due_reminders(db, now, before, limit=50):
    """Events starting within `before` seconds, not cancelled, not reminded."""
    return db.execute(text(f"""SELECT {_EVENT} FROM guild_events WHERE cancelled_utc = 0 AND reminded_utc = 0
                               AND starts_utc > :now AND starts_utc <= :until ORDER BY starts_utc LIMIT :n"""),
                      {"now": now, "until": now + before, "n": limit}).fetchall()


def claim_reminder(db, event_id, now):
    """Marks an event reminded; True only for the one caller that did (so nobody is told twice)."""
    result = db.execute(text("UPDATE guild_events SET reminded_utc = :now WHERE id = :e AND reminded_utc = 0"),
                        {"now": now, "e": event_id})
    return result.rowcount == 1


def reminder_user_ids(db, event_id, limit=5000):
    """Who answered an event, for its reminder only. Never drawn, never sent anywhere but the notification job."""
    return [row[0] for row in db.execute(text("""SELECT user_id FROM guild_event_rsvps WHERE event_id = :e
                                                 ORDER BY user_id LIMIT :n"""), {"e": event_id, "n": limit})]
