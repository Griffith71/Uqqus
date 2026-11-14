"""A stable alias for anonymous participants (the Log plan's decision 23; slice D3e): **"Anonymous #3"**.

People in a thread who can't be named still need to be told apart ("anonymous accounts should have a particular ID so people can still know who is who, even
without identifying the account"). Each person who is anonymous in a thread gets a number, **in the order they first became anonymous there**: when they
posted or commented anonymously, or when "Remove my name", a delete, the History mass tools or an account's deletion made them so (the number is given at
that moment, not when they wrote). The post's author counts from their post, so **the author is the same number on the post and on their anonymous
comments**; a post made anonymous later gives its author the next number, a minor signal, accepted.

* **A thread** is a post's comment space (`scope_of`): the post, all its guild copies, and every comment under any of them share the primary post's numbers.
  A shared draft's own scope arrives with slice D3f.
* **Per thread, never global**: the same person is a different number in every other thread, and no page or JSON links two threads.
* **Never reused or changed**: a number once given stays, and it outlives an account's deletion; the table keeps the user id only so a number can be found
  again for the same person.
* **Admins and the author always see the account**: this module only supplies the number a hidden item is drawn with (`helpers/anonymity.py`).

Plain `text()` SQL, tested on SQLite (tests/test_anon_aliases.py). Nothing here commits. The number is given under a lock per thread on Postgres
(`pg_advisory_xact_lock`), and the unique index on `(scope, n)` means two writers can never give one number twice: the loser retries."""
import time

from sqlalchemy import text

POST = "post"
LOCK = 7005                         # the advisory lock class for a thread's numbering (7002 is children, 7003 chat devices, 7004 first chat accounts)
TRIES = 4


def scope_key(post):
    """`("post", id)`: the thread a post belongs to, its primary post's (a guild copy or a repost shares it)."""
    return POST, int(getattr(post, "repost_id", 0) or post.id)


def scope_of(item):
    """The thread an anonymous post or comment is in, or None for something with no post (a system notice). A comment's is its post's."""
    post = getattr(item, "post", None) if hasattr(item, "parent_submission") else item
    if post is None:
        return None
    return scope_key(post)


def _lock(db, scope):
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(:class, :scope)"), {"class": LOCK, "scope": int(scope[1])})


def alias_for(db, scope, user_id, now=None):
    """The number this person has in this thread, given now if they have none yet (idempotent: asked twice, the same number). Never reused, never
    changed. Returns the number."""
    kind, scope_id = scope
    params = {"k": kind, "s": int(scope_id), "u": int(user_id), "now": int(now if now is not None else time.time())}
    found = db.execute(text("SELECT n FROM anon_aliases WHERE scope_kind = :k AND scope_id = :s AND user_id = :u"), params).scalar()
    if found:
        return int(found)
    _lock(db, scope)
    for _ in range(TRIES):
        db.execute(text(
            "INSERT INTO anon_aliases (scope_kind, scope_id, user_id, n, created_utc) "
            "SELECT :k, :s, :u, COALESCE(MAX(n), 0) + 1, :now FROM anon_aliases WHERE scope_kind = :k AND scope_id = :s ON CONFLICT DO NOTHING"), params)
        found = db.execute(text("SELECT n FROM anon_aliases WHERE scope_kind = :k AND scope_id = :s AND user_id = :u"), params).scalar()
        if found:
            return int(found)
    raise RuntimeError("couldn't give an anonymous alias")        # four conflicts in a row would mean the lock isn't held: say so, never hand out a duplicate


def give(db, scope, user_id, now=None):
    """`alias_for`, for the code that makes someone anonymous (a new anonymous post or comment, "Remove my name", a delete, the mass tools, an account's
    deletion): in a savepoint, so a database that hasn't reached the migration still posts, comments and deletes (the item is then drawn as plain
    "Anonymous"), and the request's cache of this thread's numbers forgets what it held, so what is drawn next in the same request already says the
    new number. Returns the number, or None when it couldn't be given."""
    savepoint = db.begin_nested()
    try:
        number = alias_for(db, scope, user_id, now)
        savepoint.commit()
    except Exception:
        savepoint.rollback()
        return None
    try:
        from flask import g, has_request_context
        if has_request_context():
            g.__dict__.get("_alias_numbers", {}).pop(scope, None)
    except Exception:
        pass
    return number


def numbers_in(db, scope):
    """{user id: number} for everyone who has one in this thread: one query a thread, which is all a page needs."""
    kind, scope_id = scope
    return {r[0]: r[1] for r in db.execute(text("SELECT user_id, n FROM anon_aliases WHERE scope_kind = :k AND scope_id = :s"), {"k": kind, "s": int(scope_id)})}


def numbers_cached(db, scope):
    """`numbers_in`, once a thread a request (what drawing a page of a thread needs, whoever asks): the request's own cache, which `give` clears. Outside a
    request, or where the table isn't there yet, nothing is cached and a missing table is no numbers."""
    try:
        from flask import g, has_request_context
        cache = g.__dict__.setdefault("_alias_numbers", {}) if has_request_context() else None
    except Exception:
        cache = None
    if cache is not None and scope in cache:
        return cache[scope]
    savepoint = db.begin_nested()
    try:
        found = numbers_in(db, scope)
        savepoint.commit()
    except Exception:                               # a database that hasn't reached the migration draws plain Anonymous
        savepoint.rollback()
        found = {}
    if cache is not None:
        cache[scope] = found
    return found


def numbers_for_items(db, post_ids=(), comment_ids=()):
    """{("post"|"comment", id): number} for these items' authors in their own threads, for the Log's lines (a deleted or edited item is drawn as its
    author's alias). An item whose author has no number yet is absent: it is drawn as plain "Anonymous"."""
    found = {}
    posts = sorted({int(i) for i in post_ids or () if i})
    comments = sorted({int(i) for i in comment_ids or () if i})
    if posts:
        marks = ", ".join(f":p{n}" for n in range(len(posts)))
        names = {f"p{n}": i for n, i in enumerate(posts)}
        for item, number in db.execute(text(
                f"SELECT s.id, a.n FROM submissions s JOIN anon_aliases a ON a.scope_kind = 'post' AND a.scope_id = COALESCE(NULLIF(s.repost_id, 0), s.id) "
                f"AND a.user_id = s.author_id WHERE s.id IN ({marks})"), names):
            found[("post", item)] = number
    if comments:
        marks = ", ".join(f":c{n}" for n in range(len(comments)))
        names = {f"c{n}": i for n, i in enumerate(comments)}
        for item, number in db.execute(text(
                f"SELECT c.id, a.n FROM comments c JOIN submissions s ON s.id = c.parent_submission JOIN anon_aliases a ON a.scope_kind = 'post' "
                f"AND a.scope_id = COALESCE(NULLIF(s.repost_id, 0), s.id) AND a.user_id = c.author_id WHERE c.id IN ({marks})"), names):
            found[("comment", item)] = number
    return found
