"""Follow a post: be told of the comments the person responsible for it is told of.

WHO IS RESPONSIBLE FOR A POST is the person who gets the notifications of its comments:
- a post on the author's own profile: its author;
- a forward copy in a guild (`forwardrels`) or a comment forwarded into a post (`comment_forwardrels`):
  **the person who forwarded it**. The copy keeps the original author's `author_id` (they keep their delete
  rights), so reading `author_id` told the original author of comments on a copy they never forwarded.
  If the author forwarded their own post, they are the forwarder.

Followers are told of the same comments the responsible person is: a comment directly on the post. A follow
is per post id: a forward copy is its own thread (own votes, own comments) and is followed on its own.

Nothing here names a follower to anyone. A follower is never told of what they could not read: blocked either
way with the commenter, muted commenter (unless the comment is anonymous, as blocks and mutes spare
anonymous items), a Circle post or private guild they may no longer see.

Plain SQL (`sqlalchemy.text`) so the same statements run on SQLite in the tests; the one ORM piece is
`owned_post_ids`, a subquery for the notification lists.
"""
import time

from sqlalchemy import bindparam, text

MAX_FOLLOWED = 500        # posts one account may follow
MAX_NOTIFIED = 1000       # followers told of one comment


class FollowError(Exception):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.message = message
        self.status = status


# --- who is responsible --------------------------------------------------------------------------------

_FORWARDER = text("SELECT forwarded_by_id FROM forwardrels WHERE forward_submission_id = :p LIMIT 1")
_COMMENT_FORWARDER = text("SELECT promoted_by_id FROM comment_forwardrels WHERE promoted_submission_id = :p LIMIT 1")


def responsible_id(db, post):
    """The account that is told of comments on `post`: its forwarder, else its author."""
    for statement in (_FORWARDER, _COMMENT_FORWARDER):
        row = db.execute(statement, {"p": post.id}).first()
        if row is not None and row[0]:
            return row[0]
    return post.author_id


_FORWARDERS = text("""
    SELECT forward_submission_id AS post, forwarded_by_id AS who FROM forwardrels WHERE forward_submission_id IN :ids
    UNION ALL
    SELECT promoted_submission_id AS post, promoted_by_id AS who FROM comment_forwardrels WHERE promoted_submission_id IN :ids
""").bindparams(bindparam("ids", expanding=True))


def responsible_ids(db, posts):
    """{post id: responsible account} for a page of posts, in one query."""
    posts = list(posts)
    if not posts:
        return {}
    forwarder = {row.post: row.who for row in db.execute(_FORWARDERS, {"ids": [p.id for p in posts]}) if row.who}
    return {p.id: forwarder.get(p.id, p.author_id) for p in posts}


# --- who follows ---------------------------------------------------------------------------------------

_FOLLOWERS = text("""
    SELECT f.user_id FROM post_follows f
    JOIN users u ON u.id = f.user_id
    WHERE f.post_id = :post
      AND f.user_id <> :commenter
      AND COALESCE(u.is_deleted, :no) = :no
      AND (COALESCE(u.is_banned, 0) = 0 OR COALESCE(u.unban_utc, 0) > 0)
      AND NOT EXISTS (SELECT 1 FROM userblocks b
                      WHERE (b.user_id = :commenter AND b.target_id = f.user_id)
                         OR (b.user_id = f.user_id AND b.target_id = :commenter))
      AND (:anonymous = :yes OR NOT EXISTS (SELECT 1 FROM usermutes m
                                            WHERE m.user_id = f.user_id AND m.target_id = :commenter))
    ORDER BY f.id
    LIMIT :cap
""")


def followers(db, post_id, commenter_id, anonymous=False):
    """Account ids that follow the post and may be told of a comment by `commenter_id`."""
    rows = db.execute(_FOLLOWERS, {"post": post_id, "commenter": commenter_id, "no": False, "yes": True,
                                   "anonymous": bool(anonymous), "cap": MAX_NOTIFIED})
    return [row[0] for row in rows]


