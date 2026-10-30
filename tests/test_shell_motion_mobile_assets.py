"""The page shell after the UI modernisation (2026-10): the navbar and sidebars, motion that only answers what the
person did, and the phone (zoom allowed, the screen's edges, the bottom bar, theme-color, heights that follow the
visible screen)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "ruqqus"
STYLE = ROOT / "assets" / "style"
NAV = (STYLE / "_nav.scss").read_text(encoding="utf-8")
MOTION = (STYLE / "_motion.scss").read_text(encoding="utf-8")
MOBILE = (STYLE / "_mobile.scss").read_text(encoding="utf-8")
JS = (ROOT / "assets" / "js" / "all_js.js").read_text(encoding="utf-8")
SCROLL = (ROOT / "assets" / "js" / "infinite_scroll.js").read_text(encoding="utf-8")
DEFAULT = (ROOT / "templates" / "default.html").read_text(encoding="utf-8")
BAR = (ROOT / "templates" / "mobile_navigation_bar.html").read_text(encoding="utf-8")
THEME = (ROOT / "templates" / "partials" / "theme.html").read_text(encoding="utf-8")
SIDEBAR = (ROOT / "templates" / "sidebar-left.html").read_text(encoding="utf-8")


def test_the_page_never_turns_zoom_off():
    for path in (ROOT / "templates").rglob("*.html"):
        if path.name == "502.html":          # served by the proxy when the app is down
            continue
        html = path.read_text(encoding="utf-8")
        assert "user-scalable=no" not in html and "maximum-scale" not in html, path.name
    assert DEFAULT.count('<meta name="viewport"') == 1
    assert 'content="width=device-width, initial-scale=1, viewport-fit=cover"' in DEFAULT


def test_the_browser_bar_takes_the_pages_colour():
    assert "{% macro theme_color() %}" in THEME and 'content="#000000" media="(prefers-color-scheme: dark)"' in THEME
    assert "{{ theme_color() }}" in DEFAULT and "<meta name=\"theme-color\" content=\"#2ca1ea\">" not in DEFAULT
    assert 'metas[0].setAttribute("content", theme == "dark" ? "#000000" : "#ffffff");' in JS


def test_the_phone_navbar_is_one_row_and_the_sidebar_shows_the_feed_being_read():
    phone = NAV.split("@media (max-width: 767.98px) {")[1]
    assert "flex-wrap: nowrap;" in phone
    assert SIDEBAR.count('aria-current="page"') == 6
    assert '#sidebar-left .guild-recommendations-item > a[aria-current="page"]' in NAV


def test_the_right_sidebar_boxes_are_panels_without_the_blue_bar():
    title = NAV.split("#main-content-row > .sidebar:not(.sidebar-left) .sidebar-section .title {")[1].split("}")[0]
    assert "background-color: transparent;" in title and "text-transform: none;" in title


def test_motion_only_answers_what_the_person_did():
    assert MOTION.lstrip().startswith("//")
    animated = MOTION.split("@media (prefers-reduced-motion: no-preference) {")[1].split("\n}\n")[0]
    for name in ("vote-pop", "menu-in", "card-arrive"):
        assert f"@keyframes {name}" in animated, name
    # a vote pops from a click only (all_js.js popVote), so a page that loads with votes cast stays still
    assert JS.count("popVote(thisUpvoteButton)") == 2 and JS.count("popVote(thisDownvoteButton)") == 2
    # the bottom bar hides with a class (a transform), not by moving its edge
    assert 'bottomBar.classList.toggle("is-hidden", !show);' in JS and "bottomBar.style.bottom" not in JS
    assert "transform: translateY(calc(100% + 1px));" in MOTION
    # only cards that arrived by infinite scroll fade in
    assert "$(root).children('.card').addClass('is-arriving')" in JS
    for duration in re.findall(r"(\d+)ms", MOTION):
        assert int(duration) <= 300, duration


def test_loading_more_shows_the_shape_of_what_is_coming():
    assert "function showSkeletons() {" in SCROLL and "sentinel.setAttribute('aria-busy', 'true');" in SCROLL
    assert SCROLL.count("sentinel.removeAttribute('aria-busy');") == 3


def test_the_bottom_bar_marks_the_page_and_keeps_clear_of_the_home_indicator():
    assert 'id="mobile-bottom-navigation-bar" aria-label="{{ pgettext(\'navigation landmark\', \'Main\') }}"' in BAR
    assert "aria-current" in BAR
    assert "transition: bottom" not in BAR
    bar = MOBILE.split("#mobile-bottom-navigation-bar {")[1].split("}")[0]
    assert "env(safe-area-inset-bottom)" in bar


def test_full_height_panels_follow_the_visible_screen_at_their_own_widths():
    dvh = MOBILE.split("@supports (height: 100dvh) {")[1]
    # the sign-in page's own menu: overriding this at every width once covered the page
    phone = dvh.split("@media (max-width: 767.98px) {")[1].split("}\n    }")[0]
    assert "#navbarResponsive .navbar-nav {" in phone
    assert dvh.split("@media (max-width: 767.98px) {")[0].count("#navbarResponsive") == 0


def test_the_navbar_lines_up_with_the_sidebars_on_desktop_and_keeps_off_a_phones_edges():
    # from 992px nav_align.js insets the bar's content to the sidebars' edges: the bar itself adds only the safe area
    bar = MOBILE.split("#navbar {\n    padding-top")[1].split("}")[0]
    assert "padding-left: env(safe-area-inset-left);" in bar and "max(1rem" not in bar
    phone = MOBILE.split("@media (max-width: 991.98px) {\n    #navbar {")[1].split("}")[0]
    assert 'max(1rem, env(safe-area-inset-left))' in phone
    icons = NAV.split("@media (min-width: 768px) {\n    #navbar #navbar-icon-row {")[1].split("}")[0]
    assert "gap: 0;" in icons            # (the glyphs grew to 20px in the same 36px buttons: the white between them fell from 24px to 16)


def test_the_cooldown_meter_is_one_vertical_boiling_bar():
    # the owner (2026-10-23): vertical, thicker, at the far right of the account block, blue low to red high, boiling.
    # One macro for the navbar's meter, in both page shells; the old Bootstrap bars are gone
    meter = (ROOT / "templates" / "partials" / "throttle_meter.html").read_text(encoding="utf-8")
    assert 'role="progressbar"' in meter and 'aria-valuenow="{{ level }}"' in meter and "aria-valuetext=" in meter
    assert 'style="--level: {{ level }}"' in meter
    header = (ROOT / "templates" / "header.html").read_text(encoding="utf-8")
    for shell in (DEFAULT, header):
        assert '{% from "partials/throttle_meter.html" import throttle_meter %}' in shell
        assert "progress-bar" not in shell and "boil_pct" not in shell
        account = shell.split('id="dropdownMenuLink"')[1].split("</a>")[0]
        after = account.split('{{ throttle_meter(throttle_state, "nav") }}')[1]
        rep = account.index('{{ _("%(score)s Rep", score=rep_number) }}</div>')
        assert rep < account.index("throttle_meter(") and after.split() == ["</div>"]  # last: far right
        # the account menu no longer repeats the name, rep and meter the navbar shows (the owner, 2026-10-24)
        assert 'throttle_meter(throttle_state, "menu")' not in shell and "dropdown-meta" not in shell
    fill = NAV.split(".boil-fill {")[1].split("}")[0]
    assert "height: calc(var(--level, 0) * 1%);" in fill and "background-size: 100% var(--meter-h);" in fill
    assert "linear-gradient(to top, var(--boil-cool)" in fill and "var(--boil-hot) 100%)" in fill
    boil = MOTION.split("// the cooldown meter boils")[1]
    assert "@media (prefers-reduced-motion: no-preference) {\n    @keyframes boil-rise" in boil    # stops under reduce
    assert ".boil-meter.is-cooling {" in boil


def test_the_chat_page_is_exactly_the_screen_between_the_bars():
    # chat.html's inline 100vh - 56px ran 80px under a phone's bottom bar (a conversation's box behind it); and the
    # bottom bar's wrapper was an inline box that left a 21px line of empty page under every phone page
    screens = (STYLE / "_screens.scss").read_text(encoding="utf-8").split("// --- the chat page")[1]
    assert "height: calc(100dvh - var(--navbar-height, 59px)) !important;" in screens
    assert "height: calc(100dvh - var(--navbar-height, 59px) - 56px - env(safe-area-inset-bottom)) !important;" in screens
    assert "body:not(.embedded)#chat #chat-app" in screens           # the side panel's frame keeps its own height
    assert '<div class="d-lg-none">' in BAR and "d-inline-flex" not in BAR
