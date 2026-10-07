"""Muting an account: its posts and comments leave your feeds and threads, and its
notifications stop. Quiet and one-way: the account is not told and can still follow
you, comment on your posts and message you (that is what a block is for).

THE RULE FOR NEW CODE: wherever a feed, a search or a notification list already
leaves out the authors a viewer BLOCKED, it leaves out the ones they MUTED too:
`hide_muted(query, v, Model)` on a query, `is_muted(item, v)` for one post or
comment in a template. Not on a profile, a saved list or a history: going to
someone you muted, or to something you saved, is a choice.

An anonymous post or comment is never hidden by a mute. Hiding only those would
tell the viewer which anonymous items their muted account wrote (the same reason
blocks leave anonymous posts in feeds, see helpers/anonymity.py).
"""
from sqlalchemy import or_, select


def muted_ids(v):
    """A subquery of the ids of the accounts `v` muted."""
    from ruqqus.classes.usermute import UserMute
    return select(UserMute.target_id).where(UserMute.user_id == v.id)


def hide_muted(query, v, model):
    """`query` without the rows by accounts `v` muted. `model` is Submission or Comment
    (anything with author_id and is_anonymous). A visitor has muted nobody."""
    if v is None:
        return query
    return query.filter(or_(model.author_id.notin_(muted_ids(v)), model.is_anonymous == True))


def muted_set(v):
    """The ids `v` muted, read once per request."""
    from flask import g, has_request_context

    if v is None:
        return frozenset()
    from ruqqus.classes.usermute import UserMute

    if not has_request_context():
        return frozenset(i for (i,) in g.db.query(UserMute.target_id).filter_by(user_id=v.id).all())
    cache = g.__dict__.setdefault("_muted_sets", {})
    if v.id not in cache:
        cache[v.id] = frozenset(i for (i,) in g.db.query(UserMute.target_id).filter_by(user_id=v.id).all())
    return cache[v.id]


def is_muted(item, v):
    """Does `v` have this post or comment's author muted (and is the item not anonymous)?"""
    if v is None or getattr(item, "is_anonymous", False):
        return False
    return item.author_id in muted_set(v)


def forget(v):
    """Drop the per-request answer after a mute or unmute."""
    from flask import g
    g.__dict__.get("_muted_sets", {}).pop(v.id, None)


def clear_cached_feeds(v):
    """Feeds that are kept for a few minutes per viewer must not keep showing (or hiding)
    an account after a mute or unmute. The per-guild lists (Board.idlist) are shared and
    last 60 seconds; they catch up by themselves."""
    from ruqqus.__main__ import cache
    from ruqqus.routes.curations import curation_idlist
    from ruqqus.routes.front import frontlist
    from ruqqus.routes.search import searchlisting

    cache.delete_memoized(v.idlist)
    cache.delete_memoized(v.for_you_idlist)
    cache.delete_memoized(frontlist, v=v)
    cache.delete_memoized(curation_idlist)
    cache.delete_memoized(searchlisting)
