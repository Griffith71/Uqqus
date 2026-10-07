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


def test_the_second_navbar_gets_the_default_alignment_from_the_same_rule():
    # Create post / settings pages use header.html, which has no script: the CSS alone must reach it
    assert 'id="navbar"' in HEADER and '<div class="container-fluid">' in HEADER
