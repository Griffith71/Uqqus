"""Which posts are collaborative (published from a shared draft), without a query per card: the ids are read once per
TTL seconds per worker, like co-authored posts (helpers/coauthors.py). routes/collab.py calls `forget()` after a first
publish; other workers catch up within TTL seconds (a post younger than that is asked of the database: `is_collab`)."""
import time

TTL = 30
_cache = {"at": 0.0, "ids": frozenset()}


def post_ids():
    from flask import g, has_request_context
    from ruqqus.helpers import collab_store

    if not has_request_context() or not hasattr(g, "db"):
        return frozenset()
    now = time.time()
    if now - _cache["at"] > TTL:
        _cache["ids"] = frozenset(collab_store.post_ids(g.db))
        _cache["at"] = now
    return _cache["ids"]


def forget():
    _cache["at"] = 0.0


def is_collab(post):
    """A post (or a forwarded copy of one) published from a shared draft. A post younger than the cache can be newer
    than the list this worker holds (published on another worker), so it is asked of the database, once a request."""
    if post is None:
        return False
    key = getattr(post, "repost_id", None) or post.id
    if key in post_ids():
        return True
    created = getattr(post, "created_utc", 0) or 0
    if not created or time.time() - created > 2 * TTL:
        return False
    from flask import g
    from ruqqus.helpers import collab_store
    memo = g.__dict__.setdefault("_collab_new", {})
    if key not in memo:
        memo[key] = collab_store.doc_for_post(g.db, key) is not None
    return memo[key]
