"""Fixes from the site-wide sweep of 2026-10-13 (every GET route as a visitor, a member and an admin, then every
page and control in Chrome). Each check pins one fix so the bug can't come back unnoticed."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(*parts):
    return ROOT.joinpath("ruqqus", *parts).read_text(encoding="utf-8")


def func(source, name):
    body = source.split(f"\ndef {name}(")[1]
    return re.split(r"\n(?=@|def |class )", body, maxsplit=1)[0]


def test_a_comment_whose_post_is_gone_is_a_404_not_a_crash():
    # /comment/<cid> and /post_short/<pid>/<cid> read comment.permalink, which reads comment.post
    body = func(read("helpers", "get.py"), "get_comment")
    check = "if x is not None and ((x.parent_submission and x.post is None)"
    # and a system notification (no post at all) is not read by id either (audit CR-01)
    assert 'or (not x.parent_submission and not kwargs.get("system_ok"))):' in body
    assert check in body
    after = body.split(check)[1][:200]
    assert "return None" in after and "abort(404)" in after
    assert body.index(check) < body.rindex("return x")


def test_the_admin_stats_page_survives_without_working_s3_keys():
    src = read("routes", "admin_api.py")
    publish = func(src, "_publish_plot")
    assert "try:" in publish and "except Exception" in publish
    assert "aws.upload_from_file" not in src.replace(publish, "")
    assert func(src, "create_plot").count("_publish_plot(") == 1


def test_the_phone_install_prompt_only_runs_where_it_is_drawn():
    js = read("assets", "js", "all_js.js")
    assert "if (window.innerWidth <= 737 && document.getElementById('mobile-prompt')){" in js


def test_the_sites_own_stylesheets_are_never_rate_limited():
    # a reader opening pages quickly got 429 on main.css and saw an unstyled page
    static, boards = read("routes", "static.py"), read("routes", "boards.py")
    assert '@app.route("/assets/style/<file>.css", methods=["GET"])\n@limiter.exempt\ndef main_css(' in static
    assert '@app.route("/favicon.ico", methods=["GET"])\n@app.route("/assets/favicon.ico", methods=["GET"])\n@limiter.exempt\ndef favicon(' in static
    assert '@app.route("/assets/<board_fullname>/<theme>/<x>.css", methods=["GET"])\n@limiter.exempt\n' in boards


def test_phone_pages_do_not_scroll_sideways():
    assert '<div class="row mx-0 border-bottom bg-white sticky' in read("templates", "notifications.html")
    for page in ("search_users.html", "search_boards.html"):
        assert '<div class="row row-cols-1 row-cols-md-3 mt-3 mx-0">' in read("templates", page), page
    assert 'class="btn-group btn-group-sm flex-wrap"' in read("templates", "guild", "modlog.html")
    for page in (("help", "formatting.html"), ("admin", "obliterations.html")):
        src = read("templates", *page)
        assert src.count('<div class="table-responsive mb-5"><table') == src.count("<table"), page
    src = read("templates", "admin", "word_filter.html")
    assert src.count('<div class="table-responsive"><table') == src.count("<table") == 1
    for sheet in ("main.scss", "main_dark.scss"):
        assert re.search(r"\.post-info \{\s+overflow-wrap: anywhere;", read("assets", "style", sheet)), sheet


def test_the_formatting_help_loads_the_site_script_once_after_jquery():
    assert "asset_url('js/all_js.js')" not in read("templates", "help", "formatting.html")
    assert "asset_url('js/all_js.js')" in read("templates", "docs_default.html")


def test_icons_on_the_sign_in_pages_come_from_a_place_that_exists():
    for path in (ROOT / "ruqqus" / "templates").rglob("*.html"):
        src = path.read_text(encoding="utf-8")
        assert "/assets/fontawesome/" not in src and "use.fontawesome.com/releases/latest" not in src, path.name


def test_the_coin_price_only_runs_where_coins_are_sold():
    js = read("assets", "js", "all_js.js")
    body = js.split("coin_quote = function() {")[1][:600]
    assert "if (!coins || !btn || !promo || !promotext) return;" in body
    assert body.index("return;") < body.index("selectedOptions")


def test_the_shared_sheets_are_on_a_page_once():
    # default.html includes them for every signed-in page; a page that included them again had two #deletePostButton,
    # two #tipModal, ... (invalid HTML, and a script or a click can reach the wrong copy)
    shared = ("gif_modal", "emoji_modal", "delete_comment_modal", "delete_post_modal", "tip_modal", "forward_post_modal")
    found = {}
    for path in (ROOT / "ruqqus" / "templates").rglob("*.html"):
        for name in re.findall(r'include "(\w+)\.html"', path.read_text(encoding="utf-8")):
            if name in shared:
                found.setdefault(name, set()).add(path.name)
    for name, pages in found.items():
        assert pages <= {"default.html", "submit.html"}, (name, pages)     # submit.html is a page of its own


def test_error_pages_know_who_is_signed_in():
    # a 4xx page drawn with v=None showed "Sign In / Sign Up" to a signed-in member
    src = read("routes", "errors.py")
    viewer = func(src, "_viewer")
    assert "g.db.rollback()" in viewer and 'getattr(g, "v", None)' in viewer
    for code in ("402", "403", "404", "405", "409", "413", "422", "429", "451"):
        assert f"render_template('errors/{code}.html', v=_viewer())" in src, code
    for code in ("500",):
        assert f"render_template('errors/{code}.html', v=None)" in src, code     # the database may be what failed


def test_a_guild_without_a_category_returns_to_the_form():
    route = func(read("routes", "boards.py"), "create_board_post") if "\ndef create_board_post(" in read("routes", "boards.py") else read("routes", "boards.py")
    assert 'error="Choose a category for the guild."' in route
    assert "raw_category.isascii() and raw_category.isdigit()" in route
    form = read("templates", "make_board.html")
    assert 'name="category" class="form-control" required>' in form and '<option value="" selected disabled>' in form
