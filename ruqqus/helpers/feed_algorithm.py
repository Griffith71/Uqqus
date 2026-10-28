"""A curation's own algorithm: which posts it shows and in what order.

Pure rules, stdlib only but for the interface's languages (helpers/i18n_runtime.py: `describe` and the messages are
in the reader's language); helpers/curation_feed.py turns a spec into the query.

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
    exclude_users / exclude_guilds   account and guild ids this curation leaves out. They are
              ids (a name can change hands), made from typed names by the route, which can
              look them up; this module cannot. They belong to the curation: they are not
              the owner's own mute list (helpers/muting.py), which they never touch.
    rank      hot, new, top, activity, or "mix": the member's own weights (`MIX`)

`describe` says the same thing in plain language; a curation shows it to everyone who
can see the curation, so a public feed's rules can be checked and forked.
"""
import json
import math
import re

from ruqqus.helpers.i18n_runtime import current_locale, tr, tr_lazy, trn

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
MAX_EXCLUDED = 50                              # accounts, and guilds, a curation may leave out
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
    "exclude_users": [], "exclude_guilds": [], "rank": "hot", "mix": {name: default for name, (_, _, default) in MIX.items()}, "server": "",
}

# name: (label, what it is for, the parts that differ from DEFAULT)
PRESETS = {
    # Translators: ready-made starting points for a curation's algorithm: a short name, then what it is for.
    "latest": (tr_lazy("Latest"), tr_lazy("Everything from your guilds and accounts, newest first."), {"rank": "new"}),
    "popular": (tr_lazy("Popular this week"), tr_lazy("The most voted posts of the last seven days."),
                {"rank": "top", "age": "week"}),
    "discussed": (tr_lazy("Most discussed"), tr_lazy("Posts with the busiest comment sections this week."),
                  {"rank": "activity", "age": "week"}),
    "media": (tr_lazy("Media only"), tr_lazy("Pictures, video and sound, nothing else."),
              {"kinds": ["image", "video", "audio"]}),
    "rising": (tr_lazy("Fresh and rising"), tr_lazy("New posts that are getting attention, with no account filling the page."),
               {"rank": MIX_RANK, "age": "week", "mix": {"votes": 6, "comments": 8, "fade": 12, "variety": 2}}),
}

_SITE = re.compile(r"^[a-z0-9-]+(?:\.[a-z0-9-]+)+$")
_SPLIT = re.compile(r"[,\n\r]+")


def _too_many(field):
    """A list that holds too much, by the field of the form it came from (each its own whole sentence)."""
    if field == "any":
        return trn("Mentions any of: %(num)s word or phrase at most.", "Mentions any of: %(num)s words or phrases at most.",
                   MAX_WORDS)
    if field == "all":
        return trn("Mentions all of: %(num)s word or phrase at most.", "Mentions all of: %(num)s words or phrases at most.",
                   MAX_WORDS)
    if field == "none":
        return trn("Never mentions: %(num)s word or phrase at most.", "Never mentions: %(num)s words or phrases at most.",
                   MAX_WORDS)
    if field == "only_sites":
        return trn("Only links to: %(num)s site at most.", "Only links to: %(num)s sites at most.", MAX_SITES)
    if field == "never_sites":
        return trn("Never links to: %(num)s site at most.", "Never links to: %(num)s sites at most.", MAX_SITES)
    if field == "exclude_users":
        return trn("Leave out these accounts: %(num)s at most.", "Leave out these accounts: %(num)s at most.",
                   MAX_EXCLUDED)
    return trn("Leave out these guilds: %(num)s at most.", "Leave out these guilds: %(num)s at most.", MAX_EXCLUDED)


def _not_a_site(field, item):
    if field == "only_sites":
        return tr("Only links to: \"%(address)s\" is not a site name. Write it like example.org", address=item)
    return tr("Never links to: \"%(address)s\" is not a site name. Write it like example.org", address=item)


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


