"""The Explore pages (Discover guilds / Discover curations), the folded left sidebar and the pages that went away with
it, the Regions page, the page layout, and the site's name (never all lowercase). Reads the templates, scripts and
stylesheets, like the other *_assets tests."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "ruqqus"


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


STYLES = {name: read("assets", "style", name) for name in ("main.scss", "main_dark.scss")}


# --- Explore ------------------------------------------------------------------------------------------------------

def test_the_explore_pages_keep_the_left_sidebar_and_have_no_right_one():
    for path in (("boards.html",), ("curations", "browse.html")):
        page = read("templates", *path)
        assert '{% extends "default.html" %}' in page, path
        assert "{% block pagetype %}explore{% endblock %}" in page, path
        assert '{% block leftSidebarBlock %}\n{% include "sidebar-left.html" %}' in page, path
        assert "{% block sidebar %}{% endblock %}" in page, path
        assert 'import explore_head' in page and "with context" in page, path


def test_the_explore_cards_draw_every_guild_and_curation_through_the_partial():
    cards = read("templates", "partials", "explore.html")
    assert "macro explore_head(" in cards and "macro guild_card(" in cards and "macro curation_card(" in cards
    assert 'href="/browse"' in cards and 'href="/curations"' in cards
    assert "| safe" not in cards
    assert "/api/follow_curation/" in cards and "/api/unfollow_curation/" in cards


def test_the_feed_column_widens_into_the_right_sidebars_room_so_the_left_sidebar_stays_put():
    for name, css in STYLES.items():
        assert "#explore #main-content-col" in css and "max-width: $layout-feed + $layout-right;" in css, name
        for rule in (".explore-grid {", ".explore-card {", ".explore-chip {", ".composer-mode {"):
            assert rule in css, (name, rule)
        # before the Create post card section, with the tokens it needs defined above it
        assert css.index(".explore-card {") < css.index("// Create post card (templates/submit.html"), name
        assert css.index("$ex-surface:") < css.index(".explore-card {"), name


def test_discover_guilds_filters_by_topic_and_never_reads_a_detached_property():
    front = read("routes", "front.py")
    browse = front.split("def browse_guilds(", 1)[1].split("\n@app", 1)[0]
    assert 'request.args.get("topic", "")' in browse and 'request.args.get("cat", type=int)' in browse and "topics=chips" in browse
    assert "x.visible" not in browse                      # CATEGORIES have no session: `visible` lazy-loads and fails
    assert "Board.subcat_id.in_(tuple(cats))" in front


# --- the folded sidebar and the pages that went away --------------------------------------------------------------

def test_the_rail_icons_open_the_filter_in_the_sidebar_instead_of_going_to_a_page():
    rail = read("templates", "sidebar-left.html")
    for name in ("regional", "language", "categorical"):
        assert f"onclick=\"openSidebarFilter('{name}')\"" in rail, name
    for old in ('href="/regional"', 'href="/language"', 'href="/categorical"'):
        assert old not in rail, old
    js = read("assets", "js", "all_js.js")
    fn = js.split("function openSidebarFilter(", 1)[1].split("\n}\n", 1)[0]
    assert "toggle_sidebar_expand();" in fn and ".collapse('show')" in fn
    assert "max-width: 1199.98px" in fn and "Modal" in fn          # under 1200px the sidebar is only a rail: the sheet opens


def test_the_filter_notices_open_the_same_panels():
    home = read("templates", "home.html")
    for name in ("regional", "language", "categorical"):
        assert f"openSidebarFilter('{name}')" in home, name
    assert 'href="/regional"' not in home and 'href="/language"' not in home and 'href="/categorical"' not in home


def test_category_and_language_have_no_page_of_their_own_any_more():
    assert not (ROOT / "templates" / "categorylisting.html").exists()
    assert not (ROOT / "templates" / "language_picker.html").exists()
    front = read("routes", "front.py")
    assert "categorylisting.html" not in front and "language_picker.html" not in front
    assert '@app.route("/categories"' not in front and "def categories_select" not in front
    assert "language_feed_legacy_redirect" not in front
    for shell in ("default.html", "header.html"):
        assert 'href="/categories"' not in read("templates", shell), shell
    js = read("assets", "js", "all_js.js")
    for gone in ("var togglecat", "var triggercat", "var cattoggle", "var all_cats"):
        assert gone not in js, gone
    # the three routes stay as what the sidebar's Apply button calls: they save the choice and go back to the feed
    for route in ('@app.route("/regional"', '@app.route("/language"', '@app.route("/categorical"'):
        assert route in front, route


# --- Regions, the panel, the name ---------------------------------------------------------------------------------

def test_regions_has_no_right_sidebar_and_lines_up_with_the_navbar():
    page = read("templates", "regions.html")
    assert "{% block sidebar %}{% endblock %}" in page
    for name, css in STYLES.items():
        assert "#regions #main-content-col" in css and "max-width: 1296px;" in css, name


def test_the_three_columns_are_reddits_and_the_panel_is_as_wide_as_the_right_sidebar():
    for name, css in STYLES.items():
        assert "$layout-left: 272px;" in css and "$layout-feed: 738px;" in css and "$layout-right: 316px;" in css, name
        # 272 + 738 + 316 is still the navbar's 1326px block, and folded (65 rail) it is 1119px
        assert 272 + 738 + 316 == 1326 and 65 + 738 + 316 == 1119
        assert "flex: 0 0 $layout-right;\n        max-width: $layout-right;" in css, name       # #side-panel
        assert "flex: 0 0 $layout-left;" in css and "max-width: $layout-feed;" in css, name
        for gone in ("380px", "#composer .inline-composer"):
            assert gone not in css, (name, gone)
    assert "BLOCK = 1326" in read("assets", "js", "nav_align.js")


def test_referrals_are_gone():
    for template in (ROOT / "templates").rglob("*.html"):
        page = template.read_text(encoding="utf-8")
        for gone in ("Invite friends", "signup?ref=", "referred_by", "ref_user"):
            assert gone not in page, (template.name, gone)
    assert not (ROOT / "templates" / "sign_up_failed_ref.html").exists()
    login = read("routes", "login.py")
    assert "referred_by" not in login and 'request.args.get("ref"' not in login


def test_the_account_menu_has_a_create_post_item_and_the_navbar_button_opens_the_feed_composer():
    for shell in ("default.html", "header.html"):
        page = read("templates", shell)
        # the dropdown and the phone menu
        assert page.count('<i class="fas fa-feather-alt fa-fw text-left mr-3"></i>Create post') == 1, shell
        assert page.count('<i class="fas fa-feather-alt fa-fw mr-3"></i>Create post') == 1, shell


def test_the_site_name_is_never_shown_all_lowercase():
    assert 'environ.get("SITE_NAME", "Laba")' in read("__main__.py")
    for shell in ("default.html", "header.html"):
        assert "ɸLaba" in read("templates", shell) and "ɸlaba" not in read("templates", shell), shell
    for template in (ROOT / "templates").rglob("*.html"):
        for line in template.read_text(encoding="utf-8").splitlines():
            if re.search(r"SITE_NAME[^|}]*\|\s*app_config\s*\|\s*lower", line):
                # only the account handles and meta tags that are addresses, never text a visitor reads
                assert "content=" in line or "href=" in line, (template.name, line.strip())
