"""Search operators: the rules (no I/O; stdlib and the language list only).

A post search is free text plus operators, as on X or Google:

    cats "black cat" -dogs from:@alice in:+pets since:2026-01-01 has:image lang:en -is:forward

`parse` turns the text into a `Query`; helpers/search_filters.py looks the names up (as the
viewer may see them) and turns the query into conditions; routes/search.py runs it under the
usual visibility rules. `OPERATORS` is the one list: the help page (/help/search) and the
advanced search form are drawn from it, so a new operator needs an entry here and its
condition in search_filters, nothing else.

Dates are days in UTC. `before:` and `after:` leave the day itself out, `since:` and `until:`
take it in, `on:` is the day. A key no operator has (`https://...`, `note:`) stays free text.
"""
import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone

from babel.support import LazyProxy

from ruqqus.helpers import i18n
from ruqqus.helpers.i18n_runtime import N_, current_locale, date_text, tr, tr_lazy, trn
from ruqqus.helpers.languages import LANGUAGE_NAMES, ROWS, UNKNOWN, key_of

MAX_CHARS = 500
MAX_PARTS = 20
MAX_NUMBER = 1000000
DAY = 86400

# One notice for an account or guild that does not exist AND for one the viewer may not see:
# a search must never confirm that a private guild, or a deleted or blocked account, exists.
# Each is a message with the name as `name` (`NO_ACCOUNT % {"name": "alice"}`), the English of the notice that is
# drawn in the reader's language where it is made (`_clean` below, helpers/search_filters.resolve).
NO_ACCOUNT = N_("No account called @%(name)s.")
NO_GUILD = N_("No guild called +%(name)s.")
NO_TOPIC = N_("No topic called #%(name)s.")
NO_CURATION = N_("No public curation called &%(name)s.")
# Translators: "in:" is a search operator and stays as it is
IN_INSIDE_GUILD = tr_lazy("in: is not used when searching one guild.")
NO_DAYS = tr_lazy("Those dates leave no days to search.")
# Translators: from:, in: are search operators and stay as they are
TOO_SLOW = tr_lazy("This search took too long. Add words, or narrow it with from:, in: or a date.")


def _too_long():
    return trn("A search can be at most %(num)s character.", "A search can be at most %(num)s characters.", MAX_CHARS)


def _too_many():
    return trn("A search can hold at most %(num)s word and operator.",
               "A search can hold at most %(num)s words and operators.", MAX_PARTS)


TOO_LONG = LazyProxy(_too_long, enable_cache=False)
TOO_MANY = LazyProxy(_too_many, enable_cache=False)


USERNAME = re.compile(r"[A-Za-z0-9_\-]{1,40}")
GUILDNAME = re.compile(r"[A-Za-z0-9_]{1,40}")
HOST = re.compile(r"[a-z0-9-]+(\.[a-z0-9-]+)+")
TOKEN = re.compile(r'(-?)"([^"]*)"|(\S+)')
OPERATOR = re.compile(r"(\w+):(.*)", re.S)

TEXT = ("word", "phrase")


@dataclass(frozen=True)
class Operator:
    key: str
    kind: str               # user, guild, date, choice, lang, domain, url, number, flair
    example: str
    help: str
    aliases: tuple = ()
    values: tuple = ()      # a choice's values
    negatable: bool = True


