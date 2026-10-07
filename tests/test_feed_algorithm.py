"""A curation's own algorithm (ruqqus/helpers/feed_algorithm.py): only fixed names and
bounded values ever make a spec, and the plain-language summary says what it does."""
import json

import pytest
from werkzeug.datastructures import MultiDict

from ruqqus.helpers import feed_algorithm as fa


def spec(**parts):
    return fa.clean(parts)


# --- only known parts survive ----------------------------------------------------------

def test_nothing_is_the_default_and_the_default_is_todays_behaviour():
    assert fa.clean({}) == fa.DEFAULT == fa.clean(None) == fa.clean("x") == fa.clean([1])
    assert fa.DEFAULT["source"] == "members" and fa.DEFAULT["rank"] == "hot" and fa.DEFAULT["mode"] == "rules"
    assert fa.is_default(fa.clean({})) and not fa.is_default(spec(rank="new"))


def test_unknown_names_are_dropped_not_stored():
    s = fa.clean({"mode": "python", "source": "everywhere", "rank": "random()", "age": "decade", "sql": "drop table",
                  "kinds": ["text", "exe", 5], "hide": ["bots", "everyone"], "mix": {"votes": 3, "secret": 9}})
    assert set(s) == set(fa.DEFAULT) and set(s["mix"]) == set(fa.MIX)
    assert (s["mode"], s["source"], s["rank"], s["age"]) == ("rules", "members", "hot", "")
    assert s["kinds"] == ["text"] and s["hide"] == ["bots"] and s["mix"]["votes"] == 3
    assert "sql" not in json.dumps(s)


@pytest.mark.parametrize("value, expected", [("7", 7), (7.9, 7), (-3, 0), (10 ** 9, fa.MAX_COUNT), ("abc", 0), (None, 0), ([], 0)])
def test_numbers_are_bounded(value, expected):
    assert spec(min_votes=value)["min_votes"] == expected


def test_mix_weights_are_bounded_and_the_fade_is_one_of_the_fixed_steps():
    s = spec(rank="mix", mix={"votes": 99, "comments": -4, "fade": 30, "members": "x", "media": 11, "variety": 50})
    assert s["mix"] == {"votes": 10, "comments": 0, "fade": 24, "members": 0, "media": 10, "variety": 5}
    for hours in (0, 1, 6, 9, 100, 10 ** 6):
        assert spec(mix={"fade": hours})["mix"]["fade"] in fa.FADES


def test_words_are_plain_text_trimmed_and_not_repeated():
    s = spec(any="  world   cup , World Cup\n\nolympics", none=["spoilers", "", "SPOILERS", 4])
    assert s["any"] == ["world cup", "olympics"] and s["none"] == ["spoilers"]


def test_too_many_or_too_long_words_are_refused_with_a_message():
    with pytest.raises(fa.AlgorithmError) as err:
        spec(any=",".join(f"w{i}" for i in range(11)))
    assert "10 words" in err.value.message
    with pytest.raises(fa.AlgorithmError) as err:
        spec(all="x" * 41)
    assert "40 characters" in err.value.message


@pytest.mark.parametrize("word, pattern", [
    ("cup", "%cup%"), ("100%", "%100\\%%"), ("snake_case", "%snake\\_case%"), ("a\\b", "%a\\\\b%"), ("%", "%\\%%"),
])
def test_a_word_is_never_a_pattern(word, pattern):
    assert fa.like_pattern(word) == pattern


@pytest.mark.parametrize("text, host", [
    ("example.org", "example.org"), ("https://www.Example.org/a/b?c#d", "example.org"), ("  news.example.co.uk  ", "news.example.co.uk"),
    ("http://user:pw@example.org:8080/x", "example.org"),
])
def test_a_site_is_stored_as_its_host_name(text, host):
    assert fa.site_name(text) == host


@pytest.mark.parametrize("text", ["", "localhost", "not a site", "exa mple.org", "example", "ex%ample.org", ".*\\.org", "a" * 120 + ".org"])
def test_what_is_not_a_site_name(text):
    assert fa.site_name(text) is None


def test_sites_are_checked_and_limited():
    assert spec(only_sites="Example.org, https://example.org/x\nnews.example.com")["only_sites"] == ["example.org", "news.example.com"]
    with pytest.raises(fa.AlgorithmError) as err:
        spec(never_sites="example.org, .*")
    assert "not a site name" in err.value.message
    with pytest.raises(fa.AlgorithmError):
        spec(only_sites=",".join(f"s{i}.example.org" for i in range(11)))


