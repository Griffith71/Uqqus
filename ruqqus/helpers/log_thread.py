"""What the Log's thread of a deleted post (`GET /log/post/<id>`, routes/log_pages.py `log_thread`) needs from the database that a page of comments
can't afford to ask one comment at a time.

The thread draws a comment by an account the reader blocked, or that blocked them, as one nameless line, as a normal thread does
(templates/comments.html). A page can hold hundreds of comments (every top-level comment brings its whole subtree), so that question is one statement
for the page's authors, never `User.any_block_exists` once per comment. Plain `text()` SQL, tested on SQLite (tests/test_log_thread.py). Nothing here
commits.

An anonymous comment is never asked about: hiding only those would tell the reader which ones their blocked account wrote. The caller leaves them out
of `author_ids`, and a mute is `helpers/muting.py is_muted`, which has the same rule."""
from sqlalchemy import bindparam, text

_BLOCKS = text(
    "SELECT user_id, target_id FROM userblocks "
    "WHERE (user_id = :me AND target_id IN :ids) OR (target_id = :me AND user_id IN :ids)"
).bindparams(bindparam("ids", expanding=True))


def blocked_among(db, viewer_id, author_ids):
    """The ids in `author_ids` with a block either way between them and the viewer: one the viewer made, or one made against the viewer. One statement
    however many ids; empty for no viewer or no ids. Never says which way a block went."""
    ids = sorted({int(i) for i in author_ids if i})
    if not viewer_id or not ids:
        return set()
    me = int(viewer_id)
    found = set()
    for user_id, target_id in db.execute(_BLOCKS, {"me": me, "ids": ids}):
        other = target_id if user_id == me else user_id
        if other != me:
            found.add(int(other))
    return found
