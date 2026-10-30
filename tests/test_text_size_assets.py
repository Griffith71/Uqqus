"""Text size and Feeds (Settings > Content > Layout): the `text_size` and `feed_paging` cookies, the attributes the server names
on <html> before the page is drawn, the root size and the type written in rem so that it follows it, the top bar measured on
every page shell, and the fixes for what a bigger size would cut off. The default has to stay exactly the 14px it was."""
import re
from pathlib import Path

import jinja2
import pytest

from stylesheets import SHEETS, expanded

ROOT = Path(__file__).resolve().parent.parent / "ruqqus"
STYLE = ROOT / "assets" / "style"


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


SETTINGS = read("templates", "settings_filters.html")
THEME = read("templates", "partials", "theme.html")
DEFAULT = read("templates", "default.html")
HEADER = read("templates", "header.html")
ALL_JS = read("assets", "js", "all_js.js")
PAGING = read("assets", "js", "infinite_scroll.js")
NAVBAR = read("assets", "js", "navbar_height.js")
TOKENS = (STYLE / "_tokens.scss").read_text(encoding="utf-8")
FIXES = (STYLE / "_text_size.scss").read_text(encoding="utf-8")
NAV = (STYLE / "_nav.scss").read_text(encoding="utf-8")


def html_attrs(cookies):
    macro = THEME.split("{% macro html_attrs() %}")[1].split("{% endmacro %}")[0]
    template = jinja2.Environment().from_string("{% macro html_attrs() %}" + macro + "{% endmacro %}{{ html_attrs() }}")
    return template.render(request=type("Request", (), {"cookies": cookies})(), ui_lang=lambda: "en", ui_dir=lambda: "ltr")


def compiled(name):
    sass = pytest.importorskip("sass")
    scss = (STYLE / name).read_text(encoding="utf-8")
    return sass.compile(string=scss.replace("{boardcolor}", "23a9f2").replace("{maincolor}", "23a9f2").replace("{ruqquspath}", str(ROOT)))


# --- the attributes the server names ---------------------------------------------------------------------------------------------

def test_the_default_names_nothing_for_either_choice():
    assert html_attrs({}) == ' lang="en" dir="ltr" data-density="comfortable"'


def test_text_size_is_named_for_the_two_values_the_settings_page_writes_and_nothing_else():
    assert html_attrs({"text_size": "large"}).endswith(' data-text-size="large"')
    assert html_attrs({"text_size": "xlarge"}).endswith(' data-text-size="xlarge"')
    for other in ("", "default", "small", "huge", "LARGE", "large ", "<script>", "xlarge\"onload=\"x"):
        assert "data-text-size" not in html_attrs({"text_size": other}), other


def test_paging_is_named_only_for_pages():
    assert html_attrs({"feed_paging": "pages"}).endswith(' data-feed-paging="pages"')
    for other in ("", "scroll", "Pages", "page", "1", "<script>"):
        assert "data-feed-paging" not in html_attrs({"feed_paging": other}), other


def test_all_the_choices_together_keep_the_density_and_the_video_choices_first():
    drawn = html_attrs({"density": "compact", "video_autoplay": "tap", "video_captions": "on", "video_sound": "muted",
                        "text_size": "xlarge", "feed_paging": "pages"})
    assert drawn == (' lang="en" dir="ltr" data-density="compact" data-video-autoplay="tap" data-video-captions="on" '
                     'data-video-sound="muted" data-text-size="xlarge" data-feed-paging="pages"')


# --- Settings > Content > Layout ---------------------------------------------------------------------------------------------------

