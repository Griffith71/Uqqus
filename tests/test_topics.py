"""Topics (ruqqus/helpers/topics.py): what `#name` means, the markdown mention, and the search box's two new prefixes.
Pure: the categories are small stand-ins shaped like classes/categories.py."""
import re
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from ruqqus.helpers import topics
from tests.markdown_harness import render

ROOT = Path(__file__).resolve().parent.parent


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


def sub(id_, name, visible=None):
    return NS(id=id_, name=name, _visible=visible)


def cat(id_, name, subs, visible=True, nsfw=False):
    return NS(id=id_, name=name, visible=visible, is_nsfw=nsfw, subcats=subs)


CATEGORIES = [
    cat(1, "Arts", [sub(8, "Film & TV"), sub(10, "Music"), sub(106, "Visual Arts")]),
    cat(5, "Entertainment", [sub(24, "Film & TV"), sub(23, "News")]),
    cat(6, "Gaming", [sub(25, "PC"), sub(26, "Console"), sub(0, "Uncategorized"), sub(108, "Hidden away")]),
    cat(11, "News", [sub(45, "Local"), sub(47, "World")]),
    cat(20, "Secret", [sub(90, "Quiet")], visible=False),
    cat(21, "Adult", [sub(91, "Adult talk")], nsfw=True),
    cat(22, "Mixed", [sub(92, "Shown", True), sub(93, "Hidden by itself", False)]),
]


@pytest.fixture(autouse=True)
def site_categories():
    topics.set_categories(CATEGORIES)
    yield
    topics.set_categories(None)


def test_a_name_is_its_letters_and_digits():
    assert topics.slug("Film & TV") == "filmtv" and topics.slug("Q&A") == "qa" and topics.slug("Counter-Culture") == "counterculture"
    assert topics.slug("#Gaming") == "gaming" and topics.slug(None) == ""


def test_a_category_is_a_topic_of_all_its_subcategories():
    gaming = topics.find("gaming")
    assert gaming.label == "Gaming" and gaming.subcat_ids == (25, 26)         # not 0 (uncategorised) or 108 (unlisted)
    assert gaming.url == "/topic/gaming"
    assert topics.find("#Gaming") == gaming and topics.find("GAMING") == gaming


def test_a_subcategory_is_a_topic_and_the_same_name_in_two_places_is_one_topic():
    assert topics.find("music").subcat_ids == (10,)
    assert topics.find("filmtv").subcat_ids == (8, 24)                         # Arts and Entertainment
    assert topics.find("filmtv").label == "Film & TV"
    # the category named News and the subcategory named News are the same topic
    assert topics.find("news").subcat_ids == (23, 45, 47) and topics.find("news").label == "News"


def test_what_the_visitor_may_not_see_is_not_a_topic():
    for name in ("secret", "quiet", "adult", "adulttalk", "hiddenaway", "uncategorized", "hiddenbyitself"):
        assert topics.find(name) is None, name
    assert topics.find("shown").subcat_ids == (92,) and topics.find("mixed").subcat_ids == (92,)


def test_only_an_existing_topic_is_found():
    assert topics.find("gamingnews") is None and topics.find("") is None and topics.find("#") is None


def test_the_search_box_finds_topics_by_part_of_a_name_the_exact_one_first():
    assert [t.slug for t in topics.matching("#gam")] == ["gaming"]
    assert [t.slug for t in topics.matching("news")][0] == "news"
    assert [t.slug for t in topics.matching("art")] == ["arts", "visualarts"]
    assert topics.matching("zzz") == []
    assert len(topics.matching("")) == len(topics.all_topics())               # a bare # lists every topic


# --- text ----------------------------------------------------------------------------------------------------------

