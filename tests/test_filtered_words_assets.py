"""The members' page that lists the word filter's words (routes/filtered_words_pages.py, helpers/filtered_words.py,
templates/filtered_words.html): members only, a warning first, nothing on the Child filter, never cached or indexed, every
word escaped in its own language, linked from Settings > Content and the admin word filter, styled by a shared partial."""
import re
from pathlib import Path

from ruqqus.helpers import filtered_words as rules

ROOT = Path(__file__).resolve().parent.parent


def read(*parts):
    return (ROOT.joinpath(*parts)).read_text(encoding="utf-8")


ROUTE = read("ruqqus", "routes", "filtered_words_pages.py")
PAGE = read("ruqqus", "templates", "filtered_words.html")
STYLE = read("ruqqus", "assets", "style", "_filtered_words.scss")


# --- the route ---------------------------------------------------------------------------------------------------------

def test_the_route_is_registered_under_a_name_no_star_import_replaces():
    assert "from .filtered_words_pages import *" in read("ruqqus", "routes", "__init__.py")
    assert not (ROOT / "ruqqus" / "routes" / "filtered_words.py").exists()
    assert '__all__ = ["filtered_words"]' in ROUTE


def test_visitors_sign_in_first_and_the_page_is_never_cached_or_indexed():
    head = ROUTE[ROUTE.index('@app.route(PATH'):ROUTE.index("def filtered_words(v)")]
    assert "@auth_required" in head                   # a visitor is a 401, which errors.py turns into the sign-in
    assert head.index("@_private_page") < head.index("@auth_required")   # assigned after the wrapper's own "private"
    assert 'NO_STORE = "private, no-store"' in ROUTE
    assert 'response.headers["Cache-Control"] = NO_STORE' in ROUTE
    assert 'response.headers["X-Robots-Tag"] = "noindex, nofollow"' in ROUTE
    assert '<meta name="robots" content="noindex, nofollow">' in PAGE


def test_the_child_filter_comes_first_then_the_warning_then_the_live_list():
    body = ROUTE[ROUTE.index("def filtered_words(v)"):]
    assert body.index("rules.child_filter(v)") < body.index('request.args.get("show") != "1"') \
        < body.index("word_filter_store.listed_entries(g.db)")
    assert 'state="child"' in body and 'state="warning"' in body and 'state="list"' in body
    assert "not_quarantined" not in ROUTE                 # a held member may read it


def test_child_filter_rule():
    class V:
        def __init__(self, level):
            self.filter_level = level
    assert rules.child_filter(V(2))
    assert not rules.child_filter(V(1)) and not rules.child_filter(V(0)) and not rules.child_filter(V(None))


def test_go_back_only_leads_to_this_site():
    assert "same_site(" in ROUTE and "parts.netloc != request.host" in ROUTE


# --- grouping ----------------------------------------------------------------------------------------------------------

LISTED = {
    "entries": [
        {"word": "beta", "severity": 2, "mode": "word", "variants": ["b3ta"], "suffixes": ["s"], "language": None},
        {"word": "alpha", "severity": 1, "mode": "anywhere", "variants": [], "suffixes": None, "language": None},
        {"word": "gamma", "severity": 0, "mode": "word", "variants": [], "suffixes": None, "language": None},
        {"word": "пёс", "severity": 1, "mode": "word", "variants": [], "suffixes": [], "language": "ru"},
        {"word": "狗狗", "severity": 1, "mode": "word", "variants": [], "suffixes": [], "language": "zh"},
        {"word": "Ärger", "severity": 1, "mode": "word", "variants": [], "suffixes": [], "language": "de"},
    ],
    "allow_phrases": [{"phrase": "beta blocker", "language": None}],
}
NAMES = {"en": "English", "ru": "Russian", "zh": "Chinese", "de": "German"}


def test_english_first_then_languages_by_their_name():
    grouped = rules.groups(LISTED, NAMES.get)
    assert [g["code"] for g in grouped] == ["en", "zh", "de", "ru"]
    assert [g["anchor"] for g in grouped] == ["lang-en", "lang-zh", "lang-de", "lang-ru"]
    english = grouped[0]
    assert [s["key"] for s in english["sections"]] == ["extreme", "profanity", "allowed"]
    assert english["count"] == 4
    allowed = english["sections"][2]["items"]
    assert [(i["word"], i["phrase"]) for i in allowed] == [("beta blocker", True), ("gamma", False)]
    assert english["sections"][0]["items"][0]["variants"] == ["b3ta"]
    assert english["sections"][1]["items"][0]["suffixes"] is None


def test_names_in_the_readers_language_sort_without_accents():
    grouped = rules.groups(LISTED, {"en": "Anglais", "ru": "Russe", "zh": "Chinois", "de": "Ëllemand"}.get)
    assert [g["code"] for g in grouped] == ["en", "zh", "de", "ru"]   # English always first; Ë sorts with E