def test_choosing_every_kind_is_no_condition():
    assert spec(kinds=list(fa.KINDS))["kinds"] == []
    assert spec(kinds=["audio", "image"])["kinds"] == ["image", "audio"]        # stored in the fixed order


# --- the whole site --------------------------------------------------------------------

def test_the_whole_site_needs_an_age_limit_of_a_month_or_less():
    for age in ("", "year", "forever"):
        with pytest.raises(fa.AlgorithmError) as err:
            spec(source="site", age=age)
        assert "age limit" in err.value.message
    for age in fa.SITE_AGES:
        assert spec(source="site", age=age)["source"] == "site"
    assert spec(source="members", age="year")["age"] == "year"                    # its own members may go further back


# --- an outside server -----------------------------------------------------------------

def test_server_mode_needs_an_address_and_rules_mode_keeps_none():
    with pytest.raises(fa.AlgorithmError):
        spec(mode="server")
    with pytest.raises(fa.AlgorithmError):
        spec(mode="server", server="https://example.org/feed with spaces")
    with pytest.raises(fa.AlgorithmError):
        spec(mode="server", server="https://example.org/" + "a" * 400)
    s = spec(mode="server", server="  https://feeds.example.org/top  ")
    assert s["server"] == "https://feeds.example.org/top" and fa.is_server(s)
    assert spec(mode="rules", server="https://feeds.example.org/top")["server"] == ""
    # the site-wide age rule is about the rules, not about a server
    assert fa.is_server(spec(mode="server", server="https://feeds.example.org/top", source="site"))


# --- storing ---------------------------------------------------------------------------

def test_what_is_stored_comes_back_the_same_and_junk_comes_back_as_the_default():
    s = spec(source="site", age="week", any="etna", kinds=["image"], rank="mix", mix={"votes": 2, "variety": 3})
    assert fa.load(fa.dump(s)) == s
    for junk in (None, "", "{", "[]", "null", '{"source": "site"}', '{"any": "' + "x" * 99 + '"}'):
        assert fa.load(junk) == fa.DEFAULT, junk


def test_every_preset_is_a_valid_spec_and_differs_from_the_default():
    assert set(fa.PRESETS) == {"latest", "popular", "discussed", "media", "rising"}
    for name, (label, text, parts) in fa.PRESETS.items():
        s = fa.preset(name)
        assert s == fa.clean(s) and not fa.is_default(s) and label and text.endswith("."), name
    assert fa.preset("nonsense") == fa.DEFAULT
    assert fa.uses_mix(fa.preset("rising")) and fa.preset("rising")["mix"]["variety"] == 2


def test_a_posted_form_becomes_a_spec():
    form = MultiDict([
        ("mode", "rules"), ("source", "site"), ("age", "week"), ("any_words", "etna, vesuvius"), ("none_words", ""),
        ("kinds", "image"), ("kinds", "video"), ("hide", "bots"), ("min_votes", "3"), ("rank", "mix"),
        ("mix_votes", "8"), ("mix_fade", "72"), ("mix_variety", "2"), ("only_sites", "example.org"), ("evil", "1"),
    ])
    s = fa.from_form(form)
    assert s["source"] == "site" and s["any"] == ["etna", "vesuvius"] and s["kinds"] == ["image", "video"]
    assert s["hide"] == ["bots"] and s["min_votes"] == 3 and s["only_sites"] == ["example.org"]
    assert s["mix"]["votes"] == 8 and s["mix"]["fade"] == 72 and s["mix"]["variety"] == 2 and s["mix"]["comments"] == 5
    assert fa.from_form(MultiDict()) == fa.DEFAULT


# --- ranking ---------------------------------------------------------------------------

MIX = fa.DEFAULT["mix"]


def test_a_mix_rewards_votes_and_comments_and_fades_with_age():
    assert fa.mix_score(MIX, 10, 0, 1) > fa.mix_score(MIX, 1, 0, 1) > fa.mix_score(MIX, 0, 0, 1) > 0
    assert fa.mix_score(MIX, 0, 10, 1) > fa.mix_score(MIX, 0, 1, 1)
    assert fa.mix_score(MIX, 5, 5, 0) == pytest.approx(2 * fa.mix_score(MIX, 5, 5, MIX["fade"]))   # one half-life
    assert fa.mix_score(MIX, -50, -5, 1) == fa.mix_score(MIX, 0, 0, 1)                              # never negative


