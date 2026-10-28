"""Interface languages: the rules, no Flask (the owner, 2026-10-24: "setting the language in the settings of the profile
should actually change the language of the site for the user"). The runtime is helpers/i18n_runtime.py, the Jinja side
helpers/i18n_jinja.py, the catalogues ruqqus/translations/<key>/LC_MESSAGES/*.po (made by scripts/i18n_catalogs.py with
the site's own LibreTranslate), the conventions for marking text docs/i18n.md.

* **Which languages**: the site's rows (helpers/languages.py ROWS: LibreTranslate's languages). One has an interface
  once its catalogue exists (`available`); English always.
* **Which one a reader gets** (`resolve`): forced (the public API is English; a job or an in-process request names the
  reader's), then a member's own first language ("Your language", `users.languages`), then the visitor's choice in the
  footer (the `ui_lang` cookie), then what their browser asks for (`Accept-Language`), then English. A language without
  a catalogue counts as not chosen, so a member whose first language has none reads English.
* **Right to left**: Arabic, Persian, Hebrew and Urdu (`RTL`): `<html dir="rtl">` and the mirrored stylesheets.
* **Pseudo-locales** (`I18N_PSEUDO=1`, never on a live site): `en-XA` writes every marked string with accents and a
  third longer ("[Ŝéţţîñĝš ···]"), `ar-XB` the same inside a right-to-left embedding. Text that stays plain English in them isn't marked.
"""
from ruqqus.helpers import languages

ENGLISH = "en"
PSEUDO = "en-XA"
PSEUDO_RTL = "ar-XB"
COOKIE = "ui_lang"
COOKIE_AGE = 60 * 60 * 24 * 365

RTL = frozenset({"ar", "fa", "he", "ur", PSEUDO_RTL})

# a row key -> Babel's locale (CLDR: plural rules, dates, language names), where they differ
BABEL_CODES = {"no": "nb", "zh": "zh_Hans", "tl": "fil", PSEUDO: "en", PSEUDO_RTL: "ar"}

# a row key -> the HTML lang attribute and the Intl tag the browser formats with, where they differ
HTML_LANG = {"zh": "zh-Hans", PSEUDO: "en-XA", PSEUDO_RTL: "ar-XB"}


def interface_keys():
    """Every language the interface could be in: the site's rows (English among them)."""
    return frozenset(languages.ROWS)


def babel_code(key):
    return BABEL_CODES.get(key, key)


def html_lang(key):
    return HTML_LANG.get(key, key)


def is_rtl(key):
    return key in RTL


def direction(key):
    return "rtl" if is_rtl(key) else "ltr"


def _key(code):
    """A saved, cookie or browser code as an interface key (row keys; "mk" is Bulgarian, "nb" Norwegian)."""
    if code in (PSEUDO, PSEUDO_RTL):
        return code
    code = (code or "").strip()
    if not code:
        return None
    base = code.replace("_", "-").split("-")[0].lower()
    key = languages.key_of(languages.BROWSER_ALIASES.get(base, base))
    return key if key and key != languages.UNKNOWN else None


def browser_keys(header):
    """What a browser asks for (Accept-Language), as interface keys, most wanted first. Unlike
    translation.from_accept_language this is empty when nothing is recognised: "asked for nothing we know" isn't
    "asked for English"."""
    weighted = []
    for i, part in enumerate((header or "").split(",")):
        piece = part.strip()
        if not piece:
            continue
        tag, _sep, q = piece.partition(";q=")
        try:
            weight = float(q) if q else 1.0
        except ValueError:
            weight = 0.0
        key = _key(tag.strip())
        if key and weight > 0:
            weighted.append((-weight, i, key))
    out = []
    for _w, _i, key in sorted(weighted):
        if key not in out:
            out.append(key)
    return out


