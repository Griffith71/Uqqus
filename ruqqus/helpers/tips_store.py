"""Tips, the database side (the rules are helpers/tips.py).

Plain SQL (`sqlalchemy.text`) so the same statements run on SQLite in the tests. Nothing here commits: the tip routes
do, once, after the coins have moved and the tip is recorded (helpers/alerts.send_notification commits on its own, so
the notification is sent only after that).

Every tip is one `award_relationships` row with its coins. A post keeps two totals: `tip_coins` (every coin tipped,
what the post shows) and `tip_boost` (what counts for its ranking: helpers/tips.py says whose tips count).
"""
import time

from sqlalchemy import bindparam, text

from ruqqus.helpers import guild_limits
from ruqqus.helpers import tips as rules

YES, NO = True, False

_RECORD = text("INSERT INTO award_relationships (user_id, submission_id, comment_id, coins, created_utc) "
               "VALUES (:u, :p, :c, :coins, :t)")

_BY_TIPPER = text("SELECT user_id, SUM(coins) FROM award_relationships WHERE submission_id = :p GROUP BY user_id")

# tippers whose tips count for the ranking: not the author, old enough, not banned or deleted
_COUNTING = text("""
    SELECT id FROM users
    WHERE id IN :ids AND id <> :author AND created_utc <= :established
      AND COALESCE(is_deleted, :no) = :no AND (is_banned = 0 OR (unban_utc > 0 AND unban_utc < :now))
""").bindparams(bindparam("ids", expanding=True))

_TOTALS = text("UPDATE submissions SET tip_coins = :coins, tip_boost = :boost WHERE id = :p")

_AUTHOR = text("SELECT is_bot, bot_owner_id FROM users WHERE id = :a")


def _now(now):
    return int(now if now is not None else time.time())


def record(db, tipper_id, coins, post_id=None, comment_id=None, now=None):
    db.execute(_RECORD, {"u": tipper_id, "p": post_id, "c": comment_id, "coins": coins, "t": _now(now)})


def counting_tippers(db, tipper_ids, author_id, now=None):
    """The tippers whose tips lift the author's post: established accounts in good standing, never the author or an
    account sharing an address with the author."""
    now = _now(now)
    if not tipper_ids:
        return set()
    ids = {row[0] for row in db.execute(_COUNTING, {"ids": list(tipper_ids), "author": author_id, "no": NO,
                                                     "established": rules.established_before(now), "now": now})}
    author_addresses = set(guild_limits.addresses_of(db, author_id, now))
    if not author_addresses:
        return ids
    return {t for t in ids if not author_addresses & set(guild_limits.addresses_of(db, t, now))}


def answerable_for(db, author_id):
    """Who stands for the author in the boost rules: the author, or the member who runs a member bot
    (helpers/tips.py `answerable_id`)."""
    row = db.execute(_AUTHOR, {"a": author_id}).fetchone()
    return rules.answerable_id(author_id, bool(row and row[0]), row[1] if row else None)


def recompute_post(db, post_id, author_id, now=None):
    """Work out a post's tip totals again from its tips and store them: (tip_coins, tip_boost)."""
    by_tipper = {int(u): int(c) for u, c in db.execute(_BY_TIPPER, {"p": post_id}).fetchall() if u is not None}
    counting = counting_tippers(db, set(by_tipper), answerable_for(db, author_id), now)
    coins = sum(by_tipper.values())
    boost = rules.post_boost({t: c for t, c in by_tipper.items() if t in counting})
    db.execute(_TOTALS, {"coins": coins, "boost": boost, "p": post_id})
    return coins, boost
