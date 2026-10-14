"""The creator fund, the database side (the rules are helpers/creator_fund.py).

Plain SQL (`sqlalchemy.text`) so the same statements run on SQLite in the tests. Nothing here commits: the caller
does, so a payment that fails half way leaves nothing behind.

**A month is paid once.** `pay` starts by inserting the month's row; `month` is unique, so a second payment of the
same month inserts nothing and is refused before any coin moves. A held payout is released by one conditional
UPDATE (`... WHERE status = 'held'`), so it is paid once too. Coins are credited with one UPDATE each
(`coin_balance = coin_balance + :c`): the caller must `flush()` its own pending changes to user rows first.

Nothing here reads or knows about ads (helpers/creator_fund.py says why).
"""
import time

from sqlalchemy import bindparam, text

from ruqqus.helpers import creator_fund as rules

YES, NO = True, False


class AlreadyPaid(Exception):
    """This month has been paid."""


def _now(now):
    return int(now if now is not None else time.time())


# The month's points. `events` is every (account, member, item) the month gave: an upvote on the member's post or
# comment, or a comment on the member's post (the post is the item, so upvoting and commenting on one post is one
# point). Only live, public items; no votes or comments made through an API app (bots). `counted` keeps the
# established accounts that are not the member; `pairs` caps what one account gives one member.
_POINTS = """
WITH events AS (
    SELECT v.user_id AS engager, s.author_id AS author, 'p' || CAST(s.id AS TEXT) AS item, v.creation_ip AS ip
    FROM votes v JOIN submissions s ON s.id = v.submission_id
    WHERE v.vote_type = 1 AND v.app_id IS NULL AND v.created_utc >= :start AND v.created_utc < :end
      AND COALESCE(s.post_public, :yes) = :yes AND COALESCE(s.is_banned, :no) = :no AND s.deleted_utc = 0
    UNION
    SELECT cv.user_id, c.author_id, 'c' || CAST(c.id AS TEXT), cv.creation_ip
    FROM commentvotes cv JOIN comments c ON c.id = cv.comment_id JOIN submissions s ON s.id = c.parent_submission
    WHERE cv.vote_type = 1 AND cv.app_id IS NULL AND cv.created_utc >= :start AND cv.created_utc < :end
      AND COALESCE(c.is_banned, :no) = :no AND c.deleted_utc = 0
      AND COALESCE(s.post_public, :yes) = :yes AND COALESCE(s.is_banned, :no) = :no AND s.deleted_utc = 0
    UNION
    SELECT c.author_id, s.author_id, 'p' || CAST(s.id AS TEXT), c.creation_ip
    FROM comments c JOIN submissions s ON s.id = c.parent_submission
    WHERE c.app_id IS NULL AND c.created_utc >= :start AND c.created_utc < :end
      AND COALESCE(c.is_banned, :no) = :no AND c.deleted_utc = 0
      AND COALESCE(s.post_public, :yes) = :yes AND COALESCE(s.is_banned, :no) = :no AND s.deleted_utc = 0
),
counted AS (
    SELECT e.engager, e.author, e.item, e.ip
    FROM events e JOIN users u ON u.id = e.engager
    WHERE e.engager <> e.author AND u.created_utc <= :established AND COALESCE(u.is_deleted, :no) = :no
      AND (u.is_banned = 0 OR (u.unban_utc > 0 AND u.unban_utc < :now))
),
pairs AS (
    SELECT engager, author, COUNT(DISTINCT item) AS n FROM counted GROUP BY engager, author
),
networks AS (
    SELECT author, COUNT(DISTINCT ip) AS n FROM counted GROUP BY author
)
SELECT p.author, SUM(CASE WHEN p.n > :cap THEN :cap ELSE p.n END) AS points, COUNT(*) AS engagers,
       MAX(nw.n) AS networks
FROM pairs p JOIN networks nw ON nw.author = p.author
"""

# who may share the budget: opted in, established, not banned or deleted, no unpaid balance
_ELIGIBLE = """
JOIN users a ON a.id = p.author
WHERE COALESCE(a.creator_fund_opt_in_utc, 0) > 0 AND a.created_utc <= :established
  AND COALESCE(a.is_deleted, :no) = :no AND (a.is_banned = 0 OR (a.unban_utc > 0 AND a.unban_utc < :now))
  AND COALESCE(a.negative_balance_cents, 0) = 0
"""


def points(db, month, now=None, member=None, eligible_only=True):
    """[(member id, points, accounts, networks)] for a month, most points first. With `member`, that member's row
    only (and, with `eligible_only=False`, whether or not they may share the budget: their own page shows it)."""
    start, end = rules.month_bounds(month)
    sql = _POINTS + (_ELIGIBLE if eligible_only else "WHERE 1 = 1")
    if member is not None:
        sql += " AND p.author = :member"
    sql += " GROUP BY p.author ORDER BY points DESC, p.author"
    args = {"start": start, "end": end, "established": rules.established_before(end), "now": _now(now),
            "cap": rules.POINTS_PER_ACCOUNT, "yes": YES, "no": NO}
    if member is not None:
        args["member"] = member
    return [(int(a), int(p), int(e), int(n or 0)) for a, p, e, n in db.execute(text(sql), args).fetchall()]


