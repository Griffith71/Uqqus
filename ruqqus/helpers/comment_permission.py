"""Who may comment on a post ("Who can comment?" in the post forms).

An author can limit comments on a post on their own profile to everyone,
accounts they follow, Premium accounts, or turn them off (no one). The author
can change it again at any time, from the post's menu or the Edit form. Only
that primary post has the setting: a forwarded copy lives in a guild and
follows that guild's own rules (Board.can_comment), so it never reads the
author's choice.

Kept free of routes, templates and the database so the rule has one home;
callers pass the post and the viewer.
"""

EVERYONE, FOLLOWED, PREMIUM, NOBODY = 0, 1, 2, 3

MODES = (EVERYONE, FOLLOWED, PREMIUM, NOBODY)

# the choices in the post forms, in display order
CHOICES = (
    (EVERYONE, "Everyone"),
    (FOLLOWED, "Accounts you follow"),
    (PREMIUM, "Premium accounts"),
    (NOBODY, "No one"),
)

# shown where the comment box would be. It names nobody, so it is as safe on an
# anonymous post as anywhere else.
CLOSED_NOTICE = "Comments are turned off for this post."

ADMIN_LEVEL = 3   # same bar as the other "admins may do this anyway" checks


def parse(raw):
    """A form value as a mode, or None when it is not one of ours."""
    try:
        mode = int(str(raw).strip())
    except ValueError:
        return None
    return mode if mode in MODES else None


# "Accounts you follow" on an anonymous post would tell each reader whether
# the author follows them, so it is refused there and never applied
ANONYMOUS_REFUSAL = ("Accounts you follow can't be chosen for an anonymous post: "
                     "it would show people whether you follow them.")


def refusal(mode, is_anonymous):
    """Why `mode` can't be chosen for a post, or None when it can."""
    if is_anonymous and mode == FOLLOWED:
        return ANONYMOUS_REFUSAL
    return None


def mode_of(post):
    """The setting that applies to a post: EVERYONE unless it is a post on its
    author's own profile with a limit."""
    if not getattr(post, "is_profile_post", False):
        return EVERYONE
    mode = getattr(post, "comment_permission", None) or EVERYONE
    if mode not in MODES or refusal(mode, getattr(post, "is_anonymous", False)):
        return EVERYONE
    return mode


def closed_notice(post):
    """The sentence for a post whose comments are off, or None. Safe for a visitor:
    it says nothing about who the viewer is."""
    return CLOSED_NOTICE if mode_of(post) == NOBODY else None


def restriction(post, viewer):
    """Why `viewer` may not comment on `post`, as a sentence for them, or None
    when they may. The author and site admins are never limited, except by "No one":
    that really is nobody (the author can switch it back)."""
    mode = mode_of(post)
    if mode == EVERYONE:
        return None
    if mode == NOBODY:
        return CLOSED_NOTICE

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
