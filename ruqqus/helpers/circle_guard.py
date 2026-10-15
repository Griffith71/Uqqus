"""Who may see something made for an audience (rules: helpers/circles.py `can_see`).

This is the second line of defence. Lists already leave a circle post out because it also has `post_public = false`;
the functions here are called by the one place every route reads a post or a comment through (helpers/get.py:
`get_post`, `get_posts`, `get_comment`, `get_comments`), so a route that forgets to think about circles still cannot
show one. A viewer is `viewer` if given, else the signed-in member of this request (`g.v`), else nobody.

A post for circles (audience 4) is seen by its author, by site admins (level 4, as for private guilds: they moderate
it) and by anyone in ANY of the circles it is for right now (circle_store.viewer_circle_ids: a custom circle's
members, a paid circle's paid-up subscribers, Friends when the two follow each other). A block either way hides it.

Plain SQL, so the same statements run on SQLite in the tests. What a request looks up (a viewer's circles, a post's
circles) is remembered for that request only.
"""
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
            return g.__dict__.setdefault("_circle_lookups", {})
    except Exception:
        pass
    return {}


def viewer_circles(db, owner_id, viewer_id, now=None):
    """The owner's circles the viewer is in right now, asked once per request."""
    cache = _remember()
    key = ("in", owner_id, viewer_id)
    if key not in cache:
        cache[key] = frozenset(circle_store.viewer_circle_ids(db, owner_id, viewer_id, now))
    return cache[key]


def post_circles(db, post_ids):
    """{post id: frozenset of circle ids} for these posts, each asked once per request."""
    cache = _remember()
    wanted = [i for i in {int(p) for p in post_ids if p} if ("post", i) not in cache]
    if wanted:
        for post_id, found in circle_store.post_circle_ids(db, wanted).items():
            cache[("post", post_id)] = frozenset(found)
    return {int(p): cache.get(("post", int(p)), frozenset()) for p in post_ids if p}


def story_circles(db, story_ids):
    """{story id: frozenset of circle ids} for these stories, each asked once per request."""
    cache = _remember()
    wanted = [i for i in {int(s) for s in story_ids if s} if ("story", i) not in cache]
    if wanted:
        for story_id, found in circle_store.story_circle_ids(db, wanted).items():
            cache[("story", story_id)] = frozenset(found)
    return {int(s): cache.get(("story", int(s)), frozenset()) for s in story_ids if s}


_IN_GUILD = text("""
    SELECT 1 WHERE (
        EXISTS (SELECT 1 FROM contributors WHERE user_id = :u AND board_id = :b AND is_active = :yes)
        OR EXISTS (SELECT 1 FROM mods WHERE user_id = :u AND board_id = :b AND accepted = :yes AND COALESCE(invite_rescinded, :no) = :no)
    ) AND NOT EXISTS (SELECT 1 FROM bans WHERE user_id = :u AND board_id = :b AND is_active = :yes)
""")


def in_guild(db, board_id, user_id):
    """Is this account a member of a Circle guild: an approved contributor or a guildmaster, and not exiled?"""
    cache = _remember()
    key = ("guild", board_id, user_id)
    if key not in cache:
        cache[key] = bool(board_id and db.execute(_IN_GUILD, {"u": user_id, "b": board_id, "yes": True, "no": False}).fetchone())
    return cache[key]


def may_see(db, audience, author_id, viewer=None, now=None, board_id=None, circle_ids=()):
    """May the viewer see something made for `audience` by `author_id`? Public always; admins always; a Circle guild's
    posts (audience 3, in board `board_id`) only to the guild's members (the author too: leaving or being exiled ends
    it); a post or story for circles (audience 4, for `circle_ids`) to its author and the people in any of them."""
    if not audience:
        return True
    viewer = current(viewer)
    if viewer is None:
        return False
    if (getattr(viewer, "admin_level", 0) or 0) >= ADMIN_LEVEL:
        return True
    if audience == circles.STORY_OPEN:
        return viewer.id == author_id or not circle_store.blocked_between(db, author_id, viewer.id)
    if audience == circles.GUILD:
        return in_guild(db, board_id, viewer.id)
    if viewer.id == author_id:
        return True
    if audience == circles.CIRCLES:
        return circles.can_see(audience, is_author=False, viewer_circles=viewer_circles(db, author_id, viewer.id, now),
                               item_circles=circle_ids)
    return False                                           # an audience nobody knows about: fail closed


