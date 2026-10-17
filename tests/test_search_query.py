"""Search operators (ruqqus/helpers/search_query.py): what a search typed in the box means."""
import pytest

from ruqqus.helpers import search_query as sq

JAN_1 = 1767225600          # 2026-01-01 00:00 UTC
DAY = 86400


def kinds(text):
    return [(p.kind, p.value, p.negated) for p in sq.parse(text).parts]


def test_words_phrases_and_minus():
    assert kinds('cats "black  cat" -dogs -"hot dogs"') == [
        ("word", "cats", False), ("phrase", "black cat", False), ("word", "dogs", True), ("phrase", "hot dogs", True)]


def test_a_stray_quote_or_minus_is_not_a_word():
    assert kinds('cats" - "" -') == [("word", "cats", False)]


def test_every_operator_and_alias_is_understood_in_any_case():
    for op in sq.OPERATORS:
        for name in (op.key,) + op.aliases:
            value = op.example.split(":", 1)[1]
            query = sq.parse(f"{name.upper()}:{value}")
            assert not query.problems, (name, query.problems)
            assert query.parts[0].kind == op.key, name


def test_names_lose_their_at_and_plus():
    assert kinds("from:@alice author:bob in:+pets guild:news") == [
        ("from", "alice", False), ("from", "bob", False), ("in", "pets", False), ("in", "news", False)]


def test_in_takes_a_guild_a_topic_or_a_public_curation():
    assert kinds("in:+pets in:#gaming in:&news -in:#pc -in:&mine") == [
        ("in", "pets", False), ("in", "#gaming", False), ("in", "&news", False), ("in", "#pc", True), ("in", "&mine", True)]
    assert kinds("guild:#arts") == [("in", "#arts", False)]


def test_a_topic_or_curation_that_cannot_exist_reads_as_one_that_does_not():
    assert sq.parse("in:#a.b").problems == (sq.NO_TOPIC.format("a.b"),)
    assert sq.parse("in:&a b").problems == ()        # "b" is a word; the operator ends at the space
    assert sq.parse("in:&a%b").problems == (sq.NO_CURATION.format("a%b"),)
    assert sq.parse("in:#").problems == ("in: needs something after it, such as in:+pets.",)
    assert sq.parse("in:&").problems == ("in: needs something after it, such as in:+pets.",)


def test_an_unknown_key_stays_free_text():
    assert kinds("https://example.com note:this") == [("word", "https://example.com", False), ("word", "note:this", False)]


def test_negated_operators():
    assert kinds("-from:@a -in:+b -has:link -lang:en -domain:x.com -url:abc -is:forward") == [
        ("from", "a", True), ("in", "b", True), ("has", "link", True), ("lang", "en", True),
        ("domain", "x.com", True), ("url", "abc", True), ("is", "forward", True)]


@pytest.mark.parametrize("text", ["-before:2026-01-01", "-since:2026-01-01", "-on:2026-01-01", "-min_votes:3"])
def test_some_operators_cannot_be_negated(text):
    query = sq.parse(text)
    assert query.parts == () and "Leave out the minus" in query.problems[0]


def test_dates_are_utc_days_and_before_and_after_leave_the_day_out():
    def bounds(text):
        return sq.time_bounds(sq.parse(text))
    assert bounds("since:2026-01-01") == (JAN_1, None)
    assert bounds("after:2026-01-01") == (JAN_1 + DAY, None)
    assert bounds("until:2026-01-01") == (None, JAN_1 + DAY)
    assert bounds("before:2026-01-01") == (None, JAN_1)
    assert bounds("on:2026-01-01") == (JAN_1, JAN_1 + DAY)
    assert bounds("since:2026-01-01 until:2026-01-03 after:2025-12-01") == (JAN_1, JAN_1 + 3 * DAY)
    assert bounds("cats") == (None, None)


def test_dates_that_leave_no_days_are_a_problem():
    assert sq.parse("since:2026-02-01 until:2026-01-31").problems == (sq.NO_DAYS,)
    assert sq.parse("after:2026-01-01 before:2026-01-02").problems == (sq.NO_DAYS,)
    assert not sq.parse("on:2026-01-01").problems


@pytest.mark.parametrize("value", ["2026-13-01", "2026-02-30", "yesterday", "01/01/2026"])
def test_a_bad_date_is_a_problem(value):
    assert sq.parse(f"before:{value}").problems == ("before: needs a day written like 2026-01-31.",)


def test_has_and_is_take_their_values_and_a_few_plurals():
    assert kinds("has:Pictures has:links has:sound is:forwarded is:paid") == [
        ("has", "image", False), ("has", "link", False), ("has", "audio", False),
        ("is", "forward", False), ("is", "partnership", False)]
    assert sq.parse("has:gif").problems == ("has: can be link, image, video, audio or poll.",)


def test_languages_by_code_or_name_and_only_listed_ones():
    assert kinds("lang:EN language:english lang:Modern-Greek") == [
        ("lang", "en", False), ("lang", "en", False), ("lang", "el", False)]
    assert sq.parse("lang:la").problems[0].startswith('No language called "la"')   # Latin was taken off the list
    assert sq.parse("lang:klingon").problems


