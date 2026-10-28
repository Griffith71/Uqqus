"""The language list (ruqqus/helpers/languages.py): languages nobody speaks in daily life
are not on it, and the detector can only answer a language that is."""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SOURCE = (ROOT / "ruqqus" / "helpers" / "languages.py").read_text(encoding="utf-8")

REMOVED = {"grc": "Ancient Greek", "hbo": "Ancient Hebrew", "la": "Latin", "vo": "Volapük"}
# the owner, 2026-10-24: Esperanto and the pidgins and creoles
REMOVED_LATER = {"eo": "Esperanto", "ht": "Haitian Creole", "pcm": "Nigerian Pidgin", "gcf": "Guadeloupean Creole French",
                 "gcr": "Guianese Creole French"}


def listed():
    """The codes of LANGUAGE_NAMES, read from the source so no detector is needed."""
    block = SOURCE.split("LANGUAGE_NAMES = {")[1].split("\n}")[0]
    return dict(re.findall(r'"(\w+)": "([^"]+)"', block))


def test_the_four_are_off_the_list_and_the_living_ones_stay():
    names = listed()
    for code, name in {**REMOVED, **REMOVED_LATER}.items():
        assert code not in names and name not in names.values(), code
    # spoken today by communities, even if small
    for code in ("sa", "ga", "cy", "gd", "ext", "lij", "vec", "crh", "dz", "kab", "wa"):
        assert code in names, code
    assert len(names) == 130        # 139 before the four, 135 before Esperanto and the pidgins and creoles
    assert names["el"] == "Greek"


def test_the_removed_languages_are_recorded_and_agree_with_the_migration():
    assert re.search(r"REMOVED_LANGUAGES = \{[^}]*\}", SOURCE, re.S)
    recorded = dict(re.findall(r'"(\w+)": "([^"]+)"', SOURCE.split("REMOVED_LANGUAGES = {")[1].split("}")[0]))
    assert recorded == {**REMOVED, **REMOVED_LATER}
    for name, codes in (("2026-10-08_languages.sql", REMOVED), ("2026-11-27_removed_languages.sql", REMOVED_LATER)):
        migration = (ROOT / "scripts" / "migrations" / name).read_text(encoding="utf-8")
        for code in codes:
            assert f"'{code}'" in migration, code
    later = (ROOT / "scripts" / "migrations" / "2026-11-27_removed_languages.sql").read_text(encoding="utf-8")
    for table in ("UPDATE submissions", "UPDATE comments", "UPDATE curations", "UPDATE users"):
        assert table in later, table


def test_the_detector_is_restricted_to_the_list_before_it_is_used():
    assert "identifier.set_languages(sorted(LANGUAGE_NAMES) + [NO_LINGUISTIC_CONTENT])" in SOURCE
    assert "norm_probs=True" in SOURCE                     # a confidence it can be held to
    assert "_detector().rank(text)" in SOURCE and "langid.classify(" not in SOURCE


def test_what_says_nothing_about_the_language_is_left_out():
    pytest.importorskip("py3langid")
    from ruqqus.helpers import languages as lg

    text = lg.detection_text("**bold** [a link](https://x.com) @alice +pets #news {c:red}red{/c} &amp; `code`", "")
    assert text == "bold a link red"


@pytest.mark.parametrize("title, body, code", [
    ("Morning fog over the reservoir", "", "en"),
    ("Ten-minute miso butter noodles", "Boil noodles. Melt butter, whisk in white miso and a splash of the pasta water.", "en"),
    ("今日はいい天気ですね", "", "ja"),
    ("Das ist ein schöner Tag", "", "de"),
    # too little, or not confident: untagged, never a wrong guess (these were Sesotho and Serbian)
    ("Ten-minute miso butter noodles", "", None),
    ("lmao same", "", None),
    ("omg 😂😂😂", "", None),
    ("ok", "", None),
])
def test_a_guess_is_kept_only_when_the_detector_is_confident(title, body, code):
    pytest.importorskip("py3langid")
    from ruqqus.helpers import languages as lg

    assert lg.detect_language(title, body) == code


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
