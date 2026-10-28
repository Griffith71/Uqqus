"""Language detection against real texts (helpers/languages.py decide, the owner's 2026-10-24 round): py3langid ranks,
LibreTranslate is a guarded second opinion (its /detect answers recorded in tests/data/language_vectors.json, so this
runs without it). The promise: never a wrong language; unknown where the two disagree."""
import json
from pathlib import Path

import pytest

from ruqqus.helpers import languages as lg

DATA = json.loads((Path(__file__).resolve().parent / "data" / "language_vectors.json").read_text(encoding="utf-8"))
LOADED = frozenset(DATA["loaded"])


def answer(case):
    text = lg.detection_text(case["text"], "")
    libre = (lambda: tuple(case["libre"])) if case["libre"] else (lambda: None)
    return lg.decide(lg._detector().rank(text), lg.letters_in(text), libre, LOADED)


@pytest.mark.parametrize("case", DATA["cases"], ids=[c["text"][:30] for c in DATA["cases"]])
def test_each_text_reads_as_recorded(case):
    assert answer(case) == case["expect"]


def test_never_a_wrong_language():
    wrong = [(c["text"], c["truth"], answer(c)) for c in DATA["cases"]
             if answer(c) is not None and answer(c) != c["truth"]]
    assert wrong == []


def test_most_texts_are_named():
    named = sum(answer(c) == c["truth"] for c in DATA["cases"] if c["truth"])
    assert named / sum(1 for c in DATA["cases"] if c["truth"]) >= 0.9


def test_the_counterexamples_stay_unknown():
    # LibreTranslate confidently names a neighbour for languages it hasn't loaded; group sums alone would call
    # "lmao same" Serbian
    by_text = {c["text"]: c for c in DATA["cases"]}
    for text in ("lmao same", "Hej allihopa", "Добро утро, как си?", "Ciao", "lol", "ok"):
        assert answer(by_text[text]) is None, text


def test_without_a_second_opinion_short_text_is_only_named_when_py3langid_is_sure():
    assert lg.decide(lg._detector().rank("Guten Morgen"), lg.letters_in("Guten Morgen"), None, LOADED) is None
    text = lg.detection_text("Das ist ein schöner Tag", "")
    assert lg.decide(lg._detector().rank(text), lg.letters_in(text), None, LOADED) == "de"


def test_the_formatting_extras_and_sounds_say_nothing():
    # the review of 2026-10-24: "center", "button" and "audio" were read as words
    assert lg.detection_text("", "::: center\nHola a todos, buenos días\n:::") == "Hola a todos, buenos días"
    assert lg.detection_text("", "[Leer más](https://x.com){.button} Bonjour") == "Leer más Bonjour"
    assert lg.detection_text("", "[audio](/media/abc/def.mp3) Guten Morgen") == "Guten Morgen"
    assert lg.detection_text("", "![](/media/abc/def.png) Ciao") == "Ciao"
    assert lg.detection_text("", "the ::: center of town") == "the ::: center of town"      # only a line of its own


@pytest.mark.parametrize("hostile", ["[" * 25000, "![" * 12500, "`" * 25000, "```" * 8000, "[a](" * 6000,
                                      "&" * 25000, "{" * 25000, "http://" + "a" * 25000])
def test_a_hostile_body_costs_no_more_than_an_ordinary_one(hostile):
    import time
    started = time.perf_counter()
    lg.detection_text("title", hostile)
    assert time.perf_counter() - started < 0.25          # "[" 25,000 times took 1.5 s before the patterns were bounded


def test_an_outage_keeps_the_code_the_text_has():
    # LibreTranslate couldn't be asked (down, slow, paused): not the same as unsure, so an edit or the backfill keeps
    # a code the text already has, while py3langid still gives its row some weight
    text = lg.detection_text("Guten Morgen", "")
    ranked, letters = lg._detector().rank(text), lg.letters_in(text)
    assert lg.decide(ranked, letters, lambda: lg.UNASKED, LOADED, keep="de") == "de"
    assert lg.decide(ranked, letters, lambda: lg.UNASKED, LOADED) is None                  # nothing to keep
    assert lg.decide(ranked, letters, lambda: lg.UNASKED, LOADED, keep="ja") is None       # not plausible any more
    assert lg.decide(ranked, letters, lambda: None, LOADED, keep="de") is None             # asked, and unsure
    long_text = lg.detection_text("Das ist ein schöner Tag und wir gehen heute in den Park", "")
    assert lg.decide(lg._detector().rank(long_text), lg.letters_in(long_text), lambda: lg.UNASKED, LOADED,
                     keep="fr") == "de"                  # py3langid sure: LibreTranslate never needed
