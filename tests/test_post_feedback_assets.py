"""Show fewer posts like this, in the source: which feeds honour it, the route, the menus, the reset."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUQQUS = ROOT / "ruqqus"


def read(*parts):
    return RUQQUS.joinpath(*parts).read_text()


FRONT = read("routes", "front.py")
USER = read("classes", "user.py")
ROUTE = read("routes", "post_feedback.py")
MENU = read("templates", "partials", "post_menu_items.html")
LISTING = read("templates", "submission_listing.html")
PAGE = read("templates", "submission.html")
DEFAULT = read("templates", "default.html")
JS = read("assets", "js", "post_feedback.js")


def func(src, name):
    start = src.index(f"def {name}(")
    nxt = re.search(r"\n    @|\ndef |\n@app", src[start + 10:])
    return src[start:start + 10 + nxt.start()] if nxt else src[start:]


def test_the_helper_module_is_not_hidden_by_a_class_module_of_the_same_name():
    assert not (RUQQUS / "classes" / "post_feedback.py").exists()
    assert "from .postfeedback import *" in read("classes", "__init__.py")


def test_all_and_for_you_hide_the_post_next_to_the_mutes():
    # every hide_muted of the two feeds has its hide_fewer beside it
    assert "posts = hide_muted(posts, v, Submission)\n    # posts the viewer asked to see fewer of leave All" in FRONT
    assert "posts = hide_fewer(posts, v, Submission)" in FRONT
    body = func(USER, "for_you_idlist")
    assert "posts = hide_muted(posts, self, Submission)\n        posts = post_feedback.hide_fewer(posts, self, Submission)" in body


def test_for_you_ranks_lower_what_was_asked_for_and_only_through_the_helper():
    body = func(USER, "for_you_idlist")
    assert "sunk = post_feedback.sink_clause(g.db, self.id, Submission)" in body
    assert body.count("order_by(*lead, social_proof_boost.desc()") == 5
    subcats = func(USER, "interest_subcats")
    assert "post_feedback.penalties(g.db, self.id).subcat_ids, -post_feedback.SUBCAT_PENALTY" in subcats


def test_the_route_asks_what_every_route_that_loads_a_post_asks():
    head = ROUTE.split("def post_fewer_like_this(")[0].rsplit("\n\n\n", 1)[1]
    assert '"/api/post/<pid>/fewer"' in head and "@is_not_banned" in head and "@validate_formkey" in head
    assert "from ruqqus.routes.post_follows import _post_or_404" in ROUTE
    assert "rules.clear_cached_feeds(v)" in ROUTE
    assert "from .post_feedback import *" in read("routes", "__init__.py")


def test_an_anonymous_post_never_holds_its_author_to_account():
    helper = (RUQQUS / "helpers" / "post_feedback.py").read_text()
    assert '"a": None if post.is_anonymous else post.author_id' in helper
    assert "model.is_anonymous == False" in helper


def test_every_post_menu_offers_it_and_the_script_is_loaded_for_members():
    for src in (LISTING, PAGE):
        assert src.count("post_menu.fewer_item(p, v)") == 1 and src.count("post_menu.fewer_button(p, v)") == 1
    assert MENU.count('data-fewer-post="{{ p.base36id }}"') == 2
    assert "p.author_id != v.id" in MENU
    assert "{% if v %}<script src=\"{{ asset_url('js/post_feedback.js') }}\"></script>{% endif %}" in DEFAULT


def test_the_script_is_delegated_so_cards_added_later_work_and_draws_with_text_only():
    assert "document.addEventListener('click'" in JS and "closest('[data-fewer-post]')" in JS
    assert "innerHTML" not in JS
    assert "/^[0-9a-z]{1,10}$/.test(id)" in JS


def test_both_stylesheets_have_the_folded_card():
    for name in ("main.scss", "main_dark.scss"):
        css = read("assets", "style", name)
        assert ".card.post-fewer > *:not(.post-fewer-note)" in css and ".post-fewer-note .post-fewer-undo" in css


def test_the_schema_and_the_migration_have_the_table():
    for sql in ((ROOT / "schema.sql").read_text(),
                (ROOT / "scripts" / "migrations" / "2026-10-10_post_follows_and_feedback.sql").read_text()):
        assert re.search(r"CREATE TABLE (IF NOT EXISTS )?public\.post_feedback", sql)
        assert "CONSTRAINT post_feedback_pair_key UNIQUE (user_id, post_id)" in sql
