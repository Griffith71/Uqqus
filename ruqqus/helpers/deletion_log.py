"""The site-wide deletion log and the filters of a guild's mod log: the rules, with no I/O.

The deletion log (`/log/deleted`, routes/deletion_log.py) is a public record of the posts and comments
their authors deleted. Each entry shows the text the item had when it was deleted (`content_edit_history`
keeps it; the owner chose a public log WITH the text, knowing a deleted post stays readable here). What
the log never shows: anything that was not public to begin with (a Circle, a private guild), anything a
moderator or an admin removed, anything an admin obliterated (its stored text is scrubbed) and anything
the viewer's word filter hides. An anonymous item shows as Anonymous and is left out of a list that
names one author, as everywhere else (helpers/anonymity.py).

A guild's mod log (`/+guild/mod/log`) already records what its moderators hid, removed or exiled;
`SHOW` lets a reader narrow it to one kind of action.
"""
import re

PER_PAGE = 25
MAX_PAGE = 1000

POSTS = "posts"
COMMENTS = "comments"
TABS = (POSTS, COMMENTS)

GUILDS_SHOWN = 5          # how many of the guilds a post was forwarded to an entry names

# the mod log's own filters: what to show of the ModAction kinds
SHOW = {
    "all": None,
    "posts": ("hide_post_from_guild", "unhide_post_from_guild", "kick_post", "yank_post", "approve_post",
              "ban_post", "unban_post", "purge_post", "obliterate_post"),
    "comments": ("hide_comment_from_guild", "unhide_comment_from_guild", "ban_comment", "unban_comment",
                 "purge_comment", "obliterate_comment"),
    "exiles": ("exile_user", "unexile_user", "chatban_user", "unchatban_user"),
}
SHOW_LABELS = {
    "all": "All",
    "posts": "Posts hidden or removed",
    "comments": "Comments hidden or removed",
    "exiles": "Exiles and chat bans",
}

_DIGITS = re.compile(r"[0-9]{1,4}")
_PICTURE = re.compile(r"<img\b[^>]*>", re.I)
_SOUND = re.compile(r"<audio\b.*?</audio>", re.I | re.S)


def parse_tab(raw):
    """posts (the default) or comments."""
    return raw if raw in TABS else POSTS


def parse_show(raw):
    """One of SHOW's names; anything else is `all`."""
    return raw if raw in SHOW else "all"


def parse_page(raw):
    """A page number: ASCII digits only (str.isdigit() also accepts other scripts' digits), 1 to MAX_PAGE."""
    if raw is None or not _DIGITS.fullmatch(str(raw)):
        return 1
    return max(1, min(int(raw), MAX_PAGE))


def kinds_for(show):
    """The ModAction kinds a filter keeps, or None for all of them."""
    return SHOW[parse_show(show)]


def _count(n, unit):
    return f"{n} {unit}{'' if n == 1 else 's'}"


def lived(seconds):
    """How long something was up before it was deleted, in words."""
    seconds = max(0, int(seconds or 0))
    if seconds < 60:
        return "less than a minute"
    if seconds < 3600:
        return _count(seconds // 60, "minute")
    if seconds < 86400:
        return _count(seconds // 3600, "hour")
    return _count(seconds // 86400, "day")


def without_media(html):
    """The stored text of an entry with its pictures and sounds replaced by a plain marker. A deleted post's
    uploads are detached when it is deleted, so the addresses in its text no longer serve anything: a broken
    picture in a public log helps nobody. Only tags are removed and a fixed marker put in, so what comes out
    is as safe as what went in."""
    html = _SOUND.sub('<span class="text-muted">[sound]</span>', html or "")
    return _PICTURE.sub('<span class="text-muted">[picture]</span>', html)


def has_text(title, body, body_html):
    """False for an entry whose stored text was scrubbed (an admin obliterated the item)."""
    return bool(title or body or body_html)
