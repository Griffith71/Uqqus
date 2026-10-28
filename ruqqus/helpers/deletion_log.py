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

from babel.support import LazyProxy
from markupsafe import escape

from ruqqus.helpers.i18n_runtime import N_, tr, trn, trp

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
# marked with N_ and drawn with msg_text() (guild/modlog.html); "All" alone is ambiguous, so it is a lazy string with
# its context (the filter's), which msg_text() draws as it is
SHOW_LABELS = {
    "all": LazyProxy(lambda: trp("filter", "All"), enable_cache=False),
    "posts": N_("Posts hidden or removed"),
    "comments": N_("Comments hidden or removed"),
    "exiles": N_("Exiles and chat bans"),
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


def lived(seconds):
    """How long something was up before it was deleted, in words."""
    seconds = max(0, int(seconds or 0))
    if seconds < 60:
        return tr("less than a minute")
    if seconds < 3600:
        return trn("%(num)s minute", "%(num)s minutes", seconds // 60)
    if seconds < 86400:
        return trn("%(num)s hour", "%(num)s hours", seconds // 3600)
    return trn("%(num)s day", "%(num)s days", seconds // 86400)


def without_media(html):
    """The stored text of an entry with its pictures and sounds replaced by a plain marker. A deleted post's
    uploads are detached when it is deleted, so the addresses in its text no longer serve anything: a broken
    picture in a public log helps nobody. Only tags are removed and a fixed marker put in, so what comes out
    is as safe as what went in."""
    # Translators: stands in a deleted post's text where a sound was
    sound = f'<span class="text-muted">{escape(tr("[sound]"))}</span>'
    # Translators: stands in a deleted post's text where a picture was
    picture = f'<span class="text-muted">{escape(tr("[picture]"))}</span>'
    html = _SOUND.sub(lambda _m: sound, html or "")
    return _PICTURE.sub(lambda _m: picture, html)


def has_text(title, body, body_html):
    """False for an entry whose stored text was scrubbed (an admin obliterated the item)."""
    return bool(title or body or body_html)
