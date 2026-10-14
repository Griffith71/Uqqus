"""Follow a post, in the source: the notification route and lists, the routes' guards, the menus, the schema."""
import re
from pathlib import Path
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parent.parent
RUQQUS = ROOT / "ruqqus"


def read(*parts):
    return (RUQQUS.joinpath(*parts)).read_text()


COMMENTS = read("routes", "comments.py")
USER = read("classes", "user.py")
ROUTES = read("routes", "post_follows.py")
LISTING = read("templates", "submission_listing.html")
PAGE = read("templates", "submission.html")
MENU = read("templates", "partials", "post_menu_items.html")


def func(src, name, end="\n@app.route"):
    start = src.index(f"def {name}(")
    stop = src.find(end, start)
    return src[start:stop if stop != -1 else len(src)]


def test_a_comment_on_a_post_tells_the_responsible_person_and_the_followers():
    body = func(COMMENTS, "api_comment", "\n@app.route")
    assert "if level == 1:" in body
    assert "post_follows.recipients(" in body
    assert "visible=lambda ids: _followers_who_may_read(parent_post, ids)" in body
    # a comment's author is still told of a comment on their comment
    assert "elif parent.author.id != v.id:" in body
    # the old rule ("parent.author is told") must not be what a post's comment does any more
    assert not re.search(r"^\s*if parent\.author\.id != v\.id:", body, re.M)


def test_the_helper_module_is_not_hidden_by_a_class_module_of_the_same_name():
    # `from ruqqus.classes import *` brings every classes submodule in as a name, after the helper import
    # (the coauthors gotcha in CLAUDE.md), so the model's module is named differently
    assert not (RUQQUS / "classes" / "post_follows.py").exists()
    assert (RUQQUS / "classes" / "postfollow.py").exists()
    assert "from .postfollow import *" in read("classes", "__init__.py")


def test_the_notification_lists_count_followed_and_forwarded_posts_as_theirs():
    assert USER.count("ps = post_follows.owned_post_ids(self.id)") == 4
    assert "ps = g.db.query(Submission.id).filter(Submission.author_id == self.id)" not in USER
    assert "ps=g.db.query(Submission.id).filter(Submission.author_id==self.id)" not in USER


def test_the_routes_check_what_every_route_that_loads_a_post_checks():
    assert "@is_not_banned\n@validate_formkey\ndef follow_post(" in ROUTES
    assert "@is_not_banned\n@validate_formkey\ndef unfollow_post(" in ROUTES
    helper = func(ROUTES, "_post_or_404", "\n@app.route")
    for needed in ("get_post(pid, v=v, graceful=True)", "post.is_banned", "post.deleted_utc", "post_hidden(post, v)",
                   "post.in_private_guild_for(v)", "post.board.can_view(v)"):
        assert needed in helper, needed
    assert "from .post_follows import *" in read("routes", "__init__.py")


def test_the_post_pages_attach_the_follow_state_once_per_page():
    get = read("helpers", "get.py")
    assert "post_follows.attach(g.db, posts, v)" in get
    assert "post_follows.attach(nSession, [x], v)" in get


def test_every_post_menu_offers_follow_and_unfollow_from_one_partial():
    assert '{% import "partials/post_menu_items.html" as post_menu %}' in LISTING
    assert '{% import "partials/post_menu_items.html" as post_menu %}' in PAGE
    assert LISTING.count("post_menu.follow_item(p, v)") == 1 and LISTING.count("post_menu.follow_button(p, v)") == 1
    assert PAGE.count("post_menu.follow_item(p, v)") == 1 and PAGE.count("post_menu.follow_button(p, v)") == 1
    assert "p.follow_state in (\"can_follow\", \"following\")" in MENU
    assert "/api/post/{{ p.base36id }}/follow" in MENU and "/api/post/{{ p.base36id }}/unfollow" in MENU


def test_the_partial_defines_every_macro_the_menus_call():
    called = set(re.findall(r"post_menu\.(\w+)\(", LISTING + PAGE))
    defined = set(re.findall(r"{% macro (\w+)\(", MENU))
    assert called == {"follow_item", "follow_button", "fewer_item", "fewer_button", "curation_item", "curation_button"}
    assert called <= defined


def test_the_menu_macros_render():
    """Rendered for real: a macro that is missing is an UndefinedError on every feed page."""
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader(str(RUQQUS / "templates")))
    env.globals["author_of"] = lambda item, viewer: item.author       # the site's Jinja global (helpers/anonymity.py)
    module = env.get_template("partials/post_menu_items.html").make_module()
    post = NS(base36id="7k", follow_state="can_follow", is_banned=False, deleted_utc=0, author_id=1)
    viewer = NS(id=2)
    assert "Follow post" in str(module.follow_item(post, viewer)) and "Unfollow post" in str(module.follow_item(post, viewer))
    assert "Follow post" in str(module.follow_button(post, viewer))
    assert "Show fewer posts like this" in str(module.fewer_item(post, viewer))
    assert "Show fewer posts like this" in str(module.fewer_button(post, viewer))
    post.follow_state = "own"
    assert "Follow post" not in str(module.follow_item(post, viewer))
    assert str(module.follow_item(post, None)).strip() == ""
    post.author_id = 2
    assert str(module.fewer_item(post, viewer)).strip() == ""


def test_the_table_is_in_the_migration_and_in_the_schema():
    migration = (ROOT / "scripts" / "migrations" / "2026-10-10_post_follows_and_feedback.sql").read_text()
    schema = (ROOT / "schema.sql").read_text()
    for sql in (migration, schema):
        assert re.search(r"CREATE TABLE (IF NOT EXISTS )?public\.post_follows", sql)
        assert "CONSTRAINT post_follows_pair_key UNIQUE (user_id, post_id)" in sql
        assert "post_follows_post_idx" in sql
    assert "REFERENCES public.users" not in migration       # no foreign keys to users, as stories
