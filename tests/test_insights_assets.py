"""Post insights cross the model, the schema, the counting hook on both post pages, the report, three pages, the
post menus and the Premium page. These checks keep the layers agreeing and keep the promises: only counts are
stored or shown (nobody who looked, voted or answered a poll is named), only the original author reads a
report, only with Premium, and opening a report never spends a coin."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


STORE = read("ruqqus", "helpers", "insights_store.py")
ROUTES = read("ruqqus", "routes", "insights.py")
POSTS = read("ruqqus", "routes", "posts.py")
PAGE = read("ruqqus", "templates", "insights.html")
OVERVIEW = read("ruqqus", "templates", "insights_overview.html")
LOCKED = read("ruqqus", "templates", "insights_locked.html")


def function(source, name):
    return source.split(f"def {name}(")[1].split("\n\n\n")[0]


# --- storage -----------------------------------------------------------------------------------------

def test_the_model_the_schema_and_the_migration_have_the_same_columns():
    model = set(re.findall(r"^    (\w+) = Column\(", read("ruqqus", "classes", "post_view.py"), re.M))
    assert model == {"id", "post_id", "day", "views"}
    for sql, header in ((read("schema.sql"), "CREATE TABLE public.post_view_days"),
                        (read("scripts", "migrations", "2026-10-09_post_insights.sql"), "CREATE TABLE IF NOT EXISTS post_view_days")):
        block = re.search(rf"{header} \((.*?)\n\);", sql, re.S).group(1)
        columns = {line.split()[0] for line in (x.strip() for x in block.splitlines()) if line and not line.startswith("CONSTRAINT")}
        assert columns == model, header
        assert "CONSTRAINT post_view_days_key UNIQUE (post_id, day)" in block
    assert "from .post_view import *" in read("ruqqus", "classes", "__init__.py")
    assert "from .insights import *" in read("ruqqus", "routes", "__init__.py")


def test_nothing_that_says_who_looked_is_stored():
    # a count per post and day: no account, no address, no user agent, in the table or the migration
    sql = read("scripts", "migrations", "2026-10-09_post_insights.sql").split("CREATE TABLE")[1].split(");")[0]
    for word in ("user", "ip", "agent", "address", "viewer"):
        assert word not in sql.lower().replace("post_id", ""), word
    # the "same viewer within half an hour" check lives in Redis and expires on its own
    assert 'r.set(f"pv:{post.id}:{key}", "1", nx=True, ex=rules.DEBOUNCE_SECONDS)' in STORE
    assert "ViewHistory" in STORE and "viewed_utc" in STORE           # unique signed-in viewers: only a count of them


# --- counting ----------------------------------------------------------------------------------------------

def test_a_view_is_not_counted_for_the_author_a_robot_a_prefetch_or_a_removed_post():
    body = function(STORE, "count_view")
    for rule in ("post.is_banned or post.deleted_utc", "viewer.id == post.author_id", "rules.is_robot(agent)",
                 "rules.is_prefetch(request.headers)"):
        assert rule in body, rule
    assert body.index("viewer.id == post.author_id") < body.index("r.set(")      # nothing is spent on the author's own opens


def test_counting_can_never_spoil_the_page():
    body = function(STORE, "count_view")
    assert "with g.db.begin_nested():" in body                          # a failed write only rolls back its savepoint
    assert "except Exception:\n        pass" in body
    assert "r is None" in body                                          # no Redis, no counting
    assert "ON CONFLICT (post_id, day) DO UPDATE SET views = post_view_days.views + 1" in STORE


def test_both_post_pages_count_after_recording_the_view_and_for_visitors_too():
    assert POSTS.count("insights_store.count_view(v, post)") == 2
    for chunk in POSTS.split("record_view(v, post)")[1:]:
        assert chunk.lstrip().startswith("insights_store.count_view(v, post)")
    # not under `if v:` (a visitor's open counts), and the import comes after the classes star-import
    assert "    if v:\n        record_view(v, post)\n    insights_store.count_view(v, post)" in POSTS
    assert POSTS.index("from ruqqus.classes import *") < POSTS.index("from ruqqus.helpers import insights_store")


# --- who may read a report -----------------------------------------------------------------------------------

def test_a_report_is_for_the_original_author_of_a_live_post_and_a_guild_copy_resolves_to_it():
    body = function(ROUTES, "_mine")
    assert "primary_id = primary_post_id(post)" in body
    assert "primary.author_id != v.id or primary.is_banned or primary.deleted_utc" in body
    assert body.count("abort(404)") == 3                                 # never a hint that it exists
    for name in ("post_insights", "post_insights_json", "insights_overview"):
        head = ROUTES.split(f"def {name}(")[0].rsplit("@app.route(", 1)[1]
        assert "@auth_required" in head and 'methods=["GET"]' in head, name


def test_premium_is_checked_without_the_renewing_property():
    assert "has_premium_no_renew" in ROUTES
    assert not re.search(r"\.has_premium\b(?!_)", ROUTES)               # the has_premium property renews as a side effect
    assert ROUTES.count("if not v.has_premium_no_renew:") == 3
    assert 'return jsonify({"error": "Post insights are for Premium accounts."}), 403' in ROUTES
    assert 'render_template("insights_locked.html", v=v, post=primary)' in ROUTES
    assert 'href="/settings/premium"' in LOCKED


# --- only counts ---------------------------------------------------------------------------------------------------

def test_the_report_never_reads_who_voted_looked_or_answered():
    # no account is selected, named or joined: counts, sums and a count of distinct viewers only
    assert "username" not in STORE and "import User" not in STORE and "User," not in STORE.split("from ruqqus.classes.history")[0]
    report = function(STORE, "report")
    assert "Vote.user_id" not in STORE and "PollVote" not in STORE
    assert "func.count(func.distinct(ViewHistory.user_id))" in report
    assert "ViewHistory.user_id != primary.author_id" in report            # the author's own opens are not "viewers"
    assert "poll_store.load_one(db, primary, viewer)" in report            # the poll's counts come from the one place that hides names
    # the JSON drops a poll to labels and counts
    assert '{"label": o["label"], "votes": o["votes"], "percent": o["percent"]}' in ROUTES


def test_the_pages_draw_no_names_and_escape_everything_but_the_title():
    for html in (PAGE, OVERVIEW, LOCKED):
        assert html.count("| safe") == html.count("title | safe")           # the title is stored escaped, as everywhere
        for word in ("username", "author", ".user", "voter"):
            assert word not in html.replace("Only you can see this", ""), word
    assert '{{ place.name }}' in PAGE and "/+{{ place.name }}" in PAGE
    assert "{{ o.label }}" in PAGE


def test_the_charts_have_a_table_for_anyone_who_cannot_see_the_bars():
    assert 'role="img" aria-label="{{ name }} per day' in PAGE and "<summary>Show as a table</summary>" in PAGE
    assert "{{ chart(data.views_chart," in PAGE and "{{ chart(data.engagement_chart," in PAGE


# --- the way in --------------------------------------------------------------------------------------------------------

def test_the_menus_of_the_authors_own_post_offer_insights_beside_co_authors():
    for name in ("submission_listing.html", "submission.html"):
        html = read("ruqqus", "templates", name)
        assert html.count("may_see_insights(p, v)") == 2, name
        assert html.count('href="/post/{{ p.base36id }}/insights"') == 2, name
        for line in html.splitlines():
            if "may_see_insights" in line:
                assert "Insights" in line
    helper = ROUTES.split("def _may_see(")[1].split("app.jinja_env")[0]
    assert "post.author_id == v.id and not post.repost_id and not post.deleted_utc and not post.is_banned" in helper
    assert "app.jinja_env.globals.update(may_see_insights=_may_see)" in ROUTES


def test_the_premium_page_lists_insights_as_a_perk():
    assert 'href="/insights"' in read("ruqqus", "templates", "settings_premium.html")


def test_both_stylesheets_style_the_cards_and_the_bars():
    for sheet in ("main.scss", "main_dark.scss"):
        css = read("ruqqus", "assets", "style", sheet)
        for selector in (".insight-stats {", ".insight-stat {", ".insight-value {", ".insight-bars {", ".insight-bar-col {",
                         ".insight-bar {", ".insight-axis {", "@keyframes insight-grow {"):
            assert selector in css, (sheet, selector)
        assert "prefers-reduced-motion" in css.split("@keyframes insight-grow")[1].split("// A continuous feed")[0], sheet
