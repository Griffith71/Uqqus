"""The SQL of guild analytics (rules: `guild_insights.py`). Plain `text()` SQL, tested on SQLite
(tests/test_guild_insights.py). Nothing here commits.

**Only counts leave this module.** Every statement is a `COUNT` or a `SUM` for one guild; no statement selects a user id,
a name or an address (tests/test_guild_insights.py checks the source). The three counters (`guild_day_stats`) are
written best effort inside a savepoint, so a failure can never spoil the join, the leave or the page that was counted.
"""
from sqlalchemy import text

from ruqqus.helpers import guild_insights as rules

_BUMP = {
    "joins": text("INSERT INTO guild_day_stats (board_id, day, joins, leaves, page_views) VALUES (:b, :d, 1, 0, 0) "
                  "ON CONFLICT (board_id, day) DO UPDATE SET joins = guild_day_stats.joins + 1"),
    "leaves": text("INSERT INTO guild_day_stats (board_id, day, joins, leaves, page_views) VALUES (:b, :d, 0, 1, 0) "
                   "ON CONFLICT (board_id, day) DO UPDATE SET leaves = guild_day_stats.leaves + 1"),
    "page_views": text("INSERT INTO guild_day_stats (board_id, day, joins, leaves, page_views) VALUES (:b, :d, 0, 0, 1) "
                       "ON CONFLICT (board_id, day) DO UPDATE SET page_views = guild_day_stats.page_views + 1"),
}

_STATS = text("SELECT day, joins, leaves, page_views FROM guild_day_stats WHERE board_id = :b AND day >= :d1 AND day <= :d2")
_MEMBERS = text("SELECT COUNT(*) FROM subscriptions WHERE board_id = :b AND COALESCE(is_active, :yes) = :yes")
_POSTS = text("SELECT created_utc / 86400, COUNT(*) FROM submissions WHERE board_id = :b AND created_utc >= :lo "
              "AND created_utc < :hi AND COALESCE(deleted_utc, 0) = 0 AND COALESCE(is_banned, :no) = :no "
              "GROUP BY created_utc / 86400")
_COMMENTS = text("SELECT c.created_utc / 86400, COUNT(*) FROM comments c JOIN submissions s ON s.id = c.parent_submission "
                 "WHERE s.board_id = :b AND c.created_utc >= :lo AND c.created_utc < :hi AND COALESCE(c.deleted_utc, 0) = 0 "
                 "AND COALESCE(c.is_banned, :no) = :no GROUP BY c.created_utc / 86400")
_UPVOTES = text("SELECT COUNT(*) FROM votes v JOIN submissions s ON s.id = v.submission_id "
                "WHERE s.board_id = :b AND v.vote_type = 1 AND v.created_utc >= :lo AND v.created_utc < :hi")
_MOD_ACTIONS = text("SELECT kind, COUNT(*) FROM modactions WHERE board_id = :b AND created_utc >= :lo AND created_utc < :hi "
                    "GROUP BY kind")
_POST_REPORTS = text("SELECT COUNT(*) FROM flags f JOIN submissions s ON s.id = f.post_id "
                     "WHERE s.board_id = :b AND f.created_utc >= :lo AND f.created_utc < :hi")
_COMMENT_REPORTS = text("SELECT COUNT(*) FROM commentflags f JOIN comments c ON c.id = f.comment_id "
                        "JOIN submissions s ON s.id = c.parent_submission "
                        "WHERE s.board_id = :b AND f.created_utc >= :lo AND f.created_utc < :hi")
_PRUNE = text("DELETE FROM guild_day_stats WHERE day < :day")


def bump(db, board_id, column, now):
    """Counts one join, leave or page view of a guild today. Best effort: a savepoint, and nothing escapes."""
    try:
        with db.begin_nested():
            db.execute(_BUMP[column], {"b": board_id, "d": rules.day_of(now)})
    except Exception:
        pass


def prune(db, now):
    """Drops counters older than `KEEP_DAYS` (a step of helpers/retention.py). Returns the rows removed."""
    return db.execute(_PRUNE, {"day": rules.day_of(now) - rules.KEEP_DAYS}).rowcount


def _per_day(db, statement, board_id, lo, hi):
    params = {"b": board_id, "lo": lo, "hi": hi, "no": False}
    return {int(day): int(n) for day, n in db.execute(statement, params)}


def _scalar(db, statement, **params):
    return int(db.execute(statement, params).scalar() or 0)


def report(db, board_id, now, days):
    """The guild's insights for the last `days` days, as plain numbers:

    {"days", "members", "joined", "left", "net", "views", "posts", "comments", "upvotes", "reports", "mod_actions": {kind: n},
     "exiles", "series": [per-day rows] (always built; the page draws it only for a guild that may show it)}
    """
    lo, hi = rules.bounds(now, days)
    d1, d2 = rules.day_of(lo), rules.day_of(hi - 1)
    stats = {int(day): {"joins": j or 0, "leaves": l or 0, "views": v or 0}
             for day, j, l, v in db.execute(_STATS, {"b": board_id, "d1": d1, "d2": d2})}
    posts = _per_day(db, _POSTS, board_id, lo, hi)
    comments = _per_day(db, _COMMENTS, board_id, lo, hi)
    per_day = {}
    for day in set(stats) | set(posts) | set(comments):
        per_day[day] = {**stats.get(day, {}), "posts": posts.get(day, 0), "comments": comments.get(day, 0)}

    joined = sum(s["joins"] for s in stats.values())
    left = sum(s["leaves"] for s in stats.values())
    actions = {kind: int(n) for kind, n in db.execute(_MOD_ACTIONS, {"b": board_id, "lo": lo, "hi": hi}) if kind}
    reports = (_scalar(db, _POST_REPORTS, b=board_id, lo=lo, hi=hi) + _scalar(db, _COMMENT_REPORTS, b=board_id, lo=lo, hi=hi))
    return {
        "days": days,
        "members": _scalar(db, _MEMBERS, b=board_id, yes=True),
        "joined": joined, "left": left, "net": rules.net(joined, left),
        "views": sum(s["views"] for s in stats.values()),
        "posts": sum(posts.values()), "comments": sum(comments.values()),
        "upvotes": _scalar(db, _UPVOTES, b=board_id, lo=lo, hi=hi),
        "reports": reports,
        "mod_actions": dict(sorted(actions.items())), "exiles": actions.get("exile_user", 0),
        "series": rules.fill_days(per_day, now, days),
    }
