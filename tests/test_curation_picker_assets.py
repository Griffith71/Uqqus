"""Add to curation, in the source: the routes' guards, the owner-only rule, the anonymity promise, the menus."""
import re
from pathlib import Path
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parent.parent
RUQQUS = ROOT / "ruqqus"


def read(*parts):
    return RUQQUS.joinpath(*parts).read_text()


CURATIONS = read("routes", "curations.py")
MENU = read("templates", "partials", "post_menu_items.html")
LISTING = read("templates", "submission_listing.html")
PAGE = read("templates", "submission.html")
DEFAULT = read("templates", "default.html")
JS = read("assets", "js", "curation_picker.js")
MODAL = read("templates", "partials", "curation_picker_modal.html")


def func(name):
    start = CURATIONS.index(f"def {name}(")
    nxt = re.search(r"\n@app\.route|\ndef ", CURATIONS[start + 10:])
    return CURATIONS[start:start + 10 + nxt.start()]


def test_the_form_routes_and_the_menu_share_one_set_of_rules_and_inserts():
    assert "from ruqqus.helpers import curation_members" in CURATIONS
    assert "curation_members.add_user(g.db, curation.id, target.id)" in func("curation_add_user")
    assert "curation_members.add_guild(g.db, curation.id, board.id)" in func("curation_add_guild")
    assert "curation_members.author_refusal(False, target)" in func("curation_add_user")
    assert "curation_members.guild_refusal(False, board, curation.is_private)" in func("curation_add_guild")


def test_the_add_route_is_owner_only_form_keyed_and_loads_the_post_like_every_route():
    head = CURATIONS.split("def post_add_to_curation(")[0].rsplit("\n\n\n", 1)[1]
    assert "/api/post/<pid>/add_to_curation" in head and "@auth_required" in head and "@validate_formkey" in head
    body = func("post_add_to_curation")
    assert "_post_for_curation(pid, v)" in body
    assert "_owned_curation_or_404(request.form.get(\"curation\"" in body
    assert 'if what not in curation_members.WHAT' in body
    assert "_invalidate_curation_feed(curation.id)" in body
    loader = func("_post_for_curation")
    assert "from ruqqus.routes.post_follows import _post_or_404" in loader


def test_an_anonymous_posts_author_is_never_added_or_named():
    body = func("post_add_to_curation")
    assert "author_refusal(bool(post.is_anonymous), post.author)" in body
    listing = func("curations_mine")
    assert "account = None if post.is_anonymous else post.author" in listing
    assert '"author": {"username": account.username} if can["author"] else None' in listing


def test_the_sheet_is_offered_to_members_outside_the_frames_and_draws_text_only():
    assert '{% if v and not request.args.get(\'embed\') %}{% include "partials/curation_picker_modal.html" %}{% endif %}' in DEFAULT
    assert "asset_url('js/curation_picker.js')" in DEFAULT
    assert "innerHTML" not in JS
    assert "/^[0-9a-z]{1,10}$/.test(id)" in JS and "closest('[data-curation-post]')" in JS
    assert 'id="curationPickerModal"' in MODAL


def test_every_post_menu_offers_it():
    for src in (LISTING, PAGE):
        assert src.count("post_menu.curation_item(p, v)") == 1 and src.count("post_menu.curation_button(p, v)") == 1


def test_the_menu_item_renders_only_where_something_can_be_added():
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader(str(RUQQUS / "templates")))
    env.globals["author_of"] = lambda item, viewer: item.author       # the site's Jinja global (helpers/anonymity.py)
    module = env.get_template("partials/post_menu_items.html").make_module()
    viewer = NS(id=2)

    def post(**kw):
        base = dict(base36id="7k", is_banned=False, deleted_utc=0, is_anonymous=False, is_profile_post=False,
                    author=NS(is_deleted=False))
        base.update(kw)
        return NS(**base)

    assert "Add to curation" in str(module.curation_item(post(), viewer))
    assert "Add to curation" in str(module.curation_button(post(), viewer))
    assert "Add to curation" in str(module.curation_item(post(is_profile_post=True), viewer))          # the author
    assert "Add to curation" in str(module.curation_item(post(is_anonymous=True), viewer))            # the guild
    assert str(module.curation_item(post(is_anonymous=True, is_profile_post=True), viewer)).strip() == ""
    assert str(module.curation_item(post(is_profile_post=True, author=NS(is_deleted=True)), viewer)).strip() == ""
    assert str(module.curation_item(post(deleted_utc=5), viewer)).strip() == ""
    assert str(module.curation_item(post(), None)).strip() == ""
