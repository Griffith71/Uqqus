"""Word filter engine (ruqqus/helpers/wordfilter.py) against its starter list.

The "must match" cases are generated from the list itself: every listed word
is run through each evasion technique the filter claims to handle. That keeps
offensive words out of this file and means a new entry is covered for free.
"""
import importlib.util
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "ruqqus" / "helpers" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


wordfilter = _load("wordfilter")
seed = _load("wordfilter_seed")

FILTER = wordfilter.WordFilter(seed.ENTRIES, seed.ALLOW_PHRASES)
LISTED = [e for e in seed.ENTRIES if e["severity"] > 0]
IDS = [f'{e["severity"]}-{i}' for i, e in enumerate(LISTED)]

DIGITS = {"a": "4", "e": "3", "i": "1", "o": "0", "s": "5", "t": "7", "b": "8", "g": "9"}
SYMBOLS = {"a": "@", "s": "$", "i": "!"}
CYRILLIC = {"a": "а", "c": "с", "e": "е", "o": "о", "p": "р", "x": "х", "y": "у", "i": "і"}
VOWELS = "aeiou"


def _swap_inner(word, table):
    # keep the first letter real: a token with no letters at all is a number
    return word[0] + "".join(table.get(c, c) for c in word[1:])


def _fullwidth(word):
    return "".join(chr(ord(c) - ord("a") + 0xFF41) for c in word)


def _wildcard(word):
    for i in range(1, len(word) - 1):
        if word[i] in VOWELS:
            return word[:i] + "*" + word[i + 1:]
    return word


EVASIONS = {
    "plain": lambda w: w,
    "upper": lambda w: w.upper(),
    "mixed case": lambda w: "".join(c.upper() if i % 2 else c for i, c in enumerate(w)),
    "in a sentence": lambda w: f"well {w} then",
    "punctuation around": lambda w: f'("{w}!!")',
    "repeated letters": lambda w: w[:1] + w[1] * 4 + w[2:],
    "digits for letters": lambda w: _swap_inner(w, DIGITS),
    "symbols for letters": lambda w: _swap_inner(w, SYMBOLS),
    "dots between letters": lambda w: ".".join(w),
    "dashes between letters": lambda w: "-".join(w),
    "spelled out": lambda w: " ".join(w),
    "accented": lambda w: w[0] + "́" + w[1:],
    "zero-width joiners": lambda w: "​".join(w),
    "look-alike alphabet": lambda w: "".join(CYRILLIC.get(c, c) for c in w),
    "fullwidth": _fullwidth,
    "asterisk for a letter": _wildcard,
}


@pytest.mark.parametrize("entry", LISTED, ids=IDS)
@pytest.mark.parametrize("technique", list(EVASIONS))
def test_listed_words_are_caught_through_every_evasion(entry, technique):
    text = EVASIONS[technique](entry["word"])
    assert FILTER.severity(text) >= entry["severity"], f"{technique}: {text!r}"


@pytest.mark.parametrize("entry", LISTED, ids=IDS)
def test_plain_word_has_exactly_its_listed_severity(entry):
    """A profanity entry must not be escalated to extreme by a similar word."""
    assert FILTER.severity(entry["word"]) == entry["severity"]


@pytest.mark.parametrize("entry", LISTED, ids=IDS)
def test_word_split_by_formatting_tags_is_caught(entry):
    w = entry["word"]
    html = f"<p>look: {w[0]}<b>{w[1:-1]}</b><em>{w[-1]}</em></p>"
    assert FILTER.severity_html(html) >= entry["severity"]


INNOCENT = [
    # contain a listed word but are not one (the "Scunthorpe problem")
    "classic", "assassin", "Dickens", "cockpit", "analysis", "Essex", "Scunthorpe", "pass", "class", "grass",
    "bass", "assess", "assessment", "cocktail", "Hancock", "peacock", "spice", "spices", "raccoon", "document",
    "button", "retardant", "country", "count", "damnation", "title", "titles", "kite", "bitcoin", "passage",
    "shiitake", "Fukushima", "fuchsia", "niggardly", "snigger", "sniggering", "Niger", "Nigeria",
    # near misses that are real words
    "duck", "shirt", "as", "but", "hello", "the", "sheet", "beach", "fork", "pitch", "can't",
    # punctuation, numbers and abbreviations
    "e.g.", "U.S.A.", "wow!!", "$5", "100%", "it's", "sh", "f", "****", "a***", "a b c", "I am ok", "push it",
    # allowed phrases
    "a Maine Coon cat", "a chink in the armour", "Moby Dick",
    "",
]