def test_the_layout_card_holds_density_text_size_and_feeds():
    layout = SETTINGS.split('id="layout"')[1].split('id="video"')[0]
    assert layout.index('id="density"') < layout.index('id="text-size"') < layout.index('id="feed-paging"')
    assert re.search(r'<select class="form-control" id="text-size" onchange="setTextSize\(this.value\)">', layout)
    assert re.search(r'<select class="form-control" id="feed-paging" onchange="setFeedPaging\(this.value\)">', layout)
    # the selected option is read from the cookie, so a value nobody writes shows the default
    assert "request.cookies.get('text_size') not in ('large', 'xlarge')" in layout
    assert "request.cookies.get('text_size') == 'large'" in layout and "request.cookies.get('text_size') == 'xlarge'" in layout
    assert "request.cookies.get('feed_paging') != 'pages'" in layout and "request.cookies.get('feed_paging') == 'pages'" in layout
    # the rows are separated like the others: the last row has no line under it
    assert layout.count('class="d-lg-flex border-bottom"') == 2 and layout.count('<div class="d-lg-flex">') == 1


def test_every_label_and_line_of_the_layout_card_is_marked_for_translation():
    layout = SETTINGS.split('id="layout"')[1].split('id="video"')[0]
    texts = re.findall(r">([^<>{}]*[A-Za-z][^<>{}]*)<", re.sub(r"\{\{.*?\}\}|\{%.*?%\}|\{#.*?#\}", "", layout, flags=re.S))
    assert not [t for t in texts if t.strip()], texts
    for text in ("Text size", "Default", "Large", "Extra large", "Feeds", "Continuous scroll", "Pages"):
        assert '{{ _("%s") }}' % text in layout, text


def test_the_setters_write_one_cookie_a_year_and_the_attribute_with_it():
    size = ALL_JS.split("function setTextSize(value) {")[1].split("\n};")[0]
    assert 'value === "large" || value === "xlarge" ? value : ""' in size          # a value nobody writes clears it
    assert "text_size=" in size and "max-age=" in size and "samesite=lax" in size and "path=/" in size
    assert 'setAttribute("data-text-size", size)' in size and 'removeAttribute("data-text-size")' in size
    # the top bar is as tall as its text, so it is measured again
    assert 'window.dispatchEvent(new Event("resize"))' in size
    paging = ALL_JS.split("function setFeedPaging(value) {")[1].split("\n};")[0]
    assert 'value === "pages"' in paging and "feed_paging=" in paging and "max-age=" in paging and "samesite=lax" in paging
    assert 'setAttribute("data-feed-paging", "pages")' in paging and 'removeAttribute("data-feed-paging")' in paging


# --- the size itself -------------------------------------------------------------------------------------------------------------------

def test_the_root_is_87_5_percent_so_the_default_is_14px_and_the_browsers_own_size_counts():
    for name in SHEETS:
        text = (STYLE / name).read_text(encoding="utf-8")
        roots = re.findall(r"^html \{\n    font-size: ([^;]+);\n\}", text, flags=re.M)
        assert roots == ["87.5%", "87.5%"], (name, roots)       # the sheet's own, and the phone query's
        assert not re.search(r"^html \{\s*font-size: \d+px", text, flags=re.M), name


def test_the_two_steps_are_the_browsers_size_and_112_5_percent_of_it():
    assert re.search(r'html\[data-text-size="large"\] \{\s*font-size: 100%;\s*\}', TOKENS)
    assert re.search(r'html\[data-text-size="xlarge"\] \{\s*font-size: 112\.5%;\s*\}', TOKENS)
    for name in SHEETS:
        css = compiled(name)
        assert re.search(r'html\[data-text-size="large"\]\s*\{\s*font-size:\s*100%', css), name
        assert re.search(r'html\[data-text-size="xlarge"\]\s*\{\s*font-size:\s*112\.5%', css), name
        # the steps outrank the sheet's own `html { font-size: 87.5% }`, wherever it sits
        assert css.count("font-size: 87.5%") >= 2, name