def test_spaceless_scripts_and_anywhere_entries_say_they_match_inside_words():
    grouped = {g["code"]: g for g in rules.groups(LISTED, NAMES.get)}
    assert grouped["zh"]["sections"][0]["items"][0]["anywhere"] is True
    assert grouped["ru"]["sections"][0]["items"][0]["anywhere"] is False
    assert grouped["en"]["sections"][1]["items"][0]["anywhere"] is True       # alpha: mode anywhere
    assert rules.has_other_languages(list(grouped.values()))
    assert not rules.has_other_languages(rules.groups({"entries": LISTED["entries"][:3]}, NAMES.get))


def test_a_row_of_several_languages_is_one_section():
    # Malay is shown with Indonesian (the site's row "Indonesian / Malay"): one heading, one jump link; each word keeps
    # its own language for its lang attribute. A language the site doesn't offer keeps its code.
    listed = {"entries": [{"word": "kata", "severity": 1, "language": "id"},
                          {"word": "kurva", "severity": 1, "language": "ms"},
                          {"word": "awal", "severity": 1, "language": "kab"}], "allow_phrases": []}
    grouped = {g["code"]: g for g in rules.groups(listed, lambda code: code)}
    assert set(grouped) == {"id", "kab"} and grouped["id"]["count"] == 2 and grouped["id"]["anchor"] == "lang-id"
    assert {i["lang"] for i in grouped["id"]["sections"][0]["items"]} == {"id", "ms"}


def test_an_empty_list_has_no_groups():
    assert rules.groups({"entries": [], "allow_phrases": []}, NAMES.get) == []


# --- the template ------------------------------------------------------------------------------------------------------

def test_words_are_escaped_and_carry_their_language():
    assert "| safe" not in PAGE and "|safe" not in PAGE and "Markup(" not in ROUTE
    assert '<span class="fw-term" lang="{{ item.lang }}" dir="auto">{{ item.word }}</span>' in PAGE
    assert '<span lang="{{ item.lang }}" dir="auto">{{ item.variants | join(sep) }}</span>' in PAGE
    # the list comma is the reader's language's (、 in Japanese, ، in Arabic), never a hard-coded ", "
    assert "{% set sep = list_separator() %}" in PAGE and 'join(", ")' not in PAGE and "{{ sep }}" in PAGE
    # section items read with brackets: `.items` on a dict is its method
    assert 'section["items"]' in PAGE and "section.items" not in PAGE


def test_the_warning_shows_no_words_and_needs_no_script():
    start = PAGE.index('{% elif state == "warning" %}')
    warning = PAGE[start:PAGE.index("{% else %}", start)]
    assert "This page lists offensive words" in warning
    assert 'href="?show=1"' in warning and 'href="{{ back }}"' in warning
    assert "item." not in warning and "<script" not in PAGE


def test_no_ads_no_right_sidebar():
    assert "ad_slot" not in PAGE
    assert "{% block sidebar %}{% endblock %}" in PAGE
    assert "wide-main" in PAGE


def test_the_credit_names_the_source_and_licence_from_the_data_file():
    assert "source.url" in PAGE and "source.license_url" in PAGE and "source.license" in PAGE
    assert '.startswith("https://")' in PAGE


def test_reader_text_is_marked():
    for text in ("Filtered words", "How words are matched", "Change your word filter", "Hidden on the Standard and Child filters",
                 "Hidden on the Child filter", "Never counted", "Show the list", "Go back",
                 "The list isn't shown on the Child filter"):
        assert re.search(r'_\(["\']' + re.escape(text), PAGE), text
    # no markup inside a sentence a machine translates: the link is a string of its own
    assert "<a href=\"%(url)s\"" not in PAGE and "&gt;" not in PAGE


# --- links and styles --------------------------------------------------------------------------------------------------

def test_settings_and_the_admin_page_link_to_it():
    settings = read("ruqqus", "templates", "settings_filters.html")
    assert 'href="/filtered_words"' in settings and "See the filtered words" in settings
    assert "/filtered_words" in read("ruqqus", "templates", "admin", "word_filter.html")


def test_both_sheets_import_the_partial_which_uses_tokens_only():
    for sheet in ("main.scss", "main_dark.scss"):
        assert '@import "{ruqquspath}/assets/style/_filtered_words";' in read("ruqqus", "assets", "style", sheet)
    assert "{boardcolor}" not in STYLE and "{ruqquspath}" not in STYLE
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", STYLE)
    assert "!important" not in STYLE
    # type in rem or tokens, never px
    for value in re.findall(r"font-size:\s*([^;]+);", STYLE):
        assert "px" not in value or "px2rem" in value, value
    # an ending never splits at its hyphen, and long words wrap on a phone
    assert "white-space: nowrap" in STYLE and "overflow-wrap: anywhere" in STYLE
