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
from babel.support import LazyProxy
from sqlalchemy import and_, not_, or_, true

ADMIN_LEVEL = 3          # the same bar as the other "admins may see this" checks
LABEL = "Anonymous"      # the stand-in's handle: English, as a handle is (what a reader reads is `shown`)
DEFAULT_AVATAR = "/assets/images/profiles/default-profile-pic.png"


def shown():
    """The stand-in's name as the reader reads it (Flask is imported only when a name is drawn)."""
    from ruqqus.helpers.i18n_runtime import tr
    return tr("Anonymous")


class AnonymousAuthor:
    """Stands in for the author of an anonymous item wherever a template draws
    the author (name, avatar, profile link, flair). Anything it does not define
    reads as empty, so a template that asks for something new cannot leak it."""

    id = None
    username = LABEL
    original_username = LABEL
    display_name = ""
    shown_name = LazyProxy(shown, enable_cache=False)
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
        return shown()


ANONYMOUS = AnonymousAuthor()


def current_viewer():
    """The logged-in viewer of the current request, for code that is not handed
    one (JSON, feeds). None for a visitor or outside a request."""
    from flask import g, has_request_context
    return getattr(g, "v", None) if has_request_context() else None


def held_from_own(item, viewer):
    """A limited QR sign-in (helpers/qr_login.py) looking at the member's own anonymous item. It is a stranger to it until the
    password is given: which ones are theirs is what anonymity exists to keep, and telling a relayed code once can't be taken back."""
    return bool(viewer is not None and getattr(viewer, "qr_limited", False) and getattr(item, "is_anonymous", False)
                and viewer.id == item.author_id)


def is_author(item, viewer):
    """Is `viewer` the author, as far as this request may be told? For anything that draws, or answers differently, for an
    item's author."""
    return viewer is not None and viewer.id == item.author_id and not held_from_own(item, viewer)


def own(model, viewer):
    """A query condition for `is_author`: the viewer's own rows of `model`, as far as this request may be told."""
    mine = model.author_id == viewer.id
    return and_(mine, not_(model.is_anonymous)) if getattr(viewer, "qr_limited", False) else mine


def viewer_sees_identity(item, viewer):
    """Only the author and site admins are told who wrote an anonymous item."""
    if viewer is None:
        return False
    return is_author(item, viewer) or (getattr(viewer, "admin_level", 0) or 0) >= ADMIN_LEVEL


def identity_hidden(item, viewer):
    return bool(getattr(item, "is_anonymous", False)) and not viewer_sees_identity(item, viewer)


def author_of(item, viewer):
    """The author to draw for this viewer: the real one, or the stand-in."""
    return ANONYMOUS if identity_hidden(item, viewer) else item.author


def label_of(item, viewer):
    """The author's name as text ("Anonymous" when hidden)."""
    return shown() if identity_hidden(item, viewer) else item.author.username


def op_badge(comment, post, viewer):
    """Show the "OP" mark on a comment by the post's author - unless that would
    give away an anonymous commenter on a post that is not anonymous.
    System notifications are comments with no post: nothing to be the submitter of."""
    if post is None or comment.author_id != post.author_id:
        return False
    if identity_hidden(comment, viewer):
        return identity_hidden(post, viewer)   # both anonymous: the same unnamed person
    # a named comment by the author of a post whose author is hidden from this reader: no mark, or the name beside it would say who wrote the
    # post ("comment as @me", the Log plan's decision 7: a named comment is linked to the anonymous post by nothing the site draws)
    return not identity_hidden(post, viewer)


def must_be_anonymous(post, commenter):
    """Someone commenting on their own anonymous post comments anonymously, or
    their name next to the "OP" mark would undo it."""
    return bool(getattr(post, "is_anonymous", False)) and post.author_id == commenter.id


def comment_is_anonymous(post, commenter, asked, as_me=False):
    """Is a new comment anonymous? On your own anonymous post it is, by default; "comment as @me" (`as_me`) is the one explicit way out, with its
    warning in the form (your name shows, and your own words can still give away that you wrote the post). Anywhere else it is what was asked."""
    if must_be_anonymous(post, commenter):
        return not as_me
    return bool(asked)


def off_profile(model, viewer):
    """A query condition for a profile's own tabs (posts, comments, forwarded): anonymous rows are left out for everyone,
    their author too, and shown only to site admins, who moderate (the owner, 2026-10-27: a profile is the account's
    public face; the author finds their own anonymous posts and comments in History > Anonymous, history_anonymous).
    Searches by author, the Following feed and the Log keep `hide_anonymous`."""
    if viewer is not None and (getattr(viewer, "admin_level", 0) or 0) >= ADMIN_LEVEL:
        return true()
    return not_(model.is_anonymous)


def hide_anonymous(model, viewer):
    """A query condition for a list of one author's posts or comments (their
    profile tabs, an author: search, the Following feed): anonymous rows are
    left out unless the viewer is that author or an admin. `model` is
    Submission or Comment. Use it ONLY on lists scoped to an author - an
    anonymous post still appears in its guild or the home feed."""
    if viewer is not None and (getattr(viewer, "admin_level", 0) or 0) >= ADMIN_LEVEL:
        return true()
    visible = not_(model.is_anonymous)
    if viewer is None or getattr(viewer, "qr_limited", False):
        return visible
    return or_(visible, and_(model.is_anonymous, model.author_id == viewer.id))
