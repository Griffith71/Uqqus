"""The SQL condition that lets a Circle's members see its posts in the lists that are about them.

A post made for an audience also has `post_public = false`, so every list that asks for public posts leaves it out. A
few lists are the viewer's own and should include what a member is allowed to see: the Following feed
(`User.idlist`), the author's profile (`User.userpagelisting`), and the viewer's saved, viewed and voted lists. They
add this condition next to their `post_public` one; nothing else does (Circle posts are not in All, For You, search,
trending, curations or a guild's list).

    Subscribers   author_id is an account whose Circle the viewer is a friend of OR a paid-up subscriber to
    Close Friends author_id is an account whose Circle the viewer is a friend of

The models are parameters so the tests can run the same expression on SQLite; `for_viewer` is what the lists call.
"""
import time

from sqlalchemy import and_, false, or_, select

from ruqqus.helpers import circles


def member_clause(viewer, submission, circle, member, now=None):
    """A condition over `submission`: true for a post made for a Circle the viewer may see. False for nobody."""
    if viewer is None:
        return false()
    now = int(now if now is not None else time.time())
    mine = and_(member.user_id == viewer.id, member.status == circles.ACTIVE, circle.user_id.isnot(None))
    friend_of = select(circle.user_id).join(member, member.circle_id == circle.id).where(mine, member.tier == circles.FRIEND)
    paid_to = select(circle.user_id).join(member, member.circle_id == circle.id).where(
        mine, member.tier == circles.SUBSCRIBER, member.renews_utc > now)
    return or_(
        and_(submission.audience == circles.SUBSCRIBERS, or_(submission.author_id.in_(friend_of), submission.author_id.in_(paid_to))),
        and_(submission.audience == circles.FRIENDS, submission.author_id.in_(friend_of)),
    )


def for_viewer(viewer, now=None):
    """`member_clause` over the real models."""
    from ruqqus.classes.circle import Circle, CircleMember
    from ruqqus.classes.submission import Submission
    return member_clause(viewer, Submission, Circle, CircleMember, now)
