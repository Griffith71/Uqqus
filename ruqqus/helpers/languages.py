import re

from ruqqus.helpers import post_formatting

# Display-only: language code (mostly ISO 639-1, a few ISO 639-3 for languages
# the detector supports that have no 2-letter code) -> English name. Generated
# from pycountry's ISO 639 registry against py3langid's actual supported set
# (confirmed via py3langid.langid.IDENTIFIER.nb_classes) - not hand-typed.
LANGUAGE_NAMES = {
    "ace": "Achinese", "af": "Afrikaans", "am": "Amharic", "an": "Aragonese", "ar": "Arabic",
    "ary": "Moroccan Arabic", "arz": "Egyptian Arabic", "as": "Assamese", "az": "Azerbaijani",
    "ba": "Bashkir", "bcl": "Central Bikol", "be": "Belarusian", "bg": "Bulgarian",
    "bn": "Bengali", "br": "Breton", "bs": "Bosnian", "ca": "Catalan", "crh": "Crimean Tatar",
    "cs": "Czech", "cy": "Welsh", "da": "Danish", "de": "German", "dz": "Dzongkha",
    "el": "Greek", "en": "English", "es": "Spanish", "et": "Estonian",
    "eu": "Basque", "ext": "Extremaduran", "fa": "Persian", "fi": "Finnish", "fo": "Faroese",
    "fr": "French", "fuv": "Nigerian Fulfulde", "fy": "Western Frisian", "ga": "Irish",
    "gd": "Scottish Gaelic",
    "gl": "Galician", "gom": "Goan Konkani", "gu": "Gujarati",
    "gug": "Paraguayan Guaraní", "guw": "Gun", "ha": "Hausa",
    "he": "Hebrew", "hi": "Hindi", "hr": "Croatian", "hu": "Hungarian",
    "hy": "Armenian", "id": "Indonesian", "ig": "Igbo", "is": "Icelandic", "it": "Italian",
    "ja": "Japanese", "jv": "Javanese", "ka": "Georgian", "kab": "Kabyle", "kik": "Kikuyu",
    "kk": "Kazakh", "km": "Khmer", "kn": "Kannada", "ko": "Korean", "ku": "Kurdish",
    "ky": "Kirghiz", "lb": "Luxembourgish", "lg": "Ganda", "lij": "Ligurian",
    "ln": "Lingala", "lo": "Lao", "lt": "Lithuanian", "ltg": "Latgalian", "lv": "Latvian",
    "mg": "Malagasy", "mk": "Macedonian", "ml": "Malayalam", "mn": "Mongolian", "mr": "Marathi",
    "ms": "Malay", "mt": "Maltese", "my": "Burmese", "ne": "Nepali", "nl": "Dutch",
    "nn": "Norwegian Nynorsk", "no": "Norwegian", "nso": "Pedi", "oc": "Occitan", "om": "Oromo",
    "or": "Oriya", "pa": "Panjabi", "pl": "Polish", "ps": "Pushto",
    "pt": "Portuguese", "qu": "Quechua", "ro": "Romanian", "ru": "Russian", "rw": "Kinyarwanda",
    "sa": "Sanskrit", "sdh": "Southern Kurdish", "se": "Northern Sami", "si": "Sinhala",
    "sk": "Slovak", "sl": "Slovenian", "sn": "Shona", "so": "Somali", "sq": "Albanian",
    "sr": "Serbian", "st": "Southern Sotho", "sv": "Swedish", "sw": "Swahili", "ta": "Tamil",
    "te": "Telugu", "tg": "Tajik", "th": "Thai", "tk": "Turkmen", "tl": "Tagalog", "tr": "Turkish",
    "tt": "Tatar", "ug": "Uighur", "uk": "Ukrainian", "ur": "Urdu", "uz": "Uzbek",
    "uzs": "Southern Uzbek", "vec": "Venetian", "vi": "Vietnamese",
    "wa": "Walloon", "wuu": "Wu Chinese", "xh": "Xhosa", "yo": "Yoruba", "yue": "Yue Chinese",
    "zh": "Chinese", "zu": "Zulu",
}

