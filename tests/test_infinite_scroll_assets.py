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


def test_the_page_buttons_go_away_even_when_there_is_no_next_page():
    # the bug: the script used to return when there was no next page, before it hid the control, so a feed
    # of 25 posts or fewer (and the last page of any feed) kept a disabled Prev/Next
    assert not re.search(r"if \(!nextUrl\) return;", SCRIPT)
    assert "'IntersectionObserver' in window" in SCRIPT
    assert "if (!pagination) return;" in SCRIPT                        # a page with no paging is left alone
    assert SCRIPT.index("control.hidden = true;") < SCRIPT.index("if (nextUrl) observer.observe(sentinel); else finish();")
    # at the end the feed says so, with a way back up, instead of buttons
    assert "You\\u2019re all caught up. " in SCRIPT and "Back to top" in SCRIPT
    assert "function finish()" in SCRIPT and "finish();" in SCRIPT.split("nextUrl = nextLink(page.doc);")[1]


def test_newer_posts_come_in_at_the_top_without_repeating_what_is_there():
    # one page-1 URL for the refresh and the pill: the current address without ?page
    assert "url.searchParams.delete('page');" in SCRIPT
    prepend = SCRIPT.split("function prepend(cards)")[1].split("function refresh()")[0]
    assert "take(cards)" in prepend and "container.insertBefore(added.firstChild, first)" in prepend
    assert "bindPostCards(added)" in prepend
    # a card already on the page (by its id) is never taken, which is also what makes "N new posts" honest
    assert "document.getElementById(card.id)" in SCRIPT.split("function take(cards)")[1].split("function postCards")[0]
    assert "!document.getElementById(card.id)" in SCRIPT.split("function freshCards(cards)")[1].split("function finish()")[0]
    # nothing is refreshed on a page opened at ?page=N
    assert SCRIPT.count("startPage > 1") >= 2 and "if (startPage === 1) {" in SCRIPT


def test_the_pill_checks_in_the_background_only_while_the_tab_is_visible():
    check = SCRIPT.split("function checkNew()")[1].split("if (startPage === 1)")[0]
    assert "document.hidden" in check and "pill" in check and "refreshing" in check
    assert "window.FEED_POLL_MS || 120000" in SCRIPT                    # two minutes; the override is for tests
    assert "'Show ' + say(count)" in SCRIPT and "visibilitychange" in SCRIPT


def test_pull_to_refresh_only_starts_at_the_top_and_never_over_a_form_or_sheet():
    start = SCRIPT.split("addEventListener('touchstart'")[1].split("{ passive: true });")[0]
    assert "window.scrollY > 0" in start and "event.touches.length !== 1" in start
    assert "modal-open" in start and "input, textarea, select, [contenteditable]" in start and "#side-panel" in start
    # only touchmove may cancel the browser's own scroll, and only while pulling down at the top
    assert SCRIPT.count("{ passive: false }") == 1 and "}, { passive: false });" in SCRIPT.split("addEventListener('touchmove'")[1]
    assert "event.cancelable" in SCRIPT
    assert "document.documentElement.classList.add('feed-live')" in SCRIPT


def test_the_script_builds_its_markup_without_innerhtml():
    assert "innerHTML" not in SCRIPT


def test_both_stylesheets_style_the_pull_the_pill_and_stop_the_browsers_own_reload():
    for sheet in ("main.scss", "main_dark.scss"):
        css = (ROOT / "assets" / "style" / sheet).read_text(encoding="utf-8")
        assert re.search(r"html\.feed-live \{\s*overscroll-behavior-y: contain;", css), sheet
        for selector in ("#feed-pull {", "#feed-pull.dragging {", "#feed-pull.ready .fa-arrow-down {", ".feed-pill-wrap {", ".feed-pill {"):
            assert selector in css, (sheet, selector)
        assert "@media (prefers-reduced-motion: reduce)" in css.split("html.feed-live")[1].split("// A sound file")[0], sheet
