"""The feed filters' wiring: the sidebar rows and rail icons, the phone pills and sheets, the popup, the routes that
save them, the three feeds that apply them (and only those), and both stylesheets. The rules and the SQL are in
test_feed_filters.py."""
import re
from pathlib import Path

from ruqqus.helpers import feed_filters as ff

ROOT = Path(__file__).resolve().parent.parent / "ruqqus"


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


SIDEBAR = read("templates", "sidebar-left.html")
FRONT = read("routes", "front.py")
USER = read("classes", "user.py")
HOME = read("templates", "home.html")
POPUP = read("templates", "partials", "advanced_filter_modal.html")


def _function(source, name):
    return source.split(f"def {name}(", 1)[1].split("\n    @", 1)[0].split("\n@", 1)[0].split("\ndef ", 1)[0]


def test_spaces_left_the_feeds_list_and_the_two_filters_follow_categorical():
    # Spaces is not a feed: it has its own section under My Curations (the owner, 2026-10-21), never in the Feeds list
    feeds = SIDEBAR.split('id="sidebar-filters-list"', 1)[0]
    assert 'href="/spaces"' not in feeds
    assert SIDEBAR.index("My Curations") < SIDEBAR.index('href="/spaces"')
    filters = SIDEBAR.split('id="sidebar-filters-list"', 1)[1]
    order = [filters.index(x) for x in ("#sidebar-filter-regional", "#sidebar-filter-language",
                                         "#sidebar-filter-categorical", "#sidebar-filter-post_type",
                                         'data-target="#advancedFilterModal"')]
    assert order == sorted(order)
    assert "filters.post_type_body('sidebar-post_type-options'" in SIDEBAR
    assert "post_type_summary" in SIDEBAR and "feed_filter_count" in SIDEBAR


def test_the_post_type_list_keeps_its_groups_and_applies_through_its_route():
    macros = read("templates", "_sidebar_filter_macros.html")
    body = macros.split("macro post_type_body", 1)[1]
    assert "data-keep-order" in body and "applySidebarFilter('{{ list_id }}', '/post_type', 'types')" in body
    assert 'role="checkbox"' in body and "aria-checked" in body
    js = read("assets", "js", "all_js.js")
    resort = js.split("var resortSidebarFilterList", 1)[1].split("};", 1)[0]
    assert "data-keep-order" in resort


def test_the_phone_has_pills_and_sheets_for_both():
    tabs = HOME.split("{% block mobileFeedTabs %}", 1)[1].split("{% endblock %}", 1)[0]
    assert 'data-target="#postTypeFilterModal"' in tabs and 'data-target="#advancedFilterModal"' in tabs
    sheet = read("templates", "post_type_filter_modal.html")
    assert 'id="postTypeFilterModal"' in sheet and "post_type_body('mobile-post_type-options'" in sheet
    default = read("templates", "default.html")
    assert '{% include "post_type_filter_modal.html" %}' in default
    assert '{% include "partials/advanced_filter_modal.html" %}' in default


def test_the_rail_and_the_notices_open_them():
    js = read("assets", "js", "all_js.js")
    fn = js.split("function openSidebarFilter(", 1)[1].split("\n}\n", 1)[0]
    assert "post_type: '#postTypeFilterModal'" in fn and "$('#advancedFilterModal').modal('show')" in fn
    assert "openSidebarFilter('post_type')" in HOME and "openSidebarFilter('advanced')" in HOME
    assert 'href="/feed_filter?clear=1"' in HOME
    for name in ("post_type_names", "feed_filter_chips", "feed_filter_notices"):
        assert name in HOME, name


def test_the_popup_is_a_plain_form_with_every_field_the_rules_know():
    assert 'action="/feed_filter" method="get"' in POPUP
    for field in ff.EMPTY:
        assert f'name="{field}"' in POPUP or f'choice("{field}"' in POPUP, field
    assert 'href="/feed_filter?clear=1"' in POPUP
    assert "| safe" not in POPUP


def test_two_routes_save_the_choice_and_go_back_to_the_feed():
    for route, name in (('@app.route("/post_type"', "post_type_filter"), ('@app.route("/feed_filter"', "advanced_feed_filter")):
        assert route in FRONT, route
        body = _function(FRONT, name)
        assert "base_feed" in body and "redirect(" in body, name
    assert "feed_filter_rules.parse_types(" in _function(FRONT, "post_type_filter")
    # only the fields that are set: the session is a cookie that also holds the sign-in
    assert "feed_filter_rules.compact(" in _function(FRONT, "advanced_feed_filter")