# Languages the detector knows that were taken off the list on purpose: those nobody speaks in daily life any more (a
# dead, purely liturgical or invented language) and, by the owner's choice of 2026-10-24, Esperanto and the pidgins
# and creoles. The detector is restricted to the list (see _detector), or it would still answer them: Latin-looking
# text would be tagged "la", a code that no filter offers; a creole's text now reads as the nearest listed language
# when the detector is sure of it, otherwise as unknown.
REMOVED_LANGUAGES = {
    "grc": "Ancient Greek",
    "hbo": "Ancient Hebrew",
    "la": "Latin",
    "vo": "Volapük",
    "eo": "Esperanto",
    "ht": "Haitian Creole",
    "pcm": "Nigerian Pidgin",
    "gcf": "Guadeloupean Creole French",
    "gcr": "Guianese Creole French",
}

# py3langid's model also emits "zxx" ("no linguistic content") for text that
# isn't really any language (pure numbers/emoji/URLs) - not a real language,
# so treat it the same as "couldn't determine a language" rather than listing
# it as a filterable option.
NO_LINGUISTIC_CONTENT = "zxx"

# Below this many letters there is nothing to go on ("ok", "lol", an emoji): such a post stays untagged
# (language_code NULL), which the Language filter's "Unknown" choice finds.
MIN_DETECTION_LETTERS = 3

# The detector's own confidence (its probabilities normalised over the listed languages) below which a guess is
# dropped rather than stored. Measured on real titles (tests/test_languages.py): short English titles came out as
# Sesotho or Serbian at 0.03-0.15 and were stored that way; real ones are well above (a four-word Russian line 0.41,
# "great shot!" 0.48, full sentences 0.8-1.0).
MIN_CONFIDENCE = 0.4

# What says nothing about the language: HTML tags, addresses, code, pictures and sounds (`[audio](/media/...)`), names
# after @ + # &, markdown's marks, the formatting extras ({c:red}...{/c}, `::: center`, `{.button}`), HTML entities. A
# link's words stay ([words](address)). Every pattern is bounded and the text is cut to DETECTION_CHARS first, so a
# hostile body ("[" 25,000 times) costs no more than an ordinary one.
DETECTION_CHARS = 4000
_ALIGN = "|".join(post_formatting.ALIGNMENTS)
_STRIP = [
    (re.compile(r"<[^>]{1,200}>"), " "),
    (re.compile(r"```[\s\S]{0,4000}?```"), " "),
    (re.compile(r"`[^`\n]{0,300}`"), " "),
    (re.compile(r"!?\[[^\]\n]{0,300}\]\(/media/[^)\s]{0,500}\)"), " "),
    (re.compile(r"!\[[^\]\n]{0,300}\]\([^)\s]{0,500}\)"), " "),
    (re.compile(r"\[([^\]\n]{0,300})\]\([^)\s]{0,500}\)"), r"\1"),
    (re.compile(r"(?:https?://|www\.)\S{1,2000}", re.I), " "),
    (re.compile(r"&[#\w]{1,10};"), " "),
    (re.compile(r"(?<![\w/])[@+#&][\w-]{1,100}"), " "),
    (re.compile(r"\{/?[a-z]{1,10}(?::[\w-]{1,20})?\}"), " "),
    (re.compile(r"\{\.[\w-]{1,20}\}"), " "),
    (re.compile(rf"^[ \t]{{0,3}}:::[ \t]*(?:{_ALIGN})?[ \t]*$", re.M | re.I), " "),
    (re.compile(r"[*_~>|=#\\]+"), " "),
    (re.compile(r"\s+"), " "),
]


def detection_text(title, body=""):
    """The words of a post, without what says nothing about its language."""
    text = f"{title or ''}\n{body or ''}"[:DETECTION_CHARS]
    for pattern, repl in _STRIP:
        text = pattern.sub(repl, text)
    return text.strip()


_identifier = None


def _detector():
    """The detector, answering only a language on the list (or "no linguistic content"), with probabilities
    normalised so its confidence can be read. Made once, the first time it is used, because loading the model is
    slow."""
    global _identifier
    if _identifier is None:
        from py3langid.langid import LanguageIdentifier, MODEL_FILE
        identifier = LanguageIdentifier.from_model_file(MODEL_FILE, norm_probs=True)
        identifier.set_languages(sorted(LANGUAGE_NAMES) + [NO_LINGUISTIC_CONTENT])
        _identifier = identifier
    return _identifier


