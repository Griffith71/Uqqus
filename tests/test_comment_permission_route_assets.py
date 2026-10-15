"""Changing who can comment (and see) on a post that already exists crosses the rule, the route, the menus, the sheet, the
script, the page and the Edit form. These checks keep the layers agreeing; the rule itself is in
tests/test_comment_permission.py."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


ROUTE = read("ruqqus", "routes", "post_commenting.py")
CIRCLES = read("ruqqus", "routes", "circles.py")
SUBMISSION_PY = read("ruqqus", "classes", "submission.py")
PAGE = read("ruqqus", "templates", "submission.html")
CARD = read("ruqqus", "templates", "submission_listing.html")
SHELL = read("ruqqus", "templates", "default.html")
MODAL = read("ruqqus", "templates", "partials", "comment_permission_modal.html")
SCRIPT = read("ruqqus", "assets", "js", "comment_permission.js")
AUDIENCE_SCRIPT = read("ruqqus", "assets", "js", "circle_audience.js")


def func(source, name, nxt="\n\n\n"):
    return source.split(f"def {name}(")[1].split(nxt)[0]


def test_only_the_author_changes_who_can_comment_on_their_own_profile_post():
    head = ROUTE.split("def post_comment_permission(")[0].rsplit("\n\n\n", 1)[1]
    assert '@app.route("/api/post/<pid>/comment_permission", methods=["POST"])' in head
    assert "@is_not_banned" in head and "@validate_formkey" in head
    body = func(ROUTE, "post_comment_permission")
    assert body.index("get_post(pid, v=v)") < body.index("if post.author_id != v.id:") < body.index("cperm.parse(raw)")
    assert "abort(403)" in body and "abort(404)" in body
    assert "post.repost_id not in (0, None) or not post.is_profile_post" in body
    assert "cperm.refusal(mode, bool(post.is_anonymous))" in body
    assert "if mode == cperm.mode_of(post):" in body and "Nothing changed." in body
    # a permission change is not an edit: no history row, no edited_utc
    assert "post.edited_utc" not in ROUTE and "ContentEditHistory" not in ROUTE
    assert "app.jinja_env.globals.update(may_change_comment_permission=may_change_comment_permission)" in ROUTE
    assert "from .post_commenting import *" in read("ruqqus", "routes", "__init__.py")


def test_the_comment_route_still_asks_the_rule_for_every_commenter():
    comments = read("ruqqus", "routes", "comments.py")
    assert "cperm.restriction(" in comments
    # the rule is the same one the page asks, so a notice and a refusal never disagree
    assert "cperm.restriction(self, v) if v else cperm.closed_notice(self)" in SUBMISSION_PY


def test_four_menu_entries_the_sheet_and_the_script_agree():
    assert PAGE.count("comment-permission-item") == 2 and CARD.count("comment-permission-item") == 2
    for source in (PAGE, CARD):
        assert source.count("may_change_comment_permission(p, v)") == 2
    ids = set(re.findall(r"getElementById\('([\w-]+)'\)", SCRIPT))
    assert {"commentPermissionModal", "comment-permission-save", "comment-permission-status"} <= ids
    generated = {i for i in ids if re.fullmatch(r"comment-permission-option-\d", i)}     # one radio per choice, drawn by a loop
    assert 'id="comment-permission-option-{{ value }}"' in MODAL
    assert not [i for i in ids - generated if f'id="{i}"' not in MODAL]
    assert "innerHTML" not in SCRIPT and "status.textContent = text || '';" in SCRIPT
    assert '{% include "partials/comment_permission_modal.html" %}' in SHELL and "comment_permission.js" in SHELL
    assert "/comment_permission'" in SCRIPT and "formkey()" in SCRIPT


def test_the_sheet_offers_the_same_four_choices_as_the_composers():
    choices = re.findall(r'\((\d), "([^"]+)"\)', MODAL)
    assert choices == [("0", "Everyone"), ("1", "Accounts you follow"), ("2", "Premium accounts"), ("3", "No one")]
    # an anonymous post can't use "Accounts you follow": the sheet greys it out, the server refuses it as well
    assert "data-anonymous" in CARD and "followed.disabled = target.anonymous;" in SCRIPT


def test_a_visitor_is_told_when_comments_are_off_instead_of_being_invited():
    assert "{% if not v and not p.is_banned and comment_restriction %}" in PAGE
    assert "{% elif not v and not p.is_banned %}" in PAGE
    assert PAGE.index("{% if not v and not p.is_banned and comment_restriction %}") < PAGE.index("Jump in the discussion.")


def test_an_empty_thread_of_a_post_with_comments_off_does_not_ask_for_the_first_comment():
    assert "comments_off=cperm.closed_notice(self) is not None," in SUBMISSION_PY
    branch = PAGE.split("{% if comments_off %}")[1]
    off, rest = branch.split("{% else %}", 1)
    assert "No comments" in off and "Be the first to comment!" not in off
    assert "Be the first to comment!" in rest          # every other post keeps its invitation


def test_the_edit_form_shows_who_can_see_the_post_and_opens_the_same_sheet():
    assert 'class="btn btn-link btn-sm audience-open' in PAGE
    assert "may_change_audience(p, v) and (p.audience or 0) != 3" in PAGE
    assert ".audience-item, .audience-open" in AUDIENCE_SCRIPT


def test_narrowing_to_an_empty_circle_asks_first_and_a_circle_guild_post_is_left_alone():
    body = func(CIRCLES, "circle_post_audience", "\n\n\ndef may_change_audience")
    assert "circles.GUILD" in body.split("circles.parse_audience(raw, chosen)")[0]
    assert "circle_store.reaches_nobody(g.db, v.id, circle_ids)" in body and 'request.values.get("confirm") != "1"' in body
    assert "res.status === 409 && res.data.confirm && !fields.confirm" in AUDIENCE_SCRIPT


def test_the_help_text_and_claude_md_say_comments_can_be_turned_off():
    options = read("ruqqus", "templates", "partials", "post_options.html")
    assert '(3, "No one")' in options and "unless you choose No one" in options
    claude = read("CLAUDE.md")
    assert "Who can comment?" in claude and "No one" in claude and "post_commenting.py" in claude
