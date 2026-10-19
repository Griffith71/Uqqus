"""What the profile entry of a post made for guilds only counts (the owner: "totals across all its spaces").

Such a post (`comment_permission.GUILDS`) has no comments or votes of its own worth showing on the profile: its guilds'
copies hold them. So its card shows the sum over the live, **public** copies the author posted (a copy in a private
guild is left out, so the number never hints at one the reader can't open), added to the entry's own votes. Everything
else is unchanged: a guild's own page and card show that guild's numbers, and a post that does have a profile space
keeps its own.

Counts only: nothing here names a guild or a person. Plain SQL (`sqlalchemy.text`), so the same statement runs on
SQLite in the tests; `attach` is called by get_posts like helpers/post_counts.py.
"""
from sqlalchemy import bindparam, text

from ruqqus.helpers.comment_permission import GUILDS

_TOTALS = text("""
    SELECT s.repost_id, COALESCE(SUM(s.comment_count), 0), COALESCE(SUM(s.upvotes), 0), COALESCE(SUM(s.downvotes), 0)
    FROM submissions s
    JOIN forwardrels f ON f.forward_submission_id = s.id
    JOIN submissions h ON h.id = s.repost_id
    JOIN boards b ON b.id = s.board_id
    WHERE s.repost_id IN :hubs AND f.forwarded_by_id = h.author_id
      AND COALESCE(s.deleted_utc, 0) = 0 AND COALESCE(s.is_banned, :no) = :no AND COALESCE(s.hidden_by_guild, :no) = :no
      AND COALESCE(s.post_public, :yes) = :yes AND COALESCE(b.is_banned, :no) = :no
    GROUP BY s.repost_id
""").bindparams(bindparam("hubs", expanding=True))


def is_hub(post):
    """A profile entry made for guilds only."""
    return not post.repost_id and getattr(post, "comment_permission", None) == GUILDS


def totals(db, posts):
    """{hub id: (comments, upvotes, downvotes)} over the copies, for the guilds-only hubs among these posts."""
    ids = sorted({p.id for p in posts if p is not None and is_hub(p)})
    if not ids:
        return {}
    rows = db.execute(_TOTALS, {"hubs": ids, "no": False, "yes": True}).fetchall()
    return {r[0]: (int(r[1]), int(r[2]), int(r[3])) for r in rows}


def attach(db, posts):
    """Give each guilds-only hub of a page the copies' counts (`_space_comments`, `_space_ups`, `_space_downs`)."""
    posts = list(posts or ())
    found = totals(db, posts)
    for post in posts:
        if post is not None and post.id in found:
            post._space_comments, post._space_ups, post._space_downs = found[post.id]