# `help` is marked for the catalogues and stays a plain str: help/search.html draws it with msg_text. The keys and
# examples are what a reader types, never translated.
OPERATORS = (
    Operator("from", "user", "from:@alice", N_("Posts by an account. Their anonymous posts are never found this way."),
             aliases=("author",)),
    Operator("in", "guild", "in:+pets",
             N_("Posts in a guild. in:#gaming is the guilds of a topic and in:&news is a public curation's guilds and "
                "accounts."),
             aliases=("guild",)),
    Operator("since", "date", "since:2026-01-01", N_("Posted on that day or later."), negatable=False),
    Operator("until", "date", "until:2026-01-31", N_("Posted on that day or earlier."), negatable=False),
    Operator("after", "date", "after:2026-01-01", N_("Posted after that day (the day itself is left out)."),
             negatable=False),
    Operator("before", "date", "before:2026-01-01", N_("Posted before that day (the day itself is left out)."),
             negatable=False),
    Operator("on", "date", "on:2026-01-01", N_("Posted on that day."), negatable=False),
    Operator("has", "choice", "has:link", N_("Posts with a link, a picture, a video, a sound or a poll."),
             values=("link", "image", "video", "audio", "poll")),
    Operator("lang", "lang", "lang:en", N_("Posts in a language: a code (en) or its name (english)."),
             aliases=("language",)),
    Operator("domain", "domain", "domain:example.com", N_("Posts that link to a site, or to one of its subdomains."),
             aliases=("site",)),
    Operator("url", "url", "url:example.com/news", N_("Posts whose link contains this text.")),
    Operator("is", "choice", "-is:forward",
             N_("Forwarded copies (-is:forward leaves them out, so a post is found once), posts made with AI, "
                "commercial promotion (paid partnerships, sponsorships, affiliate links), posts by bots (-is:bot leaves "
                "them out)."),
             values=("forward", "ai", "partnership", "bot")),
    Operator("flair", "flair", 'flair:"Question"', N_("Posts a guild tagged with this flair. With in:+guild, that "
                                                       "guild's flair."), aliases=("tag",)),
    Operator("min_votes", "number", "min_votes:10", N_("A score of at least this many votes."), negatable=False),
    Operator("min_comments", "number", "min_comments:5", N_("At least this many comments."), negatable=False),
)

BY_KEY = {op.key: op for op in OPERATORS}
KEYS = {name: op.key for op in OPERATORS for name in (op.key,) + op.aliases}
DATE_KEYS = tuple(op.key for op in OPERATORS if op.kind == "date")

CHOICE_ALIASES = {
    "has": {"links": "link", "images": "image", "picture": "image", "pictures": "image", "photo": "image",
            "videos": "video", "sound": "audio", "sounds": "audio", "polls": "poll"},
    "is": {"forwards": "forward", "forwarded": "forward", "made_with_ai": "ai", "paid": "partnership",
           "partner": "partnership", "partnerships": "partnership", "promotion": "partnership",
           "commercial": "partnership", "sponsored": "partnership", "bots": "bot", "automated": "bot"},
}

IS_LABELS = {"forward": (tr_lazy("Forwarded copies"), tr_lazy("No forwarded copies")),
             "ai": (tr_lazy("Made with AI"), tr_lazy("Not made with AI")),
             "partnership": (tr_lazy("Commercial promotion"), tr_lazy("No commercial promotion")),
             "bot": (tr_lazy("Bot posts"), tr_lazy("No bot posts"))}


def _squash(name):
    return re.sub(r"[^a-z]", "", name.lower())


# a language's name, or its row's ("bulgarianmacedonian"), finds its row (helpers/languages.py)
LANGUAGE_BY_NAME = {**{_squash(name): code for code, name in LANGUAGE_NAMES.items()},
                    **{_squash(name): key for key, (name, _codes) in ROWS.items()}}


@dataclass(frozen=True)
class Part:
    """A word, a "phrase" or an operator. `value` is cleaned (a name without its @ or +, a day as
    YYYY-MM-DD, a language code, a whole number); `text` is the part as it was typed, so it can be
    taken back out of the query; `ref` is the account or guild id search_filters found. An `in:` that names a
    topic (`#gaming`) or a public curation (`&news`) keeps its # or & in `value`; `refs` are the topic's ids (helpers/topics.py Topic.ids: its subcategories, and -N for each category it is named after)."""
    kind: str
    value: str
    negated: bool = False
    text: str = ""
    ref: int = 0
    refs: tuple = ()


