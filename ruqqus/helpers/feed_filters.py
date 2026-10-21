"""The feed filters beside Regional / Language / Categorical: Post type and the Advanced filter (the rules, no I/O).

Both narrow All, For You and Following and are kept in the session like the other three (routes/front.py). They
only ever narrow a feed: who may see which post is the feed's own business, decided before these apply.

**Post type** is one or more of `POST_TYPES` (a post shows when it is any of the ticked types). The conditions are in
helpers/feed_filter_sql.py; a new type needs its entry here and its condition there, nothing else.

**The Advanced filter** is X's Advanced search for a feed. Its fields are written as search operators
(`query_text`) and checked by helpers/search_query.parse exactly as a typed search is, so it can only ever say what
search can say. Two fields have no operator and are kept beside the query: any of these words, and mentioning.
"""
import re
from datetime import datetime

from ruqqus.helpers import search_query as sq

# (key, label, group). The order is the dropdown's.
POST_TYPES = (
    ("text", "Text", "Content"),
    ("link", "Links", "Content"),
    ("image", "Pictures", "Content"),
    ("video", "Videos", "Content"),
    ("audio", "Sounds", "Content"),
    ("poll", "Polls", "Content"),
    ("guilds", "Forwarded to guilds", "Where"),
    ("circles", "For circles", "Where"),
    ("coauthored", "Co-authored", "Who wrote it"),
    ("collab", "Collaborative", "Who wrote it"),
    ("anonymous", "Anonymous", "Who wrote it"),
    ("space_live", "Live Spaces", "Spaces"),
    ("space_upcoming", "Upcoming Spaces", "Spaces"),
    ("space_recording", "Space recordings", "Spaces"),
)
TYPE_KEYS = tuple(key for key, _, _ in POST_TYPES)
TYPE_LABELS = {key: label for key, label, _ in POST_TYPES}
GROUPS = tuple(dict.fromkeys(group for _, _, group in POST_TYPES))

# The filter lives in the session, which is a signed cookie that also holds the sign-in: a cookie over the browser's
# 4 KB is dropped whole, signing the member out. These limits keep a filter with every field full near 1 KB, and
# `compact` stores only the fields that are set.
MAX_WORDS = 5
WORD_CHARS = 30
PHRASE_CHARS = 80
MAX_NAMES = 3
SHOW, ONLY, HIDE = "show", "only", "hide"
LABEL_CHOICES = {"ai": (SHOW, ONLY, HIDE), "partnership": (SHOW, ONLY, HIDE), "forwards": (SHOW, HIDE)}

WORD_FIELDS = ("all_words", "any_words", "none_words")
NAME_FIELDS = ("from", "not_from", "mentioning", "in_guilds", "not_in_guilds", "topics")
TEXT_FIELDS = ("phrase", "domain", "since", "until")
NUMBER_FIELDS = ("min_votes", "min_comments")

EMPTY = {**{f: [] for f in WORD_FIELDS + NAME_FIELDS}, **{f: "" for f in TEXT_FIELDS + NUMBER_FIELDS},
         **{f: SHOW for f in LABEL_CHOICES}}

_USER = re.compile(r"[A-Za-z0-9_\-]{1,25}")
_GUILD = re.compile(r"[+&]?[A-Za-z0-9_]{1,25}")
_TOPIC = re.compile(r"[A-Za-z0-9_]{1,25}")
_SPLIT = re.compile(r"[\s,]+")


def parse_types(text):
    """The post types a comma-separated value names: known keys only, once each, in `POST_TYPES` order."""
    asked = set(x.strip() for x in (text or "").split(",")) if isinstance(text, str) else set(text or ())
    return [key for key in TYPE_KEYS if key in asked]


def type_labels(keys):
    return [TYPE_LABELS[k] for k in keys if k in TYPE_LABELS]


def type_summary(keys):
    """What the Post type row says it shows: "All posts", "Polls", "Polls +2"."""
    labels = type_labels(keys)
    if not labels:
        return "All posts"
    return labels[0] if len(labels) == 1 else f"{labels[0]} +{len(labels) - 1}"