def _words(value, field):
    out = []
    for item in _items(value):
        word = " ".join(item.split())
        if not word:
            continue
        if len(word) > WORD_CHARS:
            raise AlgorithmError(trn("A word or phrase can be %(num)s character at most: \"%(word)s...\"",
                                     "A word or phrase can be %(num)s characters at most: \"%(word)s...\"",
                                     WORD_CHARS, word=word[:20]))
        if word.casefold() not in (w.casefold() for w in out):
            out.append(word)
    if len(out) > MAX_WORDS:
        raise AlgorithmError(_too_many(field))
    return out


def site_name(text):
    """"https://www.Example.org/page" -> "example.org"; None when it is not a site name."""
    host = text.strip().lower()
    host = re.sub(r"^[a-z]+://", "", host).split("/")[0].split("?")[0].split("#")[0].split("@")[-1].split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    return host if _SITE.match(host) and len(host) <= 100 else None


def _sites(value, field):
    out = []
    for item in _items(value):
        if not item.strip():
            continue
        host = site_name(item)
        if host is None:
            raise AlgorithmError(_not_a_site(field, item.strip()[:40]))
        if host not in out:
            out.append(host)
    if len(out) > MAX_SITES:
        raise AlgorithmError(_too_many(field))
    return out


def _ids(value, field):
    """Whole positive numbers, no repeats, in the order given. Anything else in the list is
    dropped (the stored spec was made by this function, so it never holds anything else)."""
    out = []
    for item in value if isinstance(value, (list, tuple)) else _items(value):
        if isinstance(item, bool):
            continue
        try:
            number = int(str(item).strip())
        except ValueError:
            continue
        if number > 0 and number not in out:
            out.append(number)
    if len(out) > MAX_EXCLUDED:
        raise AlgorithmError(_too_many(field))
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
        "any": _words(raw.get("any"), "any"),
        "all": _words(raw.get("all"), "all"),
        "none": _words(raw.get("none"), "none"),
        "kinds": _choices(raw.get("kinds"), KINDS),
        "only_sites": _sites(raw.get("only_sites"), "only_sites"),
        "never_sites": _sites(raw.get("never_sites"), "never_sites"),
        "min_votes": _number(raw.get("min_votes"), 0, MAX_COUNT, 0),
        "min_comments": _number(raw.get("min_comments"), 0, MAX_COUNT, 0),
        "age": _one_of(raw.get("age"), tuple(AGES), ""),
        "hide": _choices(raw.get("hide"), HIDE),
        "exclude_users": _ids(raw.get("exclude_users"), "exclude_users"),
        "exclude_guilds": _ids(raw.get("exclude_guilds"), "exclude_guilds"),
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
            raise AlgorithmError(tr("Enter the address of the feed server."))
        if len(server) > SERVER_CHARS or any(c.isspace() for c in server):
            raise AlgorithmError(tr("That feed server address is too long or has spaces in it."))
        spec["server"] = server
    elif spec["source"] == SITE and spec["age"] not in SITE_AGES:
        raise AlgorithmError(tr("A curation of the whole site needs an age limit of a month or less."))
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
        "exclude_users": form.get("exclude_users", ""),
        "exclude_guilds": form.get("exclude_guilds", ""),
        "rank": form.get("rank"),
        "mix": {name: form.get(f"mix_{name}") for name in MIX},
        "server": form.get("server", ""),
    })


def is_default(spec):
    return spec == DEFAULT


def without_left_out(spec):
    """`spec` with nobody left out: what a fork starts from. A curation's rules are public but its
    leave-out list is not (`describe` only counts it), so a fork gets the rules and not the names."""
    return {**spec, "exclude_users": [], "exclude_guilds": []}


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
# Every line is a whole sentence in the reader's language; the moving parts are named values.

# Translators: what a curation shows, said on its page ("Only pictures and video.").
_KIND_WORDS = {"text": tr_lazy("text posts"), "link": tr_lazy("links"), "image": tr_lazy("pictures"),
               "video": tr_lazy("video"), "audio": tr_lazy("sound")}
