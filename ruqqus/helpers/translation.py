"""Translation (the owner's choices, 2026-10-23 and -24): a Translate button under a post or comment that isn't in one
of the reader's languages, which replaces the text in place ("Show original" brings it back), never automatic, by the
site's own LibreTranslate (its only service: Google was removed on 2026-10-24). The rules, no I/O. The SQL is
helpers/translation_store.py, the service helpers/translators.py, the route routes/translation_pages.py, the button
partials/translate_button.html with assets/js/translate.js. Languages are compared by row (helpers/languages.py: a
Macedonian post is in the Bulgarian row).

* **Who sees the button** (`offered`): signed-in members, under an item whose detected language (`language_code`) is
  in none of their rows (`users.languages`, Settings > Content; until they choose, the languages their browser asks
  for, `from_accept_language`), **and only when LibreTranslate can translate it** into their first (`engines`, the
  pairs it has loaded). An item with no detected language, or in a language the site doesn't offer, gets none.
* **The route** asks the same `engines` (`route`), so a button never offers what fails. LibreTranslate runs on our own
  server, so anything the reader may read may go to it; nothing that names the reader or the author is ever sent.
* **Once per text**: a translation is kept by (item, target language, service, a hash of the source) and served from
  there; an edit changes the hash.
"""
import hashlib
from dataclasses import dataclass

from ruqqus.helpers import languages as langs
from ruqqus.helpers.i18n_runtime import tr_lazy

LIBRE = "libretranslate"
ENGINE_LABELS = {LIBRE: "LibreTranslate"}

MAX_LANGUAGES = 8            # a member's own languages


def same_language(a, b):
    """The same language as far as the site is concerned: the same row (helpers/languages.py: Macedonian is read as
    Bulgarian, Cantonese as Chinese)."""
    return langs.same_row(a, b)


def _row(code):
    key = langs.key_of(code)
    return key if key and key != langs.UNKNOWN else None


def parse_languages(raw):
    """A member's saved languages ("en,fr") as row keys, in order, at most MAX_LANGUAGES (an old detailed code
    becomes its row: "mk" -> "bg")."""
    out = []
    for code in (raw or "").replace(" ", "").split(","):
        key = _row(code)
        if key and key not in out:
            out.append(key)
    return out[:MAX_LANGUAGES]


def from_accept_language(header):
    """The languages a browser asks for (Accept-Language: "fr-CA,fr;q=0.9,en;q=0.8") as row keys, most wanted first;
    English when it names none we know. Browsers' own codes are mapped ("nb" -> Norwegian, "fil" -> Tagalog)."""
    weighted = []
    for i, part in enumerate((header or "").split(",")):
        piece = part.strip()
        if not piece:
            continue
        tag, _, q = piece.partition(";q=")
        try:
            weight = float(q) if q else 1.0
        except ValueError:
            weight = 0.0
        code = tag.strip().split("-")[0].lower()
        key = _row(langs.BROWSER_ALIASES.get(code, code))
        if key and weight > 0:
            weighted.append((-weight, i, key))
    out = []
    for _, _, key in sorted(weighted):
        if key not in out:
            out.append(key)
    return out[:MAX_LANGUAGES] or ["en"]


def reader_languages(saved, accept_header):
    """(languages, chosen): the member's own choice, or what their browser asks for until they choose."""
    own = parse_languages(saved)
    return (own, True) if own else (from_accept_language(accept_header), False)


def target_for(languages):
    """The language a translation is made into: the reader's first."""
    return languages[0] if languages else "en"


def is_comment(item):
    return hasattr(item, "parent_submission")


def post_of(item):
    """The post an item is read on: itself, or a comment's post (None for a system notice)."""
    return item.post if is_comment(item) else item


def _gone(item):
    return bool(item.is_banned or item.deleted_utc or getattr(item, "purged_utc", 0))


def readable(item):
    """Something that may be translated at all: not removed, deleted or purged, nor on a post that is, nor a system
    notice, nor in a banned guild (a profile post stays: the post page's own rule). The button and the route both ask
    it, the route before its per-reader checks, so a button is never drawn under something the route refuses."""
    post = post_of(item)
    if post is None or _gone(item) or _gone(post):
        return False
    board = post.board
    return not (board is not None and board.is_banned and not post.is_profile_post)


def texts(item):
    """(title, body html) to translate. A comment has none of a post's title: `Comment.title` is its author's badge."""
    if is_comment(item):
        return "", item.body_html or ""
    return item.title or "", item.body_html or ""


def engines(source, target, libre_pairs):
    """The services that can translate from `source` into `target`: the site's LibreTranslate when it has the pair (row
    keys). The button (offered) and the route (route) both ask this, so a button never offers what can't be done."""
    src, tgt = _row(source), _row(target)
    if not src or not tgt or src == tgt:
        return ()
    return (LIBRE,) if (src, tgt) in (libre_pairs or ()) else ()


def offered(item_language, languages, libre_pairs=()):
    """Show "Translate" under an item in `item_language` to a reader of `languages`: it is in none of their languages
    and LibreTranslate can translate it into their first."""
    if not _row(item_language):
        return False
    if any(same_language(item_language, code) for code in languages):
        return False
    return bool(engines(item_language, target_for(languages), libre_pairs))


@dataclass(frozen=True)
class Route:
    engine: str = ""                 # LIBRE, or "" when nothing may translate it
    reason: str = ""                 # why not, for the reader


CANT = tr_lazy("This can't be translated right now.")


def route(source, target, libre_pairs):
    """Which service translates from `source` to `target` (engines)."""
    found = engines(source, target, libre_pairs)
    return Route(found[0]) if found else Route("", CANT)


def source_hash(*parts):
    return hashlib.sha256("\x1f".join(p or "" for p in parts).encode("utf-8")).hexdigest()