def may_see_post(db, post, viewer=None, now=None):
    audience = post.audience
    circle_ids = post_circles(db, [post.id]).get(post.id, ()) if audience == circles.CIRCLES else ()
    return may_see(db, audience, post.author_id, viewer, now, getattr(post, "board_id", None), circle_ids)


def visible_posts(db, posts, viewer=None, now=None):
    """The posts the viewer may see, in order. Costs nothing when none of them has an audience; one query for the
    circles of all the posts that are for circles."""
    if not any(getattr(p, "audience", 0) for p in posts):
        return posts
    post_circles(db, [p.id for p in posts if p.audience == circles.CIRCLES])
    return [p for p in posts if may_see_post(db, p, viewer, now)]


_PARENTS = text("SELECT id, audience, author_id, board_id FROM submissions WHERE id IN :ids AND audience <> 0").bindparams(
    bindparam("ids", expanding=True))


def hidden_parents(db, post_ids):
    """{post id: (audience, author id, board id, circle ids)} for those of these posts that are made for an audience."""
    ids = sorted({i for i in post_ids if i})
    if not ids:
        return {}
    rows = db.execute(_PARENTS, {"ids": ids}).fetchall()
    found = post_circles(db, [r.id for r in rows if r.audience == circles.CIRCLES])
    return {row.id: (row.audience, row.author_id, row.board_id, found.get(row.id, frozenset())) for row in rows}


def visible_comments(db, comments, viewer=None, now=None):
    """The comments the viewer may see: those under a post made for an audience are only for people who may see that post."""
    parents = hidden_parents(db, [getattr(comment, "parent_submission", None) for comment in comments])
    if not parents:
        return comments
    kept = []
    for comment in comments:
        found = parents.get(getattr(comment, "parent_submission", None))
        if found is None or may_see(db, found[0], found[1], viewer, now, found[2], found[3]):
            kept.append(comment)
    return kept


def under_audience(db, comment):
    """Is this comment on a post made for an audience? Such a comment is never reposted or forwarded: that would
    put what only the circles may read on a profile or in a guild."""
    return bool(hidden_parents(db, [getattr(comment, "parent_submission", None)]))


def may_see_comment(db, comment, viewer=None, now=None):
    return bool(visible_comments(db, [comment], viewer, now))


def guild_member_ids(db, board_id):
    """Account ids of a Circle guild's members (approved contributors and guildmasters, not exiled)."""
    rows = db.execute(text("""
        SELECT user_id FROM contributors WHERE board_id = :b AND is_active = :yes
        UNION SELECT user_id FROM mods WHERE board_id = :b AND accepted = :yes AND COALESCE(invite_rescinded, :no) = :no"""),
        {"b": board_id, "yes": True, "no": False}).fetchall()
    exiled = {r[0] for r in db.execute(text("SELECT user_id FROM bans WHERE board_id = :b AND is_active = :yes"), {"b": board_id, "yes": True}).fetchall()}
    return {r[0] for r in rows} - exiled


def eligible_ids(db, owner_id, audience, now=None, board_id=None, circle_ids=()):
    """Account ids that may see something the owner made for `audience`: for notifying them and nobody else."""
    if audience == circles.GUILD:
        return guild_member_ids(db, board_id)
    if audience == circles.CIRCLES:
        return circle_store.eligible_ids(db, owner_id, circle_ids, now)
    return set()
