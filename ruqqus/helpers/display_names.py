"""A member's display name, as on x.com: free text they set and change at any time (Settings > Profile), drawn first
with the unchanging @handle beside it. Pure rules, no I/O (tests/test_display_names.py).

* The @handle (`users.username`) stays the one unique name: it is the address of the profile, what mentions and
  search use, and it keeps its coin cost and 7-day wait. A display name is never unique and is never an address.
* One line of text, at most MAX_LEN characters: spaces, accents and emoji are fine. It can't hold `<`, `>` or `@` (so
  it can't read as a mention or as someone else's handle), control characters, or the invisible characters that make
  one name look like another (zero-width spaces, right-to-left overrides). The joiner and variation selector that
  emoji sequences are made of are kept. An empty value clears it.
* A display name that is, ignoring case, spaces, dots, dashes and underscores, another account's @handle is refused
  by the route (`key` is what it compares): nobody can wear someone else's handle as a name.
* The word filter rates it with the username (word_filter_store.apply_user_severity, into `name_severity`).
* Anonymous items never show one (helpers/anonymity.py: the stand-in's `shown_name` is "Anonymous").
"""
import re
import unicodedata

MAX_LEN = 50

_SPACES = re.compile(r"\s+")
_KEEP = {chr(0x200D), chr(0xFE0F)}                  # emoji sequences are glued with a joiner and a variation selector
_SEPARATORS = re.compile(r"[\s._-]+")


class Refused(ValueError):
    """A value that cannot be saved; the message is shown to the member."""


def clean(value):
    """The display name to store: one line, spaces collapsed, at most MAX_LEN characters. "" clears it."""
    text = _SPACES.sub(" ", unicodedata.normalize("NFC", str(value or ""))).strip()    # a pasted newline or tab is a space
    for char in text:
        if char in _KEEP:
            continue
        category = unicodedata.category(char)
        if char in "<>":
            raise Refused("A display name can't contain < or >.")
        if char == "@":
            raise Refused("A display name can't contain @: it would read as a handle.")
        if category[0] == "C" or category in ("Zl", "Zp"):
            raise Refused("A display name can't contain control or invisible characters.")
    if len(text) > MAX_LEN:
        raise Refused(f"A display name can be at most {MAX_LEN} characters.")
    return text


def key(name):
    """What two names are compared by: case, spaces, dots, dashes and underscores don't tell them apart."""
    return _SEPARATORS.sub("", unicodedata.normalize("NFKC", str(name or "")).casefold())


# Letters from other scripts that look like ASCII ones (Unicode TR39 "confusables", the ones that matter for
# handles, which are ASCII letters, digits and underscores). After NFKC (full-width forms), case folding and removing
# accents and combining marks, these are swapped for the letter they imitate.
_CONFUSABLES = str.maketrans({
    # Cyrillic
    "а": "a", "в": "b", "е": "e", "ё": "e", "һ": "h", "і": "i", "ї": "i", "ј": "j", "к": "k", "м": "m", "н": "h",
    "о": "o", "р": "p", "с": "c", "т": "t", "у": "y", "х": "x", "ѕ": "s", "ԁ": "d", "ԛ": "q", "ԝ": "w", "ӏ": "l",
    "ɡ": "g", "ь": "b", "ү": "y", "ѵ": "v", "ʏ": "y",
    # Greek
    "α": "a", "β": "b", "ε": "e", "η": "n", "ι": "i", "κ": "k", "ν": "v", "ο": "o", "ρ": "p", "τ": "t", "υ": "u",
    "χ": "x", "γ": "y", "ϲ": "c", "ϳ": "j", "ω": "w", "μ": "u",
    # Latin look-alikes that keep no accent to strip
    "ı": "i", "ł": "l", "ø": "o", "đ": "d", "ħ": "h", "ŀ": "l", "ƚ": "l", "ɩ": "i", "ʟ": "l", "ꞁ": "l", "ß": "ss",
    "æ": "ae", "œ": "oe", "þ": "p",
})
# what an ASCII handle itself can be mistaken for: 0/o, 1/l/i/I, rn/m, vv/w (applied to both sides)
_ASCII_LOOKALIKES = (("rn", "m"), ("vv", "w"), ("0", "o"), ("1", "l"), ("i", "l"), ("|", "l"))

RESERVED_WORDS = ("admin", "administrator", "moderator", "moderators", "mod team", "staff", "official", "support team",
                  "trust and safety", "system")
RESERVED = "That name is kept for the site's staff."


def skeleton(name):
    """What a name looks like, for telling whether it imitates a handle (audit I-04): full-width and styled letters
    folded (NFKC), case folded, accents and combining marks removed, other scripts' look-alike letters swapped for the
    ASCII they imitate, then the handle look-alikes (0/o, 1/l/i, rn/m, vv/w), keeping only letters and digits."""
    text = unicodedata.normalize("NFKC", str(name or "")).casefold()
    text = "".join(c for c in unicodedata.normalize("NFKD", text) if unicodedata.category(c) not in ("Mn", "Me", "Cf"))
    text = text.translate(_CONFUSABLES)
    for a, b in _ASCII_LOOKALIKES:
        text = text.replace(a, b)
    return "".join(c for c in text if c.isascii() and c.isalnum())


def reserved_refusal(name, site_names=()):
    """A display name that reads as the site's staff or the site itself ("Admin", "Uqqus Moderators"), refused for
    everyone but the site's admins. Whole words only, so "Badminton fan" is fine. None when it's fine."""
    words = [w for w in (skeleton(part) for part in re.split(r"[^\w]+|_", unicodedata.normalize("NFKC", str(name or "")))) if w]
    runs = {"".join(words[i:j]) for i in range(len(words)) for j in range(i + 1, min(len(words), i + 4) + 1)}
    for word in RESERVED_WORDS + tuple(site_names):
        mark = skeleton(word)
        if mark and mark in runs:
            return RESERVED
    return None


def shown(display_name, username):
    """The name to draw: the display name when there is one, else the handle."""
    return display_name or username
