"""The site's own count of requests for its pages: the rules, with no I/O (the Redis and SQL side is
helpers/traffic_store.py, the page routes/admin_traffic_pages.py, the sending helpers/traffic_sender.py).

**Requests, not people.** There are no unique visitors and no devices here, on purpose: nothing identifies who asked, so
one person opening ten pages is ten. What is kept is one number per UTC day for each kind of page, each site that linked
here and each country, each counted on its own (they are never crossed, which would multiply the rows and could single
somebody out). No id, name, address, user agent or path is stored anywhere, Redis included.

What counts is an **allowlist of endpoints** (`ENDPOINT_KINDS`): a new route counts for nothing until someone adds it here.
Only a successful GET of a real page counts:
- never HEAD: Flask answers one on every GET route, and what sends it is a monitor or a link checker. Nobody reads a page
  by HEAD.
- never a redirect, an error, or anything that isn't HTML (`.json`, the API).
- never a page in a side panel's frame (`?embed=...`), nor one a script fetched for itself: `fetchPage` in
  assets/js/infinite_scroll.js sends `MARKER_HEADER`, or an open feed tab would add a view every 2 minutes and each page
  of scrolling another, and a browser that says what a request is for (`Sec-Fetch-Dest`) is believed: anything but a
  `document` is a script's fetch or a frame.
- never a prefetch or anything on the list of robots (helpers/insights.py), which is what a robot says of itself and is never
  complete.

**It can be inflated, not grown.** Anyone can send these headers, so a bot that hides what it is adds to the numbers. What it
can't do is make more rows: `country_of` has at most 26 x 26 answers, `referrer_of` gives a normalised host and nothing
else (never one that holds an address, or a private machine's name), the hosts kept between flushes and per day are capped (`LIVE_HOSTS`, `DAILY_HOSTS`, largest first, so a flood of
random names each seen once can't push out a site that really links here for long) and the rest are `OTHER`."""
import re
import time
from collections import namedtuple
from urllib.parse import urlsplit

from ruqqus.helpers.bots import is_own_host
from ruqqus.helpers.insights import is_prefetch, is_robot

DAY = 86400
WINDOWS = (7, 28, 90)
DEFAULT_WINDOW = 28
KEEP_DAYS = 400                 # helpers/retention.py TRAFFIC_DAYS

# what fetchPage in assets/js/infinite_scroll.js sends: a page a script reads for its cards is not a page opened
MARKER_HEADER = "X-Background-Fetch"

DIRECT = "direct"               # no Referer
INTERNAL = "internal"           # one of this site's own names
OTHER = "other"                 # an address that gives no usable host, or one past the cap
UNKNOWN = "unknown"             # a country header that isn't two capital letters, or says it doesn't know

LIVE_HOSTS = 500                # referring hosts held in Redis between two flushes
DAILY_HOSTS = 200               # referring hosts kept in the table for one day
MAX_HOST = 100                  # site_traffic.value is varchar(100)
MAX_REFERER = 2048              # a longer header is not read at all

# what a counted request leaves behind, all of it. A new field here is a change to the privacy page
Hit = namedtuple("Hit", "day kind referrer country")

# the kinds, in English: the admin page is English, like every admin page
KIND_LABELS = {
    "for_you": "Home and For You", "all": "All", "following": "Following", "post": "A post", "comment": "A comment",
    "profile": "A profile", "guild": "A guild", "wiki": "A guild's wiki", "events": "A guild's events",
    "topic": "A topic or category", "explore": "Explore", "curation": "A curation", "search": "Search",
    "trending": "Trending", "happening": "What's happening", "spaces": "Spaces", "chat": "Chat",
    "notifications": "Notifications", "history": "History", "create": "Create a post, guild or curation",
    "settings": "Settings", "circles": "Circles", "mine": "My guilds and people", "creator": "Creator Studio and insights",
    "sign_in": "Sign in and sign up", "help": "Help and legal pages", "log": "Log", "regions": "Regions",
}

