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
    # a topic's ids (its subcategories, and -N: guilds filed under its category alone) and a typed list are applied the same way
    assert "category_filter.board_clause(cats, Board.subcat_id, Board.cat_id,\n" in front
    assert "cats = list(topic.ids)" in browse and "expand" not in front.split("def guild_ids(", 1)[1].split("\n@app", 1)[0]


# --- the folded sidebar and the pages that went away --------------------------------------------------------------

def test_the_rail_icons_open_the_filter_in_the_sidebar_instead_of_going_to_a_page():
    rail = read("templates", "sidebar-left.html")
    for name in ("regional", "language", "categorical", "post_type", "advanced"):
        assert f"onclick=\"openSidebarFilter('{name}')\"" in rail, name
    for old in ('href="/regional"', 'href="/language"', 'href="/categorical"'):
        assert old not in rail, old
    js = read("assets", "js", "all_js.js")
    fn = js.split("function openSidebarFilter(", 1)[1].split("\n}\n", 1)[0]
    assert "toggle_sidebar_expand();" in fn and ".collapse('show')" in fn
    assert "max-width: 1199.98px" in fn and "Modal" in fn          # under 1200px the sidebar is only a rail: the sheet opens


def test_the_filter_notices_open_the_same_panels():
    home = read("templates", "home.html")
    for name in ("regional", "language", "categorical", "post_type", "advanced"):
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
        assert "#regions #main-content-col" in css and "max-width: 1340px;" in css, name


def test_the_three_columns_are_reddits_and_the_panel_is_as_wide_as_the_right_sidebar():
    for name, css in STYLES.items():
        assert "$layout-left: 272px;" in css and "$layout-right: 316px;" in css, name
        # the feed is Reddit's 738px of content plus the 44px vote column beside it (the owner, 2026-10-23)
        assert "$layout-feed-content: 738px;" in css and "$layout-vote: 44px;" in css, name
        assert "$layout-feed: $layout-feed-content + $layout-vote;" in css, name
        # 272 + 782 + 316 is the navbar's 1370px block, and folded (65 rail) it is 1163px
        assert 272 + (738 + 44) + 316 == 1370 and 65 + (738 + 44) + 316 == 1163
        assert "flex: 0 0 $layout-right;\n        max-width: $layout-right;" in css, name       # #side-panel
        assert "flex: 0 0 $layout-left;" in css and "max-width: $layout-feed;" in css, name
        for gone in ("380px", "#composer .inline-composer"):
            assert gone not in css, (name, gone)
    assert "BLOCK = 1370" in read("assets", "js", "nav_align.js")


def test_referrals_are_gone():
    for template in (ROOT / "templates").rglob("*.html"):
        page = template.read_text(encoding="utf-8")
        for gone in ("Invite friends", "signup?ref=", "referred_by", "ref_user"):
            assert gone not in page, (template.name, gone)
    assert not (ROOT / "templates" / "sign_up_failed_ref.html").exists()
    login = read("routes", "login.py")
    assert "referred_by" not in login and 'request.args.get("ref"' not in login


def test_the_account_menu_has_a_create_post_item_and_the_navbar_button_opens_the_feed_composer():
    # the dropdown is partials/account_menu.html (both shells include it); the phone menu is in each shell
    assert read("templates", "partials", "account_menu.html").count('<i class="fas fa-feather-alt fa-fw text-left mr-3"></i>{{ _("Create post") }}') == 1
    for shell in ("default.html", "header.html"):
        page = read("templates", shell)
        assert '{% include "partials/account_menu.html" %}' in page, shell
        assert 'fa-feather-alt fa-fw text-left' not in page, shell
        assert page.count('<i class="fas fa-feather-alt fa-fw mr-3"></i>{{ _("Create post") }}') == 1, shell


