"""The language list (ruqqus/helpers/languages.py): languages nobody speaks in daily life
are not on it, and the detector can only answer a language that is."""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SOURCE = (ROOT / "ruqqus" / "helpers" / "languages.py").read_text(encoding="utf-8")

REMOVED = {"grc": "Ancient Greek", "hbo": "Ancient Hebrew", "la": "Latin", "vo": "Volapük"}


def listed():
    """The codes of LANGUAGE_NAMES, read from the source so no detector is needed."""
    block = SOURCE.split("LANGUAGE_NAMES = {")[1].split("\n}")[0]
    return dict(re.findall(r'"(\w+)": "([^"]+)"', block))


def test_the_four_are_off_the_list_and_the_living_ones_stay():
    names = listed()
    for code, name in REMOVED.items():
        assert code not in names and name not in names.values(), code
    # spoken today by communities, even if small
    for code in ("sa", "eo", "ga", "cy", "gd", "ext", "lij", "vec", "crh", "pcm", "dz", "kab", "wa"):
        assert code in names, code
    assert len(names) == 135        # 139 before the four were taken off


def test_the_removed_languages_are_recorded_and_agree_with_the_migration():
    assert re.search(r"REMOVED_LANGUAGES = \{[^}]*\}", SOURCE, re.S)
    recorded = dict(re.findall(r'"(\w+)": "([^"]+)"', SOURCE.split("REMOVED_LANGUAGES = {")[1].split("}")[0]))
    assert recorded == REMOVED
    migration = (ROOT / "scripts" / "migrations" / "2026-10-08_languages.sql").read_text(encoding="utf-8")
    for code in REMOVED:
        assert f"'{code}'" in migration, code


def test_the_detector_is_restricted_to_the_list_before_it_is_used():
    assert "langid.set_languages(sorted(LANGUAGE_NAMES) + [NO_LINGUISTIC_CONTENT])" in SOURCE
    assert "_detector().classify(text)" in SOURCE and "langid.classify(" not in SOURCE


def test_saved_choices_lose_what_has_been_removed():
    pytest.importorskip("py3langid")
    from ruqqus.helpers import languages as lg

    assert lg.known_codes(["fr", "la", "de", "grc", "xx"]) == ["fr", "de"]
    assert lg.known_codes(None) == [] and lg.known_codes([]) == []


def test_every_page_that_reads_saved_choices_goes_through_the_list():
    front = (ROOT / "ruqqus" / "routes" / "front.py").read_text(encoding="utf-8")
    assert "language_codes = known_codes(flask_session.get('langcodes'))" in front


@pytest.mark.parametrize("text", [
    "Gallia est omnis divisa in partes tres, quarum unam incolunt Belgae, aliam Aquitani, tertiam qui ipsorum lingua Celtae.",
    "Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore",
    "ἐν ἀρχῇ ἦν ὁ λόγος, καὶ ὁ λόγος ἦν πρὸς τὸν θεόν, καὶ θεὸς ἦν ὁ λόγος",
    "בראשית ברא אלהים את השמים ואת הארץ",
    "Penedunel bal ela tefon valik e fa mutan",
])
def test_the_detector_never_answers_a_removed_language(text):
    pytest.importorskip("py3langid")
    from ruqqus.helpers import languages as lg

    code = lg.detect_language(text)
    assert code is None or (code in lg.LANGUAGE_NAMES and code not in REMOVED), code


def test_a_modern_language_is_still_found():
    pytest.importorskip("py3langid")
    from ruqqus.helpers import languages as lg

    assert lg.detect_language("The weather has been lovely all week and the garden is looking great") == "en"
    assert lg.detect_language("Ich habe gestern einen langen Spaziergang am Fluss gemacht") == "de"
    assert lg.detect_language("12345 😀 !!!") is None
    assert lg.detect_language("hi") is None