def test_all_for_you_and_following_pass_the_filter_into_their_cached_id_lists():
    for name, calls in (("front_all", 1), ("for_you", 2), ("following", 1)):
        body = _function(FRONT, name)
        assert "_feed_filter_for(v)" in body, name
        assert body.count("feed_filter=feed_filter") == calls, name
        assert "**feed_filter_context" in body, name
        assert "feed_filter is None" in body, name        # the site's pinned post isn't shown in a filtered feed
    # the API's listings are never narrowed by a browser session's filters
    assert 'request.path.startswith("/api/")' in _function(FRONT, "_feed_filter_for")


def test_each_id_list_joins_submission_aux_once_and_applies_the_filter():
    lists = {"frontlist": _function(FRONT, "frontlist"),
             "idlist": _function(USER, "idlist"),
             "for_you_idlist": _function(USER, "for_you_idlist")}
    for name, body in lists.items():
        assert body.count("join(Submission.submission_aux)") == 1, name
        assert "feed_filter_sql.needs_aux(feed_filter)" in body, name
        assert "feed_filter_sql.apply(posts, feed_filter," in body, name
    # a named argument of the memoized frontlist, so it is part of the cache key
    assert "feed_filter=None" in lists["frontlist"].split("):", 1)[0]


def test_guild_pages_profiles_and_search_are_not_filtered():
    assert "feed_filter" not in read("routes", "boards.py")
    assert "feed_filter" not in read("routes", "users.py")
    assert "feed_filter_sql" not in read("routes", "search.py")


def test_both_stylesheets_draw_the_new_parts():
    for name in ("main.scss", "main_dark.scss"):
        css = read("assets", "style", name)
        for rule in (".post-type-choice", ".post-type-group", ".adv-filter-section", ".adv-filter-heading",
                     ".adv-filter-choice", ".feed-filter-notice .feed-filter-chip"):
            assert rule in css, (name, rule)
        # guild CSS and the composer test read from this marker to the end: the new rules sit before it
        assert css.index(".adv-filter-choice") < css.index("// Create post card")


def test_the_icons_are_font_awesome_free():
    names = set((ROOT.parent / "tests" / "data" / "fa_free_6_4_0_names.txt").read_text(encoding="utf-8").split())
    for icon in re.findall(r"fa-(shapes|sliders|caret-down)", SIDEBAR + HOME + POPUP):
        assert icon in names, icon


# --- the saved default (users.feed_defaults) ------------------------------------------------------------------------

def test_the_default_is_saved_on_the_account_by_a_form_key_protected_post():
    route = FRONT.split('@app.post("/settings/feed_defaults")', 1)[1].split("\ndef ", 1)[0]
    assert "@auth_required" in route and "@validate_formkey" in route
    body = _function(FRONT, "feed_defaults")
    for action in ('"save"', '"reset"', '"clear"'):
        assert action in body, action
    assert "abort(400)" in body and "base_feed" in body


def test_the_default_is_loaded_into_a_new_session_once_and_checked_again():
    assert "_apply_saved_defaults(v)" in _function(FRONT, "_resolve_active_filters")
    assert "_apply_saved_defaults(getattr(g, 'v', None))" in _function(FRONT, "_stored_feed_filters")
    once = _function(FRONT, "_apply_saved_defaults")
    assert "feed_defaults_for" in once
    saved = _function(FRONT, "_saved_defaults")
    assert "known_regions=REGION_CENTROIDS" in saved and "known_categories=ALL_SUBCAT_IDS" in saved


def test_the_sidebar_and_the_phone_sheet_offer_it_to_members_only():
    row = SIDEBAR.split('class="guild-recommendations-item feed-defaults"', 1)[1].split("</li>", 1)[0]
    before = SIDEBAR.split('class="guild-recommendations-item feed-defaults"', 1)[0]
    assert before.rstrip().endswith("{% if v %}{# the member's default filters, loaded into every new session (routes/front.py feed_defaults) #}\n                <li")
    assert 'action="/settings/feed_defaults"' in row and 'name="formkey"' in row
    for action in ('value="save"', 'value="reset"', 'value="clear"'):
        assert action in row, action
    assert 'action="/settings/feed_defaults"' in POPUP and "{% if v %}" in POPUP


def test_the_column_is_in_the_migration_the_schema_and_the_model():
    migration = (ROOT.parent / "scripts" / "migrations" / "2026-11-12_defaults_descriptions_bells.sql").read_text(encoding="utf-8")
    schema = (ROOT.parent / "schema.sql").read_text(encoding="utf-8")
    assert "ADD COLUMN IF NOT EXISTS feed_defaults text" in migration
    assert "feed_defaults text DEFAULT '' NOT NULL" in schema
    assert "('2026-11-12_defaults_descriptions_bells.sql')" in schema
    assert "feed_defaults = Column(String" in USER
