"""The members' page that lists the word filter's words (routes/filtered_words_pages.py, templates/filtered_words.html):
how the live list (word_filter_store.listed_entries) is grouped for reading. Pure: no database, no Flask.

The page groups by language: the entries with no language (the site's own list, written in English) first, under
English, then every other language by its name in the reader's language. Inside a language, three sections: what the
Standard and Child filters hide (severity 2), what only the Child filter hides (severity 1) and what is never counted
(severity 0: allowed words, and the allowed phrases)."""
import unicodedata

from ruqqus.helpers import languages, wordfilter

ENGLISH = "en"                 # the language of entries with none: the site's own list
SECTIONS = ("extreme", "profanity", "allowed")
_SECTION_OF = {2: "extreme", 1: "profanity", 0: "allowed"}


def child_filter(v):
    """The viewer browses on the Child filter (users.filter_level 2; NULL is Standard), or at that level because the law asks for it
    where they are and their age isn't confirmed (visibility.viewer_level): the list is not shown to them."""
    from ruqqus.helpers.visibility import CHILD, viewer_level
    return viewer_level(v) >= CHILD


def anywhere(entry):
    """Matched inside other words, not only as a whole word: an "anywhere" entry, or a word in a script written without
    spaces (Chinese, Japanese, Korean, Thai...), which is always found inside the text (helpers/wordfilter.py)."""
    if entry.get("mode") == "anywhere":
        return True
    return wordfilter.word_script(entry.get("word") or "") in wordfilter.SPACELESS_SCRIPTS


def _sort_key(name):
    """A language name, compared without case or accents (Ä beside A), so the order reads right in most languages."""
    folded = unicodedata.normalize("NFKD", name or "").casefold()
    return "".join(c for c in folded if not unicodedata.combining(c))


def row_of(code):
    """The language a code is shown under: the site's row for it (Malay is the Indonesian / Malay row, Nynorsk the
    Norwegian one), so one name is never two sections; a language the site doesn't offer keeps its own code."""
    key = languages.key_of(code)
    return code if key in (None, languages.UNKNOWN) else key


def groups(listed, name_of):
    """The languages of `listed` (word_filter_store.listed_entries), English first, then by `name_of(code)` (the
    language's name in the reader's language). Each:

        {"code", "name", "anchor", "count",
         "sections": [{"key": "extreme" | "profanity" | "allowed", "items": [...]}, ...]}   (empty sections left out)

    and each item {"word", "lang", "variants", "suffixes" (None: the usual English endings), "anywhere", "phrase"}:
    a group is a row of the site's languages (row_of), so an item keeps its own language code for its `lang`."""
    by_code = {}

    def language(code):
        code = row_of(code or ENGLISH)
        if code not in by_code:
            by_code[code] = {"code": code, "name": name_of(code) or code, "anchor": "lang-" + code,
                             "sections": {key: [] for key in SECTIONS}}
        return by_code[code]

    for entry in listed.get("entries") or []:
        section = _SECTION_OF.get(int(entry.get("severity") or 0), "allowed")
        language(entry.get("language"))["sections"][section].append({
            "word": entry["word"],
            "lang": entry.get("language") or ENGLISH,
            "variants": [w for w in entry.get("variants") or [] if w and w != entry["word"]],
            "suffixes": None if entry.get("suffixes") is None else [s for s in entry["suffixes"] if s],
            "anywhere": anywhere(entry),
            "phrase": False,
        })
    for phrase in listed.get("allow_phrases") or []:
        language(phrase.get("language"))["sections"]["allowed"].append({
            "word": phrase["phrase"], "lang": phrase.get("language") or ENGLISH, "variants": [], "suffixes": [], "anywhere": False, "phrase": True,
        })

    out = []
    for item in by_code.values():
        for words in item["sections"].values():
            words.sort(key=lambda w: (_sort_key(w["word"]), w["word"]))
        sections = [{"key": key, "items": item["sections"][key]} for key in SECTIONS if item["sections"][key]]
        out.append(dict(item, sections=sections, count=sum(len(s["items"]) for s in sections)))
    out.sort(key=lambda g: (g["code"] != ENGLISH, _sort_key(g["name"]), g["code"]))
    return out


def has_other_languages(grouped):
    """Any language but the site's own list: the credit for the other languages' list is shown only then."""
    return any(g["code"] != ENGLISH for g in grouped)
