"""Search (routes/search.py, helpers/search_query.py, helpers/search_filters.py): the operators run under every
rule a feed has, a name the viewer may not see reads as one that does not exist, and the page, the help page,
the stylesheets and the schema agree."""
import re
from pathlib import Path

import jinja2

from ruqqus.helpers import search_query as sq

ROOT = Path(__file__).resolve().parent.parent
ROUTE = (ROOT / "ruqqus" / "routes" / "search.py").read_text(encoding="utf-8")
FILTERS = (ROOT / "ruqqus" / "helpers" / "search_filters.py").read_text(encoding="utf-8")


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


def body(source, name):
    return re.split(r"\n(?=@|def |class )", source.split(f"\ndef {name}(")[1], maxsplit=1)[0]


def test_the_operators_come_first_and_every_visibility_rule_still_follows():
    listing = body(ROUTE, "searchlisting")
    order = ["search_filters.apply(posts, query, v)", "filter_posts(posts, v)", "hide_muted(posts, v, Submission)",
             "Submission.deleted_utc == 0", "Submission.post_public == True", "hide_blocked(posts, v, Submission)",
             "BoardBlock.board_id).filter_by(user_id=v.id)", "limited(g.db, run, TIME_LIMIT_MS)"]
    at = [listing.index(x) for x in order]
    assert at == sorted(at), order
    assert "@cache.memoize(300)\ndef searchlisting(" in ROUTE
    # the post search and the comment search each put the viewer's filter level in their cache key
    assert ROUTE.count("filter_level=viewer_level(v)") == 2 and "filter_level=viewer_level(v)" in body(ROUTE, "post_search")
    assert "filter_level=viewer_level(v)" in body(ROUTE, "comment_search")


def test_a_search_with_a_problem_does_not_run_and_a_slow_one_says_so():
    search = body(ROUTE, "post_search")
    assert search.index("if query.problems:") < search.index("searchlisting(")
    assert "except FeedTooSlow:" in search and "search_query.TOO_SLOW" in search
    for route in ("search", "search_guild"):
        assert "post_search(query, v, page, t, sort" in body(ROUTE, route), route
        assert '"notices": notices' in body(ROUTE, route), route


def test_both_post_searches_resolve_names_as_the_viewer():
    assert "search_filters.resolve(search_query.parse(raw), v, b=b)" in body(ROUTE, "post_search")


def test_an_account_the_viewer_may_not_see_reads_as_one_that_does_not_exist():
    account = body(FILTERS, "find_account")
    for check in ("graceful=True", "user.is_deleted", "user.is_private", "user.is_suspended", "user_hidden(user, v)",
                  "user.is_blocking or user.is_blocked"):
        assert check in account, check
    guild = body(FILTERS, "find_guild")
    for check in ("graceful=True", "PROFILE_BOARD_NAME", "board.is_banned", "board_hidden(board, v)",
                  "board.can_view(v)"):
        assert check in guild, check
    resolve = body(FILTERS, "resolve")
    assert 'tr("No account called @%(name)s.", name=part.value)' in resolve
    assert 'tr("No guild called +%(name)s.", name=part.value)' in resolve
    # no other message names an account or a guild: those two, in resolve, and nowhere else
    assert re.findall(r'"No (account|guild)', FILTERS) == ["account", "guild"]
    assert sq.NO_ACCOUNT == "No account called @%(name)s." and sq.NO_GUILD == "No guild called +%(name)s."


def test_nothing_typed_is_run_as_a_pattern_or_as_sql():
    assert not re.search(r"(?<![\w.])text\(", FILTERS)          # no raw SQL (sqlalchemy's text())
    assert FILTERS.count('.op("~*")(_host_pattern(value))') == 2 and "re.escape(host)" in body(FILTERS, "_host_pattern")
    assert FILTERS.count("like_pattern(") == 2 and FILTERS.count('escape="\\\\"') == 6


def test_the_results_page_escapes_what_was_typed():
    page = read("ruqqus", "templates", "search.html")
    tools = page.split("{% if post_search %}")[1].split("{% endif %}\n\n        </div>")[0]
    assert "| safe" not in tools
    assert "{{ chip_label }}" in tools and "{{ notice }}" in tools and '?q={{ rest | urlencode }}' in tools
    assert 'name="adv" value="1"' in tools and 'href="/help/search"' in tools
    assert "fiery" not in page and 'href="/?sort=' not in page
    for name in ("search.html", "search_boards.html", "search_users.html"):
        jinja2.Environment().parse(read("ruqqus", "templates", name))


def test_the_advanced_form_has_a_field_for_what_compose_reads():
    page = read("ruqqus", "templates", "search.html")
    for field in ("words", "from", "in", "since", "until", "has", "lang", "no_forwards"):
        assert f'name="{field}"' in page, field
        assert f'a.get("{field}"' in body(ROUTE, "advanced_redirect") or f'a.getlist("{field}")' in body(ROUTE, "advanced_redirect"), field


def test_the_help_page_is_drawn_from_the_operator_list_and_linked():
    page = read("ruqqus", "templates", "help", "search.html")
    jinja2.Environment().parse(page)
    assert "{% for op in search.OPERATORS %}" in page and "| safe" not in page
    route = read("ruqqus", "routes", "static.py")
    assert '@app.route("/help/search", methods=["GET"])' in route
    assert 'render_template("help/search.html", v=v, search=search_query)' in route
    assert "(/help/search)" in read("ruqqus", "templates", "help.html")


def test_the_post_text_index_is_in_the_migration_and_the_schema():
    migration = read("scripts", "migrations", "2026-10-11_search_body_index.sql")
    assert ("CREATE INDEX CONCURRENTLY IF NOT EXISTS submissions_body_trgm_idx ON submissions_aux "
            "USING gin (body gin_trgm_ops);") in migration
    assert ("CREATE INDEX submissions_body_trgm_idx ON public.submissions_aux USING gin "
            "(body public.gin_trgm_ops);") in read("schema.sql")


