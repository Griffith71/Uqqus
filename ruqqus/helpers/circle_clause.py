"""The SQL condition that lets the people in an author's circles see the author's circle posts in the lists that are
about them.

A post made for an audience also has `post_public = false`, so every list that asks for public posts leaves it out. A
few lists are the viewer's own and should include what a member is allowed to see: the Following feed
(`User.idlist`), the author's profile (`User.userpagelisting`), and the viewer's saved, viewed and voted lists. They
add this condition next to their `post_public` one; nothing else does (circle posts are not in All, For You, search,
trending, curations or a guild's list).

    a post for circles (4)   the viewer is in one of the circles listed for it (post_circles): a member of a custom
                             circle, a paid-up subscriber of a paid one, or Friends when the two follow each other;
                             and there is no block either way

It is the same rule as helpers/circle_guard.py `may_see`, written as SQL. The tables are named here rather than
taken from the models, so the tests run the same expression on SQLite; `for_viewer` is what the lists call.
"""
import time

from sqlalchemy import and_, column, exists, false, or_, select, table

from ruqqus.helpers import circles

_POST_CIRCLES = table("post_circles", column("post_id"), column("circle_id"))
_CIRCLES = table("circles", column("id"), column("user_id"), column("kind"))
_MEMBERS = table("circle_members", column("circle_id"), column("user_id"), column("tier"), column("status"), column("renews_utc"))
_FOLLOWS = table("follows", column("user_id"), column("target_id"))
_BLOCKS = table("userblocks", column("user_id"), column("target_id"))


def member_clause(viewer, submission, now=None):
    """A condition over `submission` (anything with id, author_id and audience columns): true for a post made for
    circles that the viewer is in. False for nobody."""
    if viewer is None:
        return false()
    now = int(now if now is not None else time.time())
    link, circle, member = _POST_CIRCLES.alias("vc_link"), _CIRCLES.alias("vc_circle"), _MEMBERS.alias("vc_member")
    out, back, block = _FOLLOWS.alias("vc_out"), _FOLLOWS.alias("vc_back"), _BLOCKS.alias("vc_block")
    in_by_row = exists(select(member.c.circle_id).where(
        member.c.circle_id == circle.c.id, member.c.user_id == viewer.id, member.c.status == circles.ACTIVE,
        or_(and_(circle.c.kind == circles.CUSTOM, member.c.tier == circles.MEMBER),
            and_(circle.c.kind == circles.PAID, member.c.tier == circles.SUBSCRIBER, member.c.renews_utc > now))))
    in_friends = and_(
        circle.c.kind == circles.FRIENDS,
        exists(select(out.c.user_id).where(out.c.user_id == circle.c.user_id, out.c.target_id == viewer.id)),
        exists(select(back.c.user_id).where(back.c.user_id == viewer.id, back.c.target_id == circle.c.user_id)))
    in_one = exists(select(link.c.post_id).select_from(link.join(circle, circle.c.id == link.c.circle_id)).where(
        link.c.post_id == submission.id, circle.c.user_id == submission.author_id, or_(in_by_row, in_friends)))
    blocked = exists(select(block.c.user_id).where(or_(
        and_(block.c.user_id == submission.author_id, block.c.target_id == viewer.id),
        and_(block.c.user_id == viewer.id, block.c.target_id == submission.author_id))))
    return and_(submission.audience == circles.CIRCLES, in_one, ~blocked)


def for_viewer(viewer, now=None):
    """`member_clause` over the real Submission model."""
    from ruqqus.classes.submission import Submission
    return member_clause(viewer, Submission, now)
