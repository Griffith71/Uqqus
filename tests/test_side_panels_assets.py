"""The navbar side panels (assets/js/side_panels.js): the navbar icons, the panel host,
the pages shown in it (?embed=1) and the rule that only the site itself may frame them."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "ruqqus"
DEFAULT = (ROOT / "templates" / "default.html").read_text(encoding="utf-8")
PANELS_JS = (ROOT / "assets" / "js" / "side_panels.js").read_text(encoding="utf-8")
EMBEDDED_JS = (ROOT / "assets" / "js" / "embedded.js").read_text(encoding="utf-8")
MAIN = (ROOT / "__main__.py").read_text(encoding="utf-8")
CHAT = (ROOT / "templates" / "chat.html").read_text(encoding="utf-8")
STYLES = [(ROOT / "assets" / "style" / name).read_text(encoding="utf-8") for name in ("main.scss", "main_dark.scss")]


def test_each_panel_has_a_navbar_icon_and_a_page_to_show():
    triggers = set(re.findall(r'data-side-panel="(\w+)"', DEFAULT))
    configured = set(re.findall(r"^\s{4}(\w+): \{ title:", PANELS_JS, re.M))
    assert triggers == configured == {"notifications", "chat"}
    # the bell exists in two forms (unread / all read): both open the panel
    assert DEFAULT.count('data-side-panel="notifications"') == 2


def test_the_panel_pages_are_the_real_pages_in_embed_mode():
    for src in re.findall(r"src: '([^']+)'", PANELS_JS):
        assert src.endswith("?embed=1"), src
    assert "/chat?embed=1" in PANELS_JS and "/notifications?embed=1" in PANELS_JS
    assert "/composer" not in PANELS_JS and "post:" not in PANELS_JS         # Create post is the feed's own composer


def test_every_element_the_script_uses_is_in_the_page_shell():
    shell = DEFAULT + (ROOT / "templates" / "sidebar-left.html").read_text(encoding="utf-8")
    for element_id in re.findall(r"getElementById\('([\w-]+)'\)", PANELS_JS):
        assert f'id="{element_id}"' in shell, element_id


def test_the_panel_widens_into_the_folded_sidebars_room_and_folds_it_only_when_needed():
    # the owner (2026-10-21): the panel is the right sidebar's width plus the 207px the left sidebar gives up when it
    # folds, whenever the window holds the 1326px block with the rail; the left sidebar folds only while there is no
    # room for it, and only while the panel is open
    assert "var LEFT_OPEN = 272, RAIL = 65, FEED = 738, RIGHT = 316, GIVEN_UP = 207, GUTTERS = 30;" in PANELS_JS
    assert "var WIDE_PANEL = RAIL + FEED + RIGHT + GIVEN_UP + GUTTERS;" in PANELS_JS
    assert "var room = LEFT_OPEN + FEED + (widePanel ? RIGHT + GIVEN_UP : RIGHT) + GUTTERS;" in PANELS_JS
    assert "if (width < room) fold(); else unfold();" in PANELS_JS
    # never the member's own choice: the saved preference is not touched, and a sidebar they folded stays folded
    assert "set_sidebar_pref" not in PANELS_JS and "sidebar_pref" not in PANELS_JS.split("*/", 1)[1]
    assert "toggle_sidebar_collapse" not in PANELS_JS and "toggle_sidebar_expand" not in PANELS_JS
    end = chr(10) + "  }"
    fold = PANELS_JS.split("function fold() {", 1)[1].split(end, 1)[0]
    assert "sidebar.classList.contains('sidebar-collapsed')) return;" in fold
    close = PANELS_JS.split("function close() {", 1)[1].split(end, 1)[0]
    assert "fit();" in close
    fit = PANELS_JS.split("function fit() {", 1)[1].split(end, 1)[0]
    assert "unfold();" in fit and "html.classList.remove('panel-wide');" in fit
    for css in STYLES:
        assert "html.panel-wide #main-content-row.panel-open > #side-panel {" in css
        assert "flex: 0 0 calc(#{$layout-right} + 207px);" in css


def test_it_is_desktop_only_and_never_hijacks_a_modified_click():
    assert "(min-width: 992px)" in PANELS_JS
    assert "event.metaKey" in PANELS_JS and "event.ctrlKey" in PANELS_JS
    for css in STYLES:
        assert "#main-content-row.panel-open > .sidebar:not(#sidebar-left)" in css
        # the panel is only displayed inside the desktop media query
        assert re.search(r"#side-panel \{\s*display: none;", css)


def test_embedded_pages_have_no_chrome_and_open_links_in_the_full_window():
    assert '<base target="_top">' in DEFAULT
    assert "embedded" in DEFAULT
    assert "embedded.js" in DEFAULT
    assert "'.settings-nav, .pagination'" in EMBEDDED_JS and "'_self'" in EMBEDDED_JS
    for css in STYLES:
        assert "body.embedded #navbar" in css


def test_only_the_site_itself_may_frame_the_panel_pages():
    assert re.search(r'request\.args\.get\("embed"\) and request\.path\.startswith\(\("/notifications", "/chat"\)\)', MAIN)
    assert '"sameorigin"' in MAIN and '"deny"' in MAIN
    # the page that frames a panel is never itself embedded
    assert "not request.args.get('embed')" in DEFAULT


def test_chat_shows_the_list_or_the_conversation_on_a_narrow_screen():
    for element_id in ("chat-mobile-bar", "chat-back"):
        assert f'id="{element_id}"' in CHAT
    assert "chat-has-room" in CHAT
    for css in STYLES:
        assert "#chat #chat-app.chat-has-room #chat-thread-pane" in css