def test_both_stylesheets_style_the_search_tools():
    for sheet in ("main.scss", "main_dark.scss"):
        css = read("ruqqus", "assets", "style", sheet)
        for rule in (".search-chip {", ".search-chip a:hover", ".search-notice {", ".search-tools {",
                     ".search-advanced summary {", ".search-advanced-form {", ".search-field-wide {",
                     ".search-help-example {"):
            assert rule in css, (sheet, rule)


APP_TEMPLATES = ROOT / "ruqqus" / "templates"


# --- the tab bar (the owner, 2026-10-26: Posts, Comments, Guilds, Accounts, Curations) -------------------------------------------

def test_every_results_page_has_the_tab_bar_and_only_the_posts_search_has_the_sort_dropdowns():
    page = (APP_TEMPLATES / "search.html").read_text(encoding="utf-8")
    assert page.count("{% if post_search %}") == 1                         # test_the_results_page_escapes_what_was_typed splits on it
    assert page.index('{% include "partials/search_tabs.html" %}') < page.index("{% if post_search %}")   # outside it: every kind
    assert "search-types" not in page                                      # the Posts | Comments pair is the bar now
    assert page.count("{% if sortable %}") == 2 and "sortable=True" in ROUTE.split("def search_guild(")[1]
    assert ROUTE.count("sortable=True") == 2                               # the site's post search and a guild's
    assert "{% block mobileFeedTabs %}{% endblock %}" in page              # the feed pills are no use on a search page
    assert '{% endif %}</title>' in page and '{% endif %}"</title>' not in page
    bar = (APP_TEMPLATES / "partials" / "search_tabs.html").read_text(encoding="utf-8")
    assert '<nav class="search-tabs" aria-label="{{ _(\'What to search\') }}"' in bar and 'aria-current="page"' in bar
    assert "role=\"tablist\"" not in bar and "| safe" not in bar and 'aria-hidden="true"' in bar
    comments = (APP_TEMPLATES / "search_comments.html").read_text(encoding="utf-8")
    assert "{% block mobile_sort_bar %}" in comments and comments.count("&type=comments&sort=") >= 3     # the phone bar stays on comments


def test_the_tab_decides_the_kind_before_the_prefix_branches_and_never_changes_them():
    head = ROUTE.split("def search(v, search_type=\"posts\"):")[1].split('if query.startswith("+"):')[0]
    assert 'search_tabs.resolve(request.args.get("q", \'\'), request.args.get("type"))' in head
    assert "query = resolved.query" in head and "resolved.refused" in head and "search_nothing.html" in head
    for branch in ('if query.startswith("+"):', 'elif query.startswith("#"):', 'elif query.startswith("&"):', 'elif query.startswith("@"):',
                   'elif request.args.get("type") == "comments":'):
        assert branch in ROUTE, branch
    guild = ROUTE.split("def search_guild(")[1]
    assert 'query.startswith(("+","@","#","&"))' in guild                  # the site's kinds are the site's, in a guild too
    for kind in ("guilds", "curations", "accounts"):
        assert f'"type": "{kind}"' in ROUTE                                # the JSON says what it is (keys added, none renamed)


def test_the_navbar_boxes_keep_the_tab_and_the_placeholders_are_untouched():
    for name in ("default.html", "header.html"):
        src = (APP_TEMPLATES / name).read_text(encoding="utf-8")
        assert src.count('name="type" value="{{ search_kept_type() }}"') == 2, name
        assert src.count("+guilds, @users, #topics, &curations") == 4, name


def test_the_pagers_of_the_name_pages_ask_for_what_they_have():
    users = (APP_TEMPLATES / "search_users.html").read_text(encoding="utf-8")
    assert "{% if users %}" in users and "{% if boards %}" not in users    # account results never showed Back / Next
    for name in ("search_users.html", "search_boards.html"):
        assert "No matches." in (APP_TEMPLATES / name).read_text(encoding="utf-8")
    nothing = (APP_TEMPLATES / "search_nothing.html").read_text(encoding="utf-8")
    assert nothing.startswith('{% extends "search.html" %}') and "Nothing was searched." in nothing


def test_the_tab_styles_are_tokens_only_in_a_shared_partial_both_sheets_import():
    code = re.sub(r"/\*.*?\*/|//[^\n]*", "", (ROOT / "ruqqus" / "assets" / "style" / "_search_tabs.scss").read_text(encoding="utf-8"), flags=re.S)
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", code) and not re.search(r"\$(primary|purple|info|upvote|downvote)\b", code)
    assert "!important" not in code and "transition" not in code and "animation" not in code and "--text-subtle" not in code
    assert "overflow-x: auto" in code and "flex: 0 0 auto" in code and "white-space: nowrap" in code      # a phone scrolls the bar
    assert "@media (hover: hover) and (pointer: fine)" in code
    for sheet in ("main.scss", "main_dark.scss"):
        text = (ROOT / "ruqqus" / "assets" / "style" / sheet).read_text(encoding="utf-8")
        mark = '@import "{ruqquspath}/assets/style/_search_tabs";'
        assert mark in text and text.index(mark) < text.index('@import "{ruqquspath}/assets/style/_motion";'), sheet
    script = (ROOT / "ruqqus" / "assets" / "js" / "search_tabs.js").read_text(encoding="utf-8")
    code = re.sub(r"/\*.*?\*/|//[^\n]*", "", script, flags=re.S)
    assert "scrollIntoView" not in code and "smooth" not in code and "scrollLeft" in code    # never animate on load; only the bar scrolls
