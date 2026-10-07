"""The navbar lines up with the page's columns (assets/js/nav_align.js + the rule block in
main.scss / main_dark.scss): the script, the two stylesheets and the page shell must agree."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "ruqqus"
SCRIPT = (ROOT / "assets" / "js" / "nav_align.js").read_text(encoding="utf-8")
DEFAULT = (ROOT / "templates" / "default.html").read_text(encoding="utf-8")
HEADER = (ROOT / "templates" / "header.html").read_text(encoding="utf-8")
SIDEBAR = (ROOT / "templates" / "sidebar-left.html").read_text(encoding="utf-8")
STYLES = [(ROOT / "assets" / "style" / name).read_text(encoding="utf-8") for name in ("main.scss", "main_dark.scss")]

VARIABLES = ("--nav-inset-left", "--nav-inset-right", "--nav-search-left", "--nav-search-width")


def _block(css):
    start = css.index("// The navbar lines up with the page's columns")
    return css[start:css.index("\n}\n", start) + 3]


def test_the_page_shell_loads_the_script_but_not_inside_a_panel():
    assert "/assets/js/nav_align.js" in DEFAULT
    # a page shown inside a side panel frame has no navbar
    assert re.search(r"\{% if not request\.args\.get\('embed'\) %\}<script src=\"/assets/js/nav_align\.js", DEFAULT)


def test_the_script_and_both_stylesheets_use_the_same_variables():
    for name in VARIABLES:
        assert f"'{name}'" in SCRIPT, name
        for css in STYLES:
            assert f"var({name}" in _block(css), name


def test_the_default_is_the_centered_block_with_the_column_gutter():
    # the values the script replaces, in both stylesheets, inside the desktop media query
    for css in STYLES:
        block = _block(css)
        assert "@media (min-width: 992px)" in block
        assert "1326px" in block and "15px" in block
        assert "#navbar > .container-fluid" in block
        assert "margin-left: var(--nav-search-left, auto) !important" in block   # .mx-auto is !important
    assert "BLOCK = 1326" in SCRIPT and "GUTTER = 15" in SCRIPT
    assert "(min-width: 992px)" in SCRIPT


def test_the_script_reads_elements_that_exist_in_the_page():
    shell = DEFAULT + SIDEBAR
    for element_id in set(re.findall(r"getElementById\('([\w-]+)'\)", SCRIPT)):
        assert f'id="{element_id}"' in shell, element_id
    assert "navbar-search-wrap" in DEFAULT and "searchform" in DEFAULT


def test_it_follows_the_sidebar_collapse_and_the_side_panel():
    # it watches the same elements those features change; it does not change them itself
    for element_id in ("sidebar-left", "main-content-row", "side-panel"):
        assert f"'{element_id}'" in SCRIPT
    assert "side-panel" in (ROOT / "assets" / "js" / "side_panels.js").read_text(encoding="utf-8")
    assert "toggle_sidebar_collapse" not in SCRIPT
    assert "classList.add" not in SCRIPT and "classList.remove" not in SCRIPT


def test_chat_and_settings_are_as_wide_as_the_navbars_content():
    # the block less the gutter on each side: where the logo starts and the account area ends
    block = int(re.search(r"BLOCK = (\d+)", SCRIPT).group(1))
    gutter = int(re.search(r"GUTTER = (\d+)", SCRIPT).group(1))
    content = block - 2 * gutter
    assert content == 1296
    for css in STYLES:
        # not in the 726px feed group any more
        feed = re.search(r"#frontpage #main-content-col, [^{]*\{", css).group(0)
        assert "#chat #main-content-col" not in feed
        # the chat column: the navbar's content, kept on its inset on screens narrower than the block
        chat = re.search(r"#chat #main-content-col \{([^}]*)\}", css).group(1)
        assert f"max-width: {content}px" in chat and f"margin-left: {gutter}px" in chat and f"margin-right: {gutter}px" in chat
        assert re.search(r"@media \(min-width: 992px\) \{\s*#chat #main-content-col \{", css)
        # settings: 726px up to a tablet, the whole block (its 15px padding leaves the content width) on desktop
        assert re.search(r"#settings \.container \{\s*max-width: 726px;", css)
        assert re.search(rf"@media \(min-width: 992px\) \{{\s*#settings \.container \{{\s*max-width: {block}px;", css)
        # the list is a third of the wide chat frame; its width lives in the stylesheet, not inline
        assert re.search(r"#chat-list-pane \{\s*width: 250px;\s*min-width: 220px;", css)
        assert re.search(r"@media \(min-width: 1200px\) \{\s*#chat #chat-list-pane \{\s*width: 340px;", css)
        assert 'max-width: #{"min(85%, 640px)"};' in css                        # bubbles keep a readable line
    chat_page = (ROOT / "templates" / "chat.html").read_text(encoding="utf-8")
    assert 'id="chat-list-pane" class="h-100 border-right d-flex flex-column">' in chat_page


def test_the_second_navbar_gets_the_default_alignment_from_the_same_rule():
    # Create post / settings pages use header.html, which has no script: the CSS alone must reach it
    assert 'id="navbar"' in HEADER and '<div class="container-fluid">' in HEADER
