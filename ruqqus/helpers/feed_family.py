"""One card per post in the algorithmic feeds (audit F-01).

A forward is a copy of a post in a guild (its own row, `repost_id` pointing at the original). All, For You and
Following used to list the original and every copy as separate cards, so one post filled two or three places on a
page. `one_per_family` leaves a copy out whenever its original is live, public and also in the feed: the original
stands for the whole family (its card links the guilds it was forwarded to). A copy whose original isn't there (a
guild you follow, an author you don't) still shows, so nothing goes missing.
"""
from sqlalchemy import and_, exists, or_
from sqlalchemy.orm import aliased


def one_per_family(query, model, original_in_feed=None):
    """`query` over `model` (Submission) without the copies whose original is live, public (audience 0, the profile
    post) and, when `original_in_feed(original)` is given, also in this feed."""
    original = aliased(model)
    conditions = [original.id == model.repost_id, original.deleted_utc == 0, original.is_banned.isnot(True),
                  original.audience == 0]
    if original_in_feed is not None:
        conditions.append(original_in_feed(original))
    return query.filter(or_(model.repost_id.is_(None), model.repost_id == 0, ~exists().where(and_(*conditions))))