def test_the_type_tokens_are_rem_and_the_default_is_exactly_what_they_were_in_px():
    block = TOKENS.split("--text-xs:")[0].rsplit(":root {", 1)[1] + "--text-xs:" + TOKENS.split("--text-xs:")[1].split("--leading-tight")[0]
    assert not re.search(r"--text-(xs|sm|base|body|title|title-lg|display):\s*[\d.]+px", block)
    for name, px in (("xs", 12), ("sm", 13), ("body", 15), ("title", 18), ("title-lg", 22)):
        assert f"--text-{name}: calc({px}rem / 14);" in block, name
    assert "--text-base: 1rem;" in block and "--text-display: 2rem;" in block       # 14px and 28px at the 14px root


def test_px2rem_is_exact_not_a_rounded_decimal():
    assert 'unquote("calc(#{$px / 1px}rem / 14)")' in TOKENS
    for name in SHEETS:
        css = compiled(name)
        assert "calc(13rem / 14)" in css and "calc(12rem / 14)" in css, name
        assert not re.search(r"font-size:\s*0\.\d{8,}rem", css), name                    # a ten-digit rem is 12.9999px at the default


def test_the_small_sizes_follow_the_root():
    for name in SHEETS:
        text = (STYLE / name).read_text(encoding="utf-8")
        assert '$small-font-size: #{"calc(12rem / 14)"};' in text and '$extra-small-font-size: #{"calc(11rem / 14)"};' in text, name
        assert re.search(r"\$small-font-size: \d+px", text) is None, name


# px that stays px on purpose: an icon in a box of its own, a badge's number, the glyphs in front of a toggle, the quote
# cards (a size of their own: the owner's), the zoom guard and the phone's bottom bar
PX_KEPT = (".nav-link .badge-count", ".chat-sas-name", "div:hover .thumb-img-link-overlay", ".comment-actions .fa",
           ".comment.collapsed .comment-collapse::before", ".comment .comment-collapse::before", ".story-add",
           ".story-avatar-65 .story-add", ".quote-card", ".quote-card-full", ".quote-card-mark", ".quote-card-play",
           ".quote-card-notice", ".post-actions .post-action .fa", ".comment .comment-toggle::before",
           ".comment .comment-actions .list-inline-item > .dropdown > a .far", "#{$empty-icon} > .fa-stack-1x",
           ".arrow-up:hover::before", ".bottom-tab", ".bottom-tab .fas", ".bottom-tab-create")


def test_type_is_written_in_rem_and_only_the_known_exceptions_are_px():
    for name in SHEETS:
        lines = expanded(name).split("\n")
        for i, line in enumerate(lines):
            if not re.match(r"^\s*font-size:\s*[\d.]+px", line):
                continue
            j = i
            while j > 0 and not lines[j - 1].rstrip().endswith("{"):
                j -= 1
            head = lines[j - 1].strip().rstrip("{").strip()
            assert any(head == k or head.startswith(k + " ") or head.startswith(k + ",") or head.startswith(k + "::")
                       or head.startswith(k + ":") for k in PX_KEPT), (name, i + 1, head, line.strip())


def test_the_zoom_guard_keeps_16px_and_grows_with_the_text():
    for name in SHEETS:
        css = compiled(name)
        assert "font-size: max(16px, 1rem)" in css, name          # a field under 16px makes a phone zoom the page in


# --- the top bar is measured everywhere ---------------------------------------------------------------------------------------------

def test_the_navbar_script_is_loaded_by_both_page_shells_and_nothing_else_measures():
    assert "asset_url('js/navbar_height.js')" in DEFAULT and "asset_url('js/navbar_height.js')" in HEADER
    assert "updateNavHeight" not in DEFAULT and "updateNavHeight" not in HEADER          # one copy, in the script
    assert "document.getElementById('navbar')" in NAVBAR and "'--navbar-height'" in NAVBAR
    assert "window.addEventListener('resize'" in NAVBAR and "MutationObserver" in NAVBAR
    # the header's bar is in the markup before its script
    assert HEADER.index('id="navbar"') < HEADER.index("navbar_height.js")