# --- rows: the languages the filter and every language choice offer --------------------------------------------------
# The owner (2026-10-24): the site supports only the languages its own LibreTranslate translates, everywhere (the
# Language filter, My languages, search's lang:, the curation form, the interface itself), and languages so alike that
# they are identified together share one row. The list is fixed here, not read from the running service: an outage, or
# a change to which models are loaded, would otherwise empty the filter and silently drop members' saved choices. What
# is running decides only whether the Translate button shows (helpers/translation.py).

# LibreTranslate's full model list (its argos index), in our codes: it calls Norwegian "nb", Brazilian Portuguese "pb"
# (pt-BR) and Traditional Chinese "zt" (zh-Hant); see LIBRE_ALIASES. It also has Esperanto, which isn't listed here:
# taken off the list (REMOVED_LANGUAGES)
LIBRE_SUPPORTED = frozenset({
    "ar", "az", "bg", "bn", "ca", "cs", "da", "de", "el", "en", "es", "et", "eu", "fa", "fi", "fr", "ga", "gl",
    "he", "hi", "hu", "id", "it", "ja", "ko", "ky", "lt", "lv", "ms", "nl", "no", "pl", "pt", "ro", "ru", "sk", "sl",
    "sq", "sv", "sw", "th", "tl", "tr", "uk", "ur", "vi", "zh",
})

# Languages the detector can't reliably tell apart (measured: Bosnian never wins over Croatian and Serbian, Egyptian and
# Moroccan Arabic always read as Arabic, Malay/Indonesian and Macedonian/Bulgarian are coin-flips, short Chinese splits
# between Mandarin, Cantonese and Wu). (key, label, codes): the key is one of the group's own codes and one
# LibreTranslate translates, so old addresses (/language?codes=bg, lang:bg) keep working. Macedonian is shown simply as
# Bulgarian (the owner, 2026-10-24); typing "Macedonian" still finds the row (catalogue's search text, search's
# lang:macedonian). Bosnian / Croatian / Serbian and Uzbek had rows until LibreTranslate became the only service: none
# of their members is one it translates, so they read as Other or unknown now.
GROUPS = (
    ("zh", "Chinese", ("zh", "yue", "wuu")),
    ("ar", "Arabic", ("ar", "arz", "ary")),
    ("id", "Indonesian / Malay", ("id", "ms")),
    ("bg", "Bulgarian", ("bg", "mk")),
    ("no", "Norwegian", ("no", "nn")),
)

# The stored codes the site offers: LibreTranslate's and the members of their rows. Every other listed language is
# still detected (a Walloon post is "wa" rather than forced into French) and reads as "Other or unknown".
OFFERED_CODES = frozenset(LIBRE_SUPPORTED | {code for key, _label, codes in GROUPS if key in LIBRE_SUPPORTED
                                             for code in codes})
NOT_OFFERED = frozenset(LANGUAGE_NAMES) - OFFERED_CODES

# LibreTranslate's own codes for ours (its /languages answers these)
LIBRE_ALIASES = {"zh-Hans": "zh", "zh-Hant": "zh", "zt": "zh", "pt-BR": "pt", "pb": "pt", "nb": "no"}

# The model codes LibreTranslate is told to load (LT_LOAD_ONLY in docker-compose.yml): its own codes for ours
LIBRE_LOAD_CODES = {"no": "nb"}


def libre_load_only():
    """LT_LOAD_ONLY: every language the site supports, in LibreTranslate's codes, English first."""
    return ",".join(["en"] + sorted(LIBRE_LOAD_CODES.get(c, c) for c in LIBRE_SUPPORTED if c != "en"))

# Codes browsers send (Accept-Language) for ours
BROWSER_ALIASES = {"fil": "tl", "iw": "he", "in": "id", "jw": "jv", "sh": "hr", "nb": "no", "nn": "no"}

_KEY = {}
for _key, _label, _codes in GROUPS:
    for _code in _codes:
        _KEY[_code] = _key
for _code in OFFERED_CODES:
    _KEY.setdefault(_code, _code)

# key -> (label, member codes)
ROWS = {}
for _code in sorted(OFFERED_CODES):
    _key = _KEY[_code]
    ROWS.setdefault(_key, [None, []])[1].append(_code)
for _key in ROWS:
    group = next((g for g in GROUPS if g[0] == _key), None)
    ROWS[_key] = (group[1] if group else LANGUAGE_NAMES[_key], tuple(sorted(ROWS[_key][1])))

