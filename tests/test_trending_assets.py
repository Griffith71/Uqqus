"""Trending topics cross the rules, the store, the job, the schema, the pages and the
stylesheets. These checks keep the layers agreeing, and keep the rules of the site
(word filter, anonymity, blocks) in the places that read posts."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "ruqqus"


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


def schema_columns(sql, header):
    m = re.search(rf"{header} \((.*?)\n\);", sql, re.S)
    assert m, header
    return {line.split()[0] for line in (x.strip() for x in m.group(1).splitlines()) if line and not line.startswith(("--", "CONSTRAINT"))}


def model_columns(name):
    body = read("ruqqus", "classes", "trending.py").split(f"class {name}(Base):")[1].split("\nclass ")[0]
    return set(re.findall(r"^    (\w+) = Column\(", body, re.M))


STORE = read("ruqqus", "helpers", "trending_store.py")
ROUTES = read("ruqqus", "routes", "trending.py")


def test_the_models_the_schema_and_the_migration_have_the_same_columns():
    schema, migration = read("schema.sql"), read("scripts", "migrations", "2026-10-07_trending.sql")
    for model, table in (("TrendingTopic", "trending_topics"), ("TrendingBlocked", "trending_blocked")):
        columns = model_columns(model)
        assert columns and schema_columns(schema, f"CREATE TABLE public.{table}") == columns, table
        assert schema_columns(migration, f"CREATE TABLE IF NOT EXISTS {table}") == columns, table
    for index in ("trending_topics_list_index", "trending_topics_slug_index", "trending_blocked_key_index"):
        assert index in schema and index in migration, index
    assert "from .trending import *" in read("ruqqus", "classes", "__init__.py")
    assert "from .trending import *" in read("ruqqus", "routes", "__init__.py")


def test_the_job_runs_under_supervisord_and_can_import_the_app():
    conf = read("supervisord.conf")
    block = conf[conf.index("[program:ruqqustrending]"):]
    assert "scripts/compute_trending.py" in block
    assert "autostart=true" in block and "autorestart=true" in block
    assert 'PYTHONPATH="/opt/ruqqus/service"' in block
    job = read("scripts", "compute_trending.py")
    assert "trending_store.compute(" in job and '"--once" in sys.argv' in job


def test_the_lists_are_replaced_in_one_transaction():
    compute = STORE.split("def compute(")[1].split("\n# --- reading")[0]
    replace = compute[compute.index("db.query(TrendingTopic).delete()"):]
    assert replace.index("db.add_all(out)") < replace.index("db.commit()")
    assert compute.count("db.commit()") == 1


def test_only_public_live_posts_by_accounts_in_good_standing_count():
    load = STORE.split("def _load(")[1].split("\ndef ")[0]
    for rule in ("Submission.is_banned == False", "Submission.deleted_utc == 0", "Submission.post_public == True",
                 "Submission.is_bot == False", "Board.is_private == False", "Board.is_banned == False",
                 "Board.all_opt_out == False", "User.is_deleted == False", "User.is_banned == 0",
                 "func.coalesce(Submission.repost_id, 0) == 0"):
        assert rule in load, rule


def test_every_list_is_built_per_word_filter_level_from_what_that_level_may_see():
    assert "LEVELS = (0, 1, 2)" in STORE
    assert "filter_posts(query, None, level=level)" in STORE
    assert "is_hidden(level, severity[label])" in STORE            # the topic's own words
    reading = STORE.split("# --- reading")[1].split("# --- admin")[0]
    assert reading.count("TrendingTopic.filter_level == level") == 2
    assert "posts = filter_posts(posts, v)" in reading
    # the routes always read at the viewer's own level
    assert ROUTES.count("viewer_level(v)") == 2 and "filter_level" not in ROUTES


def test_a_region_list_never_counts_an_anonymous_post():
    from types import SimpleNamespace as NS

    from ruqqus.helpers import trending_store as ts

    named = NS(language_code="fr", display_region="FR", is_anonymous=False)
    hidden = NS(language_code="fr", display_region="FR", is_anonymous=True)
    assert ts.scopes_of(named) == ["all", "lang:fr", "region:FR"]
    assert ts.scopes_of(hidden) == ["all", "lang:fr"]
    assert ts.scopes_of(NS(language_code=None, display_region=None, is_anonymous=False)) == ["all"]


def test_a_viewers_list_comes_from_their_filters_regions_first():
    from ruqqus.helpers import trending_store as ts

    assert ts.scopes_for(["AU", "NZ"], ["en"]) == ["region:AU", "region:NZ", "lang:en"]
    assert ts.scopes_for() == [] and ts.scopes_for(None, None) == []
    assert "_resolve_active_filters(v)" in ROUTES


def test_one_account_is_never_enough_whatever_the_setting(monkeypatch):
    from ruqqus.helpers import trending_store as ts

    for value, expected in (("1", 2), ("0", 2), ("5", 5), ("nonsense", 3)):
        monkeypatch.setenv("TRENDING_MIN_AUTHORS", value)
        assert ts.min_authors() == expected, value
    monkeypatch.delenv("TRENDING_MIN_AUTHORS")
    assert ts.min_authors() == 3


def test_a_topics_posts_respect_the_viewers_blocks():
    page = STORE.split("def visible_post_ids(")[1].split("\n# --- admin")[0]
    assert "UserBlock.target_id" in page and "BoardBlock.board_id" in page
    assert "Submission.post_public == True" in page and "Submission.deleted_utc == 0" in page


def test_the_admin_pages_are_for_admins_and_changes_need_the_form_key():
    admin = ROUTES.split("# --- admin")[1]
    assert admin.count("@admin_level_required(4)") == 3 == admin.count("def admin_trending")
    assert admin.count("@validate_formkey") == 2
    page = read("ruqqus", "templates", "admin", "trending.html")
    assert page.count('name="formkey"') == 3
    assert "/admin/trending" in read("ruqqus", "templates", "admin", "admin_home.html")


def test_the_box_is_in_the_right_sidebar_of_both_page_shells_for_everyone():
    slot = '<div id="trending-box" data-src="/inpage/trending"></div>'
    for name in ("default.html", "home.html"):
        html = read("ruqqus", "templates", name)
        assert slot in html, name
        assert html.index(slot) < html.index('id="who-to-follow"'), name     # above Who to follow
        line = next(x for x in html.splitlines() if slot in x)
        assert "{% if v %}" not in line, f"{name}: visitors see it too"
    assert slot not in read("ruqqus", "templates", "sidebar-left.html")
    shell = read("ruqqus", "templates", "default.html")
    line = next(x for x in shell.splitlines() if "/assets/js/trending.js" in x)
    assert "{% if" not in line
    script = read("ruqqus", "assets", "js", "trending.js")
    assert "getElementById('trending-box')" in script and "'embedded'" in script and "offsetParent === null" in script


def test_the_topic_page_can_be_scrolled_like_any_feed():
    html = read("ruqqus", "templates", "trending_topic.html")
    assert 'aria-label="Page navigation"' in html and 'class="pagination' in html
    assert re.search(r">\s*Next\s*<", html) and 'class="posts' in html
    assert '{% include "submission_listing.html" %}' in html       # authors are drawn by the shared card


def test_the_pages_never_show_who_posted():
    for name in ("partials/trending_box.html", "trending.html", "trending_topic.html"):
        html = read("ruqqus", "templates", *name.split("/"))
        # only the number of accounts is ever drawn
        assert not re.search(r"\.author(?!_count)", html) and "username" not in html, name


def test_both_stylesheets_style_every_class_the_templates_use():
    used = set()
    for name in ("partials/trending_box.html", "trending.html"):
        used |= set(re.findall(r"\btrend-[a-z-]+", read("ruqqus", "templates", *name.split("/"))))
    used.add("trending-box")
    assert {"trend-row", "trend-link", "trend-rank", "trend-label"} <= used
    for sheet in ("main.scss", "main_dark.scss"):
        css = read("ruqqus", "assets", "style", sheet)
        for cls in sorted(used - {"trend-count"}):
            assert f".{cls}" in css, f"{sheet} has no rule for .{cls}"