def test_what_sits_under_the_bar_follows_its_measured_height_with_the_default_it_measures():
    for name in SHEETS:
        text = (STYLE / name).read_text(encoding="utf-8")
        assert "--navbar-height: 56px;" in text, name                                              # (the measured bar: --bar-h, _tokens.scss)
        assert "padding-top: 98px" not in text and "top: 55px;" not in text and "100vh - 55px" not in text, name
        assert text.count("padding-top: calc(var(--navbar-height, 59px) + 39px);") >= 2, name     # (the fallback never fires: :root defines the height)
        assert "top: calc(var(--navbar-height, 59px) - 4px);" in text, name                       # the sticky bars and the sidebar
    assert "--navbar-height: calc(56px + env(safe-area-inset-top));" in (STYLE / "_mobile.scss").read_text(encoding="utf-8")


def test_the_search_pill_is_as_tall_as_its_text_and_never_under_the_default_control():
    """36px (--control-h; 32 in Compact), so the pill, the icons and the account are one height and the bar is a control and 2 x 9.5px: 56px."""
    pill = NAV.split("#navbar .form-inline.search .form-control {")[1].split("}")[0]
    assert re.search(r"height: auto;", pill) and "min-height: var(--control-h);" in pill


# --- fixes for what a bigger size cuts off ----------------------------------------------------------------------------------------------

def test_the_fixes_partial_is_imported_by_both_sheets_after_the_older_ones():
    for name in SHEETS:
        imports = re.findall(r'@import "\{ruqquspath\}/assets/style/(_\w+)";', (STYLE / name).read_text(encoding="utf-8"))
        assert "_text_size" in imports and imports.index("_text_size") > imports.index("_mobile"), (name, imports[-4:])


def test_a_button_in_a_who_to_follow_row_keeps_its_text_and_a_tab_row_scrolls_on_its_own():
    assert re.search(r"\.wtf-row \.btn \{\s*flex: 0 0 auto;\s*white-space: nowrap;\s*\}", FIXES)
    tabs = FIXES.split(".explore-tabs {")[1].split("}")[0]
    assert "overflow-x: auto;" in tabs and "overflow-y: hidden;" in tabs and "scrollbar-width: none;" in tabs
    # the line under the tabs is a shadow, so clipping the row cannot cut the active tab's underline
    assert "border-bottom: 0;" in tabs and "box-shadow: inset 0 -1px 0 $ex-border;" in tabs
    assert re.search(r"\.explore-tab \{\s*flex: 0 0 auto;\s*margin-bottom: 0;\s*white-space: nowrap;\s*\}", FIXES)
    for name in SHEETS:
        css = compiled(name)
        assert re.search(r"\.explore-tabs\s*\{[^}]*overflow-x:\s*auto", css), name
        assert re.search(r"\.wtf-row \.btn\s*\{[^}]*flex:\s*0 0 auto", css), name


# --- Feeds: pages instead of continuous scroll ----------------------------------------------------------------------------------------------

def test_pages_leave_the_feed_as_the_server_drew_it():
    # before the page's buttons are hidden, the pull-to-reload is taken over or anything is fetched
    pages = PAGING.split("document.documentElement.dataset.feedPaging === 'pages'")[1].split("var nextUrl")[0]
    assert "return;" in pages
    assert PAGING.index("dataset.feedPaging === 'pages'") < PAGING.index("document.documentElement.classList.add('feed-live')")
    assert PAGING.index("dataset.feedPaging === 'pages'") < PAGING.index("setInterval(checkNew")
    assert PAGING.index("dataset.feedPaging === 'pages'") < PAGING.index("new IntersectionObserver")
    # a feed of one page has two disabled buttons and nothing to turn to: only that pager is put away
    assert "if (!pagination.querySelector('a.page-link')) control.hidden = true;" in pages


def test_the_continuous_feed_is_unchanged_without_the_choice():
    for marker in ("control.hidden = true;", "document.documentElement.classList.add('feed-live');",
                   "if (nextUrl) observer.observe(sentinel); else finish();", "setInterval(checkNew, CHECK_MS);"):
        assert marker in PAGING, marker
