"""Addresses of posts and comments (the owner, 2026-10-20): an item's address reads as an extension of whoever owns it,
with no title slug (a slug changed with every edit of the title).

    a post on a profile      /@alice/post/<id>
    a guild copy             /+pets/@carol/post/<id>     the guild owns its comments; carol forwarded it (the author
                                                         when nobody else did)
    a comment                /@bob/comment/<id>          the comment's own author
    a Space                  /@host/space/<id>           by its hidden backing row's id; a guild copy's card opens
                                                         the Space itself (helpers/spaces.py: a Space is not a post)

An anonymous item never carries a name, whoever looks: `/post/<id>`, `/+pets/post/<id>`, `/comment/<id>`. An anonymous
copy leaves out its forwarder too, even when someone else forwarded it: naming the forwarder only when it is not the
author would tell, by the gap, which copies the author forwarded.

The name in an address is decoration: the id decides what is shown, and any other name (or none) is redirected to the
item's own address. For an anonymous item that redirect is the same whatever name was typed, so a guessed name is
never confirmed. `parse` reads every address this site has ever given a post or comment, old forms included (stored
post text keeps them).

Pure: no Flask, no database.
"""

import re
from urllib.parse import urlsplit

B36 = r"[0-9a-z]{1,16}"
_NAME = r"@[A-Za-z0-9_]{1,30}"
_GUILD = r"\+[A-Za-z0-9_]{1,30}"
_SLUG = r"[^/?#]*"
_PID = rf"(?P<pid>{B36})"
_CID = rf"(?P<cid>{B36})"
_OLD_SLUG = rf"(?:/(?P<slug>{_SLUG}))?"

_FORMS = (
    re.compile(rf"^/{_NAME}/post/{_PID}/?$", re.I),
    re.compile(rf"^/{_GUILD}/{_NAME}/post/{_PID}/?$", re.I),
    re.compile(rf"^/{_GUILD}/post/{_PID}{_OLD_SLUG}/?$", re.I),
    re.compile(rf"^/post/{_PID}{_OLD_SLUG}/?$", re.I),
    re.compile(rf"^/{_NAME}/comment/{_CID}/?$", re.I),
    re.compile(rf"^/{_NAME}/space/(?P<sid>{B36})/?$", re.I),
    re.compile(rf"^/comment/{_CID}/?$", re.I),
    re.compile(rf"^/{_GUILD}/post/{_PID}/(?P<slug>{_SLUG})/{_CID}/?$", re.I),
    re.compile(rf"^/post/{_PID}/(?P<slug>{_SLUG})/{_CID}/?$", re.I),
)

# words that follow a post's id in addresses of other pages of it (/post/<id>/history, /insights ...): never a slug
_NOT_SLUGS = {"history", "insights", "insights.json", "written_by", "revisions", "space", "forward", "repost",
              "unrepost"}


def post_path(post_id36, *, name=None, guild=None, anonymous=False):
    """The address of a post. `name` is the author on a profile post and the forwarder (or the author) on a guild
    copy; `guild` is the copy's guild, None on a profile."""
    who = "" if anonymous or not name else f"/@{name}"
    where = f"/+{guild}" if guild else ""
    return f"{where}{who}/post/{post_id36}"


def space_path(post_id36, name):
    """The address of a Space: under its host, by the id of its backing row (a Space is never anonymous)."""
    return f"/@{name}/space/{post_id36}"


def comment_path(comment_id36, *, name=None, anonymous=False):
    """The address of a comment: under its own author, or bare when anonymous."""
    who = "" if anonymous or not name else f"/@{name}"
    return f"{who}/comment/{comment_id36}"


def parse(path):
    """(kind, id) for any address this site gives a post, a comment or a Space, old or new: kind "post", "comment" or
    "space" (by its backing row's id), the id in base 36, lower case. None for anything else (a profile, a guild,
    another page of a post)."""
    path = urlsplit(str(path or "")).path
    for form in _FORMS:
        match = form.match(path)
        if not match:
            continue
        found = match.groupdict()
        if (found.get("slug") or "").lower() in _NOT_SLUGS:
            return None
        if found.get("cid"):
            return "comment", found["cid"].lower()
        if found.get("sid"):
            return "space", found["sid"].lower()
        return "post", found["pid"].lower()
    return None


def same_path(requested, canonical):
    """Whether a request was already for the item's own address (a trailing slash doesn't count)."""
    return (requested or "").rstrip("/") == (canonical or "").rstrip("/")
