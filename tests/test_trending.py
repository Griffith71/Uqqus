"""Trending topics (ruqqus/helpers/trending.py): how a topic is found and scored, and what
must never make one (a single account, everyday words, the usual amount of talk)."""
from types import SimpleNamespace as NS

import pytest

from ruqqus.helpers import trending as tr

NOW = 1_800_000_000
HOUR, DAY = 3600, 86400
EN = tr.stopwords("en")


def post(id, author, title, age=HOUR, **kw):
    return tr.Post(id, author, NOW - age, title=title, **kw)


def ranked(window, baseline=(), **kw):
    kw.setdefault("min_authors", 3)
    return tr.rank(tr.prepare(window), tr.prepare(baseline), NOW, **kw)


def keys(topics):
    return [t.key for t in topics]


# --- what a post says ------------------------------------------------------------------

def test_runs_of_one_to_three_words_without_filler_at_the_ends():
    found = tr.phrases("the eclipse over northern spain was amazing", EN)
    assert {"eclipse", "northern spain", "eclipse over northern", "spain", "amazing"} <= set(found)
    assert "the eclipse" not in found and "over" not in found and "spain was" not in found
    assert all(len(k.split()) <= tr.MAX_WORDS for k in found)


def test_a_phrase_does_not_cross_punctuation():
    found = tr.phrases("Bought a kettle. Lisbon is lovely, coffee too", EN)
    assert "kettle lisbon" not in found and "lovely coffee" not in found
    assert "kettle" in found and "lisbon" in found


def test_capitalised_runs_are_names_but_not_the_first_word_of_a_sentence():
    found = tr.phrases("we watched Mount Etna erupt. Yesterday was calm", EN)
    assert found["mount etna"] == (tr.NAME, "Mount Etna")
    assert found["etna"][0] == tr.NAME
    assert found["calm"][0] == tr.PHRASE
    assert tr.phrases("Volcano erupts again", EN)["volcano"][0] == tr.PHRASE       # only starts the sentence
    assert tr.phrases("NASA delays the launch", EN)["nasa"][0] == tr.NAME          # an acronym is a name anywhere


def test_a_title_in_title_case_names_nothing():
    found = tr.phrases("Big Storm Hits The Northern Coast Tonight", EN)
    assert found and all(kind == tr.PHRASE for kind, _ in found.values())


def test_addresses_markup_mentions_and_emoji_codes_are_not_words():
    text = "see https://example.org/a-b?c=d and [the guide](https://x.test/y) by @alice in +cooking {c:red}now{/c} :smile: **bold**"
    found = tr.phrases(text, EN)
    assert "guide" in found and "bold" in found
    for junk in ("https", "example", "alice", "cooking", "smile", "red", "test"):
        assert junk not in found, junk


def test_possessives_and_case_fold_into_one_key():
    assert "etna" in tr.phrases("it was Etna's night", EN)
    assert set(tr.phrases("the ECLIPSE", EN)) == set(tr.phrases("the eclipse", EN))


def test_each_language_has_its_own_filler_words():
    assert "war" in tr.phrases("the war", tr.stopwords("en"))
    assert "war" not in tr.phrases("es war", tr.stopwords("de"))
    assert tr.stopwords(None) == tr.stopwords("en")           # no detected language reads as English
    assert tr.stopwords("pcm") == frozenset()                 # no list: the common-share rule covers it
    assert tr.stopwords("pt-BR") == tr.stopwords("pt")


# --- links -----------------------------------------------------------------------------

@pytest.mark.parametrize("url, key", [
    ("https://www.Example.org/story/?utm_source=x&fbclid=1#top", "example.org/story"),
    ("http://example.org/story", "example.org/story"),
    ("https://m.example.org/a?b=2&a=1", "example.org/a?a=1&b=2"),
    ("https://youtu.be/dQw4w9WgXcQ?si=abc", "youtube.com/watch?v=dQw4w9WgXcQ"),
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=10", "youtube.com/watch?v=dQw4w9WgXcQ"),
    ("https://www.youtube.com/shorts/dQw4w9WgXcQ", "youtube.com/watch?v=dQw4w9WgXcQ"),
])
def test_the_same_page_is_the_same_link_however_it_was_shared(url, key):
    assert tr.link_key(url) == key


