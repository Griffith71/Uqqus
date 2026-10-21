"""Add to curation, in the source. A curation is a feed built from accounts, guilds and topics, never from a post (the
owner, 2026-10-20): nothing on a post or comment adds to one. The sheet opens from a profile's menu (an account) and a
guild page's menu (a guild); the routes' guards, the owner-only rule and the guild's own visibility."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUQQUS = ROOT / "ruqqus"


def read(*parts):
    return RUQQUS.joinpath(*parts).read_text(encoding="utf-8")


CURATIONS = read("routes", "curations.py")
MENU = read("templates", "partials", "post_menu_items.html")
LISTING = read("templates", "submission_listing.html")
PAGE = read("templates", "submission.html")
COMMENTS = read("templates", "comments.html")
DEFAULT = read("templates", "default.html")
JS = read("assets", "js", "curation_picker.js")
MODAL = read("templates", "partials", "curation_picker_modal.html")


def func(name):
    start = CURATIONS.index(f"def {name}(")
    nxt = re.search(r"\n@app\.route|\ndef ", CURATIONS[start + 10:])
    return CURATIONS[start:start + 10 + nxt.start()]


def test_nothing_on_a_post_or_comment_adds_to_a_curation():
    for src in (MENU, LISTING, PAGE, COMMENTS):
        assert "curation_item" not in src and "curation_button" not in src and "data-curation-post" not in src
    assert "/api/post/<pid>/add_to_curation" not in CURATIONS and "def post_add_to_curation(" not in CURATIONS
    assert "post=" not in JS and "/api/post/" not in JS
    assert "def offered(" not in read("helpers", "curation_members.py")


def test_the_form_routes_and_the_menus_share_one_set_of_rules_and_inserts():
    assert "from ruqqus.helpers import curation_members" in CURATIONS
    assert "curation_members.add_user(g.db, curation.id, target.id)" in func("curation_add_user")
    assert "curation_members.add_guild(g.db, curation.id, board.id)" in func("curation_add_guild")
    assert "curation_members.author_refusal(False, target)" in func("curation_add_user")
    assert "curation_members.guild_refusal(False, board, curation.is_private)" in func("curation_add_guild")


def test_the_sheet_lists_only_for_an_account_or_a_guild():
    mine = func("curations_mine")
    assert '_curations_mine_for_account(request.args.get("user", ""), v)' in mine
    assert '_curations_mine_for_guild(request.args.get("guild", ""), v)' in mine
    assert mine.rstrip().endswith("abort(400)")


def test_a_guild_is_added_only_by_a_member_who_may_see_it_and_only_to_their_own_curation():
    head = CURATIONS.split("def guild_add_to_curation(")[0].rsplit("\n\n\n", 1)[1]
    assert "/api/guild/<name>/add_to_curation" in head and "@auth_required" in head and "@validate_formkey" in head
    body = func("guild_add_to_curation")
    assert "_guild_for_curation(name, v)" in body
    assert '_owned_curation_or_404(request.form.get("curation", "").strip(), v)' in body
    assert "curation_members.guild_refusal(False, board, curation.is_private)" in body
    assert "_invalidate_curation_feed(curation.id)" in body
    # the guild page's own visibility: a private or Circle guild is a 404 to anyone it is hidden from
    loader = func("_guild_for_curation")
    for check in ("board.is_banned", "not board.can_view(v)", "board_hidden(board, v)", "abort(404)"):
        assert check in loader, check
    assert "_guild_for_curation(name, v)" in func("_curations_mine_for_guild")


def test_an_account_is_added_from_its_profile_menu_as_before():
    assert "_account_for_curation(username, v)" in func("user_add_to_curation")
    loader = func("_account_for_curation")
    assert "user_hidden(target, v) or target.is_blocking or target.is_blocked" in loader


def test_the_guild_menus_offer_it_and_the_sheet_draws_text_only():
    for name in ("board.html", "board_comments.html"):
        assert 'data-curation-guild="{{ b.name }}"' in read("templates", name), name
    assert read("templates", "board.html").count('data-curation-guild="{{ b.name }}"') == 2       # desktop, phone
    assert '{% if v and not request.args.get(\'embed\') %}{% include "partials/curation_picker_modal.html" %}{% endif %}' in DEFAULT
    assert "asset_url('js/curation_picker.js')" in DEFAULT
    assert "innerHTML" not in JS
    assert "closest('[data-curation-user], [data-curation-guild]')" in JS
    assert "/^[A-Za-z0-9_]{3,25}$/.test(name)" in JS                         # an account: a username only
    assert "/^[A-Za-z0-9][A-Za-z0-9_]{2,24}$/.test(guild)" in JS             # a guild: the guild name rule
    assert 'id="curationPickerModal"' in MODAL and "never from a" in MODAL