def test_domains_are_host_names():
    assert kinds("domain:https://www.Example.com/news?x=1 site:sub.example.co.uk") == [
        ("domain", "example.com", False), ("domain", "sub.example.co.uk", False)]
    assert sq.parse("domain:localhost").problems
    assert sq.parse("domain:exa%mple.com").problems


def test_numbers_are_whole_and_bounded():
    assert kinds("min_votes:007 min_comments:5") == [("min_votes", "7", False), ("min_comments", "5", False)]
    for bad in ("min_votes:-1", "min_votes:1.5", "min_votes:٣", f"min_votes:{sq.MAX_NUMBER + 1}"):
        assert sq.parse(bad).problems, bad


def test_a_name_that_cannot_exist_reads_as_one_that_does_not():
    assert sq.parse("from:@a%b").problems == (sq.NO_ACCOUNT.format("a%b"),)
    assert sq.parse("in:+a.b").problems == (sq.NO_GUILD.format("a.b"),)
    assert sq.parse("from:@").problems == ("from: needs something after it, such as from:@alice.",)


def test_limits():
    assert sq.parse("x" * (sq.MAX_CHARS + 1)).problems == (sq.TOO_LONG,)
    assert sq.parse(" ".join(["cat"] * sq.MAX_PARTS)).problems == ()
    assert sq.parse(" ".join(["cat"] * (sq.MAX_PARTS + 1))).problems == (sq.TOO_MANY,)


def test_problems_are_listed_once_each():
    assert sq.parse("has:x has:x").problems == ("has: can be link, image, video, audio or poll.",)


def test_one_author_means_a_positive_from():
    assert sq.parse("cats from:@a").for_one_author
    assert not sq.parse("cats -from:@a").for_one_author
    assert not sq.parse("cats").for_one_author


def test_the_query_is_a_stable_cache_key():
    a, b = sq.parse("cats from:@a has:link"), sq.parse("cats  from:@a   has:link")
    assert repr(a) == repr(b) and a == b and hash(a) == hash(b)
    assert repr(a) != repr(sq.parse("cats from:@a has:image"))


def test_a_filter_can_be_taken_back_out():
    query = sq.parse('cats -"hot dogs" from:@alice has:link')
    assert sq.without(query, 2) == 'cats -"hot dogs" has:link'
    assert sq.without(query, 0) == '-"hot dogs" from:@alice has:link'


def test_labels():
    def labels(text):
        return [sq.label(p) for p in sq.parse(text).operators]
    assert labels("from:@a -in:+b since:2026-01-05 -has:image lang:fr -is:forward min_votes:3") == [
        "From @a", "Not in +b", "Since 5 Jan 2026", "Without a picture", "In French", "No forwarded copies",
        "At least 3 votes"]
    assert labels("in:#gaming -in:&news") == ["In #gaming", "Not in &news"]


def test_the_advanced_form_round_trip_loses_nothing():
    text = 'cats -dogs from:@alice -from:@bob in:+pets since:2026-01-01 until:2026-02-01 has:link has:poll lang:en ' \
           '-is:forward domain:example.com min_votes:3'
    fields = sq.form_fields(sq.parse(text))
    assert fields["from"] == "alice" and fields["in"] == "pets" and fields["has"] == ["link", "poll"]
    assert fields["no_forwards"] and fields["lang"] == "en" and fields["since"] == "2026-01-01"
    assert fields["words"] == "cats -dogs -from:@bob domain:example.com min_votes:3"
    again = sq.compose(words=fields["words"], author=fields["from"], guild=fields["in"], since=fields["since"],
                       until=fields["until"], has=fields["has"], lang=fields["lang"], no_forwards=fields["no_forwards"])
    assert sorted(kinds(again)) == sorted(kinds(text))


def test_the_advanced_forms_in_field_keeps_a_topic_or_curation():
    for text in ("in:#gaming", "in:&news"):
        field = sq.form_fields(sq.parse(text))["in"]
        assert field == text[3:]
        assert sq.compose(guild=field) == text
    assert sq.compose(guild="#ga<ming") == "in:#gaming" and sq.compose(guild="&") == "" and sq.compose(guild="#") == ""


def test_compose_strips_what_a_field_cannot_hold():
    assert sq.compose(words="  a   b ", author="@al ice", guild="+pe<ts", since="2026-01-01<", has=["link", "evil"],
                      lang="en'", no_forwards=True) == "a b from:@alice in:+pets since:2026-01-01 has:link lang:en -is:forward"
    assert sq.compose() == ""


def test_the_help_page_lists_every_operator():
    help_page = (sq.__file__.rsplit("helpers", 1)[0] + "templates/help/search.html")
    with open(help_page, encoding="utf-8") as f:
        source = f.read()
    assert "{% for op in search.OPERATORS %}" in source
    assert len({op.key for op in sq.OPERATORS}) == len(sq.OPERATORS)
    assert set(sq.KEYS) >= {op.key for op in sq.OPERATORS}