@pytest.mark.parametrize("url", [
    "", None, "not a link", "ftp://example.org/x", "javascript:alert(1)", "https://example.org/", "https://example.org",
    "https://example.org/photo.JPG", "https://localhost/x", "https://ruqqus.test/post/abc", "https://[bad",
])
def test_what_is_not_a_shared_link(url):
    assert tr.link_key(url, own_hosts=("ruqqus.test",)) is None


def test_a_post_gives_its_words_and_its_link_once_each():
    p = tr.Post(1, 7, NOW, title="Etna erupts again, Etna!", text="etna etna etna", url="https://example.org/etna",
                link_title="Etna &amp; the night sky")
    terms = tr.terms_of(p)
    assert terms["link:example.org/etna"] == (tr.LINK, "Etna & the night sky")
    assert "etna" in terms and list(terms).count("etna") == 1


# --- scoring ---------------------------------------------------------------------------

def test_three_different_accounts_make_a_topic():
    topics = ranked([post(1, 1, "Mount Etna erupts"), post(2, 2, "wow Mount Etna tonight"), post(3, 3, "look at Mount Etna")])
    assert keys(topics) == ["mount etna"]
    assert topics[0].label == "Mount Etna" and topics[0].authors == 3
    assert sorted(topics[0].post_ids) == [1, 2, 3]


def test_a_topics_posts_are_listed_newest_first():
    window = [post(1, 1, "Mount Etna", age=5 * HOUR), post(2, 2, "Mount Etna", age=HOUR), post(3, 3, "Mount Etna", age=3 * HOUR)]
    assert ranked(window)[0].post_ids == [2, 3, 1]


def test_one_account_cannot_make_a_topic_however_much_it_posts():
    flood = [post(i, 1, f"Mount Etna erupts {i}") for i in range(1, 40)]
    assert ranked(flood) == []
    assert ranked(flood + [post(100, 2, "Mount Etna")]) == []           # two accounts are still not three
    assert keys(ranked(flood + [post(100, 2, "Mount Etna"), post(101, 3, "Mount Etna")])) == ["mount etna"]


def test_an_account_counts_once_so_a_flood_adds_nothing():
    three = [post(1, 1, "Mount Etna"), post(2, 2, "Mount Etna"), post(3, 3, "Mount Etna")]
    flood = three + [post(10 + i, 1, "Mount Etna") for i in range(30)]
    assert ranked(flood)[0].authors == 3
    assert ranked(flood)[0].score == pytest.approx(ranked(three)[0].score)


def test_the_usual_amount_of_talk_does_not_trend():
    # three accounts a day, every day last week, and three today: nothing new
    usual = [post(100 + d * 10 + a, a, "weather report", age=DAY * (d + 1) + HOUR) for d in range(7) for a in (1, 2, 3)]
    today = [post(a, a, "weather report") for a in (1, 2, 3)]
    assert ranked(today, usual) == []
    # the same three accounts about something nobody mentioned before
    assert keys(ranked([post(a, a, "Mount Etna") for a in (1, 2, 3)], usual)) == ["mount etna"]


def test_a_burst_beats_steady_volume():
    usual = [post(1000 + d * 100 + a, a, "football scores", age=DAY * (d + 1) + HOUR) for d in range(7) for a in range(1, 9)]
    today = ([post(a, a, "football scores") for a in range(1, 11)]              # 10 today against 8 a day
             + [post(50 + a, 20 + a, "Mount Etna") for a in range(1, 6)])       # 5 today against none
    assert keys(ranked(today, usual))[0] == "mount etna"


def test_words_everybody_uses_all_week_are_not_topics():
    usual = [post(1000 + i, i % 9, "zzword appears here" if i % 2 else "nothing special", age=DAY * 2) for i in range(40)]
    today = [post(a, a, "zzword") for a in range(1, 30)]
    assert ranked(today, usual) == []


def test_newer_posts_weigh_more_and_engagement_lifts_a_little():
    old = ranked([post(a, a, "Mount Etna", age=23 * HOUR) for a in (1, 2, 3)])[0].score
    new = ranked([post(a, a, "Mount Etna", age=60) for a in (1, 2, 3)])[0].score
    assert new > old > 0
    busy = ranked([post(a, a, "Mount Etna", age=60, votes=500, comments=500) for a in (1, 2, 3)])[0].score
    assert new < busy <= new * 1.5 + 1e-6
    assert tr.engagement(-50) == 1.0 and tr.engagement(10 ** 12) == 1.5


