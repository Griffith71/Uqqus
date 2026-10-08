"""Who may see something made for an audience (rules: helpers/circles.py `can_see`).

This is the second line of defence. Lists already leave a Circle post out because it also has `post_public = false`;
the functions here are called by the one place every route reads a post or a comment through (helpers/get.py:
`get_post`, `get_posts`, `get_comment`, `get_comments`), so a route that forgets to think about Circles still cannot
show one. A viewer is `viewer` if given, else the signed-in member of this request (`g.v`), else nobody.

Plain SQL, so the same statements run on SQLite in the tests. The tiers looked up in a request are remembered for
that request only. Site admins (level 4, as for private guilds) may see everything: they moderate it.
"""
import time

from sqlalchemy import bindparam, text

from ruqqus.helpers import circle_store, circles

ADMIN_LEVEL = 4


def current(viewer=None):
    """The viewer: the one given, else the signed-in member of this request, else None."""
    if viewer is not None:
        return viewer
    try:
        from flask import g, has_request_context
        return getattr(g, "v", None) if has_request_context() else None
    except Exception:
        return None


def _remember():
    """A dict that lives as long as this request (a throwaway one outside a request)."""
    try:
        from flask import g, has_request_context
        if has_request_context():
            return g.__dict__.setdefault("_circle_tiers", {})
    except Exception:
        pass
    return {}


def tier(db, owner_id, viewer_id, now=None):
    """The viewer's tier in the owner's Circle (None: not in it), asked once per request."""
    cache = _remember()
    key = (owner_id, viewer_id)
    if key not in cache:
        cache[key] = circle_store.tier_for(db, owner_id, viewer_id, now)
    return cache[key]


def may_see(db, audience, author_id, viewer=None, now=None):
    """May the viewer see something made for `audience` by `author_id`? Public always; the author and admins always;
    otherwise only a member of the author's Circle in the right tier. A Circle guild's posts (audience 3) are decided
    by the guild, not here: until a guild asks, nobody else sees them."""
    if not audience:
        return True
    viewer = current(viewer)
    if viewer is None:
        return False
    if viewer.id == author_id or (getattr(viewer, "admin_level", 0) or 0) >= ADMIN_LEVEL:
        return True
    if audience == circles.GUILD:
        return False
    return circles.can_see(audience, is_author=False, tier=tier(db, author_id, viewer.id, now))


def may_see_post(db, post, viewer=None, now=None):
    return may_see(db, post.audience, post.author_id, viewer, now)


def visible_posts(db, posts, viewer=None, now=None):
    """The posts the viewer may see, in order. Costs nothing when none of them has an audience."""
    if not any(getattr(p, "audience", 0) for p in posts):
        return posts
    return [p for p in posts if may_see_post(db, p, viewer, now)]


_PARENTS = text("SELECT id, audience, author_id FROM submissions WHERE id IN :ids AND audience <> 0").bindparams(
    bindparam("ids", expanding=True))


def hidden_parents(db, post_ids):
    """{post id: (audience, author id)} for those of these posts that are made for an audience."""
    ids = sorted({i for i in post_ids if i})
    if not ids:
        return {}
    return {row.id: (row.audience, row.author_id) for row in db.execute(_PARENTS, {"ids": ids}).fetchall()}


def visible_comments(db, comments, viewer=None, now=None):
    """The comments the viewer may see: those under a post made for an audience are only for people who may see that post."""
    parents = hidden_parents(db, [getattr(c, "parent_submission", None) for c in comments])
    if not parents:
        return comments
    kept = []
    for comment in comments:
        found = parents.get(getattr(comment, "parent_submission", None))
        if found is None or may_see(db, found[0], found[1], viewer, now):
            kept.append(comment)
    return kept


def under_audience(db, comment):
    """Is this comment on a post made for an audience? Such a comment is never reposted or forwarded: that would
    put what only the Circle may read on a profile or in a guild."""
    return bool(hidden_parents(db, [getattr(comment, "parent_submission", None)]))


def may_see_comment(db, comment, viewer=None, now=None):
    return bool(visible_comments(db, [comment], viewer, now))


def eligible_ids(db, owner_id, audience, now=None):
    """Account ids that may see something the owner made for `audience`: for notifying them and nobody else."""
    now = int(now if now is not None else time.time())
    rows = db.execute(text("""
        SELECT m.user_id, m.tier, m.status, m.renews_utc FROM circle_members m
        JOIN circles c ON c.id = m.circle_id WHERE c.user_id = :o"""), {"o": owner_id}).fetchall()
    return {r.user_id for r in rows if circles.can_see(audience, is_author=False, tier=circles.tier_of(r.tier, r.status, r.renews_utc, now))}
