"""Bot accounts, the database side (rules are in helpers/bots.py).

Plain SQL (`sqlalchemy.text`) so the same statements run on SQLite in the tests (tests/test_bot_store.py). Nothing
here commits: the caller does. Tables `bots`, `bot_sources`, `bot_seen` (scripts/migrations/2026-10-15_bots.sql);
`bot_id` in the source tables is the bot's account id (`bots.user_id`).
"""
import json
import time

from sqlalchemy import text

from ruqqus.helpers import bots

BOT_COLUMNS = ("id, user_id, owner_id, username, status, purpose, forward_guilds, created_utc, reviewed_by_id, "
               "reviewed_utc, review_note, paused_reason, paused_by_admin, day, posts_today")
SOURCE_COLUMNS = ("id, bot_id, kind, url, title, status, added_utc, approved_by_id, approved_utc, check_minutes, "
                  "last_checked_utc, next_check_utc, etag, last_modified, fail_count, last_error, posted_count, read_once")


def _now(now):
    return int(now if now is not None else time.time())


def guilds_of(row):
    """The guild names a bot forwards to, from its row (never more than the rules allow)."""
    try:
        names = json.loads(row.forward_guilds or "[]")
    except (TypeError, ValueError):
        return []
    return [n for n in names if isinstance(n, str)][:bots.MAX_FORWARD_GUILDS]


# ------------------------------------------------------------------ bots

def bot_of(db, user_id):
    return db.execute(text(f"SELECT {BOT_COLUMNS} FROM bots WHERE user_id = :u"), {"u": user_id}).fetchone()


def bot_named(db, username):
    return db.execute(text(f"SELECT {BOT_COLUMNS} FROM bots WHERE lower(username) = lower(:n) ORDER BY id DESC"),
                      {"n": username}).fetchone()


def bots_of(db, owner_id):
    """A member's bots, newest first, rejected ones included (they show the admin's note)."""
    return db.execute(text(f"SELECT {BOT_COLUMNS} FROM bots WHERE owner_id = :o ORDER BY created_utc DESC, id DESC"),
                      {"o": owner_id}).fetchall()


def active_count(db, owner_id):
    """How many bots count towards the owner's limit: every one that was not rejected."""
    return db.execute(text("SELECT COUNT(*) FROM bots WHERE owner_id = :o AND status != :r"),
                      {"o": owner_id, "r": bots.REJECTED}).scalar() or 0


def add_bot(db, user_id, owner_id, username, purpose, guild_names, now=None):
    db.execute(text("INSERT INTO bots (user_id, owner_id, username, status, purpose, forward_guilds, created_utc) "
                    "VALUES (:u, :o, :n, :s, :p, :g, :t)"),
               {"u": user_id, "o": owner_id, "n": username, "s": bots.PENDING, "p": purpose,
                "g": json.dumps(list(guild_names)), "t": _now(now)})
    return bot_of(db, user_id)


def set_forward_guilds(db, user_id, guild_names):
    db.execute(text("UPDATE bots SET forward_guilds = :g WHERE user_id = :u"),
               {"g": json.dumps(list(guild_names)), "u": user_id})


def review_bot(db, user_id, status, by_id, note="", now=None):
    """An admin's decision on a waiting bot (approved or rejected). Approving it approves the sources it was applied
    with; rejecting rejects them. False when the bot was not waiting (a decision is made once)."""
    if status not in (bots.APPROVED, bots.REJECTED):
        raise ValueError(status)
    t = _now(now)
    done = db.execute(text("UPDATE bots SET status = :s, reviewed_by_id = :b, reviewed_utc = :t, review_note = :n "
                           "WHERE user_id = :u AND status = :p"),
                      {"s": status, "b": by_id, "t": t, "n": note[:bots.NOTE_MAX], "u": user_id,
                       "p": bots.PENDING}).rowcount == 1
    if done:
        db.execute(text("UPDATE bot_sources SET status = :s, approved_by_id = :b, approved_utc = :t "
                        "WHERE bot_id = :u AND status = :p"),
                   {"s": status, "b": by_id, "t": t, "u": user_id, "p": bots.PENDING})
    return done


def set_running(db, user_id, running, reason="", by_admin=False):
    """Pause a running bot (with the reason its owner is shown) or resume a paused one. A bot an admin paused is
    resumed by an admin only (`by_admin`). False when nothing changed."""
    if running:
        only_own = "" if by_admin else " AND paused_by_admin = :no"
        return db.execute(text("UPDATE bots SET status = :a, paused_reason = '', paused_by_admin = :no "
                               f"WHERE user_id = :u AND status = :p{only_own}"),
                          {"a": bots.APPROVED, "u": user_id, "p": bots.PAUSED, "no": False}).rowcount == 1
    return db.execute(text("UPDATE bots SET status = :p, paused_reason = :r, paused_by_admin = :b "
                           "WHERE user_id = :u AND status = :a"),
                      {"a": bots.APPROVED, "p": bots.PAUSED, "r": reason[:300], "b": bool(by_admin),
                       "u": user_id}).rowcount == 1


