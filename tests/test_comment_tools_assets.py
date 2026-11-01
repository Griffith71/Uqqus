"""The writing tools of a comment box (the owner, 2026-10-30): a formatting button like Reddit's ("Aa") that opens every
formatting the body of a post has, the other tools kept but in one round, centred shape (they were bare glyphs beside grey
ovals whose glyph sat above the middle), and the Create post card's symbols. One partial draws them for the four comment
forms (templates/partials/comment_tools.html); assets/js/post_editor.js builds the formatting bar the first time it opens."""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "ruqqus"


def read(*parts):
    return APP.joinpath(*parts).read_text(encoding="utf-8")


TOOLS = read("templates", "partials", "comment_tools.html")
JS = read("assets", "js", "post_editor.js")
COMMENTS_CSS = read("assets", "style", "_comments.scss")

# each comment form, and the id of its text box as the template writes it
FORMS = [
    ("partials/comment_form.html", '"comment-form-body-" ~ p.fullname', 'id="comment-form-body-{{ p.fullname }}"'),
    ("comments.html", '"comment-form-body-" ~ c.fullname', 'id="comment-form-body-{{ c.fullname }}"'),
    ("comments.html", '"comment-edit-body-" ~ c.base36id', 'id="comment-edit-body-{{ c.base36id }}"'),
    ("comment_failed.html", '"comment-failed-body"', 'id="comment-failed-body"'),
]


@pytest.mark.parametrize("template,target,box", FORMS)
def test_every_comment_form_has_the_formatting_bar_above_its_box_and_the_tools_under_it(template, target, box):
    page = read("templates", template)
    assert '{% import "partials/comment_tools.html" as comment_tools %}' in page, template
    bar = "{{ comment_tools.format_bar(" + target + ") }}"
    tools = "{{ comment_tools.text_tools(" + target + ") }}"
    assert bar in page and tools in page, (template, target)
    # the bar comes right before its own text box, the tools after it
    assert page.index(bar) < page.index(box) < page.index(tools), (template, target)


@pytest.mark.parametrize("template", sorted({t for t, _, _ in FORMS}))
def test_no_comment_form_draws_its_own_bare_glyphs_or_grey_ovals_any_more(template):
    page = read("templates", template)
    assert '<small class="format' not in page and "<i class=\"fas fa-bold\"" not in page, template
    assert "btn btn-secondary format" not in page and "format btn btn-secondary" not in page, template
    assert "fa-film" not in page and "far fa-image" not in page and "Add EMOJI" not in page, template
    assert "&nbsp;" not in page.split('class="comment-format"', 1)[-1].split("</form>", 1)[0], template
    disclosure = read("templates", "partials", "post_options.html").split("{% macro disclosure_toggles(", 1)[1].split("{% endmacro %}", 1)[0]
    assert "btn-secondary" not in disclosure and disclosure.count('class="format{%') == 3


def test_the_tools_are_buttons_with_names_and_the_create_post_cards_symbols():
    assert 'data-comment-format aria-controls="comment-tb-{{ target }}" aria-expanded="false"' in TOOLS
    assert 'id="comment-tb-{{ target }}" data-target="{{ target }}" data-variant="comment" hidden' in TOOLS
    assert '<span class="format-text" aria-hidden="true">Aa</span>' in TOOLS
    assert '<span class="format-text" aria-hidden="true">GIF</span>' in TOOLS          # written out, as the blue row has it
    assert '<i class="fas fa-smile-beam" aria-hidden="true"></i>' in TOOLS
    for tool in re.findall(r"<button\b[^>]*>", TOOLS):
        assert 'type="button"' in tool and 'class="format' in tool and "aria-label=" in tool, tool
    # the order: Aa first, as Reddit's formatting button, then GIF and emoji
    order = [TOOLS.index(x) for x in ("data-comment-format", "getGif();", "loadEmojis()")]
    assert order == sorted(order)
    # bold, italic and quote are in the bar Aa opens, not in the row twice (the owner, 2026-10-30)
    row = TOOLS.split("{% macro text_tools(", 1)[1]
    for gone in ("makeBold(", "makeItalics(", "makeQuote(", "fa-bold", "fa-italic", "fa-quote-right"):
        assert gone not in row, gone
    for kept in ("tbButton(tr('Bold (Ctrl+B)')", "tbButton(tr('Italic (Ctrl+I)')", "tbButton(tr('Quote')"):
        assert kept in JS, kept
    for template in ("partials/comment_form.html", "comments.html"):
        page = read("templates", template)
        assert '<i class="fas fa-image" aria-hidden="true"></i>' in page and "fa-volume-up" in page, template


def test_the_comment_bar_is_the_formatting_alone_and_built_when_it_first_opens():
    branch = JS.split("if (!forComment) {", 1)[1].split("var extras = [];", 1)[0]
    assert "tbButton(tr('Undo')" in branch and "uploadStub('image'" in branch and "'post-tb-toggle'" in branch
    # the link is formatting in both editors: the first group behind "Aa", outside the post editor's own main row
    assert "Link (Ctrl+K)" not in branch.split("// A link in the text is formatting", 1)[0]
    assert JS.index("var extras = [];") < JS.index("extras.push(group(tbButton(tr('Link (Ctrl+K)')") < JS.index("dropdown(tr('Text style')")
    assert "var forComment = bar.getAttribute('data-variant') === 'comment';" in JS
    assert "if (forComment) {\n      bar.appendChild(statusNode);\n      return bindKeys();" in JS     # no Write / Preview
    assert ":not([data-variant=\"comment\"])" in JS.split("function init()", 1)[1].split("}", 1)[0]
    toggle = JS.split("closest('[data-comment-format]');\n    if (!button) return;", 1)[1].split("});", 1)[0]
    assert "build(bar);" in toggle and "bar.hidden = !open;" in toggle and "aria-expanded" in toggle
    # the post editor's own symbols match the Create post card's blue row now
    assert "withText(tbButton(tr('GIF'), null," in JS and "'fas fa-smile-beam'" in JS and "'fas fa-image'" in JS
    assert "fa-film" not in JS and "'far fa-smile-beam'" not in JS and "'far fa-image'" not in JS


def test_every_tool_in_the_row_is_one_round_centred_shape_in_the_pages_greys():
    rule = COMMENTS_CSS.split(".comment-write .comment-format .format {", 1)[1].split("}", 1)[0]
    for line in ("display: inline-flex;", "align-items: center;", "justify-content: center;", "min-width: var(--control-h);",
                 "height: var(--control-h);", "margin: 0;", "line-height: 1;", "color: var(--text-muted);",
                 "background-color: transparent;", "border-radius: var(--radius-pill);"):
        assert line in rule, line
    pressed = COMMENTS_CSS.split('.comment-format-toggle[aria-expanded="true"] {', 1)[1].split("}", 1)[0]
    assert "var(--hover)" in pressed and "--brand" not in pressed and "--link" not in pressed
    # main.scss hides the tools on a phone; the rule above comes later, so the phone's rule is said again after it
    phone = COMMENTS_CSS.split(".comment-write .comment-format .format {", 2)[2].split("}", 1)[0]
    assert "display: none;" in phone
    assert COMMENTS_CSS.rindex("@media (max-width: 767.98px)") > COMMENTS_CSS.index(".comment-write .comment-format .format {")
    section = COMMENTS_CSS.split("// --- the tools under a comment box", 1)[1]
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", section)
