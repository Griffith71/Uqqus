"""Co-authored posts cross the model, the schema, the routes, the composer and its drafts, submit_post, two
listings, the byline and the menus. These checks keep the layers agreeing and keep the promises: never on an
anonymous post, only the author invites, only the invited answers, and a name that cannot be invited stops
the post instead of vanishing."""
import re
from pathlib import Path

from ruqqus.helpers import coauthors as co

ROOT = Path(__file__).resolve().parent.parent


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


ROUTES = read("ruqqus", "routes", "coauthors.py")
POSTS = read("ruqqus", "routes", "posts.py")
USER = read("ruqqus", "classes", "user.py")
MODAL = read("ruqqus", "templates", "partials", "coauthors_modal.html")
SCRIPT = read("ruqqus", "assets", "js", "coauthors.js")


def route(name):
    head = ROUTES.split(f"def {name}(")[0].rsplit("@app.", 1)[1]
    return head + "def " + name + "(" + ROUTES.split(f"def {name}(")[1].split("\n@app.")[0]


# --- storage ------------------------------------------------------------------------------------

def test_the_model_the_schema_and_the_migration_have_the_same_columns():
    model = set(re.findall(r"^    (\w+) = Column\(", read("ruqqus", "classes", "coauthors.py"), re.M))
    assert model == {"id", "post_id", "user_id", "invited_by_id", "status", "created_utc", "accepted_utc"}
    for sql, header in ((read("schema.sql"), "CREATE TABLE public.post_coauthors"),
                        (read("scripts", "migrations", "2026-10-08_coauthors.sql"), "CREATE TABLE IF NOT EXISTS post_coauthors")):
        block = re.search(rf"{header} \((.*?)\n\);", sql, re.S).group(1)
        columns = {line.split()[0] for line in (x.strip() for x in block.splitlines()) if line and not line.startswith("CONSTRAINT")}
        assert columns == model, header
        assert "CONSTRAINT post_coauthors_pair_key UNIQUE (post_id, user_id)" in block
        assert "CHECK (status IN ('pending', 'accepted'))" in block
    assert "from .coauthors import *" in read("ruqqus", "classes", "__init__.py")
    assert "from .coauthors import *" in read("ruqqus", "routes", "__init__.py")
    assert f"'{co.PENDING}'" in read("schema.sql") and co.MAX_COAUTHORS == 5


# --- who may do what ---------------------------------------------------------------------------------

def test_every_change_is_a_post_that_needs_a_login_and_the_form_key():
    for name in ("coauthor_invite", "coauthor_accept", "coauthor_decline", "coauthor_leave", "coauthor_remove"):
        head = route(name).split("def ")[0]
        assert head.startswith("post(") and "@auth_required" in head and "@validate_formkey" in head, name
    for name in ("coauthor_list", "coauthor_page"):
        assert "@auth_required" in route(name).split("def ")[0], name


def test_only_the_author_invites_and_removes():
    for name in ("coauthor_invite", "coauthor_remove"):
        assert "_author_only(post, v)" in route(name), name
    assert "if post.author_id != v.id:\n        abort(403)" in ROUTES.split("def _author_only(")[1].split("\n\n\n")[0]


def test_only_the_invited_account_answers_and_a_decision_cannot_be_repeated():
    for name in ("coauthor_accept", "coauthor_decline"):
        body = route(name)
        assert "row = _row(post, v.id)" in body and "row.status != coauthors.PENDING" in body and "abort(404)" in body, name
    leave = route("coauthor_leave")
    assert "row = _row(post, v.id)" in leave and "row.status != coauthors.ACCEPTED" in leave
    assert "_row(post, v.id)" in route("coauthor_page") and "post.is_anonymous" in route("coauthor_page")


def test_an_invitation_that_can_no_longer_stand_is_dropped_on_accepting():
    accept = route("coauthor_accept")
    assert "post.is_anonymous or (author and author.any_block_exists(v))" in accept
    assert accept.index("g.db.delete(row)") < accept.index("row.status, row.accepted_utc")


def test_every_id_resolves_to_the_primary_post_and_a_dead_post_is_a_404():
    primary = ROUTES.split("def _primary(")[1].split("\n\n\n")[0]
    assert "coauthors.primary_post_id(post)" in primary and "post.is_banned or post.deleted_utc or post.board.is_banned" in primary
    assert "post_hidden(post, v)" in route("coauthor_list")


def test_the_list_shows_waiting_invitations_to_the_author_only():
    body = route("coauthor_list")
    assert "row.status != coauthors.ACCEPTED and not is_author" in body
    assert 'not post.is_anonymous and len(rows) < coauthors.MAX_COAUTHORS' in body


def test_the_menu_item_is_for_the_author_of_a_profile_post_that_is_not_anonymous():
    may = ROUTES.split("def _may_invite(")[1].split("app.jinja_env")[0]
    assert "post.author_id == v.id and not post.is_anonymous" in may and "post.repost_id in (0, None)" in may
    assert "app.jinja_env.globals.update(coauthors_of=_coauthors_of, is_coauthor=_is_coauthor, may_invite_coauthors=_may_invite)" in ROUTES


# --- the composer and submit_post ----------------------------------------------------------------------

