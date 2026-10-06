"""Who may comment on a post ("Who can comment?" in the post forms).

An author can limit comments on a post on their own profile to everyone,
accounts they follow, or Premium accounts. Only that primary post has the
setting: a forwarded copy lives in a guild and follows that guild's own rules
(Board.can_comment), so it never reads the author's choice.

Kept free of routes, templates and the database so the rule has one home;
callers pass the post and the viewer.
"""

EVERYONE, FOLLOWED, PREMIUM = 0, 1, 2

MODES = (EVERYONE, FOLLOWED, PREMIUM)

# the choices in the post forms, in display order
CHOICES = (
    (EVERYONE, "Everyone"),
    (FOLLOWED, "Accounts you follow"),
    (PREMIUM, "Premium accounts"),
)

ADMIN_LEVEL = 3   # same bar as the other "admins may do this anyway" checks


def parse(raw):
    """A form value as a mode, or None when it is not one of ours."""
    try:
        mode = int(str(raw).strip())
    except ValueError:
        return None
    return mode if mode in MODES else None


def mode_of(post):
    """The setting that applies to a post: EVERYONE unless it is a post on its
    author's own profile with a limit."""
    if not getattr(post, "is_profile_post", False):
        return EVERYONE
    mode = getattr(post, "comment_permission", None) or EVERYONE
    return mode if mode in MODES else EVERYONE


def restriction(post, viewer):
    """Why `viewer` may not comment on `post`, as a sentence for them, or None
    when they may. The author and site admins are never limited."""
    mode = mode_of(post)
    if mode == EVERYONE:
        return None

    if viewer is not None:
        if viewer.id == post.author_id or (viewer.admin_level or 0) >= ADMIN_LEVEL:
            return None
        if mode == FOLLOWED and viewer.id in post.author.following_ids:
            return None
        if mode == PREMIUM and viewer.has_premium_no_renew:
            return None

    if mode == FOLLOWED:
        return f"Only accounts @{post.author.username} follows can comment on this post."
    return "Only Premium accounts can comment on this post."
