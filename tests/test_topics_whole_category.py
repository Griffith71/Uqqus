"""A topic and a category (ruqqus/helpers/topics.py): a guild may be filed under a category alone, so a topic named after a
category also lists `-N` (`Topic.ids`: its subcategories, then the guilds filed under that category alone). Pure: the
categories are small stand-ins shaped like classes/categories.py."""
from types import SimpleNamespace as NS

import pytest

from ruqqus.helpers import topics


def sub(id_, name, visible=None):
    return NS(id=id_, name=name, _visible=visible)


def cat(id_, name, subs, visible=True, nsfw=False):
    return NS(id=id_, name=name, visible=visible, is_nsfw=nsfw, subcats=subs)


CATEGORIES = [
    cat(1, "Arts", [sub(8, "Film & TV"), sub(10, "Music"), sub(106, "Visual Arts")]),
    cat(5, "Entertainment", [sub(24, "Film & TV"), sub(23, "News")]),
    cat(6, "Gaming", [sub(25, "PC"), sub(26, "Console")]),
    cat(11, "News", [sub(45, "Local"), sub(47, "World")]),
    cat(12, "Politics", [sub(55, "News"), sub(56, "Debate")]),
    cat(24, "Music", [sub(330, "Rock")]),
    cat(25, "Film & TV", [sub(301, "Cult")]),
    cat(20, "Secret", [sub(90, "Quiet")], visible=False),
    cat(21, "Adult", [sub(91, "Adult talk")], nsfw=True),
    cat(22, "Mixed", [sub(92, "Shown", True), sub(93, "Hidden by itself", False)]),
    cat(23, "Unlisted", [sub(0, "Uncategorized")]),
    cat(26, "Bare", []),
]


@pytest.fixture(autouse=True)
def site_categories():
    topics.set_categories(CATEGORIES)
    yield
    topics.set_categories(None)


def test_a_topic_named_after_a_category_holds_it_whole():
    gaming = topics.find("gaming")
    assert gaming.subcat_ids == (25, 26) and gaming.cat_ids == (6,)
    assert gaming.ids == (25, 26, -6)


def test_a_subcategory_alone_is_a_topic_of_no_category():
    visual = topics.find("visualarts")
    assert visual.subcat_ids == (106,) and visual.cat_ids == () and visual.ids == (106,)
    local = topics.find("local")
    assert local.cat_ids == () and local.ids == (45,)


def test_a_subcategory_named_like_a_category_merges_into_the_topic_and_keeps_the_category():
    music = topics.find("music")                       # Arts' Music, and the category Music
    assert music.subcat_ids == (10, 330) and music.cat_ids == (24,) and music.ids == (10, 330, -24)
    filmtv = topics.find("filmtv")                     # two subcategories and the category Film & TV
    assert filmtv.subcat_ids == (8, 24, 301) and filmtv.cat_ids == (25,) and filmtv.ids == (8, 24, 301, -25)


def test_news_holds_the_category_news_and_not_the_categories_that_only_have_a_news_subcategory():
    news = topics.find("news")
    assert news.cat_ids == (11,)
    assert -11 in news.ids and -12 not in news.ids and -5 not in news.ids
    assert news.subcat_ids == (23, 45, 47, 55)         # the News rows under Entertainment and Politics are in it as rows
    assert topics.find("politics").ids == (55, 56, -12)


def test_two_categories_with_one_name_are_one_topic_of_both():
    topics.set_categories([cat(1, "Pets", [sub(5, "Cats")]), cat(2, "pets", [sub(6, "Dogs")])])
    pets = topics.find("pets")
    assert pets.subcat_ids == (5, 6) and pets.cat_ids == (1, 2) and pets.ids == (5, 6, -1, -2)


def test_a_hidden_or_adult_category_adds_no_id_to_any_topic():
    for name in ("secret", "adult"):
        assert topics.find(name) is None, name
    every_id = {i for t in topics.all_topics() for i in t.ids}
    assert not every_id & {-20, -21, 90, 91}
    assert {c for t in topics.all_topics() for c in t.cat_ids} == {1, 5, 6, 11, 12, 22, 24, 25}


def test_a_category_with_nothing_to_show_is_no_topic_and_names_no_id():
    # Unlisted holds Uncategorized (0) only, Bare holds nothing, and the hidden row of Mixed is not shown
    assert topics.find("unlisted") is None and topics.find("bare") is None
    assert not {-23, -26} & {i for t in topics.all_topics() for i in t.ids}
    assert topics.find("mixed").subcat_ids == (92,) and topics.find("mixed").ids == (92, -22)


def test_the_subcategories_of_a_topic_are_what_they_were():
    assert {t.slug: t.subcat_ids for t in topics.all_topics()} == {
        "arts": (8, 10, 106), "filmtv": (8, 24, 301), "music": (10, 330), "visualarts": (106,),
        "entertainment": (23, 24), "news": (23, 45, 47, 55), "gaming": (25, 26), "pc": (25,), "console": (26,),
        "local": (45,), "world": (47,), "politics": (55, 56), "debate": (56,), "rock": (330,), "cult": (301,),
        "mixed": (92,), "shown": (92,),
    }


def test_a_topic_can_still_be_built_by_position():
    plain = topics.Topic("gaming", "Gaming", (25, 26))
    assert plain.cat_ids == () and plain.ids == (25, 26)
    both = topics.Topic("gaming", "Gaming", (25, 26), (6,))
    assert both.ids == (25, 26, -6) and both != plain
    assert hash(both) == hash(topics.Topic("gaming", "Gaming", (25, 26), (6,)))


def test_the_categories_the_tests_install_are_forgotten_with_the_topics():
    topics.set_categories([cat(1, "Pets", [sub(5, "Cats")])])
    assert topics.find("pets").ids == (5, -1) and topics.find("gaming") is None
    topics.set_categories(CATEGORIES)
    assert topics.find("gaming") is not None and topics.find("pets") is None
    topics.set_categories(None)
    assert topics._topics is None

