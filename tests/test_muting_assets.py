"""Muting crosses the model, the schema, every feed that leaves out blocked authors, the routes
and the menus. These checks fail when new code forgets: a feed that hides blocked authors but
shows muted ones, or a mute that starts acting like a block."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


USER = read("ruqqus", "classes", "user.py")
ROUTES = read("ruqqus", "routes", "muting.py")


def body(source, name, stop):
    return source.split(f"def {name}(")[1].split(stop)[0]


def test_the_model_the_schema_and_the_migration_have_the_same_columns():
    model = set(re.findall(r"^    (\w+) = Column\(", read("ruqqus", "classes", "usermute.py"), re.M))
    assert model == {"id", "user_id", "target_id", "created_utc"}
    for sql, header in ((read("schema.sql"), "CREATE TABLE public.usermutes"),
                        (read("scripts", "migrations", "2026-10-08_muting.sql"), "CREATE TABLE IF NOT EXISTS usermutes")):
        block = re.search(rf"{header} \((.*?)\n\);", sql, re.S).group(1)
        columns = {line.split()[0] for line in (x.strip() for x in block.splitlines()) if line and not line.startswith("CONSTRAINT")}
        assert columns == model, header
        assert "CONSTRAINT usermutes_pair_key UNIQUE (user_id, target_id)" in block
    assert "from .usermute import *" in read("ruqqus", "classes", "__init__.py")
    assert "from .muting import *" in read("ruqqus", "routes", "__init__.py")


def test_every_feed_that_leaves_out_blocked_authors_leaves_out_muted_ones_too():
    expected = {
        ("routes", "front.py"): 2,               # the All feed and the site's comment listing
        ("routes", "boards.py"): 1,              # several guilds at once
        ("routes", "search.py"): 1,
        ("routes", "curations.py"): 1,           # also what an outside feed server may name (_seen_among)
        ("classes", "boards.py"): 2,             # one guild's posts and comments
        ("helpers", "trending_store.py"): 1,     # a trending topic's posts
    }
    for parts, count in expected.items():
        assert read("ruqqus", *parts).count("hide_muted(") >= count, parts
    for name in ("idlist", "for_you_idlist"):
        assert "hide_muted(posts, self, Submission)" in body(USER, name, "\n    def "), name


def test_notifications_and_their_counts_leave_muted_accounts_out():
    for name in ("notification_commentlisting", "notification_postlisting", "comment_notifications_count",
                 "post_notifications_count", "notifications_count", "mentions_count"):
        assert "hide_muted(" in body(USER, name, "\n    @property\n" if name.endswith("count") or name == "mentions_count" else "\n    def "), name


def test_a_search_for_one_author_is_not_filtered_that_is_going_to_them_on_purpose():
    search = read("ruqqus", "routes", "search.py")
    assert "if 'author' not in criteria:\n        posts = hide_muted(posts, v, Submission)" in search


def test_profiles_saved_lists_and_histories_are_not_filtered():
    for name, stop in (("userpagelisting", "\n    def "), ("commentlisting", "\n    def "), ("forwarded_idlist", "\n    def "),
                       ("history_idlist", "\n    def ")):
        assert "hide_muted" not in body(USER, name, stop), name


def test_a_mute_is_not_a_block_nothing_that_checks_blocks_reads_mutes():
    for parts in (("helpers", "chat_permissions.py"), ("helpers", "comment_permission.py")):
        assert "mute" not in read("ruqqus", *parts).lower(), parts
    assert "UserBlock" not in ROUTES and "UserMute" in ROUTES
    block_checks = USER.split("def has_block(")[1].split("def has_blocked_guild")[0]
    assert "UserMute" not in block_checks


def test_the_routes_need_a_login_and_the_form_key_and_only_write_mutes():
    for name in ("settings_mute_user", "settings_unmute_user"):
        head = ROUTES.split(f"def {name}(")[0].rsplit("@app.route(", 1)[1]
        assert 'methods=["POST"]' in head and "@auth_required" in head and "@validate_formkey" in head, name
    refusals = body(ROUTES, "_target", "\n\n\n")
    for rule in ("user.id == v.id", "user.id == 1", "user.is_deleted"):
        assert rule in refusals, rule
    assert ROUTES.count("muting.clear_cached_feeds(v)") == 2


def test_a_mute_clears_the_feeds_that_are_kept_for_a_few_minutes():
    clear = read("ruqqus", "helpers", "muting.py").split("def clear_cached_feeds(")[1]
    for part in ("v.idlist", "v.for_you_idlist", "frontlist", "curation_idlist", "searchlisting"):
        assert part in clear, part


def test_an_anonymous_item_is_never_hidden_by_a_mute():
    source = read("ruqqus", "helpers", "muting.py")
    assert "model.is_anonymous == True" in source
    assert 'getattr(item, "is_anonymous", False)' in source


def test_someone_who_muted_the_poster_gets_no_bell_notification_unless_the_post_is_anonymous():
    posts = read("ruqqus", "routes", "posts.py")
    assert "if not new_post.is_anonymous:\n        muters = {x[0] for x in g.db.query(UserMute.user_id).filter_by(target_id=v.id).all()}" in posts


def test_who_to_follow_never_suggests_a_muted_account():
    assert "UserMute" in read("ruqqus", "helpers", "suggestions.py").split("def _blocked_user_ids(")[1].split("def users_for(")[0]


def test_the_menus_offer_mute_where_they_offer_block():
    listing, page, comments = (read("ruqqus", "templates", n) for n in ("submission_listing.html", "submission.html", "comments.html"))
    for name, html in (("submission_listing.html", listing), ("submission.html", page)):
        for item in ("mute-user-{{ p.base36id }}", "unmute-user-{{ p.base36id }}", "mute-user-button-{{ p.base36id }}",
                     "unmute-user-button-{{ p.base36id }}"):
            assert html.count(f'id="{item}"') == 1, (name, item)
        assert "/settings/mute?username=" in html and "/settings/unmute?username=" in html
    assert comments.count("/settings/mute?username=") == 2
    # never offered for an anonymous author (the name would be "Anonymous")
    assert comments.count("{% if not anon_hidden(c, v) %}\n              <a class=\"dropdown-item\"") == 1


def test_a_muted_comment_is_a_notice_and_the_replies_stay():
    comments = read("ruqqus", "templates", "comments.html")
    assert "or is_muted(c, v)) and not c.distinguish_level and not (v and v.admin_level>=3)" in comments
    assert "You muted @{{ author_of(c, v).username }}" in comments and "/settings/unmute?username=" in comments


def test_a_profile_and_the_settings_page_can_mute_and_unmute():
    profile = read("ruqqus", "templates", "userpage.html")
    for item in ("mute-profile", "unmute-profile", "mute-profile-mobile", "unmute-profile-mobile"):
        assert profile.count(f'id="{item}"') == 1, item
    settings = read("ruqqus", "templates", "settings_blocks.html")
    assert 'id="muted-accounts"' in settings and "{% for mute in muted %}" in settings
    assert "muted_accounts(v)" in read("ruqqus", "routes", "settings.py")
    assert "Block &amp; Mute" in read("ruqqus", "templates", "settings.html")


def test_the_template_helpers_are_registered():
    assert "app.jinja_env.globals.update(is_muted=_is_muted, has_muted=_has_muted)" in ROUTES