# Translators: what a curation leaves out, said on its page ("Leaves out bots and forwarded copies.").
_HIDE_WORDS = {"bots": tr_lazy("bots"), "ai": tr_lazy("posts made with AI"), "paid": tr_lazy("commercial promotion"),
               "forwards": tr_lazy("forwarded copies")}
_RANK_WORDS = {"hot": tr_lazy("Ordered by Hot, the site's usual order."), "new": tr_lazy("Newest first."),
               "top": tr_lazy("Most votes first."), "activity": tr_lazy("Most discussed first.")}
# Translators: how long a post takes to fade in a curation's own mix ("fading over a day").
_FADE_WORDS = {6: tr_lazy("6 hours"), 12: tr_lazy("12 hours"), 24: tr_lazy("a day"), 72: tr_lazy("3 days"),
               168: tr_lazy("a week")}
_SOURCE_LINES = {
    (SITE, ""): tr_lazy("Posts from the whole site."),
    (SITE, "day"): tr_lazy("Posts from the whole site, no older than a day."),
    (SITE, "week"): tr_lazy("Posts from the whole site, no older than a week."),
    (SITE, "month"): tr_lazy("Posts from the whole site, no older than a month."),
    (SITE, "year"): tr_lazy("Posts from the whole site, no older than a year."),
    (MEMBERS, ""): tr_lazy("Posts from this curation's guilds and accounts."),
    (MEMBERS, "day"): tr_lazy("Posts from this curation's guilds and accounts, no older than a day."),
    (MEMBERS, "week"): tr_lazy("Posts from this curation's guilds and accounts, no older than a week."),
    (MEMBERS, "month"): tr_lazy("Posts from this curation's guilds and accounts, no older than a month."),
    (MEMBERS, "year"): tr_lazy("Posts from this curation's guilds and accounts, no older than a year."),
}


def _listed(items, last="and"):
    """"a, b and c" (or "a, b or c"): in English as the site always wrote it, in another language as CLDR joins a list
    there (Babel's format_list)."""
    items = [str(item) for item in items]
    if len(items) <= 1:
        return "".join(items)
    if current_locale() != "en":
        try:
            from babel.lists import format_list
            from ruqqus.helpers.i18n_runtime import babel_locale
            return format_list(items, style="standard" if last == "and" else "or", locale=babel_locale())
        except (ImportError, ValueError, LookupError):
            pass
    return ", ".join(items[:-1]) + f" {last} " + items[-1]


def server_host(address):
    return site_name(address) or tr("an unknown address")


def left_out_line(spec):
    """"Leaves out posts from 2 accounts and 1 guild." or None. Counts only, never names: a
    curation's rules are read by everyone who can see it, and a name could be a private or
    Circle guild, something a viewer's word filter hides, or a statement about a person."""
    accounts, guilds = len(spec["exclude_users"]), len(spec["exclude_guilds"])
    if accounts and guilds:
        # Translators: %(accounts)s is "2 accounts", %(guilds)s is "1 guild".
        return tr("Leaves out posts from %(accounts)s and %(guilds)s.",
                  accounts=trn("%(num)s account", "%(num)s accounts", accounts),
                  guilds=trn("%(num)s guild", "%(num)s guilds", guilds))
    if accounts:
        return trn("Leaves out posts from %(num)s account.", "Leaves out posts from %(num)s accounts.", accounts)
    if guilds:
        return trn("Leaves out posts from %(num)s guild.", "Leaves out posts from %(num)s guilds.", guilds)
    return None


def _quoted(words, last):
    return _listed((f"\"{w}\"" for w in words), last)


def _at_least(votes, comments):
    if votes and comments:
        # Translators: %(votes)s is "3 votes", %(comments)s is "2 comments".
        return tr("At least %(votes)s and %(comments)s.", votes=trn("%(num)s vote", "%(num)s votes", votes),
                  comments=trn("%(num)s comment", "%(num)s comments", comments))
    if votes:
        return trn("At least %(num)s vote.", "At least %(num)s votes.", votes)
    return trn("At least %(num)s comment.", "At least %(num)s comments.", comments)


