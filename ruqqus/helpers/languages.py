import py3langid as langid

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
    "el": "Modern Greek", "en": "English", "eo": "Esperanto", "es": "Spanish", "et": "Estonian",
    "eu": "Basque", "ext": "Extremaduran", "fa": "Persian", "fi": "Finnish", "fo": "Faroese",
    "fr": "French", "fuv": "Nigerian Fulfulde", "fy": "Western Frisian", "ga": "Irish",
    "gcf": "Guadeloupean Creole French", "gcr": "Guianese Creole French", "gd": "Scottish Gaelic",
    "gl": "Galician", "gom": "Goan Konkani", "gu": "Gujarati",
    "gug": "Paraguayan Guaraní", "guw": "Gun", "ha": "Hausa",
    "he": "Hebrew", "hi": "Hindi", "hr": "Croatian", "ht": "Haitian", "hu": "Hungarian",
    "hy": "Armenian", "id": "Indonesian", "ig": "Igbo", "is": "Icelandic", "it": "Italian",
    "ja": "Japanese", "jv": "Javanese", "ka": "Georgian", "kab": "Kabyle", "kik": "Kikuyu",
    "kk": "Kazakh", "km": "Khmer", "kn": "Kannada", "ko": "Korean", "ku": "Kurdish",
    "ky": "Kirghiz", "lb": "Luxembourgish", "lg": "Ganda", "lij": "Ligurian",
    "ln": "Lingala", "lo": "Lao", "lt": "Lithuanian", "ltg": "Latgalian", "lv": "Latvian",
    "mg": "Malagasy", "mk": "Macedonian", "ml": "Malayalam", "mn": "Mongolian", "mr": "Marathi",
    "ms": "Malay", "mt": "Maltese", "my": "Burmese", "ne": "Nepali", "nl": "Dutch",
    "nn": "Norwegian Nynorsk", "no": "Norwegian", "nso": "Pedi", "oc": "Occitan", "om": "Oromo",
    "or": "Oriya", "pa": "Panjabi", "pcm": "Nigerian Pidgin", "pl": "Polish", "ps": "Pushto",
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

# Languages the detector knows but nobody speaks in daily life any more (a dead or
# purely liturgical or invented language), taken off the list on purpose. The detector
# is restricted to the list (see _detector), or it would still answer them: Latin-looking
# text would be tagged "la", a code that no filter offers.
REMOVED_LANGUAGES = {
    "grc": "Ancient Greek",
    "hbo": "Ancient Hebrew",
    "la": "Latin",
    "vo": "Volapük",
}

# py3langid's model also emits "zxx" ("no linguistic content") for text that
# isn't really any language (pure numbers/emoji/URLs) - not a real language,
# so treat it the same as "couldn't determine a language" rather than listing
# it as a filterable option.
NO_LINGUISTIC_CONTENT = "zxx"

# Below this many characters, detection on short post titles is unreliable
# enough (confirmed empirically - e.g. "cat"/"lol" get misclassified into
# unrelated languages with no useful confidence signal from the raw score) that
# tagging anything isn't worth the false-confidence risk. Posts shorter than
# this stay untagged (language_code = NULL) rather than mistagged.
MIN_DETECTION_LENGTH = 3


_restricted = False


def _detector():
    """The detector, answering only a language on the list (or "no linguistic
    content"). Done once, the first time it is used, because loading the model is slow."""
    global _restricted
    if not _restricted:
        langid.set_languages(sorted(LANGUAGE_NAMES) + [NO_LINGUISTIC_CONTENT])
        _restricted = True
    return langid


def known_codes(codes):
    """The codes that are still on the list, in order (a saved choice can hold a language
    that has since been removed)."""
    return [c for c in codes or [] if c in LANGUAGE_NAMES]


def detect_language(title, body=""):
    """Best-effort language code for a post's title+body text. Returns None if
    there's not enough text to make a reasonable guess, or if the detector
    can't find any real linguistic content."""

    text = f"{title or ''} {body or ''}".strip()

    if len(text) < MIN_DETECTION_LENGTH:
        return None

    code, _score = _detector().classify(text)

    if code == NO_LINGUISTIC_CONTENT:
        return None

    return code