def _words(value):
    """Plain words, split on spaces and commas; quotes and a leading minus are dropped (the field says what they mean)."""
    out = []
    for word in _SPLIT.split((value or "").replace('"', " ")):
        word = word.lstrip("-")
        if word and len(word) <= WORD_CHARS and word not in out:
            out.append(word)
    return out[:MAX_WORDS]


def _names(value, pattern, strip):
    out = []
    for name in _SPLIT.split(value or ""):
        name = name.lstrip(strip)
        if name and pattern.fullmatch(name) and name.lower() not in (x.lower() for x in out):
            out.append(name)
    return out[:MAX_NAMES]


def _day(value):
    try:
        return datetime.strptime((value or "").strip(), "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        return ""


def _number(value):
    value = (value or "").strip()
    if value.isascii() and value.isdigit() and 0 < int(value) <= sq.MAX_NUMBER:
        return str(int(value))
    return ""


def _host(value):
    host = re.sub(r"^[a-z][a-z0-9+.-]*://", "", (value or "").strip().lower())
    host = re.split(r"[/?#:]", host, maxsplit=1)[0]
    host = host[4:] if host.startswith("www.") else host
    return host if sq.HOST.fullmatch(host) else ""


def clean(form):
    """The Advanced filter as it may be stored: known fields only, each cut to its limits. `form` is anything with
    `.get` (the request's args, or a dict read back from the session). A guild keeps its + or & (a curation)."""
    get = form.get
    spec = dict(EMPTY)
    for f in WORD_FIELDS:
        spec[f] = _words(_flat(get(f)))
    phrase = " ".join(_flat(get("phrase")).replace('"', " ").split())
    spec["phrase"] = phrase if len(phrase) <= PHRASE_CHARS else ""
    for f in ("from", "not_from", "mentioning"):
        spec[f] = _names(_flat(get(f)), _USER, "@")
    for f in ("in_guilds", "not_in_guilds"):
        spec[f] = [n if n[:1] == "&" else n.lstrip("+") for n in _names(_flat(get(f)), _GUILD, "")]
    spec["topics"] = _names(_flat(get("topics")), _TOPIC, "#")
    spec["domain"] = _host(_flat(get("domain")))
    spec["since"], spec["until"] = _day(_flat(get("since"))), _day(_flat(get("until")))
    for f in NUMBER_FIELDS:
        spec[f] = _number(_flat(get(f)))
    for f, choices in LABEL_CHOICES.items():
        choice = _flat(get(f))
        spec[f] = choice if choice in choices else SHOW
    return spec


def _flat(value):
    """A stored list back to its text (a spec read from the session), or the text a form sent."""
    if isinstance(value, (list, tuple)):
        return " ".join(str(x) for x in value)
    return value if isinstance(value, str) else ""


def is_empty(spec):
    return not spec or clean(spec) == EMPTY


def compact(spec):
    """What the session keeps: only the fields that are set (`clean` puts the others back)."""
    spec = clean(spec or {})
    return {f: value for f, value in spec.items() if value != EMPTY[f]}


def field_count(spec):
    """How many fields are set: the badge on the Advanced filter row."""
    if not spec:
        return 0
    spec = clean(spec)
    return sum(1 for f, empty in EMPTY.items() if spec[f] != empty)


def _guild_operator(name):
    return f"in:{name}" if name[:1] == "&" else f"in:+{name}"


def query_text(spec):
    """The fields that have a search operator, written as search text (search_query.parse checks it)."""
    spec = clean(spec)
    out = list(spec["all_words"])
    if spec["phrase"]:
        out.append(f'"{spec["phrase"]}"')
    out += [f"-{w}" for w in spec["none_words"]]
    out += [f"from:@{n}" for n in spec["from"]]
    out += [f"-from:@{n}" for n in spec["not_from"]]
    out += [_guild_operator(n) for n in spec["in_guilds"]]
    out += [f"in:#{n}" for n in spec["topics"]]
    out += [f"-{_guild_operator(n)}" for n in spec["not_in_guilds"]]
    if spec["domain"]:
        out.append(f"domain:{spec['domain']}")
    for f, operator in (("ai", "is:ai"), ("partnership", "is:partnership"), ("forwards", "is:forward")):
        if spec[f] == ONLY:
            out.append(operator)
        elif spec[f] == HIDE:
            out.append(f"-{operator}")
    for f in NUMBER_FIELDS:
        if spec[f]:
            out.append(f"{f}:{spec[f]}")
    if spec["since"]:
        out.append(f"since:{spec['since']}")
    if spec["until"]:
        out.append(f"until:{spec['until']}")
    return " ".join(out)


def form_values(spec):
    """The popup's fields filled in from a stored spec (lists written back as text)."""
    spec = clean(spec or {})
    out = {}
    for f, value in spec.items():
        if f in ("in_guilds", "not_in_guilds"):
            out[f] = " ".join(n if n[:1] == "&" else f"+{n}" for n in value)
        elif f == "topics":
            out[f] = " ".join(f"#{n}" for n in value)
        elif f in ("from", "not_from", "mentioning"):
            out[f] = " ".join(f"@{n}" for n in value)
        elif isinstance(value, list):
            out[f] = " ".join(value)
        else:
            out[f] = value
    return out


# --- a member's saved default (users.feed_defaults) --------------------------------------------------------------
# All five filters as one JSON object. Written by "Save as my default" in the sidebar, read into a new session once.

DEFAULT_KEYS = ("regions", "languages", "categories", "types", "advanced")
MAX_DEFAULT_ITEMS = 200            # more regions or categories than exist is not a default, it is a mistake


def defaults_payload(regions, languages, categories, types, spec):
    """The JSON text stored for a member's default."""
    import json
    return json.dumps({
        "regions": [str(x) for x in (regions or [])][:MAX_DEFAULT_ITEMS],
        "languages": [str(x) for x in (languages or [])][:MAX_DEFAULT_ITEMS],
        "categories": [int(x) for x in (categories or []) if str(x).lstrip("-").isdigit()][:MAX_DEFAULT_ITEMS],
        "types": parse_types(types or []),
        "advanced": compact(spec or {}),
    }, sort_keys=True, separators=(",", ":"))


def read_defaults(text, known_regions=(), known_languages=(), known_categories=()):
    """A saved default as {regions, languages, categories, types, advanced}, every part checked again (a region,
    language or category that no longer exists is dropped), or None when nothing is saved or it can't be read."""
    import json
    try:
        data = json.loads(text) if text else None
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None

    def listed(key):
        value = data.get(key)
        return value if isinstance(value, list) else []

    regions = [r for r in listed("regions") if isinstance(r, str) and r in known_regions]
    languages = [c for c in listed("languages") if isinstance(c, str) and c in known_languages]
    categories = [c for c in listed("categories") if isinstance(c, int) and not isinstance(c, bool)
                  and c in known_categories]
    advanced = data.get("advanced") if isinstance(data.get("advanced"), dict) else {}
    return {"regions": list(dict.fromkeys(regions))[:MAX_DEFAULT_ITEMS],
            "languages": list(dict.fromkeys(languages))[:MAX_DEFAULT_ITEMS],
            "categories": list(dict.fromkeys(categories))[:MAX_DEFAULT_ITEMS],
            "types": parse_types([t for t in listed("types") if isinstance(t, str)]),
            "advanced": compact(advanced)}


def chips(query, spec):
    """The notice's chips: one per operator the feed applies (search's own labels), then the two that have none."""
    spec = clean(spec or {})
    out = [sq.label(part) for part in query.parts] if query is not None else []
    if spec["any_words"]:
        out.append("Any of " + ", ".join(f'"{w}"' for w in spec["any_words"]))
    out += [f"Mentioning @{n}" for n in spec["mentioning"]]
    return out
