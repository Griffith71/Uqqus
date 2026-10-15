"""Creator Studio, the counting (rules: helpers/studio.py). Plain SQL (`sqlalchemy.text`) so the same statements run on
SQLite in the tests. Every statement answers a number for ONE member: nothing here lists a tipper or a subscriber."""
import time

from sqlalchemy import text

# a tip on a forward copy goes to the original author, and a copy keeps the original's author_id, so it counts here
_TIPS = text("""
    SELECT COALESCE(SUM(a.coins), 0) FROM award_relationships a
    LEFT JOIN submissions s ON s.id = a.submission_id
    LEFT JOIN comments c ON c.id = a.comment_id
    WHERE (s.author_id = :u OR c.author_id = :u) AND COALESCE(a.user_id, 0) <> :u AND a.created_utc >= :since""")
_CIRCLES = text("SELECT COALESCE(SUM(coins), 0) FROM circle_payments WHERE owner_id = :u AND created_utc >= :since")
_FUND = text("SELECT COALESCE(SUM(coins), 0) FROM creator_fund_payouts WHERE user_id = :u AND status = 'paid' AND paid_utc >= :since")
_SUBSCRIBERS = text("""
    SELECT COUNT(DISTINCT m.user_id) FROM circle_members m JOIN circles c ON c.id = m.circle_id
    WHERE c.user_id = :u AND c.kind = 'paid' AND m.tier = 'subscriber' AND m.status = 'active' AND m.renews_utc > :now""")


def earnings(db, user_id, since=0):
    """{source: coins} a member received since a date (helpers/studio.py SOURCES). A held creator fund payout is not
    counted until it is paid."""
    params = {"u": user_id, "since": int(since)}
    return {"tips": int(db.execute(_TIPS, params).scalar() or 0),
            "circles": int(db.execute(_CIRCLES, params).scalar() or 0),
            "fund": int(db.execute(_FUND, params).scalar() or 0)}


def subscribers_now(db, user_id, now=None):
    """How many people pay for at least one of the member's paid circles right now (each once)."""
    return int(db.execute(_SUBSCRIBERS, {"u": user_id, "now": int(now if now is not None else time.time())}).scalar() or 0)