def test_a_name_outranks_a_plain_word_with_the_same_support():
    plain = ranked([post(a, a, "about the eruption") for a in (1, 2, 3)])
    name = ranked([post(a, a, "about the Etna") for a in (1, 2, 3)])
    assert keys(plain) == ["eruption"] and keys(name) == ["etna"]
    assert name[0].score > plain[0].score


def test_equal_scores_keep_a_stable_order_and_the_list_stops_at_ten():
    words = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel", "india", "juliet", "kilo", "lima"]
    window = [post(i * 10 + a, a, f"only {w} here") for i, w in enumerate(words) for a in (1, 2, 3)]
    topics = ranked(window)
    assert len(topics) == tr.TOP and keys(topics) == sorted(words)[:tr.TOP]


# --- one topic, not several ------------------------------------------------------------

def test_a_word_inside_a_longer_phrase_is_the_longer_phrase():
    topics = ranked([post(a, a, "the World Cup final") for a in (1, 2, 3)])
    assert keys(topics) == ["world cup final"]


def test_a_rare_longer_phrase_does_not_replace_the_broad_one():
    window = [post(a, a, "World Cup tonight") for a in range(1, 11)] + [post(20 + a, 20 + a, "World Cup final") for a in (1, 2, 3)]
    assert keys(ranked(window)) == ["world cup"]


def test_a_link_and_the_words_of_its_posts_are_one_topic():
    window = [post(a, a, "Mount Etna erupts", url="https://example.org/etna", link_title="Etna erupts") for a in (1, 2, 3)]
    assert len(ranked(window)) == 1


def test_one_link_shared_under_different_words_trends_as_the_link():
    titles = ["look at this", "unbelievable footage", "cannot stop watching"]
    window = [post(i, i, t, url="https://example.org/video?utm_medium=social", link_title="Etna from the air") for i, t in enumerate(titles, 1)]
    topics = ranked(window)
    assert keys(topics) == ["link:example.org/video"] and topics[0].kind == tr.LINK
    assert topics[0].label == "Etna from the air" and topics[0].slug.startswith("link-")


def test_unrelated_topics_both_stay():
    window = [post(a, a, "Mount Etna") for a in (1, 2, 3)] + [post(10 + a, 10 + a, "Lisbon marathon") for a in (1, 2, 3)]
    assert sorted(keys(ranked(window))) == ["lisbon marathon", "mount etna"]


# --- hiding ----------------------------------------------------------------------------

def test_an_admin_can_hide_a_topic_and_every_phrase_holding_it():
    window = [post(a, a, "Mount Etna") for a in (1, 2, 3)] + [post(10 + a, 10 + a, "Lisbon marathon") for a in (1, 2, 3)]
    assert keys(ranked(window, blocked={"etna"})) == ["lisbon marathon"]
    assert tr.is_blocked("mount etna", {"etna"}) and not tr.is_blocked("etnas", {"etna"})
    assert not tr.is_blocked("link:example.org/etna", {"etna"}) and tr.is_blocked("link:example.org/etna", {"link:example.org/etna"})


def test_slugs_are_safe_in_an_address_and_distinct():
    assert tr.slug_of("world cup final") == "world-cup-final"
    assert tr.slug_of("año nuevo") == "año-nuevo"
    long_a, long_b = "a" * 70 + " one", "a" * 70 + " two"
    assert tr.slug_of(long_a) != tr.slug_of(long_b) and len(tr.slug_of(long_a)) <= 60
    for key in ("world cup", "link:example.org/a?b=1", "c++ / rust", "?!"):
        assert tr.slug_of(key) and not set(tr.slug_of(key)) & set("/?#& ")


def test_several_lists_merge_into_one_without_repeats():
    a = [NS(slug="etna", score=5.0), NS(slug="lisbon", score=2.0)]
    b = [NS(slug="etna", score=7.0), NS(slug="tokyo", score=3.0)]
    merged = tr.merge([a, b])
    assert [(r.slug, r.score) for r in merged] == [("etna", 7.0), ("tokyo", 3.0), ("lisbon", 2.0)]
    assert len(tr.merge([[NS(slug=str(i), score=i) for i in range(30)]])) == tr.TOP