def test_the_site_name_is_never_shown_all_lowercase():
    assert 'environ.get("SITE_NAME", "Laba")' in read("__main__.py")
    for shell in ("default.html", "header.html", "partials/site_bar.html"):
        page = read("templates", *shell.split("/"))
        # the navbar shows the mark alone (the owner, 2026-10-29: "just the ɸ will do"); the name is the link's accessible name,
        # drawn from SITE_NAME, never written out and never lowercased
        assert '<span class="brand-main" aria-hidden="true">ɸ</span>' in page, shell
        assert "aria-label=\"{{ 'SITE_NAME' | app_config }}\"" in page and "ɸLaba" not in page and "ɸlaba" not in page, shell
    for template in (ROOT / "templates").rglob("*.html"):
        for line in template.read_text(encoding="utf-8").splitlines():
            if re.search(r"SITE_NAME[^|}]*\|\s*app_config\s*\|\s*lower", line):
                # only the account handles and meta tags that are addresses, never text a visitor reads
                assert "content=" in line or "href=" in line, (template.name, line.strip())


def test_pages_without_the_right_sidebar_fit_their_content():
    # the owner (2026-10-23): no right sidebar on History, the Inbox, Circles and the Creator Studio. A list is centred at
    # the feed's width ("is-focused"); a dashboard uses the whole width as a grid of cards
    assert "{% block main_row_class %}{% endblock %}\" id=\"main-content-row\"" in read("templates", "default.html")
    lists = ("history.html", "notifications.html", "studio_locked.html", "insights_locked.html")
    grids = ("studio.html", "insights.html", "insights_overview.html")
    for page in lists + grids:
        html = read("templates", page)
        assert "{% block sidebar %}{% endblock %}" in html, page
        expected = " wide-main is-focused" if page in lists else " wide-main"
        assert "{% block main_row_class %}" + expected + "{% endblock %}" in html, page
    # Circles: one circle and the paid circles you are in are focused; the index lays its sections out as a grid
    base = read("templates", "circles_base.html")
    assert "{% block main_row_class %} wide-main{% block circles_focus %} is-focused{% endblock %}{% endblock %}" in base
    index = read("templates", "settings_circle.html")
    assert "{% block circles_focus %}{% endblock %}" in index and '<div class="circle-grid">' in index
    for hook in ('id="circle-friends"', 'id="circle-custom"', 'id="circle-new"', 'id="circle-paid"', "data-circle-form",
                 'id="circle-status"'):
        assert hook in index, hook                     # assets/js/circle_settings.js
    # both columns start with a heading and then a card, so the cards line up side by side
    for col in index.split('<section class="circle-col"')[1:]:
        start = col.split(">", 1)[1].lstrip()
        assert start.startswith('<h2 class="h6 mb-2"'), start[:60]
    screens = read("assets", "style", "_screens.scss")
    assert "#main-content-row.wide-main > #main-content-col {\n        max-width: $layout-feed + $layout-right;" in screens
    focused = screens.split("#main-content-row.wide-main.is-focused > #main-content-col > * {")[1].split("}")[0]
    assert "max-width: $layout-feed;" in focused and "margin-left: auto !important;" in focused


def test_history_reads_well_on_a_phone():
    # its tabs come first (the Show and Folder bars belong to the tab chosen), each named, and the page doesn't keep
    # #frontpage's room for feed pills it doesn't draw
    html = read("templates", "history.html")
    banner = html.split("{% block desktopBanner %}")[1].split("{% endblock %}")[0]
    assert banner.index("history-tabs") < banner.index("history-show-bar") < banner.index('id="bookmark-folders"')
    for label in ("Bookmarked", "History", "Upvoted", "Downvoted"):
        assert f"aria-label=\"{{{{ _('{label}') }}}}\"" in banner, label
    screens = read("assets", "style", "_screens.scss")
    assert "#frontpage:has(.history-tabs) {\n        padding-top: var(--navbar-height, 55px);" in screens
    # its Show control is separate pills: .btn-group would square their inner corners
    assert ".history-show-bar .btn-group > .btn-sm {" in screens


