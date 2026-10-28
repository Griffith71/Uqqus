"""Which kind a search is, and the tab bar of its results page (the owner, 2026-10-26: "the search needs a tab filter like
reddit.com", Posts, Comments, Guilds, Accounts and Curations). Rules only: no I/O, stdlib and helpers/search_query.py.

A search is of one kind. Until the tabs, the first character typed picked it: plain text searched posts, `+` guilds, `@`
accounts, `#` a topic's guilds, `&` public curations, and `?type=comments` searched comments. Those keep their meaning
(**a typed symbol still wins**, so every old link works and typing `+pets` from any results page still switches kind), and
`?type=posts|comments|guilds|accounts|curations` is the same thing written as a parameter, which is what a tab link and the
navbar box carry:

    /search?q=pets&type=guilds      is turned into      /search?q=%2Bpets      before the search runs

so the four prefix searches, their pages and their JSON are untouched. A topic search (`#food`) is the Guilds tab with the
topic chips it always had.

Only a post or comment search takes operators (from:, since:, has: ...: helpers/search_query.py). A tab-driven guild,
account or curation search takes the free words of the query alone and says so.
"""
from collections import namedtuple
from urllib.parse import quote

from ruqqus.helpers import search_query

MAX_TEXT = 40                    # the name of a guild, an account or a curation is searched, never a paragraph

Tab = namedtuple("Tab", "key symbol icon")
TABS = (
    Tab("posts", "", "fa-newspaper"),
    Tab("comments", "", "fa-comment"),
    Tab("guilds", "+", "fa-chess-rook"),
    Tab("accounts", "@", "fa-user"),
    Tab("curations", "&", "fa-stream"),
)
KEYS = tuple(t.key for t in TABS)
SYMBOL_OF = {t.key: t.symbol for t in TABS}
ICON_OF = {t.key: t.icon for t in TABS}
KIND_OF_SYMBOL = {"+": "guilds", "#": "guilds", "@": "accounts", "&": "curations"}
NAME_KINDS = ("guilds", "accounts", "curations")        # searched by a name, not by the post operators
GUILD_KINDS = ("posts", "comments")                     # the kinds a guild's own search has

Resolved = namedtuple("Resolved", "kind query refused ignored_filters")


def words_of(text):
    """The free words and phrases of a search, without its operators or its minus words: what a name search can use."""
    parsed = search_query.parse(text)
    return " ".join(p.value for p in parsed.parts if p.kind in search_query.TEXT and not p.negated).strip()


def has_operators(text):
    return bool(search_query.parse(text).operators)


def resolve(raw_q, raw_type):
    """What kind of search this is and the text the existing branches of routes/search.py `search` read.

    * a typed symbol first (`+ @ # &`) wins, the text unchanged;
    * else a whitelisted `type`; for a guild, account or curation tab the text is the query's free words with that kind's
      symbol put in front, cut to MAX_TEXT characters; with no words it is `refused` (a tab-driven search never lists a whole
      kind: typing a bare symbol still does, as it always did);
    * else posts (and `type=comments` for comments, as before)."""
    q = (raw_q or "").strip()
    if q[:1] in KIND_OF_SYMBOL:
        return Resolved(KIND_OF_SYMBOL[q[0]], q, False, False)
    kind = (raw_type or "").strip().lower()
    if kind not in KEYS:
        return Resolved("posts", q, False, False)
    if kind not in NAME_KINDS:
        return Resolved(kind, q, False, False)
    words = words_of(q)[:MAX_TEXT].strip()
    if not words:
        return Resolved(kind, q, True, False)
    return Resolved(kind, SYMBOL_OF[kind] + words, False, has_operators(q))


def kind_of(query, comment_search=False):
    """The kind of a search from the text it ran as (a typed symbol, or comments by their own page)."""
    q = (query or "").strip()
    if q[:1] in KIND_OF_SYMBOL:
        return KIND_OF_SYMBOL[q[0]]
    return "comments" if comment_search else "posts"


def bare(query, kind):
    """The text without its symbol: what every other tab starts from. Posts and comments keep their operators; a name
    search keeps its words (the symbols of a typed `++x` are all one symbol)."""
    q = (query or "").strip()
    if kind in NAME_KINDS and q[:1] in KIND_OF_SYMBOL:
        return q.lstrip(q[0]).strip()
    return q


def _href(base, kind, text):
    out = f"{base}?q={quote(text, safe='')}"
    return out + ("&type=" + kind if kind != "posts" else "")


def tabs(query, comment_search=False, guild_name=None, kind=None):
    """The tab bar: [{key, icon, href, active}], in the bar's order. In a guild's own search only Posts and Comments are
    there (the other kinds are the whole site's). The active tab links to the page as it is (a topic search stays a topic
    search); every other tab starts from the query without its symbol and, for a guild, account or curation tab, from its
    free words alone."""
    kind = kind if kind in KEYS else kind_of(query, comment_search)      # (a tab-driven search with nothing typed names its own)
    base = f"/+{guild_name}/search" if guild_name else "/search"
    text = bare(query, kind)
    typed = (query or "").strip()
    current = f"{base}?q={quote(typed, safe='')}"
    if typed[:1] not in KIND_OF_SYMBOL and kind != "posts":
        current += "&type=" + kind                      # comments, or a tab-driven search with nothing typed
    out = []
    for tab in TABS:
        if guild_name and tab.key not in GUILD_KINDS:
            continue
        if tab.key == kind:
            href = current
        elif tab.key in NAME_KINDS:
            href = _href(base, tab.key, words_of(text) if kind not in NAME_KINDS else text)
        else:
            href = _href(base, tab.key, text)
        out.append({"key": tab.key, "icon": tab.icon, "href": href, "active": tab.key == kind})
    return out


def kept_type(path, args):
    """The `type` a search box on the page it is on should send again, so searching for something else stays on the tab:
    only on a results page, only a whitelisted non-default one, and a guild's own search only keeps comments."""
    if path == "/search":
        allowed = KEYS
    elif path.startswith("/+") and path.endswith("/search"):
        allowed = GUILD_KINDS
    else:
        return ""
    kind = (args.get("type") or "").strip().lower()
    return kind if kind in allowed and kind != "posts" else ""


def filters_ignored(raw_q, raw_type):
    """Did a tab-driven guild, account or curation search drop operators the query carried?"""
    return resolve(raw_q, raw_type).ignored_filters
