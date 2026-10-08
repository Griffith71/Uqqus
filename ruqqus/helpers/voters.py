"""Who upvoted: what the AUTHOR of a post or comment is shown about its votes (the routes are routes/voters.py).

The rule, and nothing wider:
- only the author, i.e. `item.author_id` (a forward copy keeps the original author's id, and the forwarder is
  recorded elsewhere, so a forwarder is not the author and sees nothing);
- only upvotes: `vote_type = 1` is the only value either statement ever selects, so a downvote can't reach a
  response whoever the voter is;
- only upvoters the author follows AND who follow the author back, checked when the list is opened (not when
  the vote was cast);
- not removed votes, nor ones changed to a downvote, banned or deleted accounts, or anyone blocked either way;
  names the viewer's word filter hides are dropped too (`filter_users`).
The statements are fixed text (the item id and the author id are bound parameters), plain SQL so the same
text runs on SQLite in the tests.
"""
from sqlalchemy import text

LIMIT = 100

NOTE = "Only people you follow who follow you back are listed. Downvotes are never shown."

_SQL = """
    SELECT v.user_id AS user_id
    FROM {votes} v
    JOIN follows a ON a.user_id = :author AND a.target_id = v.user_id
    JOIN follows b ON b.user_id = v.user_id AND b.target_id = :author
    JOIN users u ON u.id = v.user_id
    WHERE v.{column} = :item
      AND v.vote_type = 1
      AND v.user_id <> :author
      AND COALESCE(u.is_banned, 0) = 0
      AND COALESCE(u.is_deleted, :no) = :no
      AND NOT EXISTS (SELECT 1 FROM userblocks k
                      WHERE (k.user_id = :author AND k.target_id = v.user_id)
                         OR (k.user_id = v.user_id AND k.target_id = :author))
    GROUP BY v.user_id
    ORDER BY MAX(v.created_utc) DESC, v.user_id
    LIMIT :limit
"""

_STATEMENTS = {
    "post": text(_SQL.format(votes="votes", column="submission_id")),
    "comment": text(_SQL.format(votes="commentvotes", column="comment_id")),
}


def may_see(item, viewer):
    """The author of a live post or comment."""
    return (viewer is not None and item.author_id == viewer.id
            and not item.is_banned and not item.deleted_utc)


def upvoter_ids(db, kind, item_id, author_id, limit=LIMIT):
    """The ids of the author's mutual followers who have an upvote on the item, newest vote first."""
    rows = db.execute(_STATEMENTS[kind], {"item": item_id, "author": author_id, "no": False, "limit": limit})
    return [row.user_id for row in rows]


def _users(db, ids, viewer):
    """{id: User} for the ids, minus the names the viewer's word filter hides."""
    from ruqqus.classes.user import User
    from ruqqus.helpers.visibility import filter_users

    return {user.id: user for user in filter_users(db.query(User).filter(User.id.in_(ids)), viewer).all()}


def friends_who_upvoted(db, kind, item_id, author_id, viewer, limit=LIMIT):
    """The accounts behind `upvoter_ids`, in that order (an account that is gone or filtered out is skipped)."""
    ids = upvoter_ids(db, kind, item_id, author_id, limit)
    if not ids:
        return []

    by_id = _users(db, ids, viewer)
    return [by_id[i] for i in ids if i in by_id]
