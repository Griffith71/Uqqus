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