# "Other or unknown" in the Language filter: posts with no detected language (too short, an emoji, a link, or not
# confident) and posts in a language no service translates. Without it every language choice hid them. ISO 639-2's
# "undetermined".
UNKNOWN = "und"
UNKNOWN_LABEL = "Other or unknown"


def key_of(code):
    """The row a stored or chosen code belongs to: its key, UNKNOWN for a listed language the site doesn't offer (and
    for "und" itself), or None for anything else."""
    if not code:
        return None
    if code in _KEY:
        return _KEY[code]
    if code == UNKNOWN or code in LANGUAGE_NAMES:
        return UNKNOWN
    return None


def members(key):
    """The stored codes a row stands for."""
    return ROWS[key][1] if key in ROWS else ()


def same_row(a, b):
    """Are two codes the same language as far as the site is concerned?"""
    return bool(a and b) and key_of(a) is not None and key_of(a) == key_of(b)


def choosable(code):
    """A code the Language filter (feeds, curations) may be given: any code of a row (old addresses hold detailed
    codes such as "mk"), or Unknown."""
    return key_of(code) is not None


def label(code):
    """A code's row label ("Bulgarian" for "mk", "Spanish", "Other or unknown")."""
    key = key_of(code)
    if key == UNKNOWN:
        return UNKNOWN_LABEL
    return ROWS[key][0] if key in ROWS else None


def catalogue():
    """The choices, in the filter and wherever a language is chosen: the rows by name (searchable by every member's
    name too), then Other or unknown."""
    rows = sorted(ROWS.items(), key=lambda kv: kv[1][0])
    out = []
    for key, (name, codes) in rows:
        search = " ".join([name] + [LANGUAGE_NAMES[c] for c in codes if LANGUAGE_NAMES[c] != name])
        out.append({"value": key, "label": name, "search": search})
    out.append({"value": UNKNOWN, "label": UNKNOWN_LABEL, "search": "other unknown undetected"})
    return out


def language_condition(column, codes):
    """The SQL condition for a Language filter's choice: every code of the chosen rows, and, for Other or unknown, the
    untagged posts and those in a language no service translates."""
    from sqlalchemy import and_, or_
    keys = known_codes(codes)
    named = sorted({code for key in keys if key != UNKNOWN for code in members(key)})
    parts = []
    if named:
        parts.append(column.in_(named))
    if UNKNOWN in keys:
        parts.append(column.is_(None))
        parts.append(and_(column.isnot(None), column.notin_(sorted(OFFERED_CODES))))
    return or_(*parts)


def known_codes(codes):
    """A saved or requested choice as row keys, in order, once each: a detailed code becomes its row ("mk" -> "bg"),
    a language no service translates becomes Other or unknown, anything else is dropped (a saved choice can hold a
    language that has since been removed)."""
    out = []
    for code in codes or []:
        key = key_of(code)
        if key is not None and key not in out:
            out.append(key)
    return out


# --- detection -------------------------------------------------------------------------------------------------------
# py3langid names the language: LibreTranslate's detector can only answer the languages it has loaded, and for any
# other it confidently names a neighbour (measured: Belarusian -> uk 100, Kazakh -> ru 86, Galician -> pt 71, Swedish
# "Hej allihopa" -> pl 90). It is asked only as a second opinion on short or unclear text, where py3langid is weak and
# LibreTranslate is good within its own languages ("Guten Morgen" de 90, "谢谢"), and only believed when nothing
# py3langid ranks as likely is a language it can't know. Google's detection is never used: it is billed per character
# and would send every post, Circle and private-guild ones included, to Google.

LIBRE_MIN_CONFIDENCE = 40         # LibreTranslate's 0-100
LIBRE_SUPPORT_FLOOR = 0.10        # py3langid must give LibreTranslate's language at least this much
STORE_GROUP_BELOW = 0.8           # a code holding less of its group's share is stored as the group's key

# Short text (under SHORT_LETTERS letters, a CJK character counting 2 as in helpers/text_length.py) is where py3langid
# is weakest: a single language needs SHORT_MIN_CONFIDENCE there ("Дякую" is Ukrainian, but Belarusian 0.47), and a
# row's members together need ROW_MIN_CONFIDENCE ("Selamat pagi semuanya": Malay 0.55 + Indonesian 0.44) - high
# enough that "lmao same" (Serbian 0.40 + Croatian 0.29 + Bosnian 0.12) stays unknown.
SHORT_LETTERS = 20
SHORT_MIN_CONFIDENCE = 0.6
ROW_MIN_CONFIDENCE = 0.7
SHORT_ROW_MIN_CONFIDENCE = 0.9


