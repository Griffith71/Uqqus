"""Formatting extras in posts: text colour, highlight, alignment, button.

Authors only ever pick from fixed names; the renderer turns them into CSS
classes and the sanitizer keeps no other class (helpers/post_formatting.py)."""
import re
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

import markdown_harness as mh
from ruqqus.helpers import post_formatting as pf

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def md(monkeypatch):
    return lambda text: mh.render(monkeypatch, text)


def soup(html):
    return BeautifulSoup(html, "html.parser")


# --- text colour -------------------------------------------------------------

@pytest.mark.parametrize("name", pf.TEXT_COLORS)
def test_every_palette_colour_renders(md, name):
    assert f'<span class="tc-{name}">hi</span>' in md(f"{{c:{name}}}hi{{/c}}")


def test_colour_wraps_other_formatting_and_sits_inside_a_sentence(md):
    html = md("plain {c:red}**loud** words{/c} plain")
    assert '<span class="tc-red"><strong>loud</strong> words</span>' in html
    assert html.count("plain") == 2


def test_unknown_colour_names_stay_as_text(md):
    html = md("{c:hotpink}x{/c} and {c:red;position:fixed}y{/c}")
    assert "tc-" not in html and "{c:hotpink}x{/c}" in html


def test_colour_syntax_is_left_alone_in_code(md):
    assert "tc-red" not in md("`{c:red}x{/c}`")
    assert "tc-red" not in md("```\n{c:red}x{/c}\n```")


# --- highlight ---------------------------------------------------------------

@pytest.mark.parametrize("name", pf.HIGHLIGHT_COLORS)
def test_every_highlight_colour_renders(md, name):
    assert f'<mark class="hl-{name}">hi</mark>' in md(f"{{h:{name}}}hi{{/h}}")


def test_double_equals_is_the_default_highlight(md):
    assert '<mark class="hl-yellow">key</mark>' in md("the ==key== point")


def test_comparisons_with_spaces_are_not_highlights(md):
    html = md("if a == b == c then")
    assert "<mark" not in html and "a == b == c" in html


# --- alignment ---------------------------------------------------------------

@pytest.mark.parametrize("name", pf.ALIGNMENTS)
def test_alignment_blocks_render_with_their_content_parsed(md, name):
    html = md(f"::: {name}\nHello **world**\n\nsecond para\n:::")
    s = soup(html)
    div = s.find("div", class_=f"ta-{name}")
    assert div is not None, html
    assert div.find("strong").text == "world"
    assert [p.text for p in div.find_all("p")] == ["Hello world", "second para"]


def test_an_alignment_block_can_interrupt_a_paragraph_and_ends_at_its_closing_marker(md):
    s = soup(md("before\n::: right\ninside\n:::\nafter"))
    div = s.find("div", class_="ta-right")
    assert div and div.text.strip() == "inside"
    assert "before" in s.text and "after" in s.text
    assert "before" not in div.text and "after" not in div.text


def test_an_unclosed_alignment_block_runs_to_the_end(md):
    div = soup(md("::: center\nstill centred\n\nand this")).find("div", class_="ta-center")
    assert div and "still centred" in div.text and "and this" in div.text


def test_unknown_alignments_are_plain_text(md):
    html = md("::: justify\nx\n:::")
    assert "<div" not in html and "::: justify" in html


def test_alignment_markers_in_a_code_fence_are_code(md):
    assert "<div" not in md("```\n::: center\nx\n:::\n```")


def test_alignment_wraps_lists_and_quotes(md):
    s = soup(md("::: center\n- one\n- two\n\n> quoted\n:::"))
    div = s.find("div", class_="ta-center")
    assert [li.text for li in div.find_all("li")] == ["one", "two"] and div.find("blockquote")


# --- button ------------------------------------------------------------------

def test_a_link_followed_by_button_becomes_a_button(md):
    a = soup(md("[Read more](https://example.com/page){.button}")).find("a")
    assert a["href"] == "https://example.com/page" and a.get("class") == ["post-button"]
    assert a.text == "Read more" and "{.button}" not in a.parent.text
    assert "nofollow" in a["rel"]          # external links keep the safety attributes


def test_a_plain_link_is_not_a_button(md):
    a = soup(md("[Read more](https://example.com/page)")).find("a")
    assert a.get("class") is None


def test_a_button_cannot_carry_a_script_address(md):
    for bad in ("javascript:alert(1)", "data:text/html,<script>alert(1)</script>", "vbscript:x"):
        html = md(f"[Click]({bad}){{.button}}")
        a = soup(html).find("a")
        assert a is None or not (a.get("href") or "").lower().startswith(("javascript:", "data:", "vbscript:")), html


def test_button_syntax_in_code_is_left_alone(md):
    assert "post-button" not in md("`[x](https://a.com){.button}`")


# --- the sanitizer keeps only the allowed classes ----------------------------

def test_foreign_classes_are_dropped_from_every_tag(md):
    s = soup(md(
        '<div class="ta-center d-none fixed-top">a</div>'
        '<span class="tc-red fa-spin">b</span>'
        '<mark class="hl-blue badge">c</mark>'
        '<a class="post-button btn btn-danger" href="https://example.com">d</a>'
    ))
    assert s.find("div")["class"] == ["ta-center"]
    assert s.find("span")["class"] == ["tc-red"]
    assert s.find("mark")["class"] == ["hl-blue"]
    assert s.find("a")["class"] == ["post-button"]


@pytest.mark.parametrize("html", [
    '<div class="d-none">x</div>',
    '<span class="tc-nope">x</span>',
    '<mark class="highlight">x</mark>',
    '<a class="btn btn-primary" href="https://example.com">x</a>',
])
def test_a_tag_with_only_foreign_classes_ends_up_with_none(md, html):
    tag = soup(md(html)).find(["div", "span", "mark", "a"])
    assert tag.get("class") is None


def test_style_attributes_and_event_handlers_never_survive(md):
    html = md('<div class="ta-center" style="position:fixed;top:0" onclick="alert(1)">x</div>'
              '<mark style="background:url(x)" onmouseover="alert(1)">y</mark>')
    assert "style=" not in html and "onclick" not in html and "onmouseover" not in html


def test_script_inside_a_colour_span_is_removed(md):
    html = md("{c:red}<script>alert(1)</script>boo{/c}")
    assert "<script" not in html and "tc-red" in html


def test_spoilers_keep_working(md):
    assert '<span class="spoiler">secret</span>' in md("||secret||")


def test_the_mention_link_classes_are_unchanged_from_before(md):
    # no such user in the harness, so this only checks the plain-text fallback
    assert "<a" not in md("hello @nobody_here")


# --- the palette is defined once and agrees everywhere ----------------------

def _scss(name):
    return (ROOT / "ruqqus" / "assets" / "style" / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("sheet", ["main.scss", "main_dark.scss"])
def test_every_class_has_a_rule_in_both_stylesheets(sheet):
    css = _scss(sheet)
    classes = [f"tc-{n}" for n in pf.TEXT_COLORS] + [f"hl-{n}" for n in pf.HIGHLIGHT_COLORS] + \
              [f"ta-{n}" for n in pf.ALIGNMENTS] + [pf.BUTTON_CLASS]
    for cls in classes:
        assert re.search(rf"\.{re.escape(cls)}\b", css), f"{sheet} has no rule for .{cls}"
