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

# A post made for guilds only (the owner's "where to post", routes/posts.py `post_to_profile`) still lists on its
# author's profile, but its profile entry has no comment space: the comments are in the guilds' own spaces
# (routes/post_spaces.py). Not a choice in "Who can comment?" (the forms offer MODES); the composer's "My profile"
# switch and the post menu's "comments on my profile" switch set and clear it. Literal like No one: nobody comments on
# the profile entry, the author and admins included.
GUILDS = 4

# A Space's post (routes/spaces.py): it is a post, with votes and shares, but its discussion is the Space's own board,
# so neither the post nor any copy of it in a guild takes comments. Literal for everyone, set when the Space is made.
SPACE = 5

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
GUILDS_NOTICE = "This post has no comments on the profile. Its comments are in the guilds it was posted to."
SPACE_NOTICE = "This is a Space: its discussion is in the Space."

ADMIN_LEVEL = 3   # same bar as the other "admins may do this anyway" checks


def parse(raw, allow_guilds=False):
    """A form value as a mode, or None when it is not one of ours. GUILDS is only taken where the caller says so (the
    post's own menu and edit, for a post that has guilds to hold its comments)."""
    try:
        mode = int(str(raw).strip())
    except ValueError:
        return None
    if mode == GUILDS and allow_guilds:
        return mode
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
    author's own profile with a limit, or a Space's post (SPACE on every copy)."""
    if getattr(post, "comment_permission", None) == SPACE:
        return SPACE
    if not getattr(post, "is_profile_post", False):
        return EVERYONE
    mode = getattr(post, "comment_permission", None) or EVERYONE
    if mode == GUILDS:
        return GUILDS
    if mode not in MODES or refusal(mode, getattr(post, "is_anonymous", False)):
        return EVERYONE
    return mode


def is_space(post):
    """True for a Space's post or a guild copy of it: it takes no comments (see SPACE)."""
    return mode_of(post) == SPACE


def is_guilds_only(post):
    """True for a profile entry whose comment space is the guilds' (see GUILDS)."""
    return mode_of(post) == GUILDS


def closed_notice(post):
    """The sentence for a post whose comments are off, or None. Safe for a visitor:
    it says nothing about who the viewer is."""
    mode = mode_of(post)
    if mode == SPACE:
        return SPACE_NOTICE
    if mode == GUILDS:
        return GUILDS_NOTICE
    return CLOSED_NOTICE if mode == NOBODY else None


def restriction(post, viewer):
    """Why `viewer` may not comment on `post`, as a sentence for them, or None
    when they may. The author and site admins are never limited, except by "No one":
    that really is nobody (the author can switch it back)."""
    mode = mode_of(post)
    if mode == EVERYONE:
        return None
    if mode == SPACE:
        return SPACE_NOTICE
    if mode == GUILDS:
        return GUILDS_NOTICE
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
