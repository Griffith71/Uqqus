"""What the action row under a post counts (templates/partials/post_actions.html): comments, shares, bookmarks and
tips. Comments (`comment_count`) and tips (`tip_coins`, helpers/tips.py) are already on the post; this adds the
other two, for a whole page of posts in a few queries (`attach`, called by get_posts), or for one post on first use.

- **Shares**: how many times the post was passed on, counted for its family (the post it was copied from and every
  copy, `primary_post_id`): reposts of any member, plus the forward copies that are live and public. A copy in a
  private guild or one that was removed is not counted, so the number never hints at where a post went that the
  reader could not see.
- **Bookmarks**: the post's own `save_relationship` rows.

Only numbers leave here: nothing names who shared or bookmarked. Plain SQL (`sqlalchemy.text`), so the same
statements run on SQLite in the tests.
"""
from sqlalchemy import bindparam, text

_BOOKMARKS = text(
    "SELECT submission_id, COUNT(*) FROM save_relationship WHERE submission_id IN :ids GROUP BY submission_id"
).bindparams(bindparam("ids", expanding=True))

# the live, public forward copies of each primary post
_FORWARDS = text("""
    SELECT repost_id, COUNT(*) FROM submissions
    WHERE repost_id IN :families AND COALESCE(deleted_utc, 0) = 0
      AND COALESCE(is_banned, :no) = :no AND COALESCE(post_public, :yes) = :yes
    GROUP BY repost_id
""").bindparams(bindparam("families", expanding=True))

# reposts of any member of each family
_REPOSTS = text("""
    SELECT COALESCE(NULLIF(s.repost_id, 0), s.id) AS family, COUNT(*)
    FROM repost_relationship r JOIN submissions s ON s.id = r.submission_id
    WHERE s.id IN :families OR s.repost_id IN :families
    GROUP BY COALESCE(NULLIF(s.repost_id, 0), s.id)
""").bindparams(bindparam("families", expanding=True))


def family_of(post):
    """The id a post's shares are counted under: the post it was copied from, or itself."""
    return post.repost_id or post.id


def counts(db, posts):
    """{post id: (shares, bookmarks)} for these posts."""
    posts = [p for p in posts if p is not None]
    if not posts:
        return {}
    ids = sorted({p.id for p in posts})
    families = sorted({family_of(p) for p in posts})
    bookmarks = dict(db.execute(_BOOKMARKS, {"ids": ids}).fetchall())
    forwards = dict(db.execute(_FORWARDS, {"families": families, "no": False, "yes": True}).fetchall())
    reposts = dict(db.execute(_REPOSTS, {"families": families}).fetchall())
    out = {}
    for post in posts:
        family = family_of(post)
        out[post.id] = (int(forwards.get(family, 0)) + int(reposts.get(family, 0)), int(bookmarks.get(post.id, 0)))
    return out


def attach(db, posts):
    """Give each post of a page `_share_count` and `_bookmark_count` (Submission.share_count / bookmark_count)."""
    posts = list(posts or ())
    found = counts(db, posts)
    for post in posts:
        post._share_count, post._bookmark_count = found.get(post.id, (0, 0))
