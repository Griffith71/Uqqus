"""A curation's own algorithm: which posts it shows and in what order.

Pure rules, stdlib only (helpers/curation_feed.py turns a spec into the query).

A member builds an algorithm from fixed parts in the curation form. They never write
code, patterns or SQL: every value is one of the names below, a bounded number, or a
plain word. `clean` is the only way a spec is made, so anything stored or run has been
through it:

    mode      "rules" (the parts below) or "server" (an outside feed server orders the
              posts: helpers/feed_server.py)
    source    "members" (this curation's guilds and accounts) or "site" (every public
              post, as All; needs an age limit of a month or less)
    any / all / none   plain words or phrases a post must / must not mention
    kinds     text, link, image, video, audio
    only_sites / never_sites   host names of a post's link
    min_votes, min_comments, age, hide (bots, ai, paid, forwards)
    rank      hot, new, top, activity, or "mix": the member's own weights (`MIX`)

`describe` says the same thing in plain language; a curation shows it to everyone who
can see the curation, so a public feed's rules can be checked and forked.
"""
import json
import math
import re

RULES, SERVER = "rules", "server"
MODES = (RULES, SERVER)
MEMBERS, SITE = "members", "site"
SOURCES = (MEMBERS, SITE)
KINDS = ("text", "link", "image", "video", "audio")
AGES = {"day": 86400, "week": 604800, "month": 2592000, "year": 31536000}
SITE_AGES = ("day", "week", "month")          # the whole site is never searched further back
HIDE = ("bots", "ai", "paid", "forwards")
MIX_RANK = "mix"
RANKS = ("hot", "new", "top", "activity", MIX_RANK)
FADES = (6, 12, 24, 72, 168)                   # hours for a post's weight to halve

MAX_WORDS = 10
WORD_CHARS = 40
MAX_SITES = 10
MAX_COUNT = 100000
SERVER_CHARS = 300
MIX_POOL = 1000                                # a mix ranks the newest matching posts, no more
PAGE = 25

# name: (lowest, highest, default)
MIX = {
    "votes": (0, 10, 5),
    "comments": (0, 10, 5),
    "fade": (FADES[0], FADES[-1], 24),
    "members": (0, 10, 0),      # boost for the curation's own guilds and accounts (source "site")
    "media": (0, 10, 0),        # boost for posts with a picture, sound or video
    "variety": (0, 5, 0),       # at most this many posts per author or guild on a page; 0 = no limit
}

DEFAULT = {
    "mode": RULES, "source": MEMBERS, "any": [], "all": [], "none": [], "kinds": [],
    "only_sites": [], "never_sites": [], "min_votes": 0, "min_comments": 0, "age": "", "hide": [],
    "rank": "hot", "mix": {name: default for name, (_, _, default) in MIX.items()}, "server": "",
}

# name: (label, what it is for, the parts that differ from DEFAULT)
PRESETS = {
    "latest": ("Latest", "Everything from your guilds and accounts, newest first.", {"rank": "new"}),
    "popular": ("Popular this week", "The most voted posts of the last seven days.", {"rank": "top", "age": "week"}),
    "discussed": ("Most discussed", "Posts with the busiest comment sections this week.", {"rank": "activity", "age": "week"}),
    "media": ("Media only", "Pictures, video and sound, nothing else.", {"kinds": ["image", "video", "audio"]}),
    "rising": ("Fresh and rising", "New posts that are getting attention, with no account filling the page.",
               {"rank": MIX_RANK, "age": "week", "mix": {"votes": 6, "comments": 8, "fade": 12, "variety": 2}}),
}

_SITE = re.compile(r"^[a-z0-9-]+(?:\.[a-z0-9-]+)+$")
_SPLIT = re.compile(r"[,\n\r]+")


class AlgorithmError(ValueError):
    """Something the member can fix; the message is shown to them."""

    def __init__(self, message):
        super().__init__(message)
        self.message = message


# --- making a spec -----------------------------------------------------------------------

def _items(value):
    if isinstance(value, str):
        return _SPLIT.split(value)
    if isinstance(value, (list, tuple)):
        return [x for x in value if isinstance(x, str)]
    return []


def _words(value, label):
    out = []
    for item in _items(value):
        word = " ".join(item.split())
        if not word:
            continue
        if len(word) > WORD_CHARS:
            raise AlgorithmError(f"A word or phrase can be {WORD_CHARS} characters at most: \"{word[:20]}...\"")
        if word.casefold() not in (w.casefold() for w in out):
            out.append(word)
    if len(out) > MAX_WORDS:
        raise AlgorithmError(f"{label}: {MAX_WORDS} words or phrases at most.")
    return out


def site_name(text):
    """"https://www.Example.org/page" -> "example.org"; None when it is not a site name."""
    host = text.strip().lower()
    host = re.sub(r"^[a-z]+://", "", host).split("/")[0].split("?")[0].split("#")[0].split("@")[-1].split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    return host if _SITE.match(host) and len(host) <= 100 else None