def recipients(db, post, commenter_id, anonymous=False, visible=None):
    """Account ids to notify of a comment made directly on `post`: the responsible person and the followers.

    `visible(ids) -> set` removes the ones that may not read the post (Circle audience, private guild); the
    route supplies it. The commenter is never told of their own comment. The responsible person is not
    removed by `visible`: they are the author or the one who forwarded it into that guild."""
    chosen = followers(db, post.id, commenter_id, anonymous)
    if visible is not None and chosen:
        chosen = [uid for uid in chosen if uid in visible(chosen)]
    responsible = responsible_id(db, post)
    if responsible and responsible != commenter_id and responsible not in chosen:
        chosen.insert(0, responsible)
    return chosen


# --- following ---------------------------------------------------------------------------------------

_IS_FOLLOWING = text("SELECT 1 FROM post_follows WHERE user_id = :u AND post_id = :p")
_COUNT = text("SELECT COUNT(*) FROM post_follows WHERE user_id = :u")
_INSERT = text("INSERT INTO post_follows (user_id, post_id, created_utc) VALUES (:u, :p, :t)")
_DELETE = text("DELETE FROM post_follows WHERE user_id = :u AND post_id = :p")


def is_following(db, user_id, post_id):
    return db.execute(_IS_FOLLOWING, {"u": user_id, "p": post_id}).first() is not None


def follow(db, user_id, post, now=None):
    """Start following `post`. FollowError when the person already gets its notifications, already follows
    it, or follows too many. Does not commit."""
    if responsible_id(db, post) == user_id:
        raise FollowError("You already get notifications for this post.")
    if is_following(db, user_id, post.id):
        raise FollowError("You already follow this post.")
    if db.execute(_COUNT, {"u": user_id}).scalar() >= MAX_FOLLOWED:
        raise FollowError(f"You can follow {MAX_FOLLOWED} posts at most. Unfollow one first.")
    db.execute(_INSERT, {"u": user_id, "p": post.id, "t": int(now if now is not None else time.time())})


def unfollow(db, user_id, post):
    """Stop following. FollowError when they were not. Does not commit."""
    if not is_following(db, user_id, post.id):
        raise FollowError("You don't follow this post.")
    db.execute(_DELETE, {"u": user_id, "p": post.id})


_FOLLOWED_PAGE = text("SELECT post_id FROM post_follows WHERE user_id = :u AND post_id IN :ids").bindparams(
    bindparam("ids", expanding=True))


def attach(db, posts, viewer):
    """Give each post of a page `_follow_state`: "own" (the viewer is told of its comments anyway),
    "following", or "can_follow". Two queries for the page. A visitor gets None (no menu item)."""
    posts = list(posts)
    for post in posts:
        post._follow_state = None
    if viewer is None or not posts:
        return
    who = responsible_ids(db, posts)
    followed = {row[0] for row in db.execute(_FOLLOWED_PAGE, {"u": viewer.id, "ids": [p.id for p in posts]})}
    for post in posts:
        if who.get(post.id) == viewer.id:
            post._follow_state = "own"
        else:
            post._follow_state = "following" if post.id in followed else "can_follow"


# --- the notification lists ----------------------------------------------------------------------------

_OWNED = """
    SELECT s.id FROM submissions s
    WHERE s.author_id = :u
      AND NOT EXISTS (SELECT 1 FROM forwardrels f
                      WHERE f.forward_submission_id = s.id AND f.forwarded_by_id <> :u)
      AND NOT EXISTS (SELECT 1 FROM comment_forwardrels c
                      WHERE c.promoted_submission_id = s.id AND c.promoted_by_id <> :u)
    UNION
    SELECT forward_submission_id FROM forwardrels
    WHERE forwarded_by_id = :u AND forward_submission_id IS NOT NULL
    UNION
    SELECT promoted_submission_id FROM comment_forwardrels
    WHERE promoted_by_id = :u AND promoted_submission_id IS NOT NULL
    UNION
    SELECT post_id FROM post_follows WHERE user_id = :u
"""


def owned_post_ids(user_id):
    """A subquery of the ids of the posts whose comments `user_id` is told of as "their post": posts they
    authored that were not forwarded by someone else, copies they forwarded, and posts they follow, for
    `Comment.parent_submission.in_(...)` in the notification lists. `Submission.author_id == me` alone counted
    a copy someone else forwarded."""
    from sqlalchemy import Integer

    return text(_OWNED).bindparams(u=int(user_id)).columns(id=Integer)


def owned_ids(db, user_id):
    """The same ids as a set (tests, and anything that needs them in Python)."""
    return {row[0] for row in db.execute(text(_OWNED), {"u": int(user_id)})}
