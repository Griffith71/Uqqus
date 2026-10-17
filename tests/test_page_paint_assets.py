"""Pages paint right the first time (2026-10-09): the theme, the folded sidebar, the navbar, the right sidebar and the
pictures are right at the first paint, and the stylesheet and scripts are cached. These checks keep the pieces
(templates, stylesheets, scripts, routes) agreeing; the behaviour was measured with Playwright on the Docker site."""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent / "ruqqus"


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


DEFAULT = read("templates", "default.html")
THEME = read("templates", "partials", "theme.html")
LATE = read("templates", "partials", "late_boxes.html")
JS = read("assets", "js", "all_js.js")
STYLES = {name: read("assets", "style", name) for name in ("main.scss", "main_dark.scss")}


@pytest.mark.parametrize("name", ["main.scss", "main_dark.scss"])
def test_both_stylesheets_compile(name):
    # routes/static.py compiles them on request with libsass: a rule it cannot parse used to empty the stylesheet
    sass = pytest.importorskip("sass")
    scss = STYLES[name].replace("{boardcolor}", "805ad5").replace("{maincolor}", "805ad5").replace("{ruqquspath}", str(ROOT))
    css = sass.compile(string=scss)
    for needle in ("scrollbar-gutter: stable", "html.sidebar-folded", "late-box-in", "expects-right-sidebar"):
        assert needle in css, needle


# --- theme ----------------------------------------------------------------------------------------------------

def test_the_theme_is_chosen_by_the_server_or_the_device_never_after_load():
    assert 'media="not all and (prefers-color-scheme: dark)"' in THEME
    assert 'media="(prefers-color-scheme: dark)"' in THEME and 'id="css-link-auto-dark"' in THEME
    assert 'data-light="{{ light }}" data-dark="{{ dark }}"' in THEME
    # the old load-time swap from localStorage is gone
    assert "localStorage.getItem('dark_mode_enabled')" not in JS and "dark_mode_enabled" not in JS
    assert "function switch_css()" in JS and 'getAttribute(theme == "dark" ? "data-dark" : "data-light")' in JS


def test_every_page_shell_uses_the_one_theme_macro():
    for name in ("default.html", "submission.html", "board.html", "submit.html", "make_board.html", "settings.html",
                 "docs_default.html", "guild/guild_settings.html"):
        page = read("templates", *name.split("/"))
        assert "theme_links(" in page, name
        assert 'session.get("dark_mode_enabled")' not in page and "switch_css_board" not in page, name
    assert "theme_choice=theme_choice" in read("helpers", "page_assets.py")


def test_the_body_names_the_device_theme_before_anything_is_drawn():
    assert '{{ body_theme_class() }}' in DEFAULT and "{{ body_theme_script() }}" in DEFAULT
    body = DEFAULT.split("<body", 1)[1]
    assert body.index("{{ body_theme_script() }}") < body.index("<div")


# --- folded sidebar and navbar ----------------------------------------------------------------------------------

def test_the_folded_sidebar_is_drawn_by_the_server():
    sidebar = read("templates", "sidebar-left.html")
    assert "request.cookies.get('sidebar_pref') == 'collapsed'" in sidebar
    assert "toggle_collapse" not in sidebar and "session.get('sidebar_collapsed'" not in sidebar
    assert "class=\"sidebar-folded\"" in DEFAULT.split("<head>")[0]
    assert "sidebar_pref=collapsed; Max-Age=31536000; Path=/; SameSite=Lax" in JS
    assert "localStorage.sidebar_pref == 'collapsed'" in JS  # the one-time move of an old choice to the cookie
    assert "/settings/toggle_collapse" not in read("routes", "settings.py")


def test_the_navbar_defaults_fit_the_folded_sidebar_and_the_search_box_fades_in():
    for name, css in STYLES.items():
        assert "html.sidebar-folded #navbar > .container-fluid" in css, name
        assert 'calc((100% - 1119px) / 2 + 15px)' in css, name   # 65 + 738 + 316
        assert "html.nav-align:not(.nav-aligned) #navbar #searchform" in css, name
        assert "nav-search-fallback" in css, name                # never left hidden without the script
    assert "root.classList.add('nav-aligned')" in read("assets", "js", "nav_align.js")
    head = DEFAULT.split("</head>")[0]
    assert 'classList.add("nav-align")' in head and "request.args.get('embed')" in head


# --- the right sidebar and the scrollbar --------------------------------------------------------------------------

