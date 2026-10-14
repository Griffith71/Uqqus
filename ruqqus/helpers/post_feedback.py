"""Show fewer posts like this: a soft negative signal for the algorithm, so a member need not downvote.

What one click does, for that member only:
- the post leaves their All and For You feeds at once (`hide_fewer`), together with its other copies (the
  feedback is kept under the PRIMARY post). It stays on the guild page, the profile, Following and search:
  going there is a choice, as with a mute;
- for 60 days (`WINDOW_SECONDS`) For You ranks lower: the post's category (`interest_subcats` subtracts
  `SUBCAT_PENALTY` per click), and its guild and its author go behind everything else (`sink_clause`).

An anonymous post is never held against its author: `author_id` is stored NULL for it, and `sink_clause`
only matches an author's posts that are not anonymous (the same promise hide_muted and hide_blocked make).

Plain SQL (`sqlalchemy.text`) so the same statements run on SQLite in the tests. Reset For You
(`/settings/for_you/reset`) deletes a member's rows with `clear`.
"""
import time
from types import SimpleNamespace

from sqlalchemy import Integer, and_, case, func, or_, text

MAX_ROWS = 1000                       # per member: the oldest are dropped
WINDOW_SECONDS = 60 * 24 * 3600       # how long a click lowers a category, guild and author
SUBCAT_PENALTY = 2                    # a subscribed guild adds 3 to its category, so one click does not erase it


class FeedbackError(Exception):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.message = message
        self.status = status


def primary_post_id(post):
    """The id a post's feedback is kept under: the post it was copied from, or itself."""
    return post.repost_id or post.id


# --- the feeds -----------------------------------------------------------------------------------------

def hide_fewer(query, v, model):
    """`query` without the posts `v` asked to see fewer of. `model` is Submission. A visitor has asked for none."""
    if v is None:
        return query
    asked = text("SELECT post_id FROM post_feedback WHERE user_id = :u").bindparams(u=int(v.id)).columns(post_id=Integer)
    return query.filter(func.coalesce(func.nullif(model.repost_id, 0), model.id).notin_(asked))


_PENALTIES = text("""
    SELECT f.board_id AS board_id, f.author_id AS author_id, b.subcat_id AS subcat_id
    FROM post_feedback f
    LEFT JOIN boards b ON b.id = f.board_id
    WHERE f.user_id = :u AND f.created_utc >= :since
""")


def penalties(db, user_id, now=None):
    """What a member's recent clicks lower: `subcat_ids` (one entry per click), `board_ids`, `author_ids`."""
    now = int(now if now is not None else time.time())
    rows = db.execute(_PENALTIES, {"u": int(user_id), "since": now - WINDOW_SECONDS}).fetchall()
    return SimpleNamespace(
        subcat_ids=[r.subcat_id for r in rows if r.subcat_id is not None],
        board_ids={r.board_id for r in rows if r.board_id is not None},
        author_ids={r.author_id for r in rows if r.author_id is not None},
    )


def sink_clause(db, user_id, model, now=None):
    """An ORDER BY key (0 first, 1 last) that puts a penalised guild's posts, and a penalised author's posts
    that are not anonymous, behind the others; None when nothing is penalised."""
    lowered = penalties(db, user_id, now)
    conditions = []
    if lowered.board_ids:
        conditions.append(model.board_id.in_(lowered.board_ids))
    if lowered.author_ids:
        conditions.append(and_(model.author_id.in_(lowered.author_ids), model.is_anonymous == False))
    if not conditions:
        return None
    return case((or_(*conditions), 1), else_=0)


# --- clicking --------------------------------------------------------------------------------------------

_EXISTS = text("SELECT 1 FROM post_feedback WHERE user_id = :u AND post_id = :p")
_INSERT = text("INSERT INTO post_feedback (user_id, post_id, board_id, author_id, created_utc) "
               "VALUES (:u, :p, :b, :a, :t)")
_DELETE = text("DELETE FROM post_feedback WHERE user_id = :u AND post_id = :p")
_TRIM = text("""
    DELETE FROM post_feedback WHERE user_id = :u AND id NOT IN (
        SELECT id FROM post_feedback WHERE user_id = :u ORDER BY created_utc DESC, id DESC LIMIT :cap)
""")
_CLEAR = text("DELETE FROM post_feedback WHERE user_id = :u")


def add(db, user_id, post, now=None):
    """Record "fewer like this" for `post`. Asking twice is not an error (the answer is the same). A member's
    own post is refused. Does not commit."""
    if post.author_id == user_id:
        raise FeedbackError("That's your own post.")
    pid = primary_post_id(post)
    if db.execute(_EXISTS, {"u": user_id, "p": pid}).first() is not None:
        return
    db.execute(_INSERT, {"u": user_id, "p": pid, "b": post.board_id,
                         "a": None if post.is_anonymous else post.author_id,
                         "t": int(now if now is not None else time.time())})
    db.execute(_TRIM, {"u": user_id, "cap": MAX_ROWS})


def remove(db, user_id, post):
    """Take it back (Undo). Not having asked is not an error. Does not commit."""
    db.execute(_DELETE, {"u": user_id, "p": primary_post_id(post)})


def clear(db, user_id):
    """Every row of a member's (Reset For You). Does not commit."""
    db.execute(_CLEAR, {"u": user_id})


def clear_cached_feeds(v):
    """The lists kept for a few minutes per member must not keep showing, or hiding, a post after a click."""
    from ruqqus.__main__ import cache
    from ruqqus.helpers import muting

    muting.clear_cached_feeds(v)
    cache.delete_memoized(v.interest_subcats)