class _Unasked:
    """LibreTranslate is set up but couldn't be asked (down, slow, paused after a failure): not the same as it being
    unsure, so a code the text already has can be kept (decide's `keep`) instead of being wiped by an outage."""

    def __repr__(self):
        return "UNASKED"


UNASKED = _Unasked()


def letters_in(text):
    """How many letters `text` has, a CJK character (and anything else X counts double) counting 2."""
    from ruqqus.helpers.text_length import weight
    return sum(weight(ord(ch)) for ch in text if ch.isalpha())


def _row_or_self(code):
    """A code's row key, or the code itself for a language no service translates (it is its own group)."""
    key = _KEY.get(code)
    return key if key is not None else code


def decide(ranked, letters, libre=None, libre_loaded=frozenset(), keep=None):
    """The code to store, or None. Pure.

    ranked: py3langid's [(code, probability)], best first, normalised; letters: how many letters the text has;
    libre: a callable asked (once, only if needed) for LibreTranslate's (row key, confidence 0-100), None, or UNASKED;
    libre_loaded: the row keys LibreTranslate has loaded right now; keep: the code the text has now (an edit, the
    backfill), kept when the answer needed LibreTranslate and it couldn't be asked, as long as py3langid still gives
    its row some weight."""
    if letters < MIN_DETECTION_LETTERS or not ranked:
        return None
    top, p = ranked[0]
    if top == NO_LINGUISTIC_CONTENT:
        return None
    groups = {}
    for code, prob in ranked:
        if code != NO_LINGUISTIC_CONTENT:
            group = _row_or_self(code)
            groups[group] = groups.get(group, 0.0) + prob

    short = letters < SHORT_LETTERS

    # 1. py3langid is sure of one language: that language, or its row when the row's other members share much of it
    if p >= (SHORT_MIN_CONFIDENCE if short else MIN_CONFIDENCE):
        group = _row_or_self(top)
        return top if p >= STORE_GROUP_BELOW * groups[group] else group

    # 2. py3langid is sure of a row, split between its members ("你好，世界" is yue 0.57 / zh 0.33: Chinese)
    best_row = max(groups, key=groups.get)
    if best_row in ROWS and groups[best_row] >= (SHORT_ROW_MIN_CONFIDENCE if short else ROW_MIN_CONFIDENCE):
        return best_row

    # 3. a second opinion: LibreTranslate names one of its own languages with confidence, py3langid gives that row some
    #    weight, and nothing py3langid ranks at least as high is a language LibreTranslate can't know
    if libre is None:
        return None
    answer = libre()
    if answer is UNASKED:
        kept = _row_or_self(keep) if keep else None
        return keep if kept and groups.get(kept, 0.0) >= LIBRE_SUPPORT_FLOOR else None
    if not answer:
        return None
    key, confidence = answer
    if confidence < LIBRE_MIN_CONFIDENCE or key not in libre_loaded:
        return None
    share = groups.get(key, 0.0)
    if share < LIBRE_SUPPORT_FLOOR:
        return None
    if any(group not in libre_loaded and weight >= share for group, weight in groups.items() if group != key):
        return None
    return key


def detect_language(title, body="", second_opinion=True, keep=None):
    """Best-effort language code for a post's or comment's text, or None when there is too little to go on, no real
    language in it, or neither detector is sure (an untagged item; the filter's "Other or unknown" finds it).
    second_opinion=False never asks LibreTranslate (tests, scripts that run without it). keep: the code the text has
    now (an edit, the backfill), kept while LibreTranslate can't be asked (decide)."""
    text = detection_text(title, body)
    letters = letters_in(text)
    if letters < MIN_DETECTION_LETTERS:
        return None
    ranked = _detector().rank(text)

    libre, loaded = None, frozenset()
    if second_opinion:
        from ruqqus.helpers import translators
        if translators.libre_url():
            loaded = translators.libre_keys()

            def libre():                                        # asked only if needed
                return translators.libre_detect(text)
    return decide(ranked, letters, libre, loaded, keep=keep)