def test_the_vote_column_is_as_wide_as_the_feed_adds_for_it():
    feed = read("assets", "style", "_feed.scss").split(".card .voting {")[1].split("}")[0]
    tokens = read("assets", "style", "_tokens.scss")
    assert "min-width: 40px;" in feed and "margin: 0 var(--space-3) 0 calc(-1 * var(--space-2)) !important;" in feed
    assert "--space-3: 8px;" in tokens and "--space-2: 4px;" in tokens
    assert 40 + 8 - 4 == 44        # $layout-vote


def test_left_sidebar_rows_are_close_together_with_room_inside():
    # the owner (2026-10-23, Reddit's sidebar): rows 2px apart rather than 1rem, padded inside their highlight, and the
    # highlight not cut square by the section's overflow
    nav = read("assets", "style", "_nav.scss")
    assert "#sidebar-left .guild-recommendations-list .guild-recommendations-item {\n    margin-top: 0;" in nav
    row = nav.split("#sidebar-left .guild-recommendations-item > a {\n    display: block;")[1].split("\n}\n")[0]       # (not "}": the min-height holds an interpolation)
    # the owner (2026-10-30, Reddit's again): 40px rows (44px under a finger: --hit-min), an 8px highlight; the 6px of padding is what the
    # 28px icon box leaves of the 40 (8px when a finger sets the height, so the box stays centred)
    assert "margin: 2px -12px;" in row and "padding: 6px 12px;" in row
    assert 'min-height: #{"max(var(--control-lg), var(--hit-min))"};' in row and "border-radius: var(--radius-sm);" in row
    assert "@media (pointer: coarse) {\n    #sidebar-left .guild-recommendations-item > a {\n        padding-block: 8px;" in nav
    assert "#sidebar-left .sidebar-section {\n    overflow: visible;" in nav


def test_save_as_my_default_is_an_action_not_a_filter():
    # the owner (2026-10-23): it looked like one more filter. A button with a plain icon under the filters (no purple
    # circle, not the rows' rule), a line saying so once the filters are the default, and on the rail an outlined circle
    left = read("templates", "sidebar-left.html")
    row = left.split('<li class="guild-recommendations-item feed-defaults">')[1].split("</li>")[0]
    assert "fa-stack" not in row and 'class="btn feed-defaults-save"' in row
    assert 'class="feed-defaults-status"' in row and "Saved as your default" in row
    assert "These are your default" not in left
    rail = left.split('<li class="guild-recommendations-item feed-defaults-rail">')[1].split("</li>")[0]
    assert 'action="/settings/feed_defaults"' in rail and 'name="formkey"' in rail and 'value="save"' in rail
    assert 'aria-label="{{ _(\'Save these filters as my default\') }}"' in rail and "fa-stack" not in rail
    assert left.index("feed-defaults-rail") < left.index('id="sidebar-filters-list"')     # the rail's own list
    nav = read("assets", "style", "_nav.scss")
    assert 'feed-defaults button[value="save"]' not in nav
    assert "#sidebar-left .feed-defaults-save {" in nav and "#sidebar-left .feed-defaults-rail-btn {" in nav


def test_a_focused_chip_in_a_row_that_scrolls_sideways_keeps_its_whole_ring():
    """`overflow-x: auto` clips the other axis too, so the keyboard ring (2px, offset 2px) was cut off at the row's start and top edges on
    the first chip. It sits inside the chip there. The selector is as specific as the global ring's (`body:not(.using-mouse) ...`), or it
    would lose to it."""
    css = read("assets", "style", "_components.scss")
    rule = css[css.index("body:not(.using-mouse) .explore-chips .explore-chip:focus-visible {"):]
    assert "outline-offset: -2px;" in rule[:rule.index("}")]
    assert css.index("body:not(.using-mouse) :focus-visible:not(.form-control) {") < css.index("body:not(.using-mouse) .explore-chips .explore-chip:focus-visible {")
