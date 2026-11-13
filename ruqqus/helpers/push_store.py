"""Push subscriptions in the database (`push_subscriptions`: one per browser a member turned notifications on in). Plain
`text()` SQL with bound values, tested on SQLite. Nothing here commits."""
from sqlalchemy import text

MAX_PER_USER = 10                # browsers a member may have notifications on in (the oldest goes)
MAX_FAILURES = 10                # a subscription whose push service failed this many times in a row is dropped


def save(db, user_id, endpoint, p256dh, auth, now):
    """This browser's subscription, for this member: an endpoint already known (the same browser, or one another account
    used here) is moved to them, so a shared browser tells only whoever turned it on last."""
    db.execute(text("DELETE FROM push_subscriptions WHERE endpoint = :e"), {"e": endpoint})
    db.execute(text("""INSERT INTO push_subscriptions (user_id, endpoint, p256dh, auth, created_utc, last_ok_utc, failures)
                       VALUES (:u, :e, :p, :a, :now, 0, 0)"""), {"u": user_id, "e": endpoint, "p": p256dh, "a": auth, "now": now})
    extra = db.execute(text("""SELECT id FROM push_subscriptions WHERE user_id = :u ORDER BY created_utc DESC, id DESC
                               LIMIT 1000 OFFSET :n"""), {"u": user_id, "n": MAX_PER_USER}).fetchall()
    for (sid,) in extra:
        db.execute(text("DELETE FROM push_subscriptions WHERE id = :i"), {"i": sid})


def remove(db, user_id, endpoint):
    return db.execute(text("DELETE FROM push_subscriptions WHERE user_id = :u AND endpoint = :e"),
                      {"u": user_id, "e": endpoint}).rowcount


def remove_all(db, user_id):
    db.execute(text("DELETE FROM push_subscriptions WHERE user_id = :u"), {"u": user_id})


def has(db, user_id, endpoint):
    return db.execute(text("SELECT 1 FROM push_subscriptions WHERE user_id = :u AND endpoint = :e"),
                      {"u": user_id, "e": endpoint}).first() is not None


def count(db, user_id):
    return db.execute(text("SELECT COUNT(*) FROM push_subscriptions WHERE user_id = :u"), {"u": user_id}).scalar() or 0


def of_users(db, user_ids):
    """{user id: [{id, endpoint, p256dh, auth}]} for these members."""
    ids = list({int(u) for u in user_ids})
    if not ids:
        return {}
    out = {}
    for row in db.execute(text("SELECT id, user_id, endpoint, p256dh, auth FROM push_subscriptions WHERE user_id IN ("
                               + ",".join(str(i) for i in ids) + ")")).fetchall():
        out.setdefault(row[1], []).append({"id": row[0], "endpoint": row[2], "p256dh": row[3], "auth": row[4]})
    return out


def note(db, sub_id, outcome, now):
    """What the push service said: kept on a success, a failure counted (dropped after MAX_FAILURES in a row), dropped
    when it says the browser unsubscribed."""
    if outcome == "sent":
        db.execute(text("UPDATE push_subscriptions SET last_ok_utc = :now, failures = 0 WHERE id = :i"), {"i": sub_id, "now": now})
    elif outcome == "gone":
        db.execute(text("DELETE FROM push_subscriptions WHERE id = :i"), {"i": sub_id})
    else:
        db.execute(text("UPDATE push_subscriptions SET failures = failures + 1 WHERE id = :i"), {"i": sub_id})
        db.execute(text("DELETE FROM push_subscriptions WHERE id = :i AND failures >= :n"), {"i": sub_id, "n": MAX_FAILURES})