def forget_bot(db, user_id):
    """Everything about a bot except its account (the caller deals with that)."""
    ids = [r[0] for r in db.execute(text("SELECT id FROM bot_sources WHERE bot_id = :u"), {"u": user_id}).fetchall()]
    for source_id in ids:
        db.execute(text("DELETE FROM bot_seen WHERE source_id = :s"), {"s": source_id})
    db.execute(text("DELETE FROM bot_sources WHERE bot_id = :u"), {"u": user_id})
    db.execute(text("DELETE FROM bots WHERE user_id = :u"), {"u": user_id})


def posts_today(db, user_id, day):
    row = db.execute(text("SELECT day, posts_today FROM bots WHERE user_id = :u"), {"u": user_id}).fetchone()
    return row[1] if row and row[0] == day else 0


def count_post(db, user_id, day):
    """One more post today (the count starts again on a new UTC day)."""
    db.execute(text("UPDATE bots SET posts_today = CASE WHEN day = :d THEN posts_today + 1 ELSE 1 END, day = :d "
                    "WHERE user_id = :u"), {"d": day, "u": user_id})


# ------------------------------------------------------------------ sources

def sources_of(db, bot_id):
    return db.execute(text(f"SELECT {SOURCE_COLUMNS} FROM bot_sources WHERE bot_id = :b ORDER BY id"),
                      {"b": bot_id}).fetchall()


def source(db, source_id):
    return db.execute(text(f"SELECT {SOURCE_COLUMNS} FROM bot_sources WHERE id = :s"), {"s": source_id}).fetchone()


def source_count(db, bot_id):
    return db.execute(text("SELECT COUNT(*) FROM bot_sources WHERE bot_id = :b"), {"b": bot_id}).scalar() or 0


def add_source(db, bot_id, url, now=None, check_minutes=bots.CHECK_MINUTES_DEFAULT):
    """A new source, waiting for an admin. None when the bot already reads that address."""
    done = db.execute(text("INSERT INTO bot_sources (bot_id, kind, url, status, added_utc, check_minutes) "
                           "VALUES (:b, 'feed', :u, :s, :t, :m) ON CONFLICT DO NOTHING"),
                      {"b": bot_id, "u": url, "s": bots.PENDING, "t": _now(now), "m": check_minutes}).rowcount == 1
    if not done:
        return None
    return db.execute(text("SELECT id FROM bot_sources WHERE bot_id = :b AND url = :u"), {"b": bot_id, "u": url}).scalar()


def remove_source(db, bot_id, source_id):
    """Removing takes effect at once and needs nobody's approval. False when it is not this bot's source."""
    if not db.execute(text("SELECT 1 FROM bot_sources WHERE id = :s AND bot_id = :b"),
                      {"s": source_id, "b": bot_id}).fetchone():
        return False
    db.execute(text("DELETE FROM bot_seen WHERE source_id = :s"), {"s": source_id})
    db.execute(text("DELETE FROM bot_sources WHERE id = :s"), {"s": source_id})
    return True


def review_source(db, source_id, status, by_id, now=None):
    """An admin approves or rejects a source added to a bot after it was approved. False when it was not waiting."""
    if status not in (bots.APPROVED, bots.REJECTED):
        raise ValueError(status)
    return db.execute(text("UPDATE bot_sources SET status = :s, approved_by_id = :b, approved_utc = :t "
                           "WHERE id = :i AND status = :p"),
                      {"s": status, "b": by_id, "t": _now(now), "i": source_id, "p": bots.PENDING}).rowcount == 1


def set_source_paused(db, source_id, paused, error=""):
    """Pause a source (after too many failures in a row) or start it again (its failures forgotten)."""
    if paused:
        db.execute(text("UPDATE bot_sources SET status = :p, last_error = :e WHERE id = :i AND status = :a"),
                   {"p": bots.PAUSED, "a": bots.APPROVED, "e": error[:300], "i": source_id})
    else:
        db.execute(text("UPDATE bot_sources SET status = :a, fail_count = 0, next_check_utc = 0 "
                        "WHERE id = :i AND status = :p"), {"p": bots.PAUSED, "a": bots.APPROVED, "i": source_id})


# ------------------------------------------------------------------ the admins' queue

def pending_bots(db, limit=50):
    """Bots waiting for a decision, oldest first, with their owner's name."""
    return db.execute(text(f"""
        SELECT b.user_id, b.username, b.owner_id, o.username AS owner_name, b.purpose, b.forward_guilds, b.created_utc
        FROM bots b JOIN users o ON o.id = b.owner_id
        WHERE b.status = :p ORDER BY b.created_utc, b.id LIMIT :l"""), {"p": bots.PENDING, "l": limit}).fetchall()


