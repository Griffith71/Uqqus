"""The post/comment markdown pipeline: text preprocessing and the sanitizer."""
import re
from html import escape

import pytest
from bs4 import BeautifulSoup

import markdown_harness as mh


@pytest.fixture
def md(monkeypatch):
    return lambda text, **kw: mh.render(monkeypatch, text, **kw)


@pytest.fixture
def preprocess(monkeypatch):
    return mh.load(monkeypatch)[0].preprocess


# --- preprocess ------------------------------------------------------------

def test_a_column_of_single_words_is_kept_as_one_line(preprocess):
    out = preprocess("intro\napple\nbanana\ncherry\ndone with it")
    # the words used to be deleted outright
    for word in ("apple", "banana", "cherry"):
        assert word in out
    assert "\napple\nbanana" not in out   # still not stacked into a tall column
    assert out.endswith("done with it")


def test_two_single_word_lines_are_left_alone(preprocess):
    assert preprocess("intro\nsome\nwords") == "intro\nsome\nwords"


def test_fenced_code_is_never_altered(preprocess):
    code = "```\nimport\nos\nsys\nprint\n```"
    assert preprocess("before\n\n" + code + "\n\nafter") == "before\n\n" + code + "\n\nafter"
    tilde = "~~~\nimport\nos\nsys\n~~~"
    assert preprocess(tilde) == tilde


def test_an_unclosed_fence_protects_the_rest_of_the_text(preprocess):
    text = "```\nimport\nos\nsys\nprint"
    assert preprocess(text) == text


def test_words_after_a_fence_stay_on_their_own_line_after_it(preprocess):
    out = preprocess("```\ncode\n```\none\ntwo\nthree\nfour")
    assert out.startswith("```\ncode\n```\n")   # the closing fence is still on its own line
    assert "one two three four" in out


def test_zero_width_characters_are_still_removed(preprocess):
    assert preprocess("a​b‌c‍d") == "abcd"


def test_code_blocks_render_with_all_their_lines(md):
    html = md("```\nimport\nos\nsys\nprint\n```")
    for word in ("import", "os", "sys", "print"):
        assert word in html


# --- line breaks -----------------------------------------------------------
# The owner (2026-10-30): the writing box has to look like the posted post, so a single Enter in a post or a comment is a
# line break as typed (`breaks=True`: routes/posts.py, routes/comments.py and /api/preview). The wiki, bios, descriptions
# and notices keep standard markdown, where the lines of a paragraph are joined.

@pytest.fixture
def breaks(monkeypatch):
    return lambda text: mh.render(monkeypatch, text, breaks=True)


def test_a_single_enter_is_a_line_break_in_a_post_or_a_comment(breaks):
    html = breaks("first line\nsecond line\nthird line")
    assert html.count("<br") == 2 and html.count("<p>") == 1
    soup = BeautifulSoup(html, "html.parser")
    assert [t.strip() for t in soup.p.stripped_strings] == ["first line", "second line", "third line"]


def test_a_single_enter_is_still_joined_everywhere_else(md):
    html = md("first line\nsecond line")
    assert "<br" not in html and "first line\nsecond line" in html


def test_a_blank_line_is_still_a_new_paragraph_and_adds_no_break(breaks):
    html = breaks("one\n\ntwo")
    assert html.count("<p>") == 2 and "<br" not in html


def test_a_hard_break_is_one_break_not_two(breaks):
    assert breaks("one  \ntwo").count("<br") == 1
    assert breaks("one\\\ntwo").count("<br") == 1


def test_lists_headings_quotes_and_code_get_no_stray_breaks(breaks):
    assert "<br" not in breaks("- a\n- b\n- c")
    assert "<br" not in breaks("# Title\nbody")
    assert "<br" not in breaks("```\nline one\nline two\n```")
    quote = breaks("> first\n> second")
    assert quote.count("<br") == 1 and "<blockquote>" in quote


def test_the_one_word_column_is_still_kept_as_one_line_with_breaks_on(breaks):
    html = breaks("intro\napple\nbanana\ncherry\ndone with it")
    assert "apple banana cherry" in html


# --- image links -----------------------------------------------------------

def _onclicks(html):
    return [a.get("onclick") for a in BeautifulSoup(html, "html.parser").find_all("a") if a.get("onclick")]


# one well-formed call with ONE JS string argument and nothing else
SAFE_CALL = re.compile(r'^expandDesktopImage\("(?:[^"\\]|\\.)*"\);$')


@pytest.mark.parametrize("src", [
    "/x'-alert(1)-'",
    '/x"-alert(1)-"',
    "/x\\'-alert(1)//",
    "/a</script><script>alert(1)",
    "/plain/path.png",
])
def test_the_image_expand_handler_cannot_be_broken_out_of(md, src):
    html = md(f'<img src="{escape(src)}">')   # escaped so the HTML is valid: the parser sees `src` as written
    handlers = _onclicks(html)
    assert len(handlers) == 1, html
    assert SAFE_CALL.match(handlers[0]), handlers[0]


def test_markdown_image_syntax_cannot_inject_script(md):
    html = md("![](/x'-alert(1)-')")
    handlers = _onclicks(html)
    assert handlers and all(SAFE_CALL.match(h) for h in handlers), handlers


def test_the_expand_handler_still_receives_the_image_address(md):
    import json

    handlers = _onclicks(md('<img src="/some/where.png">'))
    assert json.loads(handlers[0][len("expandDesktopImage("):-2]) == "/some/where.png"


def test_images_from_unlisted_hosts_become_links(md):
    html = md('<img src="https://evil.example/x.png">')
    assert "<img" not in html and 'href="https://evil.example/x.png"' in html
