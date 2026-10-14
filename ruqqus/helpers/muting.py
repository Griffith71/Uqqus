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

MUTING A GUILD is the same idea for a guild (`boardblocks`, the model is still called
BoardBlock): its posts leave All, For You and Following and the member can still visit it,
search it and stay joined. `hide_muted_guilds(query, v, model)` is the helper for NEW
listings; the older ones (All, For You, search, trending, curations, suggestions) carry the
same subquery inline. The member's own mutes are not a curation's "leave out" list: that
one belongs to the curation (helpers/feed_algorithm.py) and uses `exclude_authors` below.
"""
from sqlalchemy import or_, select


def muted_ids(v):
    """A subquery of the ids of the accounts `v` muted."""
    from ruqqus.classes.usermute import UserMute
    return select(UserMute.target_id).where(UserMute.user_id == v.id)


def exclude_authors(query, ids, model):
    """`query` without the rows by the accounts in `ids` (a subquery or a list of ids), keeping
    anonymous rows and dropping a post co-authored with one of them. `model` is Submission or
    Comment (anything with author_id and is_anonymous). The one rule behind a member's mutes and
    a curation's "leave out" list."""
    query = query.filter(or_(model.author_id.notin_(ids), model.is_anonymous == True))
    if getattr(model, "__tablename__", "") == "submissions":
        # a post co-authored with such an account carries its name too (helpers/blocks.py does the same for blocks)
        from ruqqus.helpers.blocks import coauthored_with
        query = query.filter(~coauthored_with(model, ids))
    return query


def hide_muted(query, v, model):
    """`query` without the rows by accounts `v` muted. `model` is Submission or Comment
    (anything with author_id and is_anonymous). A visitor has muted nobody."""
    if v is None:
        return query
    return exclude_authors(query, muted_ids(v), model)


def muted_guild_ids(v):
    """A subquery of the ids of the guilds `v` muted (the `boardblocks` rows)."""
    from ruqqus.classes.board_relationships import BoardBlock
    return select(BoardBlock.board_id).where(BoardBlock.user_id == v.id)


def hide_muted_guilds(query, v, model):
    """`query` without the posts in guilds `v` muted. `model` is Submission (anything with
    board_id). A visitor has muted nothing. Not on a guild's own page, a profile or a search
    for that guild: going there is a choice."""
    if v is None:
        return query
    return query.filter(model.board_id.notin_(muted_guild_ids(v)))


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


def clear_guild_mute_caches(v):
    """After muting or unmuting a guild: the feeds above, and also what For You learns its categories from
    (`interest_subcats`, an hour) and the All comments list, so nothing keeps showing a muted guild."""
    from ruqqus.__main__ import cache
    from ruqqus.routes.front import comment_idlist

    clear_cached_feeds(v)
    cache.delete_memoized(v.interest_subcats)
    cache.delete_memoized(comment_idlist, v=v)
