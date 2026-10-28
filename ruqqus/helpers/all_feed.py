"""An account's choice to keep its profile posts out of All (the owner, 2026-10-27: "maybe have an option if the profile
wants their post to appear on All"). `users.all_opt_out`, false by default: every account is in All until it switches
this off in Settings > Content, as a guild already can (`boards.all_opt_out`).

What it covers: the account's own posts, which all live on its profile. Not:
* **its anonymous posts**, which always stay in All: leaving out only some anonymous posts because of who wrote them would
  tell readers which ones are this account's (the same promise mutes, blocks and sensitive accounts keep);
* **guild copies** of its posts, which follow each guild's own rule: a forward is the guild's, and a copy shows in All
  when its original is out of it (`feed_family.one_per_family` is given this same condition);
* **the viewer's own posts**: your All still shows yours.

Pure: it takes the columns, so tests/test_all_feed.py runs it on SQLite.
"""
from sqlalchemy import func, or_, select


def in_all(model, viewer, user_model):
    """A condition over `model` (Submission, or an alias of it): may this post be in All for `viewer`?"""
    opted_out = select(user_model.id).where(user_model.all_opt_out == True)
    terms = [
        func.coalesce(model.repost_id, 0) != 0,                     # a guild copy: the guild's rule, not the account's
        func.coalesce(model.is_anonymous, False) == True,           # never judged by who wrote it
        model.author_id.notin_(opted_out),
    ]
    if viewer is not None:
        terms.append(model.author_id == viewer.id)
    return or_(*terms)