@pytest.mark.parametrize("text", INNOCENT)
def test_innocent_text_is_clean(text):
    assert FILTER.explain(text) == []


def test_severity_reports_the_worst_word():
    mild = next(e["word"] for e in LISTED if e["severity"] == 1)
    extreme = next(e["word"] for e in LISTED if e["severity"] == 2)
    assert FILTER.severity(f"some {mild} here") == 1
    assert FILTER.severity(f"some {mild} and {extreme} here") == 2
    assert FILTER.severity(None) == 0


def test_html_ignores_mentions_urls_and_attributes():
    w = next(e["word"] for e in LISTED if e["severity"] == 1)
    clean = (f'<p>hi <a href="/@{w}" data-original-name="{w}"><img class="profile-pic-20" src="/x/{w}.png">@{w}</a>, '
             f'see <a href="https://example.com/{w}">https://example.com/{w}</a> '
             f'and <a href="/+{w}">+{w}</a> <span title=":{w}:"><img class="emoji" src="/e/{w}"></span></p>')
    assert FILTER.severity_html(clean) == 0
    # ordinary link text, code and entities ARE prose
    assert FILTER.severity_html(f'<p><a href="https://example.com">{w}</a></p>') == 1
    assert FILTER.severity_html(f"<pre><code>{w}</code></pre>") == 1
    assert FILTER.severity_html(f"<p>{w[0]}&#{ord(w[1])};{w[2:]}</p>") == 1
    assert FILTER.severity_html("") == 0 and FILTER.severity_html(None) == 0


def test_words_in_separate_blocks_are_not_joined():
    w = next(e["word"] for e in LISTED if e["severity"] == 1)
    assert FILTER.severity_html(f"<p>{w[:2]}</p><p>{w[2:]}</p>") == 0


def test_version_changes_with_the_list():
    same = wordfilter.WordFilter(list(reversed(seed.ENTRIES)), seed.ALLOW_PHRASES)
    assert same.version == FILTER.version
    changed = wordfilter.WordFilter(seed.ENTRIES + [{"word": "zzzexample", "severity": 1}], seed.ALLOW_PHRASES)
    assert changed.version != FILTER.version
    assert changed.severity("zzzexample") == 1 and FILTER.severity("zzzexample") == 0


def test_allowed_entry_overrides_a_listed_word():
    f = wordfilter.WordFilter([{"word": "grape", "severity": 2, "mode": "anywhere"}, {"word": "grapefruit", "severity": 0}])
    assert f.severity("grape") == 2
    assert f.severity("grapes") == 2
    assert f.severity("grapefruit") == 0


@pytest.mark.parametrize("level, severity, sensitive, hidden", [
    (0, 2, True, False),    # Off shows everything
    (1, 0, False, False),
    (1, 1, False, False),   # Standard leaves profanity
    (1, 2, False, True),    # ...and hides extreme terms
    (1, 0, True, False),    # ...but not sensitive content
    (2, 0, False, False),
    (2, 1, False, True),    # Child hides profanity
    (2, 2, False, True),
    (2, 0, True, True),     # ...and anything marked sensitive
    (1, None, False, False),
])
def test_visibility_rule(level, severity, sensitive, hidden):
    assert wordfilter.is_hidden(level, severity, sensitive) is hidden


def test_survives_arbitrary_input_and_stays_fast():
    for nasty in ["\x00😀‮", "<<<>>>&&&", "a" * 100_000, " ".join("x" * 5000), "<p" * 2000, "*" * 5000]:
        FILTER.severity(nasty)
        FILTER.severity_html(nasty)

    comment = "<p>" + " ".join(["This is an ordinary comment about nothing in particular, with a link"] * 10) + "</p>"
    start = time.perf_counter()
    for _ in range(300):
        assert FILTER.severity_html(comment) == 0
    assert time.perf_counter() - start < 5, "classifying 300 comments should take well under 5 seconds"
