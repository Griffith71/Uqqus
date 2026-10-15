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


def shown(display_name, username):
    """The name to draw: the display name when there is one, else the handle."""
    return display_name or username
