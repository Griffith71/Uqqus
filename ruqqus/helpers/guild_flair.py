"""Guild flair (pure rules, no I/O; the SQL is helpers/guild_flair_store.py, the routes routes/guild_flair.py).

A guild's guildmasters keep a list of flairs (Reddit's post flair): a short name and a colour from a fixed palette
(`COLOURS`, drawn by `.flair-<colour>` in both stylesheets: never free CSS, like helpers/post_formatting.py). Posts
reach a guild only as forwards, so a flair belongs to the guild's copy (`submissions.guild_flair_id`), never to the
post on its author's profile.

- **Who sets it**: whoever forwards the post picks one in the Forward dialog; a guildmaster with content permission
  changes or clears it at any time; in a Circle guild, where members post straight in, the author picks it.
- **Required**: a guild can require a flair (`boards.require_flair`): a forward without one is refused. The composer's
  "Forward to guilds" can't pick a flair per guild, so a guild that requires one is forwarded to from the post's own
  Forward dialog (`COMPOSER_REQUIRED`).
- **Filtering**: readers filter a guild by one or more flairs (`?flair=3&flair=7`, `parse_filter`); search takes
  `flair:"Name"` inside a guild.
- Deleting a flair clears it from the posts that had it; no post is ever deleted with it.
"""
import re

MAX_FLAIRS = 30
NAME_MAX = 24
MAX_FILTER = 10                 # flairs one filter may combine

# the palette: names only; each is a rule in main.scss and main_dark.scss
COLOURS = ("gray", "red", "orange", "yellow", "green", "teal", "blue", "indigo", "purple", "pink")
DEFAULT_COLOUR = "gray"

REQUIRED = "+{guild} needs a flair on every forward. Pick one."
COMPOSER_REQUIRED = ("+{guild} needs a flair on every forward: post first, then forward it from the post's menu "
                     "(Forward), where you can pick one.")
NOT_THIS_GUILD = "That flair isn't one of +{guild}'s."

_SPACE = re.compile(r"\s+")


class Refused(ValueError):
    """Shown to the member as it is."""


def clean_name(value):
    """A flair's name: one line of plain text, NAME_MAX characters, no markup characters."""
    name = _SPACE.sub(" ", str(value or "")).strip()
    if not name:
        raise Refused("Name the flair.")
    if len(name) > NAME_MAX:
        raise Refused(f"A flair can be at most {NAME_MAX} characters.")
    if any(c in name for c in "<>{}[]`\\") or any(ord(c) < 32 for c in name):
        raise Refused("A flair is plain text: no < > { } [ ] ` or \\.")
    return name


def clean_colour(value):
    colour = str(value or "").strip().lower()
    if not colour:
        return DEFAULT_COLOUR
    if colour not in COLOURS:
        raise Refused("Pick one of the colours.")
    return colour


def make_refusal(count):
    return f"A guild can have at most {MAX_FLAIRS} flairs." if count >= MAX_FLAIRS else None


def parse_id(value):
    """An id from a form or the address: ASCII digits only, else None."""
    text = str(value or "").strip()
    return int(text) if text.isascii() and text.isdigit() and len(text) <= 12 else None


def parse_filter(values, known_ids):
    """The flairs a reader filters a guild by (`?flair=` repeated), kept only when they are this guild's, sorted and
    without repeats, at most MAX_FILTER: the tuple is part of the guild listing's cache key."""
    known = set(known_ids or ())
    chosen = {fid for fid in (parse_id(v) for v in (values or ())) if fid is not None and fid in known}
    return tuple(sorted(chosen))[:MAX_FILTER]


def may_set(viewer_id, forwarder_id, is_content_guildmaster):
    """Who may set or change the flair on a guild's copy: whoever forwarded it, or a guildmaster with content
    permission."""
    return viewer_id is not None and (viewer_id == forwarder_id or bool(is_content_guildmaster))


def forward_refusal(require_flair, flair_id, guild_flair_ids, guild_name):
    """Why a forward (or a Circle-guild post) with this flair can't go into the guild, or None."""
    if flair_id is not None and flair_id not in set(guild_flair_ids or ()):
        return NOT_THIS_GUILD.format(guild=guild_name)
    if require_flair and flair_id is None and guild_flair_ids:
        return REQUIRED.format(guild=guild_name)
    return None