def test_late_sidebar_boxes_keep_their_room():
    for name in ("default.html", "home.html"):
  # the page shells include the shared sidebar (partials/sidebar_common.html), which holds the late boxes
        assert '{% include "partials/sidebar_common.html" %}' in read("templates", name), name
    assert '{% include "partials/late_boxes.html" %}' in read("templates", "partials", "sidebar_common.html")
    assert 'localStorage.getItem("late_box_h:" + box.id)' in LATE and "box.style.minHeight" in LATE
    assert 'localStorage.setItem("late_box_h:" + slot.id' in JS
    for name in ("trending.js", "who_to_follow.js"):
        script = read("assets", "js", name)
        assert "fill_late_box(slot, html)" in script and "offsetParent" in script, name


def test_the_feed_does_not_move_when_the_scrollbar_or_the_right_sidebar_arrives():
    for name, css in STYLES.items():
        assert "scrollbar-gutter: stable" in css, name
        assert "#main-content-row.expects-right-sidebar:not(:has(> .sidebar:not(#sidebar-left)))::after" in css, name
    assert "{% set right_sidebar = self.sidebar() %}" in DEFAULT
    assert "{% if right_sidebar | trim %} expects-right-sidebar{% endif %}" in DEFAULT
    assert DEFAULT.count("{{ right_sidebar }}") == 1


# --- pictures ---------------------------------------------------------------------------------------------------

def test_linked_media_pictures_keep_their_size():
    for name in ("submission_listing.html", "submission.html"):
        page = read("templates", name)
        assert 'width="{{ p.image_size[0] }}" height="{{ p.image_size[1] }}"' in page, name
        assert 'onload="this.dataset.loaded=1"' in page, name
    get = read("helpers", "get.py")
    assert get.count("attach_image_sizes(") == 2  # get_posts and get_post
    assert "def attach_image_sizes(db, posts, server_name):" in read("helpers", "media", "attach.py")
    for name, css in STYLES.items():
        assert ".post-img-lg img:not([width]):not([data-loaded])" in css, name


# --- assets -----------------------------------------------------------------------------------------------------

def test_scripts_and_stylesheets_are_versioned_by_content_and_cached_for_good():
    templates = ROOT / "templates"
    for path in templates.rglob("*.html"):
        html = path.read_text(encoding="utf-8")
        assert not re.search(r'src="/assets/js/', html), path.name
        assert not re.search(r'href="/assets/style/main(_dark)?\.css', html), path.name
    main = read("__main__.py")
    assert 'response.headers["Cache-Control"] = "public, max-age=31536000, immutable"' in main
    assert 'request.path.startswith("/assets/") and request.args.get("v")' in main
    assets = read("helpers", "page_assets.py")
    assert "hashlib.sha256" in assets and "st_mtime" in assets


def test_no_dead_host_blocks_the_first_paint():
    for path in (ROOT / "templates").rglob("*.html"):
        assert "fa.ruqqus.com" not in path.read_text(encoding="utf-8"), path.name
    assert '<link rel="preconnect" href="https://cdnjs.cloudflare.com">' in DEFAULT


def test_a_stylesheet_that_does_not_compile_is_never_served_empty():
    body = read("routes", "static.py").split("def main_css(")[1].split("@app.route")[0]
    failed = body.split("except sass.CompileError")[1].split("_compiled_css[file] = css")[0]
    assert "send_file" not in failed and "abort(500)" in failed


def test_the_card_byline_reads_its_forward_records_once_per_request():
    # the card template reads these several times per post; each was a query every time (measured: the member
    # home page went from 638 to 283 queries)
    src = read("classes", "submission.py")
    for name in ("is_forward_copy", "forwarded_by", "is_comment_forward", "forwarded_from_comment",
                 "comment_forwarded_by"):
        assert f"    @property\n    @lazy\n    def {name}(self):" in src, name


def test_the_account_area_reaches_its_column_edge_before_any_script():
    # the phone menu's empty .navbar-collapse used to sit after the account area on desktop
    assert 'class="collapse navbar-collapse navbar-phone-menu" id="navbarResponsive"' in DEFAULT
    for name, css in STYLES.items():
        assert re.search(r"min-width: 768px\)\s*\{\s*#navbar \.navbar-phone-menu \{\s*display: none !important;", css), name


def test_a_feed_video_thumbnail_has_the_players_box():
    for name, css in STYLES.items():
        assert re.search(r"\.embed-lg\[data-embed-kind\] \.feed-video-thumb \{[^}]*aspect-ratio: 16 / 9;", css), name
        assert re.search(r"\.feed-video-thumb img \{[^}]*object-fit: cover;", css), name
    script = read("assets", "js", "feed_video_autoplay.js")
    assert "media.style.aspectRatio = '16 / 9';" in script


def test_a_link_preview_picture_keeps_its_room_while_it_loads():
    for name, css in STYLES.items():
        assert re.search(r"\.post-preview-lg img \{\s*aspect-ratio: 1\.91 / 1;", css), name