def _sites(value, label):
    out = []
    for item in _items(value):
        if not item.strip():
            continue
        host = site_name(item)
        if host is None:
            raise AlgorithmError(f"{label}: \"{item.strip()[:40]}\" is not a site name. Write it like example.org")
        if host not in out:
            out.append(host)
    if len(out) > MAX_SITES:
        raise AlgorithmError(f"{label}: {MAX_SITES} sites at most.")
    return out


def _choices(value, allowed):
    picked = set(_items(value))
    return [name for name in allowed if name in picked]


def _number(value, lowest, highest, default):
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return default
    return max(lowest, min(highest, number))


def _one_of(value, allowed, default):
    return value if isinstance(value, str) and value in allowed else default


def clean(raw):
    """A spec made only of known names and bounded values, or AlgorithmError."""
    raw = raw if isinstance(raw, dict) else {}
    mix = raw.get("mix") if isinstance(raw.get("mix"), dict) else {}

    spec = {
        "mode": _one_of(raw.get("mode"), MODES, RULES),
        "source": _one_of(raw.get("source"), SOURCES, MEMBERS),
        "any": _words(raw.get("any"), "Mentions any of"),
        "all": _words(raw.get("all"), "Mentions all of"),
        "none": _words(raw.get("none"), "Never mentions"),
        "kinds": _choices(raw.get("kinds"), KINDS),
        "only_sites": _sites(raw.get("only_sites"), "Only links to"),
        "never_sites": _sites(raw.get("never_sites"), "Never links to"),
        "min_votes": _number(raw.get("min_votes"), 0, MAX_COUNT, 0),
        "min_comments": _number(raw.get("min_comments"), 0, MAX_COUNT, 0),
        "age": _one_of(raw.get("age"), tuple(AGES), ""),
        "hide": _choices(raw.get("hide"), HIDE),
        "rank": _one_of(raw.get("rank"), RANKS, "hot"),
        "mix": {name: _number(mix.get(name), low, high, default) for name, (low, high, default) in MIX.items()},
        "server": "",
    }
    spec["mix"]["fade"] = min(FADES, key=lambda hours: abs(hours - spec["mix"]["fade"]))
    if len(spec["kinds"]) == len(KINDS):
        spec["kinds"] = []                      # every kind is no condition

    if spec["mode"] == SERVER:
        server = raw.get("server").strip() if isinstance(raw.get("server"), str) else ""
        if not server:
            raise AlgorithmError("Enter the address of the feed server.")
        if len(server) > SERVER_CHARS or any(c.isspace() for c in server):
            raise AlgorithmError("That feed server address is too long or has spaces in it.")
        spec["server"] = server
    elif spec["source"] == SITE and spec["age"] not in SITE_AGES:
        raise AlgorithmError("A curation of the whole site needs an age limit of a month or less.")
    return spec


def load(text):
    """The spec stored on a curation. Whatever is stored, what comes back is clean."""
    try:
        return clean(json.loads(text)) if text else clean({})
    except (ValueError, TypeError):
        return clean({})


def dump(spec):
    return json.dumps(clean(spec), separators=(",", ":"), sort_keys=True)


def preset(name):
    """The spec a ready-made starting point stands for (DEFAULT for an unknown name)."""
    parts = dict(PRESETS[name][2]) if name in PRESETS else {}
    return clean(parts)


def from_form(form):
    """The spec a posted curation form describes. `form` has .get and .getlist."""
    return clean({
        "mode": form.get("mode"),
        "source": form.get("source"),
        "any": form.get("any_words", ""),
        "all": form.get("all_words", ""),
        "none": form.get("none_words", ""),
        "kinds": form.getlist("kinds"),
        "only_sites": form.get("only_sites", ""),
        "never_sites": form.get("never_sites", ""),
        "min_votes": form.get("min_votes"),
        "min_comments": form.get("min_comments"),
        "age": form.get("age"),
        "hide": form.getlist("hide"),
        "rank": form.get("rank"),
        "mix": {name: form.get(f"mix_{name}") for name in MIX},
        "server": form.get("server", ""),
    })


def is_default(spec):
    return spec == DEFAULT


def is_server(spec):
    return spec["mode"] == SERVER


def uses_mix(spec):
    return spec["mode"] == RULES and spec["rank"] == MIX_RANK


def like_pattern(word):
    """A plain word as an SQL LIKE pattern: its own %, _ and \\ are text, not wildcards.
    Use with escape="\\\\"."""
    return "%" + word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


# --- ranking -----------------------------------------------------------------------------

def mix_score(mix, votes, comments, age_hours, is_member=False, has_media=False):
    """The number a mix orders by (helpers/curation_feed.py computes the same in SQL):
    votes and comments on a log scale, each with the member's weight, boosted for the
    curation's own guilds and accounts and for media, then fading with age."""
    base = 1.0 + mix["votes"] * math.log1p(max(votes, 0)) + mix["comments"] * math.log1p(max(comments, 0))
    boost = (1.0 + 0.2 * mix["members"] * bool(is_member)) * (1.0 + 0.2 * mix["media"] * bool(has_media))
    return base * boost * 0.5 ** (max(age_hours, 0.0) / mix["fade"])