@dataclass(frozen=True)
class Query:
    """A parsed search. Frozen and made of tuples, so its repr is a stable cache key."""
    parts: tuple = ()
    problems: tuple = field(default=())

    @property
    def operators(self):
        return [p for p in self.parts if p.kind not in TEXT]

    @property
    def for_one_author(self):
        """A search for one account's posts (`from:`), which is not filtered by mutes: going
        to someone's posts is a choice, as on their profile."""
        return any(p.kind == "from" and not p.negated for p in self.parts)

    def first(self, kind, negated=False):
        return next((p for p in self.parts if p.kind == kind and p.negated == negated), None)


def day_start(day):
    """The first second of a YYYY-MM-DD day, in UTC."""
    return int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())


def time_bounds(query):
    """(from, to): posts with from <= created_utc < to. Either is None when no date sets it."""
    low, high = None, None
    for part in query.parts:
        if part.kind not in DATE_KEYS:
            continue
        start = day_start(part.value)
        lo, hi = {"since": (start, None), "after": (start + DAY, None), "until": (None, start + DAY),
                  "before": (None, start), "on": (start, start + DAY)}[part.kind]
        if lo is not None:
            low = lo if low is None else max(low, lo)
        if hi is not None:
            high = hi if high is None else min(high, hi)
    return low, high


def _clean(op, value):
    """(cleaned value, problem). Exactly one of them is None."""
    if op.kind == "user":
        name = value.lstrip("@")
        return (name, None) if USERNAME.fullmatch(name) else (None, tr("No account called @%(name)s.", name=name))
    if op.kind == "guild":
        if value[:1] in ("#", "&"):
            # a topic (#gaming) or a public curation (&news): the prefix says which
            prefix, name = value[0], value[1:]
            if GUILDNAME.fullmatch(name):
                return prefix + name, None
            if prefix == "#":
                return None, tr("No topic called #%(name)s.", name=name)
            return None, tr("No public curation called &%(name)s.", name=name)
        name = value.lstrip("+")
        return (name, None) if GUILDNAME.fullmatch(name) else (None, tr("No guild called +%(name)s.", name=name))
    if op.kind == "date":
        try:
            day = datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            # Translators: %(operator)s is a search operator (since, until...) and stays as it is
            return None, tr("%(operator)s: needs a day written like 2026-01-31.", operator=op.key)
        return day.strftime("%Y-%m-%d"), None
    if op.kind == "choice":
        choice = value.lower()
        choice = CHOICE_ALIASES.get(op.key, {}).get(choice, choice)
        if choice in op.values:
            return choice, None
        # Translators: a search operator (has, is) and the values it takes, which stay as they are
        return None, tr("%(operator)s: can be %(values)s or %(last)s.", operator=op.key,
                        values=", ".join(op.values[:-1]), last=op.values[-1])
    if op.kind == "lang":
        code = value.lower()
        code = code if code in LANGUAGE_NAMES else LANGUAGE_BY_NAME.get(_squash(value))
        key = key_of(code)
        if key and key != UNKNOWN:
            return key, None                  # its row: lang:mk and lang:macedonian are the Bulgarian row
        # Translators: lang:en and lang:english are typed as they are
        return None, tr('No language called "%(name)s". Use a code such as lang:en or a name such as lang:english.',
                        name=value)
    if op.kind == "domain":
        host = re.sub(r"^[a-z][a-z0-9+.-]*://", "", value.lower())
        host = re.split(r"[/?#:]", host, maxsplit=1)[0]
        host = host[4:] if host.startswith("www.") else host
        if HOST.fullmatch(host):
            return host, None
        # Translators: domain: is a search operator and stays as it is
        return None, tr("domain: needs a site name, such as domain:example.com.")
    if op.kind == "url":
        if len(value) <= 200:
            return value, None
        # Translators: url: is a search operator and stays as it is
        return None, trn("url: can be at most %(num)s character.", "url: can be at most %(num)s characters.", 200)
    if op.kind == "flair":
        name = " ".join(value.split())
        if 0 < len(name) <= 24:
            return name, None
        # Translators: flair: is a search operator and stays as it is
        return None, trn("flair: needs a flair's name, at most %(num)s character.",
                         "flair: needs a flair's name, at most %(num)s characters.", 24)
    if op.kind == "number":
        if value.isascii() and value.isdigit() and int(value) <= MAX_NUMBER:
            return str(int(value)), None
        # Translators: a search operator and an example of it, which stay as they are
        return None, tr("%(operator)s: needs a whole number, such as %(example)s.", operator=op.key,
                        example=op.example)
    raise ValueError(op.kind)