def test_posts_imports_the_helper_after_the_classes_star_import_that_has_a_module_of_the_same_name():
    # `from ruqqus.classes import *` also brings in the module ruqqus.classes.coauthors; imported before it,
    # the helper is silently replaced and submit_post fails on its first use of it
    assert POSTS.index("from ruqqus.classes import *") < POSTS.index("from ruqqus.helpers import coauthors")
    assert POSTS.count("import coauthors") == 1


def test_a_name_that_cannot_be_invited_stops_the_post_before_it_is_made():
    refusal = POSTS.index("coauthors.parse_names(request.form.get(\"coauthors\"))")
    made = POSTS.index("    new_post = Submission(\n        author_id=v.id,")
    assert refusal < made
    block = POSTS[refusal:made]
    assert "coauthors.check_names(g.db, v, coauthor_names, anonymous=flag(request.form, \"anonymous\"))" in block
    assert "({\"error\": coauthor_error}, 400)" in block and "submit.html" in block
    # the invitations go out once the post exists, and before the draft is used up
    assert POSTS.index("coauthors.invite_names(g.db, v, new_post, coauthor_names)") < POSTS.index("_discard_draft(v, request.form.get(\"draft_id\"))")


def test_the_field_is_in_both_composers_and_travels_through_a_draft():
    macro = read("ruqqus", "templates", "partials", "post_options.html")
    assert 'name="coauthors"' in macro and 'maxlength="200"' in macro and "{% macro coauthors_field(value, prefix) %}" in macro
    assert 'post_options.coauthors_field(draft.option("coauthors", "")' in read("ruqqus", "templates", "submit.html")
    inline = read("ruqqus", "templates", "partials", "inline_composer.html")
    assert "coauthors_field" in inline.split("{% from")[1].split("\n")[0] and 'coauthors_field("", "ic-coauthors")' in inline
    # the feed composer sends its whole form, so the field needs no script there
    assert "new FormData(form)" in read("ruqqus", "assets", "js", "inline_composer.js")
    drafts = read("ruqqus", "helpers", "post_drafts.py")
    assert '"coauthors": ", ".join(names)' in drafts and 'data["coauthors"] = options["coauthors"]' in drafts
    assert "input[name=\"coauthors\"]" in read("ruqqus", "assets", "js", "post_drafts.js")


# --- where the post is listed and named ---------------------------------------------------------------------

def test_a_co_authored_post_is_on_the_co_authors_profile_and_in_the_following_feed():
    listing = USER.split("def userpagelisting")[1].split("def commentlisting")[0]
    assert 'select(PostCoauthor.post_id).where(PostCoauthor.user_id == self.id, PostCoauthor.status == "accepted")' in listing
    assert "or_(Submission.author_id == self.id, Submission.id.in_(coauthored))" in listing
    feed = USER.split("def idlist")[1][:3500]
    assert "PostCoauthor.user_id.in_(user_ids), PostCoauthor.status == \"accepted\"" in feed
    assert "and_(or_(Submission.author_id.in_(user_ids), Submission.id.in_(coauthored)), not_(Submission.is_anonymous))" in feed
    count = USER.split("def public_post_count")[1].split("@property")[0]
    assert "is_anonymous=False" in count[:200] and "PostCoauthor.status == \"accepted\"" in count and "Submission.is_anonymous == False" in count


def test_the_byline_and_the_menus_are_in_every_place_a_post_is_drawn():
    for name in ("submission_listing.html", "submission.html"):
        html = read("ruqqus", "templates", name)
        assert '{% from "partials/coauthors.html" import coauthor_line %}' in html, name
        assert html.count('{{ coauthor_line(p, v) }}{% if not p.is_public %}') == 2, name
        assert html.count('class="dropdown-item coauthors-item"') == 1 and html.count("btn-lg text-left text-muted coauthors-item") == 1, name
    assert "{{ coauthor_line(p, v, false) }}" in read("ruqqus", "templates", "submission.html")
    macro = read("ruqqus", "templates", "partials", "coauthors.html")
    assert "coauthors_of(p, v)" in macro and "u.permalink" in macro
    assert ".author" not in macro and "author_id" not in macro          # it draws co-authors, never reads who wrote the post


def test_the_sheet_is_only_for_a_member_outside_the_panel_frames_and_its_script_matches_it():
    shell = read("ruqqus", "templates", "default.html")
    assert '{% include "partials/coauthors_modal.html" %}' in shell
    line = next(x for x in shell.splitlines() if "/assets/js/coauthors.js" in x or "coauthors.js" in x)
    assert "coauthors.js" in line
    ids = set(re.findall(r"getElementById\('([\w-]+)'\)", SCRIPT))
    assert {"coauthorsModal", "coauthors-list", "coauthors-form", "coauthors-input", "coauthors-invite", "coauthors-status"} <= ids
    assert not [i for i in ids if f'id="{i}"' not in MODAL]
    assert "innerHTML" not in SCRIPT and "strong.textContent = '@' + person.username" in SCRIPT


def test_both_stylesheets_style_it():
    for sheet in ("main.scss", "main_dark.scss"):
        css = read("ruqqus", "assets", "style", sheet)
        assert ".coauthors .user-name" in css and ".coauthor-row" in css, sheet