def resolve(forced=None, saved=(), cookie=None, accept="", available=frozenset({ENGLISH})):
    """The interface language for a request.

    forced: a key that wins (the public API's English, a job's reader); saved: the member's own languages, in order
    (only the first counts: it is "Your language"); cookie: a visitor's footer choice; accept: the Accept-Language
    header; available: the keys with a catalogue (English always)."""
    available = frozenset(available) | {ENGLISH}
    if forced:
        key = _key(forced)
        return key if key in available else ENGLISH
    if saved:
        key = _key(saved[0])
        return key if key in available else ENGLISH
    key = _key(cookie) if cookie else None
    if key in available:
        return key
    for key in browser_keys(accept):
        if key in available:
            return key
    return ENGLISH


# --- language names ----------------------------------------------------------------------------------------------------
# From CLDR (Babel), never typed: a language named in the reader's interface language ("español" -> "Spanish" for an
# English reader, "inglés" for a Spanish one), and each language in its own name where a reader picks theirs. A row of
# two (Indonesian / Malay) names both.

ROW_NAMES = {"id": ("id", "ms")}
OWN_NAMES = {"id": "Bahasa Indonesia", "ms": "Bahasa Melayu"}


def _cldr(code, in_key):
    from babel import Locale
    try:
        return Locale.parse(babel_code(in_key)).languages.get(code)
    except (ValueError, TypeError):
        return None


def _first_upper(name):
    return name[:1].upper() + name[1:] if name else name


def language_name(code, in_key):
    """A stored code's row (helpers/languages.py) named in the interface language `in_key`, or None for a code the
    site doesn't offer. English is exactly languages.label (so English pages don't change)."""
    key = languages.key_of(code)
    if not key or key == languages.UNKNOWN:
        return None
    if in_key in (ENGLISH, PSEUDO, PSEUDO_RTL) or in_key not in interface_keys():
        return languages.label(key)
    names = [_cldr(c, in_key) for c in ROW_NAMES.get(key, (key,))]
    if not all(names):
        return languages.label(key)
    return " / ".join(_first_upper(n) for n in names)


def own_name(key):
    """A row in its own language ("Español", "日本語", "Bahasa Indonesia / Bahasa Melayu"): how a reader finds theirs."""
    names = []
    for code in ROW_NAMES.get(key, (key,)):
        name = OWN_NAMES.get(code) or _cldr(code, key if code == key else code)
        names.append(_first_upper(name) if name else languages.LANGUAGE_NAMES.get(code, code))
    return " / ".join(names)


# --- pseudo-locales ---------------------------------------------------------------------------------------------------

_ACCENTS = str.maketrans(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ",
    "àƀçđéƒĝĥîĵķļɱñöþǫŕšţûṽŵẋýžÀƁÇĐÉƑĜĤÎĴĶĻṀÑÖÞǪŔŠŢÛṼŴẊÝŽ")


def _pseudo_text(text):
    """Accented letters, leaving placeholders (%(name)s, %s, %%), tags and entities alone."""
    out, i, n = [], 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "%" and i + 1 < n:
            if text[i + 1] == "%":
                out.append("%%")
                i += 2
                continue
            if text[i + 1] == "(":
                end = text.find(")", i)
                if end != -1 and end + 1 < n:
                    out.append(text[i:end + 2])
                    i = end + 2
                    continue
            out.append(text[i:i + 2])
            i += 2
            continue
        if ch == "<":
            end = text.find(">", i)
            if end != -1:
                out.append(text[i:end + 1])
                i = end + 1
                continue
        if ch == "&":
            end = text.find(";", i)
            if end != -1 and end - i <= 10:
                out.append(text[i:end + 1])
                i = end + 1
                continue
        if text.startswith("](", i):                # a markdown link's address stays as it is
            end = text.find(")", i)
            if end != -1:
                out.append(text[i:end + 1])
                i = end + 1
                continue
        if ch in "[]()`":
            out.append(ch)                      # markdown's marks keep their shape
            i += 1
            continue
        out.append(ch.translate(_ACCENTS))
        i += 1
    return "".join(out)


def pseudo(text, rtl=False):
    """The pseudo-locale form of a string: accented, a third longer, and bracketed, so unmarked text stands out."""
    if not text or not text.strip():
        return text
    padding = "·" * max(1, len(text) // 3)
    body = f"[{_pseudo_text(text)} {padding}]"
    return f"\u202b{body}\u202c" if rtl else body
