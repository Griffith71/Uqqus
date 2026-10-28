"""Where a post is discussed: its places for the post page's Post Info box (templates/submission.html, the owner's
2026-10-23 ask). A post lives on its author's profile (the hub) and in the guilds it was forwarded to (copies, each
with its own votes and comments); the box lists the other places, the ones with the most traction first, three of
them until the reader opens the full list.

Counts only, never who. A place is listed only when the viewer may open it: the hub through get_post's own guards (a
Circle post they may not see is None), every copy through forwards_shown_to (live, not removed, not hidden by its
guild, not in a banned guild, not in a private guild they can't read) and post_hidden (their word filter). The hub is
"Original post", never the author's name, so an anonymous post stays anonymous.
"""
from dataclasses import dataclass

from ruqqus.helpers.i18n_runtime import tr

SHOWN = 3          # rows before "Show all"
LISTED = 100       # rows in the full list; the rest are counted


@dataclass(frozen=True)
class Place:
    url: str
    label: str
    comments: int
    ups: int
    is_hub: bool = False


def ranked(places):
    """Most traction first: comments, then upvotes, then the label (a stable order)."""
    return sorted(places, key=lambda pl: (-pl.comments, -pl.ups, pl.label))


def place_of(post, is_hub=False):
    return Place(url=post.permalink, label=tr("Original post") if is_hub else f"+{post.board.name}",
                 comments=post.comment_count or 0, ups=post.upvotes_fuzzed or 0, is_hub=is_hub)


def places_for(p, v):
    """The places other than `p` itself where its post is discussed, ranked, for this viewer."""
    from flask import g
    from ruqqus.helpers.get import get_post
    from ruqqus.helpers.visibility import post_hidden

    cache = g.__dict__.setdefault("_post_places", {})
    if p.id in cache:
        return cache[p.id]

    places = []
    if p.is_profile_post and not p.repost_id:
        hub = p
    elif p.repost_id:
        hub = get_post(p.repost_id, v=v, graceful=True)
        if hub is not None and (not hub.is_profile_post or hub.deleted_utc or hub.is_banned
                                or hub.in_private_guild_for(v) or post_hidden(hub, v)):
            hub = None
        if hub is not None:
            places.append(place_of(hub, is_hub=True))
    else:
        hub = None
    if hub is not None:
        places += [place_of(copy) for copy in hub.forwards_shown_to(v)
                   if copy.id != p.id and not post_hidden(copy, v)]
    cache[p.id] = ranked(places)
    return cache[p.id]
