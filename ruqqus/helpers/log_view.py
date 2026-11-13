"""How the Log is drawn and filtered: the public page (`/log`), a guild's own log (`/+guild/mod/log`) and the rules they share,
with no I/O (helpers/action_log.py holds the entries and who may read them).

The public Log shows, for a hide or a removal, **who (by role), when, where, which rule and the written reason, never the
title or the text** of what was hidden (the owner, 2026-10-31). A sentence names its subject by id ("post #a1b2c"), so
nothing the author wrote reaches it. What an author deleted themselves is not in the Log at all.

A guild's own log keeps its filters (`SHOW`): the owner can narrow it to one kind of action.
"""
import re

from babel.support import LazyProxy
from markupsafe import Markup, escape

from ruqqus.helpers.i18n_runtime import N_, msg, tr, trp

PER_PAGE = 25
MAX_PAGE = 1000

# the public Log's tabs: the entries' `log_type`s a visitor may read (suppressions are level 4, in /admin/log)
CONTENT, SANCTION, GUILD_CHANGE = "content", "sanction", "guild"
TABS = (CONTENT, SANCTION, GUILD_CHANGE)
TAB_LABELS = {
    CONTENT: N_("Moderation"),
    SANCTION: N_("Sanctions"),
    GUILD_CHANGE: N_("Guild changes"),
}

# the guild log's own filters: what to show of the ModAction kinds
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
_CURSOR = re.compile(r"[0-9]{1,15}")

_PICTURE = re.compile(r"<img\b[^>]*>", re.I)
_SOUND = re.compile(r"<audio\b.*?</audio>", re.I | re.S)


def parse_tab(raw):
    """content (the default), sanction or guild."""
    return raw if raw in TABS else CONTENT


def parse_show(raw):
    """One of SHOW's names; anything else is `all`."""
    return raw if raw in SHOW else "all"


def parse_page(raw):
    """A page number: ASCII digits only (str.isdigit() also accepts other scripts' digits), 1 to MAX_PAGE."""
    if raw is None or not _DIGITS.fullmatch(str(raw)):
        return 1
    return max(1, min(int(raw), MAX_PAGE))


def parse_cursor(raw):
    """The id an 'older entries' link starts after: ASCII digits only, at most 15 of them, or None."""
    return int(raw) if raw is not None and _CURSOR.fullmatch(str(raw)) else None


def kinds_for(show):
    """The ModAction kinds a filter keeps, or None for all of them."""
    return SHOW[parse_show(show)]


def person(name, deleted=False, hidden=False):
    """A person in a sentence: a link to their profile, 'a member' when their name trips the reader's filter."""
    if deleted:
        return Markup(escape(tr("[deleted user]")))
    if hidden:
        return Markup(escape(tr("A member")))
    return Markup('<a href="/@{0}" class="font-weight-bold text-black">@{0}</a>').format(name)


def site_account(site_name):
    """The site's own account: what an admin or an automatic action is drawn as (an admin is never named)."""
    return Markup('<a href="/@{0}" class="font-weight-bold text-black">@{0}</a>').format(site_name)


def post_author():
    """A post's author hiding a comment under it: who is not drawn by name (the post may be anonymous), only the role."""
    return Markup(escape(tr("The post's author")))


def item_link(kind, base36id):
    """A post or a comment as a sentence names it: a link by id, never by title."""
    if kind == "post":
        return Markup('<a href="/post/{0}">#{0}</a>').format(base36id)
    return Markup('<a href="/comment/{0}">{1}</a>').format(base36id, tr("comment"))


def sentence(action, templates, who, target="", guild=""):
    """The entry as a line of words: `who` + what was done. `templates` is the ModAction table (classes/mod_logs.py
    ACTIONTYPES) so the words are the ones the guild logs always used, in the reader's language; `target` is a person or an
    item by id (item_link, person), `guild` the guild's name. An action the table doesn't know reads as its own name."""
    entry = templates.get(action)
    if entry is None:
        return Markup("{} {}").format(who, str(action).replace("_", " "))
    template = msg(entry["str"])
    names = set(re.findall(r"%\((\w+)\)s", template))
    values = {"target": target, "name": target, "guild": guild or "", "title": target}
    try:
        body = Markup(template) % {n: values.get(n, "") for n in names}
    except (KeyError, ValueError, TypeError):
        body = Markup(escape(entry["str"]))
    return Markup("{} {}").format(who, body)


def without_media(html):
    """The stored text of a preserved copy with its pictures and sounds replaced by a plain marker: an item's uploads are
    detached when it is deleted, so their addresses no longer serve anything. Only tags are removed and a fixed marker put
    in, so what comes out is as safe as what went in."""
    # Translators: stands in a preserved copy's text where a sound was
    sound = f'<span class="text-muted">{escape(tr("[sound]"))}</span>'
    # Translators: stands in a preserved copy's text where a picture was
    picture = f'<span class="text-muted">{escape(tr("[picture]"))}</span>'
    html = _SOUND.sub(lambda _m: sound, html or "")
    return _PICTURE.sub(lambda _m: picture, html)
