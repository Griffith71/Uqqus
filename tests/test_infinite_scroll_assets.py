"""Infinite scroll (assets/js/infinite_scroll.js) reads the server-rendered Prev/Next
control and the post list of each feed page. These checks keep the script, the page
shell, all_js.js and the feed templates agreeing."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "ruqqus"
SCRIPT = (ROOT / "assets" / "js" / "infinite_scroll.js").read_text(encoding="utf-8")
ALL_JS = (ROOT / "assets" / "js" / "all_js.js").read_text(encoding="utf-8")
TEMPLATES = ROOT / "templates"

# every template that renders a feed's own Prev/Next control
FEEDS = ("home.html", "userpage.html", "search.html", "history.html")


def test_the_page_shell_loads_the_script():
    assert "/assets/js/infinite_scroll.js" in (TEMPLATES / "default.html").read_text(encoding="utf-8")


def test_the_script_uses_the_markers_the_feed_templates_render():
    assert "nav[aria-label=\"Page navigation\"]" in SCRIPT and "ul.pagination a.page-link" in SCRIPT
    assert "'Next'" in SCRIPT
    for name in FEEDS:
        html = (TEMPLATES / name).read_text(encoding="utf-8")
        assert 'aria-label="Page navigation"' in html, name
        assert 'class="pagination' in html, name
        assert re.search(r">\s*Next\s*<", html), f"{name} must label its next-page link exactly 'Next'"
        assert 'class="posts' in html, name


def test_all_js_can_wire_up_cards_added_after_load():
    assert "function bindPostCards(root)" in ALL_JS
    # voting is bound for the new cards only (its keydown handlers are anonymous and would double-fire)
    assert "var register_votes = function(root)" in ALL_JS
    assert "register_votes(root)" in ALL_JS
    for handler in ("onHideButtonListingClick", "onExpandableImageClick", "onTextExpandClick"):
        assert f"function {handler}(" in ALL_JS
        assert ALL_JS.count(handler) >= 3, f"{handler} must be bound at load and by bindPostCards"
    # the script hands the new cards over, and skips markup the page already has
    assert "bindPostCards(added)" in SCRIPT
    assert "document.getElementById(card.id)" in SCRIPT


def test_the_script_does_nothing_without_a_next_page():
    assert re.search(r"if \(!nextUrl\) return;", SCRIPT)
    assert "'IntersectionObserver' in window" in SCRIPT
