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


def test_both_forms_have_a_toolbar_a_preview_pane_and_the_script():
    submit = (ROOT / "templates" / "submit.html").read_text(encoding="utf-8")
    assert 'class="post-toolbar" data-target="post-body"' in submit
    assert 'class="post-preview d-none"' in submit and "post_editor.js" in submit

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
