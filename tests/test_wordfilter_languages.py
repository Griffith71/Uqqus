"""The word filter's words for other languages (ruqqus/helpers/wordfilter_intl.json, adapted from LDNOOBW, CC BY
4.0): the file's shape, that each listed word is caught, and that the site's own text in every language (the
translation catalogues) and the language-detection vectors stay clean, apart from the few strings listed below that
really do swear.

Every msgstr is checked with WORDFILTER_FULL=1 (about 20 seconds); by default a fixed sample of each catalogue."""
import hashlib
import json
import os
import re
from pathlib import Path

import pytest

from ruqqus.helpers import languages, wordfilter_seed as seed
from ruqqus.helpers.wordfilter import WordFilter, visible_text

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "ruqqus" / "helpers" / "wordfilter_intl.json"
RAW = json.loads(DATA.read_text(encoding="utf-8"))
FULL = WordFilter(seed.BUILTIN_ENTRIES, seed.BUILTIN_ALLOW_PHRASES)
ENGLISH = WordFilter(seed.ENTRIES, seed.ALLOW_PHRASES)
LISTED = [e for e in RAW["entries"] if e["severity"] > 0]
ALLOWED = [e for e in RAW["entries"] if e["severity"] == 0]
SAMPLE_PER_CATALOGUE = 600


# ------------------------------------------------------------------ the file
def test_the_file_is_credited_and_shaped():
    assert RAW["source"].startswith("List of Dirty, Naughty, Obscene, and Otherwise Bad Words")
    assert RAW["url"] == "https://github.com/LDNOOBW/List-of-Dirty-Naughty-Obscene-and-Otherwise-Bad-Words"
    assert RAW["license"] == "CC BY 4.0"
    assert RAW["license_url"] == "https://creativecommons.org/licenses/by/4.0/"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", RAW["fetched"])
    assert RAW["adapted"].strip()
    for entry in RAW["entries"]:
        assert set(entry) == {"word", "severity", "mode", "variants", "suffixes", "language"}, entry
        assert isinstance(entry["word"], str) and entry["word"].strip() == entry["word"] != ""
        assert entry["severity"] in (0, 1, 2)
        assert entry["mode"] in ("word", "anywhere")
        assert isinstance(entry["variants"], list) and all(isinstance(v, str) and v for v in entry["variants"])
        # always a list: None would give these words the English endings
        assert isinstance(entry["suffixes"], list) and all(isinstance(s, str) and s for s in entry["suffixes"])
        assert WordFilter.clean_word(entry["word"]), entry
        # what the table can hold (word_filter_entries: word 64, variants and suffixes 512)
        assert len(entry["word"]) <= 64
        assert len(",".join(entry["variants"])) <= 512 and len(",".join(entry["suffixes"])) <= 512
    for phrase in RAW["allow_phrases"]:
        assert set(phrase) == {"phrase", "language"}
        assert len(phrase["phrase"].split()) > 1 and len(phrase["phrase"]) <= 64


def test_every_entry_has_a_language_the_site_knows():
    for item in RAW["entries"] + RAW["allow_phrases"]:
        assert item["language"] in languages.LANGUAGE_NAMES, item


def test_sorted_by_language_then_word_and_no_word_twice():
    keys = [(e["language"], e["word"]) for e in RAW["entries"]]
    assert keys == sorted(keys)
    words = [e["word"] for e in RAW["entries"]] + [p["phrase"] for p in RAW["allow_phrases"]]
    assert len(words) == len(set(words))


def test_the_loader_keeps_every_entry():
    source, entries, phrases = seed.load_intl()
    assert len(entries) == len(RAW["entries"]) and len(phrases) == len(RAW["allow_phrases"])
    assert source["license"] == "CC BY 4.0"
    assert all(e["suffixes"] is not None for e in entries)


# ------------------------------------------------------------------ the words are caught
def test_every_imported_language_has_words_that_are_caught():
    caught = {}
    for entry in LISTED:
        if FULL.severity(f"well {entry['word']} then") >= entry["severity"]:
            caught.setdefault(entry["language"], 0)
            caught[entry["language"]] += 1
    assert set(caught) == {e["language"] for e in LISTED}


@pytest.mark.parametrize("entry", LISTED, ids=[f"{e['language']}-{i}" for i, e in enumerate(LISTED)])
def test_each_listed_word_and_spelling_is_caught(entry):
    for word in [entry["word"]] + entry["variants"]:
        assert FULL.severity(f"well {word} then") >= entry["severity"], word
        assert FULL.severity(word.upper()) >= entry["severity"], word


@pytest.mark.parametrize("entry", ALLOWED, ids=[f"{e['language']}-{i}" for i, e in enumerate(ALLOWED)])
def test_each_allowed_word_is_clean(entry):
    for word in [entry["word"]] + entry["variants"]:
        assert FULL.explain(f"well {word} then") == [], word


def test_each_allowed_phrase_is_clean():
    for phrase in RAW["allow_phrases"]:
        assert FULL.explain(phrase["phrase"]) == [], phrase