def test_the_weights_decide_what_wins():
    voted, discussed = (100, 0), (0, 100)
    by_votes = dict(MIX, votes=10, comments=0)
    by_comments = dict(MIX, votes=0, comments=10)
    assert fa.mix_score(by_votes, *voted, 1) > fa.mix_score(by_votes, *discussed, 1)
    assert fa.mix_score(by_comments, *discussed, 1) > fa.mix_score(by_comments, *voted, 1)
    slow, fast = dict(MIX, fade=168), dict(MIX, fade=6)
    assert fa.mix_score(slow, 5, 5, 48) > fa.mix_score(fast, 5, 5, 48)


def test_boosts_only_apply_when_asked_for():
    assert fa.mix_score(MIX, 5, 5, 1, is_member=True, has_media=True) == fa.mix_score(MIX, 5, 5, 1)
    boosted = dict(MIX, members=5, media=5)
    plain = fa.mix_score(boosted, 5, 5, 1)
    assert fa.mix_score(boosted, 5, 5, 1, is_member=True) == pytest.approx(2 * plain)
    assert fa.mix_score(boosted, 5, 5, 1, is_member=True, has_media=True) == pytest.approx(4 * plain)


def test_variety_limits_an_author_and_a_guild_per_page_and_keeps_every_post():
    rows = [(i, "a", "g") for i in range(1, 7)] + [(7, "b", "h"), (8, "c", "h")]
    out = fa.spread(rows, 2, page=4)
    # two of author a, then the others move up; nobody else is left, so page two is filled anyway
    assert out == [1, 2, 7, 8, 3, 4, 5, 6]
    assert fa.spread(rows, 0) == [r[0] for r in rows]
    # the guild limit works alone too (different authors, one guild)
    assert fa.spread([(1, "a", "g"), (2, "b", "g"), (3, "c", "g"), (4, "d", "h")], 2, page=3) == [1, 2, 4, 3]


def test_pages_stay_full_so_paging_never_skips_or_repeats_a_post():
    rows = [(i, "a" if i % 3 else "b", None) for i in range(1, 101)]
    out = fa.spread(rows, 1, page=25)
    assert sorted(out) == list(range(1, 101)) and len(set(out)) == 100
    assert fa.spread([], 3) == []


def test_an_anonymous_post_is_never_counted_against_an_author():
    # the same person wrote all of these, three of them anonymously (key None)
    rows = [(1, "a", None), (2, None, None), (3, None, None), (4, None, None), (5, "a", None), (6, "a", None)]
    assert fa.spread(rows, 1, page=6)[:4] == [1, 2, 3, 4]      # the anonymous ones are not held back with "a"


# --- plain language --------------------------------------------------------------------

def test_the_default_reads_as_what_a_curation_always_did():
    assert fa.describe(fa.DEFAULT) == ["Posts from this curation's guilds and accounts.", "Ordered by Hot, the site's usual order."]


def test_every_part_is_said():
    s = spec(source="site", age="week", any="etna, vesuvius", all="italy", none="spoilers", kinds=["image", "video"],
             only_sites="example.org", never_sites="spam.example", min_votes=1, min_comments=3, hide=list(fa.HIDE), rank="new")
    text = " ".join(fa.describe(s))
    for part in ("whole site, no older than a week", '"etna" or "vesuvius"', 'all of "italy"', 'Never posts that mention "spoilers"',
                 "Only pictures and video", "Only links to example.org", "Never links to spam.example",
                 "At least 1 vote and 3 comments", "Leaves out bots, posts made with AI, paid partnerships and forwarded copies",
                 "Newest first"):
        assert part in text, part


def test_a_mix_is_described_with_its_numbers():
    s = spec(source="site", age="day", rank="mix", mix={"votes": 2, "comments": 9, "fade": 72, "members": 4, "media": 3, "variety": 1})
    line = fa.describe(s)[-1]
    assert line == ("Ordered by its own mix: votes 2, comments 9, fading over 3 days, a boost of 4 for this curation's guilds "
                    "and accounts, a boost of 3 for posts with media. At most 1 post per author or guild on a page.")
    # the members boost means nothing when everything already comes from the members
    assert "guilds and accounts," not in fa.describe(spec(rank="mix", mix={"members": 4}))[-1]


def test_an_outside_server_is_named_and_said_to_be_unseen():
    lines = fa.describe(spec(mode="server", server="https://feeds.example.org/top?x=1"))
    assert lines[0] == "Ranked by an outside server (feeds.example.org). Ruqqus cannot see how it picks posts."
    assert "only orders posts you are allowed to see" in lines[1]


def test_the_summary_keeps_the_sites_vocabulary():
    text = " ".join(" ".join(fa.describe(fa.preset(name))) for name in fa.PRESETS).lower()
    for banned in ("reply", "replies", "crosspost", "promote"):
        assert banned not in text