def preview(db, month, budget, now=None):
    """The payout list for a month and budget: dicts with the member, their points, how many accounts and networks
    gave them, the coins they would get (0 under the minimum) and the reasons to look closer."""
    rows = points(db, month, now)
    coins = rules.split(budget, {a: p for a, p, _, _ in rows}, rules.min_points())
    names = dict(db.execute(text("SELECT id, username FROM users WHERE id IN :ids").bindparams(
        bindparam("ids", expanding=True)), {"ids": [r[0] for r in rows] or [0]}).fetchall())
    return [{"user_id": a, "username": names.get(a, "?"), "points": p, "engagers": e, "networks": n,
             "coins": coins.get(a, 0), "flags": rules.flags(p, e, n)} for a, p, e, n in rows]


def is_paid(db, month):
    return db.execute(text("SELECT 1 FROM creator_fund_months WHERE month = :m"), {"m": month}).fetchone() is not None


def pay(db, month, budget, held_ids, admin_id, now=None):
    """Pay a month: [(member id, coins, status)]. Held members get a 'held' row and no coins until `release`.
    Raises AlreadyPaid when the month was paid before (nothing is written then)."""
    now = _now(now)
    if not rules.is_over(month, now):
        raise ValueError("the month is not over")
    lines = [r for r in preview(db, month, budget, now) if r["coins"] > 0]
    held_ids = set(held_ids)
    paid = sum(r["coins"] for r in lines if r["user_id"] not in held_ids)
    inserted = db.execute(text(
        "INSERT INTO creator_fund_months (month, budget_coins, total_points, paid_coins, created_utc, paid_by_id) "
        "VALUES (:m, :b, :t, :p, :now, :admin) ON CONFLICT (month) DO NOTHING"),
        {"m": month, "b": budget, "t": sum(r["points"] for r in lines), "p": paid, "now": now, "admin": admin_id})
    if inserted.rowcount != 1:
        raise AlreadyPaid(month)
    out = []
    for r in lines:
        status = "held" if r["user_id"] in held_ids else "paid"
        db.execute(text(
            "INSERT INTO creator_fund_payouts (month, user_id, points, coins, status, created_utc, paid_utc) "
            "VALUES (:m, :u, :p, :c, :s, :now, :paid)"),
            {"m": month, "u": r["user_id"], "p": r["points"], "c": r["coins"], "s": status, "now": now,
             "paid": now if status == "paid" else 0})
        if status == "paid":
            _credit(db, r["user_id"], r["coins"])
        out.append((r["user_id"], r["coins"], status))
    return out


def release(db, payout_id, now=None):
    """Pay a held payout: (member id, coins, month), or None when it is not a held payout (or was released already)."""
    row = db.execute(text("SELECT user_id, coins, month FROM creator_fund_payouts WHERE id = :id AND status = 'held'"),
                     {"id": payout_id}).fetchone()
    if row is None:
        return None
    done = db.execute(text("UPDATE creator_fund_payouts SET status = 'paid', paid_utc = :now "
                           "WHERE id = :id AND status = 'held'"), {"id": payout_id, "now": _now(now)})
    if done.rowcount != 1:
        return None
    _credit(db, row[0], row[1])
    db.execute(text("UPDATE creator_fund_months SET paid_coins = paid_coins + :c WHERE month = :m"),
               {"c": row[1], "m": row[2]})
    return int(row[0]), int(row[1]), row[2].strip()


def _credit(db, user_id, coins):
    db.execute(text("UPDATE users SET coin_balance = coin_balance + :c WHERE id = :u"), {"c": coins, "u": user_id})


def paid_months(db):
    """[(month, budget, paid coins, created_utc)] newest first."""
    return [(m.strip(), b, p, t) for m, b, p, t in db.execute(text(
        "SELECT month, budget_coins, paid_coins, created_utc FROM creator_fund_months ORDER BY month DESC")).fetchall()]


def payouts_of_month(db, month):
    """[(payout id, member id, username, points, coins, status)] of a paid month."""
    return db.execute(text(
        "SELECT p.id, p.user_id, u.username, p.points, p.coins, p.status FROM creator_fund_payouts p "
        "LEFT JOIN users u ON u.id = p.user_id WHERE p.month = :m ORDER BY p.coins DESC, p.id"), {"m": month}).fetchall()


def payouts_of_member(db, user_id):
    """[(month, points, coins, status)] of one member, newest first: their own page only."""
    return [(m.strip(), p, c, s) for m, p, c, s in db.execute(text(
        "SELECT month, points, coins, status FROM creator_fund_payouts WHERE user_id = :u ORDER BY month DESC"),
        {"u": user_id}).fetchall()]


def set_opt_in(db, user_id, on, now=None):
    db.execute(text("UPDATE users SET creator_fund_opt_in_utc = :t WHERE id = :u"),
               {"t": _now(now) if on else 0, "u": user_id})
