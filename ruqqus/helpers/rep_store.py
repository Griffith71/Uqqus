"""Rep, the database side: the upvotes and downvotes an account's posts and comments have had from other accounts.

Rep is the upvotes (helpers/vote_colour.py colours it by the share of upvotes). Only live items count (not removed,
not deleted); forward copies of a post count, as they always did; the account's own votes never count (so the
automatic upvote on one's own post adds nothing). Anonymous items count for their author like any other: only a total
is ever shown, never which items made it. Plain SQL (`sqlalchemy.text`), so the same statements run on SQLite in the
tests."""
from sqlalchemy import text

_COUNTS = text("""
    SELECT
      COALESCE(SUM(CASE WHEN t.vote_type = 1 THEN 1 ELSE 0 END), 0),
      COALESCE(SUM(CASE WHEN t.vote_type = -1 THEN 1 ELSE 0 END), 0)
    FROM (
      SELECT v.vote_type FROM votes v JOIN submissions s ON s.id = v.submission_id
      WHERE s.author_id = :u AND v.user_id <> :u
        AND COALESCE(s.is_banned, :no) = :no AND COALESCE(s.deleted_utc, 0) = 0
      UNION ALL
      SELECT cv.vote_type FROM commentvotes cv JOIN comments c ON c.id = cv.comment_id
      WHERE c.author_id = :u AND cv.user_id <> :u
        AND COALESCE(c.is_banned, :no) = :no AND COALESCE(c.deleted_utc, 0) = 0
    ) t
""")


def counts(db, user_id):
    """(upvotes, downvotes) on the account's live posts and comments from other accounts."""
    ups, downs = db.execute(_COUNTS, {"u": user_id, "no": False}).fetchone()
    return int(ups or 0), int(downs or 0)