def parse(text):
    """The search typed in the box, as a Query. Problems are listed, never raised: a search
    with a problem shows the problem instead of results."""
    text = " ".join((text or "").split())
    if len(text) > MAX_CHARS:
        return Query(problems=(str(TOO_LONG),))

    parts, problems = [], []
    for match in TOKEN.finditer(text):
        minus, phrase, token = match.groups()
        if phrase is not None:
            phrase = " ".join(phrase.split())
            if phrase:
                parts.append(Part("phrase", phrase, negated=bool(minus), text=match.group(0)))
            continue

        negated = token.startswith("-") and len(token) > 1
        body = token[1:] if negated else token
        op = OPERATOR.fullmatch(body)
        key = KEYS.get(op.group(1).lower()) if op else None
        if key is None:
            word = body.strip('"')
            if word and word != "-":
                parts.append(Part("word", word, negated=negated, text=token))
            continue

        spec, value = BY_KEY[key], op.group(2).strip('"')
        if not value.strip("@+#&"):
            # Translators: a search operator and an example of it, which stay as they are
            problems.append(tr("%(operator)s: needs something after it, such as %(example)s.", operator=key,
                               example=spec.example.lstrip("-")))
            continue
        if negated and not spec.negatable:
            # Translators: a search operator with a minus before it (-since), which stays as it is
            problems.append(tr("-%(operator)s: can't be used. Leave out the minus.", operator=key))
            continue
        cleaned, problem = _clean(spec, value)
        if problem:
            problems.append(problem)
            continue
        parts.append(Part(key, cleaned, negated=negated, text=token))

    if len(parts) > MAX_PARTS:
        problems.append(str(TOO_MANY))
    query = Query(parts=tuple(parts), problems=tuple(dict.fromkeys(problems)))
    low, high = time_bounds(query)
    if low is not None and high is not None and low >= high:
        query = replace(query, problems=query.problems + (str(NO_DAYS),))
    return query


def without(query, index):
    """The query's text with one part taken out (a filter's remove link)."""
    return " ".join(p.text for i, p in enumerate(query.parts) if i != index)


def _day_label(day):
    """A day as a chip shows it: "1 Jan 2026" in English, the reader's own short date otherwise."""
    d = datetime.strptime(day, "%Y-%m-%d")
    if current_locale() == i18n.ENGLISH:
        return f"{d.day} {d:%b %Y}"
    return date_text(day_start(day), "%d %b %Y", "medium")


def _date_label(kind, day):
    if kind == "since":
        return tr("Since %(date)s", date=day)
    if kind == "until":
        return tr("Until %(date)s", date=day)
    if kind == "after":
        return tr("After %(date)s", date=day)
    if kind == "before":
        return tr("Before %(date)s", date=day)
    return tr("On %(date)s", date=day)


HAS_CHIPS = {"link": (tr_lazy("With a link"), tr_lazy("Without a link")),
             "image": (tr_lazy("With a picture"), tr_lazy("Without a picture")),
             "video": (tr_lazy("With a video"), tr_lazy("Without a video")),
             "audio": (tr_lazy("With a sound"), tr_lazy("Without a sound")),
             "poll": (tr_lazy("With a poll"), tr_lazy("Without a poll"))}