def spread(rows, limit, page=PAGE):
    """Ranked ids with at most `limit` posts per author and per guild on each page.

    rows: [(post id, author key, guild key)] best first. A key of None is never limited:
    pass it for an anonymous post (so the limit cannot hint at who wrote it) and for a
    post that is only on a profile. A post that does not fit moves to the next page; a
    page that could not be filled otherwise is filled anyway, so pages stay `page` long."""
    if limit <= 0:
        return [row[0] for row in rows]
    out, waiting = [], list(rows)
    while waiting:
        authors, guilds, taken, left = {}, {}, [], []
        for row in waiting:
            _, author, guild = row
            fits = (author is None or authors.get(author, 0) < limit) and (guild is None or guilds.get(guild, 0) < limit)
            if fits and len(taken) < page:
                taken.append(row[0])
                if author is not None:
                    authors[author] = authors.get(author, 0) + 1
                if guild is not None:
                    guilds[guild] = guilds.get(guild, 0) + 1
            else:
                left.append(row)
        room = page - len(taken)
        taken.extend(row[0] for row in left[:room])
        out.extend(taken)
        waiting = left[room:]
    return out


# --- saying it in plain language ---------------------------------------------------------

_AGE_WORDS = {"day": "a day", "week": "a week", "month": "a month", "year": "a year"}
_KIND_WORDS = {"text": "text posts", "link": "links", "image": "pictures", "video": "video", "audio": "sound"}
_HIDE_WORDS = {"bots": "bots", "ai": "posts made with AI", "paid": "paid partnerships", "forwards": "forwarded copies"}
_RANK_WORDS = {"hot": "Ordered by Hot, the site's usual order.", "new": "Newest first.", "top": "Most votes first.",
               "activity": "Most discussed first."}
_FADE_WORDS = {6: "6 hours", 12: "12 hours", 24: "a day", 72: "3 days", 168: "a week"}


def _listed(items, last="and"):
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + f" {last} " + items[-1]


def server_host(address):
    return site_name(address) or "an unknown address"


def describe(spec):
    """What the algorithm does, as sentences anyone can read."""
    if is_server(spec):
        return [f"Ranked by an outside server ({server_host(spec['server'])}). Ruqqus cannot see how it picks posts.",
                "It only orders posts you are allowed to see."]

    lines = []
    age = f", no older than {_AGE_WORDS[spec['age']]}" if spec["age"] else ""
    if spec["source"] == SITE:
        lines.append(f"Posts from the whole site{age}.")
    else:
        lines.append(f"Posts from this curation's guilds and accounts{age}.")
    if spec["any"]:
        lines.append("Only posts that mention " + _listed((f"\"{w}\"" for w in spec["any"]), "or") + ".")
    if spec["all"]:
        lines.append("Only posts that mention all of " + _listed(f"\"{w}\"" for w in spec["all"]) + ".")
    if spec["none"]:
        lines.append("Never posts that mention " + _listed((f"\"{w}\"" for w in spec["none"]), "or") + ".")
    if spec["kinds"]:
        lines.append("Only " + _listed(_KIND_WORDS[k] for k in spec["kinds"]) + ".")
    if spec["only_sites"]:
        lines.append("Only links to " + _listed(spec["only_sites"], "or") + ".")
    if spec["never_sites"]:
        lines.append("Never links to " + _listed(spec["never_sites"], "or") + ".")
    least = []
    if spec["min_votes"]:
        least.append(f"{spec['min_votes']} vote{'' if spec['min_votes'] == 1 else 's'}")
    if spec["min_comments"]:
        least.append(f"{spec['min_comments']} comment{'' if spec['min_comments'] == 1 else 's'}")
    if least:
        lines.append("At least " + _listed(least) + ".")
    if spec["hide"]:
        lines.append("Leaves out " + _listed(_HIDE_WORDS[h] for h in spec["hide"]) + ".")

    if spec["rank"] != MIX_RANK:
        lines.append(_RANK_WORDS[spec["rank"]])
    else:
        mix = spec["mix"]
        parts = [f"votes {mix['votes']}", f"comments {mix['comments']}", f"fading over {_FADE_WORDS[mix['fade']]}"]
        if mix["members"] and spec["source"] == SITE:
            parts.append(f"a boost of {mix['members']} for this curation's guilds and accounts")
        if mix["media"]:
            parts.append(f"a boost of {mix['media']} for posts with media")
        line = "Ordered by its own mix: " + ", ".join(parts) + "."
        if mix["variety"]:
            line += f" At most {mix['variety']} post{'' if mix['variety'] == 1 else 's'} per author or guild on a page."
        lines.append(line)
    return lines
