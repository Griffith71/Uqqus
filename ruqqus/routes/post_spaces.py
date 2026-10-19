"""A post's comment spaces (helpers/post_spaces.py): the guilds a post on its author's profile was posted to, each with
its own comment section, listed on the post's page and opened one at a time, or all together.

- **Who sees which space**: only live copies the author posted or forwarded themselves, in guilds the viewer may see
  (`Submission.forwards_shown_to`: not removed, not in a banned or private guild they can't open) and that the viewer's
  own filters leave in (`post_hidden`). A guild that can't be shown reads as one that does not exist.
- **Writing**: a space is that guild's copy of the post, so its comment box posts to the copy through the unchanged
  `/api/comment` and follows the guild's own rules (`Board.can_comment`, exiles, blocks). "All guilds" is for reading:
  it merges the copies' threads in one list, each tagged with its guild, and has no box.
- The routes start with the checks every route that loads a post by id makes (`get_post` with its Circle guard,
  `in_private_guild_for`, `post_hidden`).
"""
from flask import abort, g, render_template, request
from jinja2 import Undefined

from ruqqus.classes import Submission
from ruqqus.helpers import comment_permission as cperm
from ruqqus.helpers import post_spaces as space_rules
from ruqqus.helpers.get import get_post, get_post_with_comments
from ruqqus.helpers.visibility import post_hidden
from ruqqus.helpers.wrappers import auth_desired
from ruqqus.__main__ import app


def spaces_of(hub, v):
    """The guild copies of a profile post that make up its comment spaces for this viewer (unranked)."""
    if hub.repost_id or not hub.is_profile_post or hub.deleted_utc or hub.is_banned:
        return []
    found = []
    for copy in hub.forwards_shown_to(v):
        if copy.forwarded_by is not None:        # forwarded by somebody else: it is theirs, not a space of this post
            continue
        if copy.in_private_guild_for(v) or post_hidden(copy, v):
            continue
        found.append(copy)
    return found


class Spaces:
    """What the post page draws: the ranked copies, the ones listed up front and the rest."""

    def __init__(self, copies, sort):
        self.copies = space_rules.ranked(copies, sort)
        self.shown, self.more = space_rules.listed(self.copies)
        self.sort = sort
        self.total_comments = sum(int(c.comment_count or 0) for c in self.copies)


def comment_spaces(post, viewer):
    """Jinja global: the comment spaces of a post as this viewer sees them (empty for a guild's own copy)."""
    viewer = None if isinstance(viewer, Undefined) else viewer
    sort = space_rules.sort_of(request.args.get("sort"))
    if not isinstance(post, Submission) or post.repost_id or not post.is_profile_post or cperm.is_space(post):
        return Spaces([], sort)
    memo = g.__dict__.setdefault("_comment_spaces", {})
    key = (post.id, sort, getattr(viewer, "id", 0))
    if key not in memo:
        memo[key] = Spaces(spaces_of(post, viewer), sort)
    return memo[key]


app.jinja_env.globals.update(comment_spaces=comment_spaces)


def _copy_with_comments(copy, sort, v):
    loaded = get_post_with_comments(copy.base36id, sort_type=sort, v=v)
    loaded.tree_comments(v=v)
    return loaded


@app.get("/post/<pid>/space/<which>")
@auth_desired
def post_space(pid, which, v):
    """One guild's comment space of a post on a profile, or `all` of them, as a piece of page."""
    hub = get_post(pid, v=v)
    if hub.in_private_guild_for(v) or post_hidden(hub, v):
        abort(404)

    sort = space_rules.sort_of(request.args.get("sort"))
    copies = space_rules.ranked(spaces_of(hub, v), sort)

    if which == "all":
        per_copy = []
        for copy in copies:
            loaded = _copy_with_comments(copy, sort, v)
            per_copy.append((loaded, list(loaded.__dict__.get("nested_comments", []))))
        entries = space_rules.merge_threads(per_copy, sort)
        return render_template("post_space.html", v=v, hub=hub, copies=copies, entries=entries, copy=None, sort=sort,
                               render_child_comments=True)

    name = which.lstrip("+").lower()
    copy = next((c for c in copies if c.board.name.lower() == name), None)
    if copy is None:
        abort(404)
    loaded = _copy_with_comments(copy, sort, v)
    allowed = loaded.board.can_comment(v) and not loaded.is_archived
    return render_template("post_space.html", v=v, hub=hub, copies=copies, entries=None, copy=loaded, sort=sort,
                           p=loaded, is_allowed_to_comment=allowed,
                           comment_restriction=cperm.restriction(loaded, v) if v else cperm.closed_notice(loaded),
                           render_child_comments=True)
