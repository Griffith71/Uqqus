"""Comment search, the rules that need no database (helpers/search_comments.py): which operators apply to a comment, what a
search must hold to run. The conditions on real data are tests/pg/test_comment_search.py."""
import pytest

from ruqqus.helpers import search_comments, search_query


def prepared(text):
    return search_comments.prepare(search_query.parse(text))


def kinds(query):
    return [p.kind for p in query.parts]


def test_the_operators_that_describe_a_comment_are_kept():
    query, notes = prepared('"vet bill" from:@alice in:+pets since:2026-01-01 min_votes:3 is:ai -is:partnership')
    assert kinds(query) == ["phrase", "from", "in", "since", "min_votes", "is", "is"]
    assert notes == []


@pytest.mark.parametrize("text,note", [
    ("cats has:image", "has: is not used when searching comments."),
    ("cats lang:en", "lang: is not used when searching comments."),
    ("cats domain:example.com", "domain: is not used when searching comments."),
    ("cats url:example.com/x", "url: is not used when searching comments."),
    ('cats flair:"Question"', "flair: is not used when searching comments."),
    ("cats min_comments:5", "min_comments: is not used when searching comments."),
    ("cats -is:forward", "is:forward is not used when searching comments."),
])
def test_operators_about_a_posts_own_content_are_dropped_with_a_notice(text, note):
    query, notes = prepared(text)
    assert kinds(query) == ["word"] and notes == [note]


def test_the_same_notice_is_given_once():
    _, notes = prepared("cats has:image has:video")
    assert notes == ["has: is not used when searching comments."]


@pytest.mark.parametrize("text", ["cat", "cats", '"ab c"', "from:@alice", "in:+pets", "ab from:@alice", "-dog cats"])
def test_a_search_that_narrows_itself_runs(text):
    assert search_comments.problem(prepared(text)[0]) is None


@pytest.mark.parametrize("text", ["", "ab", "a", "-cats", "-from:@alice", "since:2026-01-01", "min_votes:5", "-in:+pets ab", "is:ai"])
def test_a_search_that_would_read_every_comment_does_not(text):
    assert search_comments.problem(prepared(text)[0]) == search_comments.TOO_BROAD


def test_the_broad_search_notice_names_the_way_out():
    assert "at least 3 letters" in search_comments.TOO_BROAD and "from:" in search_comments.TOO_BROAD


def test_a_positive_from_is_going_to_someones_comments_so_mutes_do_not_apply():
    assert prepared("from:@alice cats")[0].for_one_author is True
    assert prepared("-from:@alice cats")[0].for_one_author is False
    assert prepared("cats")[0].for_one_author is False


def test_comment_text_is_searched_in_the_body_column_the_trigram_index_covers():
    source = open("ruqqus/helpers/search_comments.py", encoding="utf-8").read()
    assert "CommentAux.body.ilike" in source and "body_html" not in source.replace("never `body_html`", "")
