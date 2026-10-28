"""Hot threads for the "What's happening" box: the posts people are busiest with in the last two days, for when no topic
is trending (helpers/trending.py needs three different accounts on one phrase, which a quiet site rarely has).

- **Which posts**: exactly the ones Trending counts (`trending_store._load`: live, public, original posts, not bots, not
  private or opted-out guilds, nothing sensitive), minus a Space's hidden backing row, at the viewer's word-filter level.
  Scored from what the post has now (votes plus two for each comment, fading with age): it does not read `score_hot`,
  which another job keeps (and which stands still where that job is off).
- **The ids are shared, the rest is per viewer.** The ranking is kept in Redis for everyone at one word-filter level
  (ids only, `CACHE_SECONDS`, one rebuild at a time with the last copy served while it runs, as helpers/livestream_status.py).
  Each viewer's own rules (blocks, mutes, "show fewer", Hide bot content, muted guilds) are applied AFTER the cache, to the
  few ids a page shows. Counts only, never who: a row shows the title, the guild and the comment count.
- Where Redis is not set up nothing is cached and nothing is shown (the box says nothing is trending).
"""
import json
import time

CACHE_SECONDS = 300
STALE_SECONDS = 900                 # the last copy served while a rebuild fails (a guild may have gone private since)
FAILED_SECONDS = 30                 # a failed rebuild is not tried again for this long
LOCK_SECONDS = 30
WINDOW_SECONDS = 48 * 3600
KEEP = 10


def score(votes, comments, age_seconds):
    """Votes plus two a comment, fading with age: a day-old post needs about three times the activity of a new one."""
    hours = max(age_seconds, 0) / 3600.0
    return (max(votes, 0) + 2 * max(comments, 0) + 1) / ((hours + 2.0) ** 1.5)


def rank(rows, now, keep=KEEP):
    """The ids of `rows` (id, created_utc, votes, comments) best first. Posts with no activity at all are not hot."""
    scored = []
    for post_id, created, votes, comments in rows:
        if votes <= 0 and comments <= 0:
            continue
        scored.append((score(votes, comments, now - created), post_id))
    scored.sort(key=lambda s: (-s[0], -s[1]))
    return [post_id for _, post_id in scored[:keep]]


def _redis():
    from ruqqus.__main__ import r
    return r


def _text(value):
    return value.decode() if isinstance(value, bytes) else value


def compute(db, level, now=None):
    """The hot ids at one word-filter level (not cached here)."""
    from sqlalchemy import select
    from ruqqus.classes import Submission
    from ruqqus.helpers import trending_store as ts

    now = int(now or time.time())
    since = now - WINDOW_SECONDS
    rows = ts._load(db, since, now)
    if not rows:
        return []
    ids = [row.id for row in rows]
    spaces = {pid for (pid,) in db.execute(select(Submission.id).where(Submission.id.in_(ids), Submission.is_space.is_(True)))}
    visible = ts._visible_ids(db, since, level)
    comments = ts._comment_counts(db, [pid for pid in ids if pid in visible])
    return rank([(row.id, row.created_utc, (row.upvotes or 0) - (row.downvotes or 0), comments.get(row.id, 0))
                 for row in rows if row.id in visible and row.id not in spaces], now)


def cached_ids(db, level, now=None):
    """The hot ids for everyone at `level`: from Redis, rebuilt by one request at a time, [] when there is no Redis."""
    r = _redis()
    if r is None:
        return []
    key, stale, lock = f"happening:hot:{level}", f"happening:hot:stale:{level}", f"happening:hot:lock:{level}"
    value = r.get(key)
    if value is not None:
        return json.loads(_text(value))
    if r.set(lock, "1", ex=LOCK_SECONDS, nx=True):
        try:
            ids = compute(db, level, now)
        except Exception:
            db.rollback()                           # on Postgres a failed query leaves the transaction unusable
            ids = None
        if ids is not None:
            r.set(key, json.dumps(ids), ex=CACHE_SECONDS)
            r.set(stale, json.dumps(ids), ex=STALE_SECONDS)
            r.delete(lock)
            return ids
        r.set(lock, "1", ex=FAILED_SECONDS)         # keep the lock a while: a database in trouble is not asked by every sidebar
    old = r.get(stale)
    return json.loads(_text(old)) if old is not None else []


def _on_open_boards(db, ids):
    """`ids` whose board is not private, a Circle guild, banned or opted out of All (the rules compute() applied, read again)."""
    if not ids:
        return set()
    from ruqqus.classes import Board, Submission
    rows = db.query(Submission.id).join(Board, Board.id == Submission.board_id).filter(
        Submission.id.in_(ids), Board.is_private.is_(False), Board.is_circle.is_(False), Board.is_banned.is_(False),
        Board.all_opt_out.is_(False))
    return {pid for (pid,) in rows}


def for_viewer(db, v, level, limit=3, now=None):
    """The posts (as get_posts returns them) of the hot list this viewer may see, hottest first."""
    from ruqqus.classes import Submission
    from ruqqus.helpers import post_feedback
    from ruqqus.helpers import trending_store as ts
    from ruqqus.helpers.get import get_posts

    ids = cached_ids(db, level, now)
    if not ids:
        return []
    allowed = set(ts.visible_post_ids(db, v, ids, sort="top"))
    allowed = _on_open_boards(db, allowed)          # a shared list can be minutes old: ask again whether the board is still open
    if v is not None and allowed:
        kept = post_feedback.hide_fewer(db.query(Submission.id).filter(Submission.id.in_(allowed)), v, Submission)
        allowed = {pid for (pid,) in kept}
    chosen = [pid for pid in ids if pid in allowed][:limit]
    if not chosen:
        return []
    posts = {p.id: p for p in get_posts(chosen, v=v)}
    return [posts[pid] for pid in chosen if pid in posts]