# words that are innocent in one language and listed in another, settled by an allowed word or phrase
@pytest.mark.parametrize("text", [
    "Новий рік ми зустріли вдома",                 # uk "year": the Latin look-alike of Danish pik
    "Ο κύκλος των φίλων μου",                     # el: κύκλ- is not Swedish kuk
    "Спасибо, что сообщили об этом",               # ru: not "coon" read across б and щ
    "Tunahitaji kuunda kikundi kipya",             # sw "to create", not Czech kunda
    "Chuja kwa",                                   # sw "filter by"
    "Cada pocs minuts passa un autobús",           # ca "few", not Hungarian pöcs
    "A kút mélyén víz van",                        # hu "well", not Dutch kut
    "A virág szára hosszú, a szárt levágta",       # hu "stalk", not szar
    "Fág an bealach!",                             # ga "clear the way": the allowed phrase, not the English slur
    "قائد جنده شجاع",                              # ar "his soldiers" (the Persian word was left out of the list)
    "كنافخ الكير",                                 # ar "like the blower of the bellows" (a hadith), not Persian
    "ينفخ بالكير",
    "Han er sikker på det",                        # da "sure", not Turkish siker
    "Nagy siker volt a koncert",                   # hu "success"
    "Rovdjuret jagade sitt rov",                   # sv "prey", not røv
    "The ROV dived to the wreck",
    "Kurva ini menunjukkan pertumbuhan",           # id "curve"
    "Ishujt Kuk ndodhen në Paqësor",               # sq: the Cook Islands
    "Det er slutt for i dag",                      # no "end"
    "Opkaldet er slut.",                           # da
    "Mjölken är slut",                             # sv
    "La tapette à mouches est sur la table",
    "他妈妈的生日是明天。",
    "体操比赛很精彩。",
    "ホモ・サピエンスの歴史",
    "아저씨가 날씨가 좋다고 말했어요",
    "ผงกระหรี่ใช้ทำแกง",
    "این خط را با یک لکیر صاف بکشید",
])
def test_innocent_look_alikes_are_clean(text):
    assert FULL.explain(text) == []


@pytest.mark.parametrize("text, word", [
    ("you fag", "fag"), ("fäg", "fag"),            # an accent never excuses the extreme word its skeleton spells
    ("fág", "fag"), ("FÁG!", "fag"),
    ("dat is kut", "kut"), ("szar", "szar"), ("szart", "szar"),
    ("pöcsök", "pocsok"), ("røven", "roven"), ("Ty kurva", "kurva"), ("do chuja", "chuj"),
])
def test_the_listed_word_beside_an_allowed_look_alike_is_still_caught(text, word):
    assert word in [base for _, base, _ in FULL.explain(text)]


def test_english_dodges_with_foreign_look_alikes_still_count():
    # a Latin word written partly or wholly in Cyrillic look-alikes is still read as Latin
    assert FULL.severity("сооп") == 2 and FULL.severity("fuсk") >= 1
    # but a letter that is no look-alike makes the run a word of its own script
    assert FULL.explain("κύκλος") == [] and FULL.explain("**бот") == []


# ------------------------------------------------------------------ the site's own text stays clean
# Strings in the catalogues that really swear (machine translations of "No bullshit", "Aw shucks" and "It sucks to
# be you"), and the English starter list's words that are everyday words in another language (Scandinavian
# slut / slutt "end", Estonian paki "parcel's"): the other-language list can't settle those without unlisting the
# English word. A new pair here needs a reason.
KNOWN = {
    ("*", "bullshit"),
    ("ca", "merda"), ("es", "mierda"), ("gl", "mierda"), ("fi", "paskiainen"), ("it", "cazzo"), ("it", "stronzo"),
    ("nl", "klote"), ("ru", "дерьмо"), ("sq", "fuck"), ("sq", "idiot"),
    ("da", "slut"), ("no", "slut"), ("sv", "slut"), ("et", "paki"),
}
_EMPHASIS = re.compile(r"\*\*|__|(?<![\w*])\*(?=\S)|(?<=\S)\*(?![\w*])|`")


def _plain(text):
    """What a reader sees: tags and markdown emphasis off (posts are rated from their rendered HTML)."""
    if "<" in text:
        text = visible_text(text)
    return _EMPHASIS.sub("", text)


def _strings(path):
    from babel.messages.pofile import read_po
    with open(path, "rb") as fh:
        catalogue = read_po(fh)
    out = []
    for message in catalogue:
        if not message.id:
            continue
        strings = message.string if isinstance(message.string, (list, tuple)) else [message.string]
        out.extend(s for s in strings if s)
    return out


def _sample(strings):
    if os.environ.get("WORDFILTER_FULL"):
        return strings
    keyed = sorted(strings, key=lambda s: hashlib.sha1(s.encode("utf-8")).hexdigest())
    return keyed[:SAMPLE_PER_CATALOGUE]


CATALOGUES = sorted((ROOT / "ruqqus" / "translations").glob("*/LC_MESSAGES/messages.po"))


def test_there_are_catalogues_to_check():
    assert len(CATALOGUES) >= 40


@pytest.mark.parametrize("path", CATALOGUES, ids=[p.parts[-3] for p in CATALOGUES])
def test_the_site_text_in_every_language_stays_clean(path):
    lang = path.parts[-3]
    strings = _sample(_strings(path))
    assert len(strings) >= min(300, len(_strings(path)))
    problems = []
    for text in strings:
        for token, base, _ in FULL.explain(_plain(text)):
            if (lang, base) not in KNOWN and ("*", base) not in KNOWN:
                problems.append((token, base, text[:120]))
    assert problems == []


def test_the_language_vectors_stay_clean():
    cases = json.loads((ROOT / "tests" / "data" / "language_vectors.json").read_text(encoding="utf-8"))["cases"]
    assert cases
    for case in cases:
        assert FULL.explain(case["text"]) == [], case["text"]


def test_the_english_list_alone_is_unchanged_by_the_file():
    # the other languages' words live only in the built-in list; the English starter list is what it was
    assert all(e.get("language") is None for e in seed.ENTRIES)
    assert ENGLISH.version != FULL.version