def test_a_topic_in_text_is_a_link_and_anything_else_stays_as_typed(monkeypatch):
    known = {"gaming": topics.find("gaming"), "qa": NS(url="/topic/qa", label="Q&A")}
    html = render(monkeypatch, "I love #gaming and #notatopic and #qa", topics=known)
    assert 'href="/topic/gaming"' in html and 'href="/topic/qa"' in html
    assert ">#gaming</a>" in html and ">#qa</a>" in html
    assert "#notatopic" in html and 'href="/topic/notatopic"' not in html
    assert 'title="Q&amp;A"' in html                                          # the label is escaped


def test_headings_urls_and_words_with_a_hash_inside_are_left_alone(monkeypatch):
    known = {"gaming": topics.find("gaming"), "heading": topics.find("gaming")}
    assert "<h1>Heading</h1>" in render(monkeypatch, "# Heading", topics=known)
    assert 'href="/topic/' not in render(monkeypatch, "see https://example.com/#gaming and word#gaming", topics=known)


def test_nothing_is_a_topic_without_the_site(monkeypatch):
    assert '<a' not in render(monkeypatch, "#gaming")


# --- the wiring ---------------------------------------------------------------------------------------------------

def test_the_word_filter_reads_a_topic_link_like_the_other_mentions():
    assert '"/topic/"' in read("ruqqus", "helpers", "wordfilter.py")
    from ruqqus.helpers.wordfilter import visible_text
    assert "fuck" not in visible_text('<p>hi <a href="/topic/fuck">#fuck</a> there</p>')


def test_the_search_box_has_the_two_branches_and_every_search_box_says_so():
    route = read("ruqqus", "routes", "search.py")
    assert 'elif query.startswith("#"):' in route and 'elif query.startswith("&"):' in route
    # one rule for who may be shown a guild in a list, used by + and #
    assert route.count("listable_boards(boards, v)") == 3 and "def listable_boards(boards, v):" in route   # its definition and its two uses
    # a curation: public only, owner gone or banned left out, the word filter as it is drawn
    branch = route.split('elif query.startswith("&"):')[1].split('elif query.startswith("@"):')[0]
    assert "Curation.is_private == False" in branch and "User.is_deleted == False" in branch and "User.is_banned == 0" in branch
    assert "shown_curations(" in branch and "is_hidden(level, name_severity(" in branch
    for shell in ("default.html", "header.html"):
        page = read("ruqqus", "templates", shell)
        assert page.count("+guilds, @users, #topics, &curations") == 4, shell       # desktop and phone, placeholder and label
    cards = read("ruqqus", "templates", "search_curations.html")
    assert "curation_card(c, v, c.id in followed_ids)" in cards and "partials/explore.html" in cards
    assert "topics_found" in read("ruqqus", "templates", "search_boards.html")


def test_a_topic_link_lands_on_explore_and_explore_filters_by_topic():
    front = read("ruqqus", "routes", "front.py")
    assert '@app.route("/topic/<name>"' in front and 'redirect(f"/browse?topic={topic.slug}")' in front
    browse = front.split("def browse_guilds(", 1)[1].split("\n@app", 1)[0]
    assert 'request.args.get("topic", "")' in browse and "x.visible" not in browse and "topics=chips" in browse
    assert "Board.subcat_id != topic_rules.UNLISTED_SUBCAT" in front
    assert re.search(r"topic=\{\{ t\.slug \}\}", read("ruqqus", "templates", "boards.html"))


def test_the_category_lists_take_a_topic_the_way_the_search_box_does():
    js = read("ruqqus", "assets", "js", "all_js.js")
    fn = js.split("var filterSidebarList = function(", 1)[1].split("\n};", 1)[0]
    assert "/categor/i.test(listId)" in fn and "replace(/^#/, '')" in fn


def test_the_help_pages_say_what_each_symbol_does():
    search = read("ruqqus", "templates", "help", "search.html")
    for symbol in ("<code>+pets</code>", "<code>@alice</code>", "<code>#gaming</code>", "<code>&amp;news</code>"):
        assert symbol in search, symbol
    formatting = read("ruqqus", "templates", "help", "formatting.html")
    assert "Topic Mention" in formatting and "Curation Mention" in formatting
