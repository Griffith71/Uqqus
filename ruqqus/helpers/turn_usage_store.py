"""The relay's monthly count in the database (`turn_usage`, one row per month; rules in helpers/turn_usage.py).

Plain `text()` SQL, tested on SQLite (`tests/test_turn_usage.py`). Nothing here commits: the counting job does.
"""

from sqlalchemy import text

from ruqqus.helpers import turn_usage


def record(db, counter, now):
    """Adds what the relay sent since the last reading to this month. Returns this month's total."""
    last = db.execute(text("SELECT last_counter FROM turn_usage ORDER BY updated_utc DESC, month DESC LIMIT 1")).fetchone()
    delta = turn_usage.advance(last[0] if last else None, counter)
    month = turn_usage.month_of(now)
    db.execute(
        text("""INSERT INTO turn_usage (month, sent_bytes, last_counter, updated_utc) VALUES (:m, :d, :c, :now)
                 ON CONFLICT (month) DO UPDATE SET sent_bytes = turn_usage.sent_bytes + :d,
                                                   last_counter = :c, updated_utc = :now"""),
        {"m": month, "d": delta, "c": counter, "now": int(now)})
    return used(db, now)


def used(db, now):
    row = db.execute(text("SELECT sent_bytes FROM turn_usage WHERE month = :m"), {"m": turn_usage.month_of(now)}).fetchone()
    return int(row[0]) if row else 0


def last_reading(db):
    row = db.execute(text("SELECT MAX(updated_utc) FROM turn_usage")).fetchone()
    return int(row[0]) if row and row[0] else 0


def call_state(db, now, allowance):
    """(state, used bytes this month) for the call and Space routes."""
    spent = used(db, now)
    return turn_usage.state(spent, allowance, last_reading(db), now), spent