_PAGES = {
    "for_you": ("for_you",),    # `/` too, and a visitor's `/` is All: one endpoint, so one row, and its label says so
    "all": ("front_all", "all_comments"),
    "following": ("following",),
    "post": ("post_base36id_with_comments",),
    "comment": ("post_pid_comment_cid",),
    "profile": ("u_username", "u_username_comments", "u_username_forwarded", "profile_followers", "profile_following",
                "profile_subscribers", "profile_subscriptions", "profile_channels"),
    "guild": ("board_name", "board_comments", "guild_chat"),
    "wiki": ("guild_wiki_index", "guild_wiki_page", "guild_wiki_pages", "guild_wiki_history", "guild_wiki_diff", "guild_wiki_revision"),
    "events": ("guild_events", "guild_events_calendar", "guild_event"),
    "topic": ("topic_page", "subcat"),
    "explore": ("browse_guilds", "curations_browse"),
    "curation": ("curation_detail", "multiboard"),
    "search": ("search", "search_guild"),
    "trending": ("trending_page", "trending_topic"),
    "happening": ("happening_overview", "happening_tab"),
    "spaces": ("spaces_home", "space_home_page", "spaces_new"),
    "chat": ("chat_page",),
    "notifications": ("notifications", "notifications_posts"),
    "history": ("history_bookmarked", "history_upvoted", "history_downvoted", "history_viewed", "history_anonymous"),
    "create": ("submit_get", "collab_list", "collab_editor", "create_board_get", "curations_create_get"),
    "settings": ("settings_profile", "settings_content_get", "settings_security", "settings_media", "settings_premium",
                 "settings_apps", "settings_bots", "settings_chat_apps", "settings_creator_fund", "settings_blockedpage",
                 "settings_purchase_history"),
    "circles": ("settings_circle", "settings_circle_one", "settings_circles"),
    "mine": ("my_guilds", "my_subs"),
    "creator": ("studio", "insights_overview", "post_insights"),
    "sign_in": ("login_get", "sign_up_get", "get_forgot", "get_reset"),
    "help": ("help_home", "help_path", "help_terms", "help_search", "help_admins", "help_feed_servers", "badges", "titles",
             "docs", "legal_1", "about_path", "filtered_words"),
    "log": ("public_log",),
    "regions": ("regions_list",),
}

# {endpoint: kind}: the allowlist. Anything not here is never counted, whatever it answers
ENDPOINT_KINDS = {endpoint: kind for kind, endpoints in _PAGES.items() for endpoint in endpoints}

# one endpoint can answer several addresses (front_all is also /api/v1/all/listing and /inpage/all): these never count,
# whichever endpoint they reached
NEVER_PATHS = ("/api/", "/inpage/", "/admin", "/assets/", "/embed/", "/media/")

_COUNTRY = re.compile(r"[A-Z]{2}")
_LABEL = re.compile(r"[a-z0-9-]{1,63}")
_TLD = re.compile(r"[a-z][a-z0-9-]*[a-z0-9]")
# an address written into a name, dotted or dashed: 1.2.3.4.nip.io, 203-0-113-5.sslip.io, host-10-0-0-1.isp.example
_ADDRESS_IN_NAME = re.compile(r"(?<![0-9])[0-9]{1,3}[.-][0-9]{1,3}[.-][0-9]{1,3}[.-][0-9]{1,3}(?![0-9])")
# names that only mean something on someone's own network, or that are nobody's site (RFC 6761, 6762, 8375 and the customary ones)
_PRIVATE_ENDINGS = frozenset(("local", "lan", "internal", "intranet", "home", "corp", "localdomain", "localhost", "private", "test",
                              "invalid", "arpa"))
# whose every name answers with an address written in it, in hex too (app.c0a80101.nip.io), or with the reader's own machine
_ADDRESS_DOMAINS = ("nip.io", "sslip.io", "xip.io", "localtest.me", "lvh.me")


def country_of(raw):
    """Two capital letters, or `UNKNOWN`: Cloudflare's T1 (Tor) and XX (it doesn't know), no header, anything else."""
    value = raw if isinstance(raw, str) else ""
    return value if _COUNTRY.fullmatch(value) and value != "XX" else UNKNOWN


def _host_in(raw):
    """The host of an http(s) address, lower case, or None. Only the host is ever looked at: the path and query of the
    address go no further than this function."""
    try:
        parts = urlsplit(raw)
        if parts.scheme not in ("http", "https"):
            return None
        return (parts.hostname or "").lower().rstrip(".") or None
    except ValueError:
        return None


def _usable_host(host):
    """A name a site could have: dotted ASCII labels ending in a real top-level label. Never an address, which would be
    somebody's (`203.0.113.5`, `[::1]`, `1.2.3.4.nip.io`), a private machine's name (`alice.local`, `nas.home.arpa`), a bare
    word, or anything with characters a name doesn't have."""
    if len(host) > MAX_HOST:
        return False
    labels = host.split(".")
    if len(labels) < 2 or not _TLD.fullmatch(labels[-1]) or labels[-1] in _PRIVATE_ENDINGS:
        return False
    if _ADDRESS_IN_NAME.search(host) or any(host == domain or host.endswith("." + domain) for domain in _ADDRESS_DOMAINS):
        return False
    return all(_LABEL.fullmatch(label) and not label.startswith("-") and not label.endswith("-") for label in labels)


