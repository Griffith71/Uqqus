"""The post editor toolbar (assets/js/post_editor.js) and the things it has to
agree with: the fixed formatting names, the stylesheets and the two forms."""
import re
from pathlib import Path

import pytest

from ruqqus.helpers import post_formatting as pf

ROOT = Path(__file__).resolve().parent.parent / "ruqqus"
JS = (ROOT / "assets" / "js" / "post_editor.js").read_text(encoding="utf-8")


def js_list(name):
    m = re.search(rf"var {name} = \[(.*?)\];", JS, re.S)
    assert m, name
    return re.findall(r"'([^']+)'", m.group(1))


def test_the_toolbar_offers_exactly_the_names_the_server_accepts():
    assert tuple(js_list("TEXT_COLORS")) == pf.TEXT_COLORS
    assert tuple(js_list("HIGHLIGHTS")) == pf.HIGHLIGHT_COLORS
    assert tuple(js_list("ALIGNMENTS")) == pf.ALIGNMENTS


@pytest.mark.parametrize("sheet", ["main.scss", "main_dark.scss"])
def test_every_toolbar_class_the_script_uses_is_styled_in_both_stylesheets(sheet):
    css = (ROOT / "assets" / "style" / sheet).read_text(encoding="utf-8")
    used = set(re.findall(r"'((?:post-tb-|post-)[a-z-]+)", JS)) | {"post-toolbar", "post-preview"}
    # class names the script writes (post-tb-* and the two panes), not file or url fragments
    used = {c for c in used if c.startswith(("post-tb-", "post-toolbar", "post-preview"))}
    missing = sorted(c for c in used if not re.search(rf"\.{re.escape(c)}\b", css))
    assert not missing, f"{sheet} has no rule for {missing}"


def test_all_three_forms_have_a_toolbar_a_preview_pane_and_the_script():
    submit = (ROOT / "templates" / "submit.html").read_text(encoding="utf-8")
    assert 'class="post-toolbar" data-target="post-body"' in submit
    assert 'class="post-preview d-none"' in submit and "post_editor.js" in submit

    inline = (ROOT / "templates" / "partials" / "inline_composer.html").read_text(encoding="utf-8")
    assert 'class="post-toolbar" data-target="ic-body"' in inline and 'class="post-preview d-none"' in inline
    default = (ROOT / "templates" / "default.html").read_text(encoding="utf-8")
    assert "asset_url('js/post_editor.js')" in default       # the feed composer's page loads it
    assert "if (window.PostEditor) return;" in JS             # and it is safe to load twice (the edit form's own tag)

    page = (ROOT / "templates" / "submission.html").read_text(encoding="utf-8")
    assert 'class="post-toolbar" data-target="post-edit-box-{{ p.base36id }}"' in page
    assert 'class="post-preview d-none"' in page and "post_editor.js" in page


def test_the_placeholder_buttons_can_be_switched_on_by_an_uploader():
    for kind in ("image", "audio", "video"):
        assert f"uploadStub('{kind}'" in JS
    assert "register: function (kind, handler)" in JS


def test_the_toolbar_never_builds_html_from_user_text():
    # the only innerHTML is the server-rendered preview; names and labels go through textContent
    assert JS.count("innerHTML") == 1 and "area.innerHTML = res.data.html" in JS


def test_undo_redo_then_what_goes_into_the_text_then_the_folded_formatting():
    # the owner (2026-10-21): Reddit's order, the formatting folded behind "Aa" at the top of the box
    order = [JS.index(x) for x in ("tbButton('Undo'", "tbButton('Redo'", "uploadStub('image'", "uploadStub('video'",
                                   "uploadStub('audio'", "tbButton('GIF'", "tbButton('Emoji'", "tbButton('Link (Ctrl+K)'",
                                   "'post-tb-toggle'", "dropdown('Text style'")]
    assert order == sorted(order)
    assert "extras.forEach(function (g) { g.classList.add('post-tb-extra'); });" in JS
    assert "setFolded(!startOpen, false);" in JS and "localStorage" in JS and "try {" in JS
    more = JS.split("dropdown('More'", 1)[1].split("})\n    ));", 1)[0]
    assert "'Emoji'" not in more and "'GIF'" not in more          # moved to the front, not twice
    assert "use: function (kind, textarea)" in JS and "'posteditor:register'" in JS


def test_a_gif_from_the_blue_row_is_the_posts_link():
    js = (ROOT / "assets" / "js" / "all_js.js").read_text(encoding="utf-8")
    gif = js.split("function insertGIF(url,form) {", 1)[1].split("\n  }", 1)[0]
    assert "target.hasAttribute('data-attach-link')" in gif and "target.value = url;" in gif
