"""Taking a name off a post or a comment, for good (the Log plan's decisions 3, 6 and 11; slice D3a). The one place that does it.

`anonymise_post` and `anonymise_comment` set `is_anonymous` on the item and on every copy of it by the same author, clear what would give the author
away or no longer applies (a herald, a distinguish), take the item out of every entry that pairs it with its author (an exile that cited it, a guild
action by the author on their own post), and clear the caches that list by author. **It is irreversible and writes no entry**: the time of an
anonymising would itself be a signal. A delete calls it (the words stay in the Log as Anonymous), an account's deletion calls it for everything the
account wrote (D3c), and "Remove my name" calls it for one item (`deletion.unlink_refusal` says when that is refused: this module never refuses, a
delete and an account's closing must always be able to anonymise, whatever the item).

Not here: the delete itself (`routes/posts.py delete_post_as`, `routes/comments.py delete_comment`), the Log entry and its public copy
(`helpers/action_log.py`), the pages (`routes/log_pages.py`)."""
from sqlalchemy import or_, text


def _marks(prefix, ids):
    names = {f"{prefix}{n}": int(i) for n, i in enumerate(ids)}
    return ", ".join(f":{n}" for n in names), names


def family_of(db, post):
    """A post's own row and its forwarded copies by the same author (a copy keeps the original author's id; a repost by someone else is that
    person's own post and is not part of this family). Asked of a copy, it is the primary's family."""
    from ruqqus.classes import Submission
    primary = post.repost_id or post.id
    return db.query(Submission).filter(Submission.author_id == post.author_id,
                                       or_(Submission.id == primary, Submission.repost_id == primary)).all()


def strip_pointers(db, *, author_id, post_ids=(), comment_ids=()):
    """Take these items out of every entry that pairs them with their author: an entry that names the account AND the item (an exile that cited
    it), and a guild action the author did to their own item (a herald or a pin). The entry stays, without the pointer; so does the guild
    log's row (`modactions`). A deliberate, documented exception to "the Log is append-only": an anonymous item's author must not be findable
    through it. The pointers a ban's approved reveal holds (D3d) are in their own table, which this never touches."""
    for column, ids in (("target_submission_id", post_ids), ("target_comment_id", comment_ids)):
        ids = [int(i) for i in ids or () if i]
        if not ids:
            continue
        marks, names = _marks("i", ids)
        params = {**names, "a": int(author_id)}
        db.execute(text(f"UPDATE log_entries SET {column} = NULL WHERE {column} IN ({marks}) AND "
                        f"(target_user_id = :a OR (actor_id = :a AND actor_role = 'guildmaster'))"), params)
        db.execute(text(f"UPDATE modactions SET {column} = NULL WHERE {column} IN ({marks}) AND (target_user_id = :a OR user_id = :a)"), params)


def clear_caches(posts=(), comments=(), author=None):
    """What lists by author, by guild or by feed: the lists the item was on before it was anonymous."""
    from ruqqus.__main__ import cache
    from ruqqus.classes import Board, User
    if author is not None:
        cache.delete_memoized(User.userpagelisting, author, sort="new")
        cache.delete_memoized(User.commentlisting, author)
    seen = set()
    for post in posts:
        board = getattr(post, "board", None)
        if board is not None and board.id not in seen:
            seen.add(board.id)
            cache.delete_memoized(Board.idlist, board)
            cache.delete_memoized(Board.idlist, board, sort="new")
    if posts:
        from ruqqus.routes.front import frontlist
        cache.delete_memoized(frontlist, sort="new")


def anonymise_post(db, post, *, scope="family"):
    """Anonymise a post and, by default, every forwarded copy of it by the same author (`scope="row"` is only the one row, for a delete that is
    scoped to a copy). Returns the rows changed."""
    rows = family_of(db, post) if scope == "family" else [post]
    for row in rows:
        row.is_anonymous = True
        row.distinguish_level = 0                     # a distinguished post names its author's role: it ends here
        row.gm_distinguish = 0                        # and so does a guildmaster's herald
        db.add(row)
    strip_pointers(db, author_id=post.author_id, post_ids=[r.id for r in rows])
    clear_caches(posts=rows, author=post.author)
    return rows


def anonymise_comment(db, comment):
    """Anonymise a comment and the posts it was forwarded as. Returns the rows changed (the comment first)."""
    forwards = list(getattr(comment, "forwarded_posts", None) or [])
    comment.is_anonymous = True
    db.add(comment)
    for forward in forwards:
        forward.is_anonymous = True
        forward.distinguish_level = 0
        forward.gm_distinguish = 0
        db.add(forward)
    strip_pointers(db, author_id=comment.author_id, post_ids=[f.id for f in forwards], comment_ids=[comment.id])
    clear_caches(posts=forwards, author=comment.author)
    return [comment] + forwards


def anonymise_replies(db, post_ids, author_id):
    """The author's OWN comments under these posts, made anonymous by the delete (decision 25f): a named comment under an anonymous post would say who
    wrote it. Comments by others stay as they are. Returns how many were changed."""
    ids = [int(i) for i in post_ids if i]
    if not ids:
        return 0
    marks, names = _marks("p", ids)
    result = db.execute(text(f"UPDATE comments SET is_anonymous = :yes WHERE parent_submission IN ({marks}) AND author_id = :a "
                             f"AND NOT COALESCE(is_anonymous, false)"), {**names, "a": int(author_id), "yes": True})
    return result.rowcount or 0