def referrer_of(raw, own_hosts):
    """Where a request says it came from: `DIRECT` with no Referer, `INTERNAL` for this site's own names, the normalised host
    of another site (www. taken off), or `OTHER` for anything else. Never a path or a query."""
    value = raw.strip() if isinstance(raw, str) else ""
    if not value:
        return DIRECT
    if len(value) > MAX_REFERER:
        return OTHER
    host = _host_in(value)
    if host is None:
        return OTHER
    if is_own_host(host, own_hosts):
        return INTERNAL
    if host.startswith("www."):
        host = host[4:]
    return host if _usable_host(host) else OTHER


def day_of(timestamp):
    """The UTC day number of a time."""
    return int(timestamp) // DAY


def hit_of(method, endpoint, path, status, mimetype, embedded, headers, own_hosts, now):
    """The `Hit` a finished request leaves, or None when it counts for nothing. `headers` is anything with `.get`."""
    if method != "GET" or status != 200 or mimetype != "text/html" or embedded:
        return None
    kind = ENDPOINT_KINDS.get(endpoint)
    if kind is None or (path or "").startswith(NEVER_PATHS):
        return None
    if headers.get(MARKER_HEADER) is not None:
        return None
    purpose = headers.get("Sec-Fetch-Dest")
    if purpose and purpose != "document":
        return None
    if is_prefetch(headers) or is_robot(headers.get("User-Agent")):
        return None
    return Hit(day_of(now), kind, referrer_of(headers.get("Referer"), own_hosts), country_of(headers.get("CF-IPCountry")))


def text_of(value):
    """What Redis answered as text: this site's client answers bytes or str depending on how its pool was made."""
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else value


def keep_top(counts, cap):
    """({value: count} of the `cap` largest, the sum of the rest). A tie goes by name, so it doesn't depend on the order
    things arrived in."""
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return dict(ranked[:cap]), sum(n for _, n in ranked[cap:])


def merge_referrers(existing, new, cap=DAILY_HOSTS):
    """A day's referrer rows after a flush. `DIRECT`, `INTERNAL` and `OTHER` always stay; of the hosts, the `cap` largest of
    what was already in the table and what came in, and whatever else there was is added to `OTHER`, so that a day's
    total never changes and a real site overtakes a flood of names seen once."""
    fixed = {DIRECT: 0, INTERNAL: 0, OTHER: 0}
    hosts = {}
    for source in (existing, new):
        for value, n in source.items():
            (fixed if value in fixed else hosts)[value] = (fixed if value in fixed else hosts).get(value, 0) + int(n)
    kept, spilled = keep_top(hosts, cap)
    fixed[OTHER] += spilled
    return {**kept, **{value: n for value, n in fixed.items() if n > 0}}


def parse_days(raw):
    """`?days=`: 7, 28 or 90, anything else the default."""
    value = str(raw if raw is not None else "").strip()
    return int(value) if value.isascii() and value.isdigit() and int(value) in WINDOWS else DEFAULT_WINDOW


def window(now, days):
    """(first day, last day) of the last `days` UTC days, today being the last."""
    today = day_of(now)
    return today - days + 1, today


def day_label(day):
    """A UTC day number as "8 Oct"."""
    moment = time.gmtime(int(day) * DAY)
    return f"{moment.tm_mday} {time.strftime('%b', moment)}"


def bar_heights(values, floor=2):
    """Whole percentages of the largest value: 0 for nothing, at least `floor` for something."""
    top = max(values) if values else 0
    if top <= 0:
        return [0 for _ in values]
    return [0 if value <= 0 else max(floor, round(value * 100 / top)) for value in values]


def ranked(counts, total, limit=None):
    """[{value, requests, share}] largest first: `share` is a whole percentage of `total`, with "<1" for under one."""
    rows = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    if limit is not None:
        rows = rows[:limit]
    out = []
    for value, n in rows:
        exact = n * 100 / total if total else 0
        out.append({"value": value, "requests": n, "width": round(exact, 1),
                    "share": "<1" if 0 < exact < 1 else str(round(exact))})
    return out


def ranked_top(counts, total, limit, always=()):
    """`ranked` for a table that stops: the `limit` largest, whatever is in `always` (the site's own rows), then one last row
    `OTHER` with all the rest and how many values went into it (`more`), so the rows always add up to the whole."""
    named = sorted(((value, n) for value, n in counts.items() if value != OTHER), key=lambda item: (-item[1], item[0]))
    chosen = set(always) | {value for value, _ in [item for item in named if item[0] not in always][:limit]}
    past = [n for value, n in named if value not in chosen]
    rows = ranked({value: n for value, n in named if value in chosen}, total)
    rest = counts.get(OTHER, 0) + sum(past)
    if rest:
        rows += ranked({OTHER: rest}, total)
    for row in rows:
        row["more"] = 0
    if rest:
        rows[-1]["more"] = len(past)
    return rows
