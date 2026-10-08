"""A post made for an audience crosses the model, the getters, the lists, the composer, drafts, the share routes, the
notifications, the media route and the error handler. These checks keep the layers agreeing and keep the one promise: a
Circle post is shown to the author, an admin and the right members of the Circle, and to nobody else, whichever route
asks. (The end-to-end proof is the leak sweep run against real Postgres; these pin the code it depends on.)"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


GET = read("ruqqus", "helpers", "get.py")
USER = read("ruqqus", "classes", "user.py")
POSTS = read("ruqqus", "routes", "posts.py")
COMMENTS = read("ruqqus", "routes", "comments.py")
COAUTHORS = read("ruqqus", "routes", "coauthors.py")
CIRCLES = read("ruqqus", "routes", "circles.py")
MEDIA_ROUTE = read("ruqqus", "routes", "media.py")
ERRORS = read("ruqqus", "routes", "errors.py")
SUBMISSION = read("ruqqus", "classes", "submission.py")


def func(source, name, nxt="\ndef "):
    return source.split(f"def {name}(")[1].split(nxt)[0]


# --- storage ------------------------------------------------------------------------------------------

def test_the_column_is_in_the_model_the_schema_and_the_migration():
    assert "audience = Column(SmallInteger, nullable=False, default=0)" in SUBMISSION
    table = read("schema.sql").split("CREATE TABLE public.submissions (")[1].split("\n);")[0]
    assert "    audience smallint DEFAULT 0 NOT NULL" in table
    assert "ALTER TABLE submissions ADD COLUMN IF NOT EXISTS audience smallint NOT NULL DEFAULT 0;" in read("scripts", "migrations", "2026-10-10_circle_posts.sql")


def test_a_post_for_an_audience_is_not_public_and_says_who_it_is_for():
    assert "return not self.audience and (self.post_public or not self.board.is_private)" in SUBMISSION
    assert "def private_label(self, v):" in SUBMISSION and "you are in their Circle." in SUBMISSION
    for name in ("submission.html", "submission_listing.html"):
        html = read("ruqqus", "templates", name)
        assert "p.private_label(v)" in html and "Private post, visible" not in html, name


# --- every route reads a post through the getters, and the getters guard -------------------------------

def test_get_post_refuses_a_post_the_viewer_may_not_see_and_it_is_a_404():
    body = func(GET, "get_post", "\ndef get_posts(")
    assert "circle_guard.may_see_post(nSession, x, v)" in body and "raise CircleOnly(x)" in body
    assert "return None" in body.split("circle_guard.may_see_post(nSession, x, v)")[1].split("raise CircleOnly")[0]   # graceful: None
    assert body.rindex("circle_guard.may_see_post") > body.rindex("x=items")
    errors = read("ruqqus", "classes", "custom_errors.py")
    assert "class CircleOnly(NotFound):" in errors


def test_every_list_of_posts_and_comments_is_filtered_by_what_the_viewer_may_see():
    assert "posts = circle_guard.visible_posts(g.db, posts, v)" in func(GET, "get_posts", "\ndef get_post_with_comments(")
    assert "circle_guard.may_see_comment(nSession or g.db, x, v)" in func(GET, "get_comment", "\ndef get_comments(")
    assert "return circle_guard.visible_comments(nSession or g.db, output, v)" in func(GET, "get_comments", "\ndef get_board(")


def test_a_post_with_its_comments_goes_through_get_post():
    assert "post = get_post(pid, v=v)" in func(GET, "get_post_with_comments", "\ndef get_comment(")


def test_the_guard_asks_the_viewer_of_the_request_when_the_caller_names_none():
    guard = read("ruqqus", "helpers", "circle_guard.py")
    assert 'getattr(g, "v", None) if has_request_context() else None' in guard
    assert "if viewer is None:\n        return False" in guard
    assert guard.count("ADMIN_LEVEL = 4") == 1 and "(getattr(viewer, \"admin_level\", 0) or 0) >= ADMIN_LEVEL" in guard


def test_a_page_request_gets_a_gate_that_shows_nothing_of_the_post_and_an_api_request_a_404():
    body = func(ERRORS, "error_circle_only", "\n@app.errorhandler(403)")
    assert "@app.errorhandler(CircleOnly)" in ERRORS and "@api()" in ERRORS.split("def error_circle_only")[0][-80:]
    assert 'response.headers["Cache-Control"] = "private, no-store"' in body and ", 403)" in body
    assert '"api": lambda: (jsonify({"error": "404 Not Found"}), 404)' in body
    gate = read("ruqqus", "templates", "errors", "circle_gate.html")
    assert not re.search(r"\b(post|p)\.(title|body|board|permalink|url)\b", gate)
    assert "owner.username" in gate and "audience_name" in gate and "| safe" not in gate


# --- lists --------------------------------------------------------------------------------------------

def test_lists_that_ask_for_public_posts_are_the_ones_known_and_each_decision_is_recorded():
    # Every use of post_public decides whether a Circle post may appear. A Circle post has post_public = false, so a list
    # that is not changed leaves it out. Adding another use means deciding here, on purpose.
    counts = {}
    for path in (ROOT / "ruqqus").rglob("*"):
        if path.suffix in (".py", ".html") and "__pycache__" not in path.parts:
            n = path.read_text(encoding="utf-8").count("post_public")
            if n:
                counts[str(path.relative_to(ROOT)).replace("\\", "/")] = n
    assert counts == {
        "ruqqus/classes/boards.py": 4, "ruqqus/classes/submission.py": 3, "ruqqus/classes/user.py": 15,
        "ruqqus/helpers/circle_clause.py": 2, "ruqqus/helpers/circle_guard.py": 1, "ruqqus/helpers/trending_store.py": 2,
        "ruqqus/routes/chat.py": 1, "ruqqus/routes/circles.py": 1, "ruqqus/routes/curations.py": 2,
        "ruqqus/routes/deletion_log.py": 2, "ruqqus/routes/front.py": 4, "ruqqus/routes/legal.py": 1,
        "ruqqus/routes/posts.py": 2, "ruqqus/routes/search.py": 2, "ruqqus/templates/comments.html": 1,
    }, counts


def test_only_the_viewers_own_lists_add_the_circle_condition():
    assert USER.count("circle_clause.for_viewer(") == 5
    for name in ("idlist", "userpagelisting", "saved_idlist", "history_idlist", "_voted_post_idlist"):
        body = USER.split(f"def {name}(")[1].split("\n    def ")[0]
        assert "circle_clause.for_viewer(" in body, name
    for name in ("for_you_idlist", "commentlisting", "forwarded_idlist", "saved_comment_idlist", "_voted_comment_idlist"):
        body = USER.split(f"def {name}(")[1].split("\n    def ")[0]
        assert "circle_clause" not in body, name


def test_the_circle_condition_is_only_for_posts_made_for_an_account_circle():
    clause = read("ruqqus", "helpers", "circle_clause.py")
    assert "submission.audience == circles.SUBSCRIBERS" in clause and "submission.audience == circles.FRIENDS" in clause
    assert "circles.GUILD" not in clause and "member.renews_utc > now" in clause
    assert "if viewer is None:\n        return false()" in clause


def test_the_rss_feed_carries_nothing_made_for_a_circle():
    feeds = read("ruqqus", "routes", "feeds.py")
    assert "posts = [p for p in get_posts(ids, sort=sort, v=user) if not p.audience]" in feeds
    # and a text post (no link, no picture) no longer breaks the whole feed
    assert feeds.index('if post.url:') < feeds.index('doc.stag("link", href=post.url)')
    assert feeds.index('if image_url:') < feeds.index('doc.stag("media:thumbnail", url=image_url)')


# --- making a post --------------------------------------------------------------------------------------

def test_a_post_is_made_for_an_audience_only_after_it_is_checked_and_is_not_public_then():
    body = func(POSTS, "submit_post", "\n@app.route")
    assert body.index("audience, audience_error = circle_rules.parse_audience(") < body.index("new_post = Submission(")
    assert "circle_rules.post_refusal(" in body and "own_video=bool(url and media_attach.own_video(g.db, v.id, url))" in body
    assert "audience=audience," in body and "post_public=not audience and not board.is_private," in body


def test_only_people_who_may_see_a_circle_post_are_told_about_it():
    body = func(POSTS, "submit_post", "\n@app.route")
    assert "if not new_post.is_public and not new_post.audience:" in body
    assert "eligible = circle_guard.eligible_ids(g.db, v.id, new_post.audience, board_id=new_post.board_id)" in body
    assert body.index("uids = [uid for uid in uids if uid in eligible]") < body.index("new_notif=Notification(")


def test_a_circle_post_cannot_be_forwarded_reposted_or_co_authored_and_neither_can_its_comments():
    assert "if primary.audience:" in func(POSTS, "forward_post", "\n@app.route")
    assert "if primary.audience:" in func(POSTS, "repost_post", "\n@app.route")
    assert "circle_guard.under_audience(g.db, comment)" in func(COMMENTS, "repost_comment", "\n@app.route")
    assert "circle_guard.under_audience(g.db, comment)" in func(COMMENTS, "forward_comment", "\n@app.route")
    assert "if post.audience:" in func(COAUTHORS, "coauthor_invite", "\n@app.get")
    assert "and not post.audience" in func(COAUTHORS, "_may_invite", "\n\n\napp.jinja_env")


# --- the audience of a post that exists -----------------------------------------------------------------

def test_only_the_author_changes_who_can_see_a_post_and_going_public_needs_a_confirmation():
    body = func(CIRCLES, "circle_post_audience", "\n\ndef may_change_audience")
    head = CIRCLES.split("def circle_post_audience(")[0].rsplit("\n\n\n", 1)[1]
    assert '@app.route("/api/post/<pid>/audience", methods=["POST"])' in head and "@validate_formkey" in head and "@is_not_banned" in head
    assert body.index("get_post(pid, v=v)") < body.index("if post.author_id != v.id:") < body.index("circles.parse_audience(raw)")
    assert 'request.values.get("confirm") != "1"' in body and "409" in body
    assert "post.post_public = not audience and not post.board.is_private" in body
    assert "media_cdn.purge(media_attach.attached_paths(g.db, submission_id=post.id))" in body
    assert "post_refusal(" in body and "shared=shared" in body


def test_the_menu_item_the_sheet_and_the_script_agree():
    assert sum(read("ruqqus", "templates", n).count("audience-item") for n in ("submission.html", "submission_listing.html")) == 4
    modal = read("ruqqus", "templates", "partials", "audience_modal.html")
    script = read("ruqqus", "assets", "js", "circle_audience.js")
    ids = set(re.findall(r"getElementById\('([\w-]+)'\)", script))
    assert {"audienceModal", "audience-save", "audience-status"} <= ids
    assert not [i for i in ids if f'id="{i}"' not in modal]
    assert "innerHTML" not in script and "status.textContent = text || '';" in script
    shell = read("ruqqus", "templates", "default.html")
    assert '{% include "partials/audience_modal.html" %}' in shell and "circle_audience.js" in shell
    assert "app.jinja_env.globals.update(may_change_audience=may_change_audience)" in CIRCLES


# --- the composer and drafts ----------------------------------------------------------------------------

def test_the_composer_the_create_page_and_drafts_all_carry_the_audience():
    options = read("ruqqus", "templates", "partials", "post_options.html")
    assert "{% macro audience_field(selected, prefix) %}" in options and 'name="audience"' in options
    assert "post_options.audience_field(" in read("ruqqus", "templates", "submit.html")
    assert 'audience_field(0, "ic-audience")' in read("ruqqus", "templates", "partials", "inline_composer.html")
    drafts = read("ruqqus", "helpers", "post_drafts.py")
    assert "circles.parse_audience(form.get(\"audience\"))" in drafts and '"audience": audience,' in drafts
    assert 'data["audience"] = str(options["audience"])' in drafts
    assert 'input[name="audience"]:checked' in read("ruqqus", "assets", "js", "post_drafts.js")


# --- media --------------------------------------------------------------------------------------------

def test_a_file_in_a_circle_post_is_served_only_to_the_people_who_may_see_the_post_and_never_cached():
    body = MEDIA_ROUTE.split("def media_file(")[1]
    assert "attach.audience_of(g.db, asset) if live else (0, None)" in body
    assert "allowed = bool(audience) and circle_guard.may_see(g.db, audience, circle_owner, v)" in body
    assert "mode = rules.access(asset.status, live, is_owner, audience, allowed)" in body
    # the private answer is the uncached one, and only a public one skips the session cookie
    assert 'response.headers["Cache-Control"] = FOREVER if mode == rules.PUBLIC else NO_STORE' in body
    assert re.search(r"if mode == rules\.PUBLIC:\s+g\.skip_session_cookie = True", body)
    attach = read("ruqqus", "helpers", "media", "attach.py")
    assert "def audience_of(db, asset):" in attach and "return 0, None" in attach


# --- docs ---------------------------------------------------------------------------------------------

def test_claude_md_describes_posts_made_for_an_audience():
    doc = read("CLAUDE.md")
    assert "### Posts made for a Circle" in doc
    for needle in ("fail closed", "post_public", "CircleOnly", "circle_clause", "get_post", "no-store"):
        assert needle in doc, needle
