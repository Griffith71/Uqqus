"""Addresses of posts and comments as an extension of whoever owns them (ruqqus/helpers/permalinks.py)."""
import re
from pathlib import Path

import pytest

from ruqqus.helpers import permalinks as pl

ROOT = Path(__file__).resolve().parent.parent


def read(*parts):
    return (ROOT / "ruqqus").joinpath(*parts).read_text(encoding="utf-8")


def func(src, name):
    start = src.index(f"def {name}(")
    nxt = re.search(r"\n(?:@|def |class )", src[start + 1:])
    return src[start:start + 1 + nxt.start()] if nxt else src[start:]


# --- the forms -----------------------------------------------------------------------------------------------

def test_a_post_reads_as_an_extension_of_its_author_and_a_copy_of_its_guild_and_forwarder():
    assert pl.post_path("abc", name="alice") == "/@alice/post/abc"
    assert pl.post_path("abc", name="carol", guild="pets") == "/+pets/@carol/post/abc"


def test_a_comment_reads_as_an_extension_of_its_own_author():
    assert pl.comment_path("x1", name="bob") == "/@bob/comment/x1"


def test_an_anonymous_item_never_carries_a_name_whatever_it_is_given():
    assert pl.post_path("abc", name="alice", anonymous=True) == "/post/abc"
    # a copy leaves out its forwarder too, even someone else: naming only other forwarders would mark the author's own
    assert pl.post_path("abc", name="carol", guild="pets", anonymous=True) == "/+pets/post/abc"
    assert pl.comment_path("x1", name="bob", anonymous=True) == "/comment/x1"


def test_no_address_has_a_title_slug():
    for path in (pl.post_path("abc", name="alice"), pl.post_path("abc", name="c", guild="g"),
                 pl.comment_path("x", name="b")):
        assert path.rsplit("/", 1)[-1] in ("abc", "x")


# --- reading them back -------------------------------------------------------------------------------------

@pytest.mark.parametrize("path, found", [
    ("/@alice/post/abc", ("post", "abc")),
    ("/+pets/@carol/post/abc/", ("post", "abc")),
    ("/post/abc", ("post", "abc")),
    ("/+pets/post/abc", ("post", "abc")),
    ("/@bob/comment/x1", ("comment", "x1")),
    ("/comment/x1", ("comment", "x1")),
    # the older forms, which stored post text keeps
    ("/post/abc/some-title", ("post", "abc")),
    ("/+pets/post/abc/some-title/", ("post", "abc")),
    ("/post/abc/some-title/x1", ("comment", "x1")),
    ("/+pets/post/abc/-/x1", ("comment", "x1")),
    ("https://example.com/@alice/post/ABC?sort=new#c", ("post", "abc")),
    ("/post/history", ("post", "history")),            # an id that is also a word is still a post
])
def test_every_form_old_and_new_reads_back(path, found):
    assert pl.parse(path) == found


@pytest.mark.parametrize("path", [
    "/@alice", "/+pets", "/post/abc/history", "/post/abc/insights", "/post/abc/space/pets", "/post/abc/revisions/3",
    "/+pets/post/abc/history", "/@alice/posts", "/topic/news", "", None,
])
def test_other_pages_are_not_posts_or_comments(path):
    assert pl.parse(path) is None


def test_same_path_ignores_a_trailing_slash_only():
    assert pl.same_path("/@alice/post/abc/", "/@alice/post/abc")
    assert not pl.same_path("/@bob/post/abc", "/@alice/post/abc")
    assert not pl.same_path("/post/abc", "/@alice/post/abc")


# --- the models and routes --------------------------------------------------------------------------------

def test_the_models_decide_anonymity_before_any_name():
    post = func(read("classes", "submission.py"), "permalink")
    assert "anonymous = bool(self.is_anonymous)" in post
    assert "name = None\n        if not anonymous:" in post
    # a copy names whoever forwarded it (a comment's forwarder first), else its author
    assert "self.comment_forwarded_by or self.forwarded_by or self.author" in post
    comment = func(read("classes", "comment.py"), "permalink")
    assert "name=None if anonymous else self.author.username" in comment
    assert "self.post.permalink" not in comment


def test_every_address_is_redirected_only_after_the_page_s_own_checks():
    """A redirect to the canonical address names the author: it must never happen for something the reader may not
    open (a Circle post raises in get_post, then the private guild, hidden and banned checks)."""
    posts = read("routes", "posts.py")
    page = func(posts, "post_base36id_with_comments")
    for route in ('"/@<username>/post/<pid>"', '"/+<boardname>/@<username>/post/<pid>"', '"/post/<pid>/<anything>"',
                  '"/+<boardname>/post/<pid>/<anything>"', '"/post_short/<pid>"'):
        assert route in posts, route
    order = [page.index(x) for x in ("get_post_with_comments(", "in_private_guild_for(v)", "post_hidden(post, v)",
                                     "board.is_banned", "canonical_redirect(post)")]
    assert order == sorted(order)
    comments = read("routes", "comments.py")
    page = func(comments, "post_pid_comment_cid")
    for route in ('"/@<username>/comment/<cid>"', '"/comment/<cid>"', '"/post/<pid>/<anything>/<cid>"'):
        assert route in comments, route
    order = [page.index(x) for x in ("get_comment(cid, v=v)", "in_private_guild_for(v)", "comment_thread_hidden(",
                                     "board.is_banned", "canonical_redirect(comment)")]
    assert order == sorted(order)
    redirect = func(posts, "canonical_redirect")
    assert 'request.path.startswith("/api/")' in redirect and "code=301" in redirect
    assert "request.query_string" in redirect                     # ?sort= and ?context= survive


def test_the_readers_of_addresses_use_the_one_parser():
    assert "permalinks.parse(parts.path)" in read("helpers", "quote_cards.py")
    assert "permalinks.parse(link)" in func(read("helpers", "get.py"), "get_from_permalink")
    assert "permalinks.parse(self.url)" in func(read("classes", "submission.py"), "links_to_post")
    # a mention is the bare /@name: a link to someone's post names nobody and isn't skipped by the word filter
    for name in ("posts.py", "comments.py"):
        assert 'href=re.compile(r"^/@(\\w+)/?$")' in read("routes", name), name
