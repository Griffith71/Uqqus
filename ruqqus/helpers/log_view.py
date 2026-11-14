"""How the Log is drawn and filtered: the public page (`/log`), a guild's own log (`/+guild/mod/log`) and the rules they share,
with no I/O (helpers/action_log.py holds the entries and who may read them).

The public Log shows, for a hide or a removal, **who (by role), when, where, which rule and the written reason, never the
title or the text** of what was hidden (the owner, 2026-10-31). A sentence names its subject by id ("post #a1b2c"), so
nothing the author wrote reaches it. **What an author deleted themselves** (the Deleted tab, from slice D3a: the owner's decision of
2026-11-05) **stays here as Anonymous, with its words** under the word-filter, sensitive and Child-level rules of any member text, and
without a clickable link or a picture (`without_links`, `without_media`); inside the five-minute grace window a delete leaves nothing.

A guild's own log keeps its filters (`SHOW`): the owner can narrow it to one kind of action.
"""
import re

from babel.support import LazyProxy
from markupsafe import Markup, escape

from ruqqus.helpers.i18n_runtime import N_, msg, tr, trp

PER_PAGE = 25
MAX_PAGE = 1000

# the public Log's tabs: the entries' `log_type`s a visitor may read (suppressions are level 4, in /admin/log)
CONTENT, SANCTION, GUILD_CHANGE, DELETED, EDITED = "content", "sanction", "guild", "deletion", "edit"
TABS = (CONTENT, SANCTION, GUILD_CHANGE, DELETED, EDITED)
TAB_LABELS = {
    CONTENT: N_("Moderation"),
    SANCTION: N_("Sanctions"),
    GUILD_CHANGE: N_("Guild changes"),
    DELETED: N_("Deleted"),
    EDITED: N_("Edits"),
}
# the Deleted and Edits tabs' one extra filter: posts or comments (the actions their entries are written with)
WHAT = {DELETED: {"post": "author_delete_post", "comment": "author_delete_comment"},
        EDITED: {"post": "edit_post", "comment": "edit_comment"}}
WHAT_NAMES = ("post", "comment")


def action_for(tab, what):
    """The action a tab's posts-or-comments filter selects, or None for both."""
    return WHAT.get(tab, {}).get(what)

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
    """content (the default), sanction, guild or deletion."""
    return raw if raw in TABS else CONTENT


def parse_what(raw):
    """The Deleted and Edits tabs' filter: `post`, `comment` or None for both."""
    return raw if raw in WHAT_NAMES else None


def group_edits(rows):
    """The Edits tab's lines with an item's CONSECUTIVE edits folded into one ("N edits in a row", the others behind a disclosure), as Wikipedia folds a
    page's run of changes: the log stays one row per edit, only the drawing is short. `rows` are `_rows` lines, newest first, each with an `item`
    (kind, id); a run is broken by any other item between two edits of one."""
    grouped = []
    for row in rows:
        if grouped and row.get("item") is not None and grouped[-1].get("item") == row["item"]:
            grouped[-1].setdefault("more", []).append(row)
        else:
            grouped.append(dict(row))
    return grouped


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


def anonymous_actor(number=None):
    """Who deleted it, on the Deleted tab: nobody is named, whoever looks (the item is anonymous from the moment it is deleted), only the number its author has
    in that thread ("Anonymous #3", helpers/anon_aliases.py) so the thread's people can be told apart without being named."""
    if number:
        return Markup(escape(tr("Anonymous #%(num)s", num=int(number))))
    return Markup(escape(tr("Anonymous")))


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


def without_links(html):
    """The stored text of a deleted item with its links taken off, the words kept: a link in text that stays public after its author is gone is
    not a door to anywhere the site vouches for (it may lead to a domain banned since), and a mention names an account. Only tags are removed, so
    what comes out is as safe as what went in."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html or "", "html.parser")
    for link in soup.find_all("a"):
        link.unwrap()
    return str(soup)


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