def label(part):
    """How a filter reads on the results page, in the reader's language."""
    k, v, no = part.kind, part.value, part.negated
    if k == "from":
        return tr("Not from @%(name)s", name=v) if no else tr("From @%(name)s", name=v)
    if k == "in":
        shown = v if v[:1] in ("#", "&") else f"+{v}"
        # Translators: %(place)s is a guild (+pets), a topic (#gaming) or a curation (&news)
        return tr("Not in %(place)s", place=shown) if no else tr("In %(place)s", place=shown)
    if k in DATE_KEYS:
        return _date_label(k, _day_label(v))
    if k == "has":
        return str(HAS_CHIPS[v][1 if no else 0])
    if k == "lang":
        language = i18n.language_name(v, current_locale()) or v
        # Translators: %(language)s is a language's name (posts written in it)
        return tr("Not in %(language)s", language=language) if no else tr("In %(language)s", language=language)
    if k == "domain":
        return tr("Not linking to %(domain)s", domain=v) if no else tr("Linking to %(domain)s", domain=v)
    if k == "url":
        return tr('Link without "%(text)s"', text=v) if no else tr('Link with "%(text)s"', text=v)
    if k == "flair":
        return tr('Not flaired "%(flair)s"', flair=v) if no else tr('Flaired "%(flair)s"', flair=v)
    if k == "is":
        return str(IS_LABELS[v][1 if no else 0])
    # the English has always read "votes" and "comments" whatever the number (min_votes:1 is "At least 1 votes"), so
    # both English forms are the same; other languages choose their plural by the number
    if k == "min_votes":
        return trn("At least %(count)s votes", "At least %(count)s votes", int(v), count=v)
    if k == "min_comments":
        return trn("At least %(count)s comments", "At least %(count)s comments", int(v), count=v)
    return tr('Without "%(text)s"', text=v) if no else f'"{v}"'


# --- the advanced search form ------------------------------------------------------------

FORM_HAS = BY_KEY["has"].values


def form_fields(query):
    """The advanced form, filled in from a query. Whatever the form has no field for stays in
    `words` as it was typed, so sending the form back loses nothing."""
    fields = {"words": [], "from": "", "in": "", "since": "", "until": "", "has": [], "lang": "",
              "no_forwards": False}
    for part in query.parts:
        k = part.kind
        if k in ("from", "in", "since", "until", "lang") and not part.negated and not fields[k]:
            fields[k] = part.value
        elif k == "has" and not part.negated and part.value not in fields["has"]:
            fields["has"].append(part.value)
        elif k == "is" and part.negated and part.value == "forward" and not fields["no_forwards"]:
            fields["no_forwards"] = True
        else:
            fields["words"].append(part.text)
    fields["words"] = " ".join(fields["words"])
    return fields


def compose(words="", author="", guild="", since="", until="", has=(), lang="", no_forwards=False):
    """The search text the advanced form stands for (parse checks it like anything typed)."""
    out = [" ".join((words or "").split())]
    author = re.sub(r"[^A-Za-z0-9_\-]", "", (author or "").strip().lstrip("@"))
    guild = (guild or "").strip()
    prefix = guild[:1] if guild[:1] in ("#", "&") else ""
    guild = prefix + re.sub(r"[^A-Za-z0-9_]", "", guild.lstrip("+#&"))
    guild = guild if guild.lstrip("#&") else ""
    if author:
        out.append(f"from:@{author}")
    if guild:
        out.append(f"in:{guild}" if guild[:1] in ("#", "&") else f"in:+{guild}")
    for key, day in (("since", since), ("until", until)):
        day = re.sub(r"[^0-9-]", "", day or "")
        if day:
            out.append(f"{key}:{day}")
    for kind in FORM_HAS:
        if kind in (has or ()):
            out.append(f"has:{kind}")
    lang = re.sub(r"[^A-Za-z_-]", "", lang or "")
    if lang:
        out.append(f"lang:{lang}")
    if no_forwards:
        out.append("-is:forward")
    return " ".join(x for x in out if x)
