"""Blocks in lists: what a viewer's feeds leave out.

A block works both ways (owner's rule, 2026-10-09): a list leaves out the posts and comments of the accounts the
viewer blocked AND of the accounts that blocked the viewer, and posts co-authored with any of them. An anonymous
item is never left out for its author: leaving out only those would tell the viewer who wrote them (the same rule
as mutes, helpers/muting.py). A block is not a mute: this module reads `userblocks` only.
"""
from sqlalchemy import and_, exists, func, or_, select


def _ids(v):
    from ruqqus.classes.userblock import UserBlock
    return select(UserBlock.target_id).where(UserBlock.user_id == v.id).union(
        select(UserBlock.user_id).where(UserBlock.target_id == v.id))


def hide_blocked(query, v, model):
    """`query` without the rows by accounts blocked either way with `v`. `model` is Submission or Comment.
    A visitor has blocked nobody."""
    if v is None:
        return query
    ids = _ids(v)
    query = query.filter(or_(model.author_id.notin_(ids), model.is_anonymous.is_(True)))
    if model.__tablename__ == "submissions":
        query = query.filter(~coauthored_with(model, ids))
    return query


def coauthored_with(model, user_ids):
    """A condition: the post (its primary, for a forwarded copy) has an accepted co-author in `user_ids`."""
    from ruqqus.classes.coauthors import PostCoauthor
    primary = func.coalesce(func.nullif(model.repost_id, 0), model.id)
    return exists().where(and_(PostCoauthor.post_id == primary, PostCoauthor.status == "accepted",
                               PostCoauthor.user_id.in_(user_ids)))
