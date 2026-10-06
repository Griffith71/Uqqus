"""Anonymous posts and comments.

An author can post or comment anonymously. The real `author_id` stays on the
row (bans, spam handling, copyright and CSAM reports and admin tools all need
it) but nobody except the author and site admins is told who it is. It is
anonymous to other users, not to Ruqqus.

THE RULE FOR NEW CODE: wherever a page, a JSON answer, a feed or a
notification would show who wrote a post or comment, go through this module -
`author_of(item, viewer)` for the author object, `identity_hidden(item,
viewer)` for a yes/no, `hide_anonymous(model, viewer)` for a query that lists
by author. Never read `.author` / `.author_id` for display directly.

Kept free of Flask and the database so the rule can be tested; the Jinja
globals that expose it to templates are registered in routes/anonymity.py.
"""
from sqlalchemy import and_, not_, or_, true

ADMIN_LEVEL = 3          # the same bar as the other "admins may see this" checks
LABEL = "Anonymous"
DEFAULT_AVATAR = "/assets/images/profiles/default-profile-pic.png"


class AnonymousAuthor:
    """Stands in for the author of an anonymous item wherever a template draws
    the author (name, avatar, profile link, flair). Anything it does not define
    reads as empty, so a template that asks for something new cannot leak it."""

    id = None
    username = LABEL
    original_username = LABEL
    fullname = LABEL
    permalink = "javascript:void(0)"
    permalink_full = "javascript:void(0)"
    profile_url = DEFAULT_AVATAR
    is_deleted = False
    is_private = False
    is_banned = False
    admin_level = 0

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return None

    def __bool__(self):
        return True

    def __str__(self):
        return LABEL


ANONYMOUS = AnonymousAuthor()


def current_viewer():
    """The logged-in viewer of the current request, for code that is not handed
    one (JSON, feeds). None for a visitor or outside a request."""
    from flask import g, has_request_context
    return getattr(g, "v", None) if has_request_context() else None


def viewer_sees_identity(item, viewer):
    """Only the author and site admins are told who wrote an anonymous item."""
    if viewer is None:
        return False
    return viewer.id == item.author_id or (getattr(viewer, "admin_level", 0) or 0) >= ADMIN_LEVEL


def identity_hidden(item, viewer):
    return bool(getattr(item, "is_anonymous", False)) and not viewer_sees_identity(item, viewer)


def author_of(item, viewer):
    """The author to draw for this viewer: the real one, or the stand-in."""
    return ANONYMOUS if identity_hidden(item, viewer) else item.author


def label_of(item, viewer):
    """The author's name as text ("Anonymous" when hidden)."""
    return LABEL if identity_hidden(item, viewer) else item.author.username


def op_badge(comment, post, viewer):
    """Show the "OP" mark on a comment by the post's author - unless that would
    give away an anonymous commenter on a post that is not anonymous."""
    if comment.author_id != post.author_id:
        return False
    if identity_hidden(comment, viewer):
        return identity_hidden(post, viewer)   # both anonymous: the same unnamed person
    return True


def must_be_anonymous(post, commenter):
    """Someone commenting on their own anonymous post comments anonymously, or
    their name next to the "OP" mark would undo it."""
    return bool(getattr(post, "is_anonymous", False)) and post.author_id == commenter.id


def hide_anonymous(model, viewer):
    """A query condition for a list of one author's posts or comments (their
    profile tabs, an author: search, the Following feed): anonymous rows are
    left out unless the viewer is that author or an admin. `model` is
    Submission or Comment. Use it ONLY on lists scoped to an author - an
    anonymous post still appears in its guild or the home feed."""
    if viewer is not None and (getattr(viewer, "admin_level", 0) or 0) >= ADMIN_LEVEL:
        return true()
    visible = not_(model.is_anonymous)
    if viewer is None:
        return visible
    return or_(visible, and_(model.is_anonymous, model.author_id == viewer.id))
