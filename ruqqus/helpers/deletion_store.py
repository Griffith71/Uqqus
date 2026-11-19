"""The database side of a member's own delete (helpers/deletion.py has the rules): plain `text()` SQL, tested on SQLite
(tests/test_deletion.py). Nothing here commits."""
from sqlalchemy import text

from ruqqus.helpers import action_log


def _marks(prefix, ids):
    names = {f"{prefix}{n}": int(i) for n, i in enumerate(ids)}
    return ", ".join(f":{n}" for n in names), names


DELETE_LOCK = 7011               # pg_advisory_xact_lock(DELETE_LOCK, account id): one delete at a time per account


def lock_deletes(db, user_id):
    """Take the account's delete lock until the request's commit (Postgres; SQLite has one writer). The cap on erases is a count of copies and
    "already deleted" is a flag read before the work, so two parallel requests both passed both: two public entries for one delete, and an erase
    counted against the daily cap twice (the second review, C08 and C09). The caller re-reads the item after this and stops if it is deleted."""
    if db.get_bind().dialect.name != "postgresql":
        return
    db.execute(text("SELECT pg_advisory_xact_lock(:ns, :u)"), {"ns": DELETE_LOCK, "u": int(user_id)})


def erased_today(db, user_id, now):
    """How many items this account erased inside the grace window in the last day: their `grace` copies that no entry points at (a child's delete,
    which writes an entry, is not an erase). The cap is per account (`deletion.GRACE_ERASES_PER_DAY`)."""
    return db.execute(text(
        "SELECT count(*) FROM log_snapshots WHERE kind = :kind AND author_id = :u AND entry_id IS NULL AND created_utc >= :since"),
        {"kind": action_log.GRACE, "u": int(user_id), "since": int(now) - action_log.DAY}).scalar() or 0


def others_replied_to_posts(db, post_ids, author_id):
    """Has anyone but the author written under any of these posts (the post and its forwarded copies)? A comment that was deleted counts: it stays in
    the Log's thread of this post, and nobody's words may be orphaned by an erase."""
    ids = [int(i) for i in post_ids if i]
    if not ids:
        return False
    marks, names = _marks("p", ids)
    return db.execute(text(f"SELECT 1 FROM comments WHERE parent_submission IN ({marks}) AND author_id <> :a LIMIT 1"),
                      {**names, "a": int(author_id)}).first() is not None


def own_named_comments(db, post_ids, author_id):
    """The ids of the author's own live comments, still under their name, under these posts (a post and its copies): what "also remove my name
    from my comments here" would change. A deleted comment is the Log's already, a removed one keeps its own record."""
    ids = [int(i) for i in post_ids if i]
    if not ids:
        return []
    marks, names = _marks("p", ids)
    rows = db.execute(text(f"SELECT id FROM comments WHERE parent_submission IN ({marks}) AND author_id = :a AND deleted_utc = 0 "
                           f"AND NOT COALESCE(is_banned, false) AND NOT COALESCE(is_anonymous, false) ORDER BY id"), {**names, "a": int(author_id)})
    return [r[0] for r in rows]


def others_replied_to_comment(db, comment_id, author_id):
    """Has anyone but the author commented directly on this comment, or on a post it was forwarded as? A forwarded comment is a post of its own with its
    own thread, and a delete takes it with the comment: erasing a comment whose forwarded post has a discussion would orphan other people's words (the second
    review, C73)."""
    params = {"c": int(comment_id), "a": int(author_id)}
    if db.execute(text("SELECT 1 FROM comments WHERE parent_comment_id = :c AND author_id <> :a LIMIT 1"), params).first() is not None:
        return True
    return db.execute(text("SELECT 1 FROM comments k JOIN comment_forwardrels f ON f.promoted_submission_id = k.parent_submission "
                           "WHERE f.comment_id = :c AND k.author_id <> :a LIMIT 1"), params).first() is not None


def forwarded_to_ids(db, post_id):
    """The ids of the live forwarded copies of a primary post (every row whose `repost_id` is it): a delete reaches each."""
    return [r[0] for r in db.execute(text("SELECT id FROM submissions WHERE repost_id = :p"), {"p": int(post_id)})]


def comment_tipped(db, comment_id):
    """Has this comment been tipped? A tip went to its author in coins, which "Remove my name" would sever from them."""
    return db.execute(text("SELECT 1 FROM award_relationships WHERE comment_id = :c LIMIT 1"), {"c": int(comment_id)}).first() is not None


def post_tipped(db, post_ids):
    """Has any of these posts (a post and its copies) been tipped? Each copy keeps its own count; the coins went to the one author."""
    ids = [int(i) for i in post_ids if i]
    if not ids:
        return False
    marks, names = _marks("t", ids)
    return db.execute(text(f"SELECT 1 FROM submissions WHERE id IN ({marks}) AND COALESCE(tip_coins, 0) > 0 LIMIT 1"), names).first() is not None


def is_shared(db, post_id):
    """Is this primary post written with others: accepted co-authors, or a shared draft it was published from? Its authors' names are credits on
    one post, and taking one name off is the per-credit rule of slice D3f, not this one."""
    if db.execute(text("SELECT 1 FROM post_coauthors WHERE post_id = :p AND status = 'accepted' LIMIT 1"), {"p": int(post_id)}).first():
        return True
    return db.execute(text("SELECT 1 FROM collab_docs WHERE post_id = :p AND deleted_utc = 0 LIMIT 1"), {"p": int(post_id)}).first() is not None


def languages(db, post_ids=(), comment_ids=()):
    """({post id: language code}, {comment id: language code}) for the deleted items on a page, so their words carry `lang` like any member text. The
    rows stay after a delete (until a purge blanks them), so this asks the item rather than keeping the code in the Log."""
    out = ({}, {})
    for number, (table, ids) in enumerate((("submissions", post_ids), ("comments", comment_ids))):
        ids = [int(i) for i in ids or () if i]
        if not ids:
            continue
        marks, names = _marks("l", ids)
        for row_id, code in db.execute(text(f"SELECT id, language_code FROM {table} WHERE id IN ({marks})"), names):
            if code:
                out[number][row_id] = code
    return out


def post_entry(db, post_ids):
    """The id of the newest `author_delete_post` entry about any of these posts (the row asked about, then its primary), or None: a delete inside the
    grace window leaves no entry, which is how a page knows the words are not the Log's to show."""
    ids = [int(i) for i in post_ids if i]
    if not ids:
        return None
    marks, names = _marks("p", ids)
    row = db.execute(text(f"SELECT id FROM log_entries WHERE action = 'author_delete_post' AND target_submission_id IN ({marks}) "
                          f"ORDER BY id DESC LIMIT 1"), names).first()
    return row[0] if row else None


def comment_entries(db, comment_ids):
    """{comment id: entry id} of the `author_delete_comment` entries about these comments (a comment erased inside the grace window has none)."""
    ids = [int(i) for i in comment_ids if i]
    if not ids:
        return {}
    marks, names = _marks("c", ids)
    return {r[0]: r[1] for r in db.execute(text(
        f"SELECT target_comment_id, max(id) FROM log_entries WHERE action = 'author_delete_comment' AND target_comment_id IN ({marks}) "
        f"GROUP BY target_comment_id"), names)}