def pending_sources(db, limit=100):
    """Sources added to bots that were already approved (or paused), waiting for a decision."""
    return db.execute(text("""
        SELECT s.id, s.url, s.added_utc, b.user_id, b.username, o.username AS owner_name
        FROM bot_sources s JOIN bots b ON b.user_id = s.bot_id JOIN users o ON o.id = b.owner_id
        WHERE s.status = :p AND b.status IN (:a, :z) ORDER BY s.added_utc, s.id LIMIT :l"""),
        {"p": bots.PENDING, "a": bots.APPROVED, "z": bots.PAUSED, "l": limit}).fetchall()


def running_bots(db, limit=200):
    """Approved and paused bots, paused first, with their owner's name and their sources' newest error."""
    return db.execute(text("""
        SELECT b.user_id, b.username, b.status, b.paused_reason, b.posts_today, b.day, o.username AS owner_name,
               (SELECT MAX(s.last_checked_utc) FROM bot_sources s WHERE s.bot_id = b.user_id) AS last_checked,
               (SELECT COUNT(*) FROM bot_sources s WHERE s.bot_id = b.user_id AND s.last_error != '') AS failing
        FROM bots b JOIN users o ON o.id = b.owner_id
        WHERE b.status IN (:a, :z)
        ORDER BY CASE WHEN b.status = :z THEN 0 ELSE 1 END, b.username LIMIT :l"""),
        {"a": bots.APPROVED, "z": bots.PAUSED, "l": limit}).fetchall()


def waiting_count(db):
    """What waits for an admin: bots and sources together (the count beside Admin > Bots)."""
    return (db.execute(text("SELECT COUNT(*) FROM bots WHERE status = :p"), {"p": bots.PENDING}).scalar() or 0) + \
        len(pending_sources(db))


# ------------------------------------------------------------------ the job (scripts/run_bots.py)

def claim_due(db, now=None, batch=20, lease=300):
    """Ids of approved sources of approved bots whose time has come, now held for `lease` seconds so another runner
    leaves them alone. FOR UPDATE SKIP LOCKED on Postgres (as scripts/publish_scheduled.py claims drafts)."""
    t = _now(now)
    lock = "" if db.get_bind().dialect.name == "sqlite" else " FOR UPDATE OF s SKIP LOCKED"
    due = [r[0] for r in db.execute(text(f"""
        SELECT s.id FROM bot_sources s JOIN bots b ON b.user_id = s.bot_id
        WHERE s.status = :a AND b.status = :a AND s.next_check_utc <= :t
        ORDER BY s.next_check_utc, s.id LIMIT :n{lock}"""), {"a": bots.APPROVED, "t": t, "n": batch}).fetchall()]
    for source_id in due:
        db.execute(text("UPDATE bot_sources SET next_check_utc = :l WHERE id = :s"), {"l": t + lease, "s": source_id})
    return due


def seen_keys(db, source_id, keys):
    keys = list(keys)
    if not keys:
        return set()
    found = set()
    for i in range(0, len(keys), 200):
        chunk = keys[i:i + 200]
        params = {f"k{n}": k for n, k in enumerate(chunk)}
        params["s"] = source_id
        marks = ", ".join(f":k{n}" for n in range(len(chunk)))
        found.update(r[0] for r in db.execute(text(
            f"SELECT item_key FROM bot_seen WHERE source_id = :s AND item_key IN ({marks})"), params).fetchall())
    return found


def mark_seen(db, source_id, key, post_id=None, now=None):
    db.execute(text("INSERT INTO bot_seen (source_id, item_key, post_id, seen_utc) VALUES (:s, :k, :p, :t) "
                    "ON CONFLICT DO NOTHING"), {"s": source_id, "k": key, "p": post_id, "t": _now(now)})


def record_check(db, source_id, now, next_check, fail_count=0, error="", title=None, etag=None, last_modified=None,
                 read_once=None, posted=0):
    """What one read of a source found. Fields left as None keep their stored value."""
    sets = ["last_checked_utc = :t", "next_check_utc = :n", "fail_count = :f", "last_error = :e",
            "posted_count = posted_count + :p"]
    params = {"t": now, "n": next_check, "f": fail_count, "e": (error or "")[:300], "p": posted, "i": source_id}
    for column, value in (("title", title), ("etag", etag), ("last_modified", last_modified), ("read_once", read_once)):
        if value is not None:
            sets.append(f"{column} = :{column}")
            params[column] = value[:200] if column == "title" else value[:300] if isinstance(value, str) else value
    db.execute(text(f"UPDATE bot_sources SET {', '.join(sets)} WHERE id = :i"), params)


def prune_seen(db, now=None):
    return db.execute(text("DELETE FROM bot_seen WHERE seen_utc < :c"),
                      {"c": _now(now) - bots.SEEN_DAYS * 86400}).rowcount