def _mix_line(mix, members_boost):
    values = {"votes": mix["votes"], "comments": mix["comments"], "fade": _FADE_WORDS[mix["fade"]],
              "members": mix["members"], "media": mix["media"]}
    # "votes" in a mix are upvotes less downvotes plus the post's tip boost (helpers/tips.py)
    # Translators: a curation's own mix of weights; %(votes)s, %(comments)s and the boosts are numbers from 0 to 10,
    # %(fade)s is how long a post takes to fade ("a day").
    if members_boost and mix["media"]:
        line = tr("Ordered by its own mix: votes and tips %(votes)s, comments %(comments)s, fading over %(fade)s, a boost "
                  "of %(members)s for this curation's guilds and accounts, a boost of %(media)s for posts with media.",
                  **values)
    elif members_boost:
        line = tr("Ordered by its own mix: votes and tips %(votes)s, comments %(comments)s, fading over %(fade)s, a boost "
                  "of %(members)s for this curation's guilds and accounts.", **values)
    elif mix["media"]:
        line = tr("Ordered by its own mix: votes and tips %(votes)s, comments %(comments)s, fading over %(fade)s, a boost "
                  "of %(media)s for posts with media.", **values)
    else:
        line = tr("Ordered by its own mix: votes and tips %(votes)s, comments %(comments)s, fading over %(fade)s.",
                  **values)
    if mix["variety"]:
        line += " " + trn("At most %(num)s post per author or guild on a page.",
                          "At most %(num)s posts per author or guild on a page.", mix["variety"])
    return line


def describe(spec):
    """What the algorithm does, as sentences anyone can read."""
    if is_server(spec):
        host = site_name(spec["server"])
        if host:
            first = tr("Ranked by an outside server (%(host)s). This site cannot see how it picks posts.", host=host)
        else:
            first = tr("Ranked by an outside server (an unknown address). This site cannot see how it picks posts.")
        lines = [first, tr("It only orders posts you are allowed to see.")]
        if left_out_line(spec):
            lines.append(left_out_line(spec))
        return lines

    lines = [str(_SOURCE_LINES[(SITE if spec["source"] == SITE else MEMBERS, spec["age"])])]
    if spec["any"]:
        lines.append(tr("Only posts that mention %(words)s.", words=_quoted(spec["any"], "or")))
    if spec["all"]:
        lines.append(tr("Only posts that mention all of %(words)s.", words=_quoted(spec["all"], "and")))
    if spec["none"]:
        lines.append(tr("Never posts that mention %(words)s.", words=_quoted(spec["none"], "or")))
    if spec["kinds"]:
        # Translators: %(kinds)s is a list of kinds of post ("pictures, video and sound").
        lines.append(tr("Only %(kinds)s.", kinds=_listed(_KIND_WORDS[k] for k in spec["kinds"])))
    if spec["only_sites"]:
        lines.append(tr("Only links to %(sites)s.", sites=_listed(spec["only_sites"], "or")))
    if spec["never_sites"]:
        lines.append(tr("Never links to %(sites)s.", sites=_listed(spec["never_sites"], "or")))
    if spec["min_votes"] or spec["min_comments"]:
        lines.append(_at_least(spec["min_votes"], spec["min_comments"]))
    if spec["hide"]:
        # Translators: %(things)s is a list ("bots and forwarded copies").
        lines.append(tr("Leaves out %(things)s.", things=_listed(_HIDE_WORDS[h] for h in spec["hide"])))
    if left_out_line(spec):
        lines.append(left_out_line(spec))

    if spec["rank"] != MIX_RANK:
        lines.append(str(_RANK_WORDS[spec["rank"]]))
    else:
        lines.append(_mix_line(spec["mix"], bool(spec["mix"]["members"] and spec["source"] == SITE)))
    return lines
