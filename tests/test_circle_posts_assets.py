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
    assert "def private_label(self, v):" in SUBMISSION and "it was shared with you." in SUBMISSION
    label = SUBMISSION.split("def circle_label(self, v):")[1].split("\n    def ")[0]
    assert "circles.shared_label(" in label and "circles.viewer_label(" in label   # names for the author, never for others
    assert "kind == circles.PAID and cid in mine" in label
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
    assert "@app.errorhandler(CircleOnly)" in ERRORS and "@api(error_handler=True)" in ERRORS.split("def error_circle_only")[0][-80:]
    assert 'response.headers["Cache-Control"] = "private, no-store"' in body and ", 403)" in body
    assert '"api": lambda: (jsonify({"error": "404 Not Found"}), 404)' in body
    # only paid circles with a price are offered; a custom circle is never named
    assert "row.kind == circles.PAID and row.price_coins > 0" in body
    gate = read("ruqqus", "templates", "errors", "circle_gate.html")
    assert not re.search(r"\b(post|p)\.(title|body|board|permalink|url)\b", gate)
    assert "owner.username" in gate and "Shared with a circle" in gate and "| safe" not in gate
    # Join opens the subscribe sheet on the circles this post is for that take subscribers (no browser confirm)
    assert 'data-circle-open="{{ c.id }}"' in gate and "c.id in joinable" in gate and "confirm(" not in gate
    assert "subscribe_sheet(owner, offered, v)" in gate and 'selectattr("joinable")' in gate


# --- lists --------------------------------------------------------------------------------------------

def test_lists_that_ask_for_public_posts_are_the_ones_known_and_each_decision_is_recorded():
    # Every use of post_public decides whether a Circle post may appear. A Circle post has post_public = false, so a list
    # that is not changed leaves it out. Adding another use means deciding here, on purpose.
    # front.py 4 -> 6 (2026-10-09): /random/post and /random/comment now ask for public posts only (they redirected
    # into Circle and private-guild items).
    # helpers/ads.py 1 (2026-10-12): no ad beside a post that is not public (a Circle or private-guild post).
    # helpers/media/attach.py 2 (2026-10-22, bug hunt L5): a picture on a post in a private guild that was never public
    # there is served by the post page's own rule (circles.PRIVATE_GUILD); one that was public there stays public.
    # -> 3: board_paths purges the CDN's copies of exactly those posts' pictures when the guild turns private.
    # helpers/creator_fund_store.py 3 (2026-10-12): the creator fund counts only votes and comments on public posts.
    # helpers/space_counts.py 1: a post made for guilds only counts its guilds' public copies, never one in a private guild
    # (the same rule as post_counts: the number never hints at a guild the reader can't open).
    # helpers/post_counts.py 1 (2026-10-13): a post's share count leaves out forward copies that are not public, so the
    # number never hints at a private guild the post went to.
    # helpers/similar_store.py 1 (2026-10-14): Similar accounts ties accounts to guilds through public posts only.
    # classes/boards.py 4 -> 0 (2026-11, audit A-01, the owner's choice): a private guild's page and comment list show a
    # non-member only their own posts, not the ones forwarded while it was public.
    # helpers/guild_channel_store.py 1 (2026-11): a bot's new posts go into an announcement channel (helpers/guild_channel_feeds.py)
    # only when public. A bot can't post for circles (bots.option_refusal), so this is a second lock, never a way to leak one.
    # routes/chat.py 1 -> 0 (2026-11, audit M-12): the chat post preview asks `in_private_guild_for`, the post page's
    # own rule, instead of its own `post_public` / `can_view` test.
    # routes/search.py 2 -> 4 (2026-10-21): the comment search asks for public posts the same way (comment_searchlisting:
    # a non-public post's comments are found only by its author, the guild's mods and contributors).
    counts = {}
    for path in (ROOT / "ruqqus").rglob("*"):
        if path.suffix in (".py", ".html") and "__pycache__" not in path.parts:
            n = path.read_text(encoding="utf-8").count("post_public")
            if n:
                counts[str(path.relative_to(ROOT)).replace("\\", "/")] = n
    assert counts == {
        "ruqqus/classes/submission.py": 3, "ruqqus/classes/user.py": 15,
        "ruqqus/helpers/ads.py": 1, "ruqqus/helpers/media/attach.py": 3, "ruqqus/helpers/guild_channel_store.py": 1, "ruqqus/helpers/post_counts.py": 1, "ruqqus/helpers/space_counts.py": 1, "ruqqus/helpers/similar_store.py": 1, "ruqqus/helpers/circle_clause.py": 2, "ruqqus/helpers/creator_fund_store.py": 3, "ruqqus/helpers/circle_guard.py": 1, "ruqqus/helpers/trending_store.py": 2,
        "ruqqus/routes/circles.py": 1, "ruqqus/routes/curations.py": 2,
        "ruqqus/routes/deletion_log.py": 2, "ruqqus/routes/front.py": 6, "ruqqus/routes/legal.py": 1,
        "ruqqus/routes/posts.py": 2, "ruqqus/routes/search.py": 4, "ruqqus/templates/comments.html": 1,
    }, counts


def test_only_the_viewers_own_lists_add_the_circle_condition():
    assert USER.count("circle_clause.for_viewer(") == 5
    for name in ("idlist", "userpagelisting", "saved_idlist", "history_idlist", "_voted_post_idlist"):
        body = USER.split(f"def {name}(")[1].split("\n    def ")[0]
        assert "circle_clause.for_viewer(" in body, name
    for name in ("for_you_idlist", "commentlisting", "forwarded_idlist", "saved_comment_idlist", "_voted_comment_idlist"):
        body = USER.split(f"def {name}(")[1].split("\n    def ")[0]
        assert "circle_clause" not in body, name


def test_the_circle_condition_is_only_for_posts_made_for_circles():
    clause = read("ruqqus", "helpers", "circle_clause.py")
    assert "submission.audience == circles.CIRCLES" in clause and "circles.GUILD" not in clause
    assert "member.c.renews_utc > now" in clause and "circle.c.user_id == submission.author_id" in clause
    assert "~blocked" in clause
    assert "return false()" in clause.split("def member_clause(")[1].split("now = ")[0]


def test_the_rss_feed_carries_nothing_made_for_a_circle():
    feeds = read("ruqqus", "routes", "feeds.py")
    assert "if not p.audience and not p.hidden_by_guild and not p.in_private_guild_for(None)]" in feeds   # B-01 too
    # and a text post (no link, no picture) no longer breaks the whole feed
    assert feeds.index('if post.url:') < feeds.index('doc.stag("link", href=post.url)')
    assert feeds.index('if image_url:') < feeds.index('doc.stag("media:thumbnail", url=image_url)')


# --- making a post --------------------------------------------------------------------------------------

def test_a_post_is_made_for_an_audience_only_after_it_is_checked_and_is_not_public_then():
    body = func(POSTS, "submit_post", "\n@app.route")
    assert body.index("circle_rules.parse_audience(request.form.get(\"audience\"), request.form.getlist(\"circles\"))") < body.index("new_post = Submission(")
    assert body.index("circle_store.resolve(g.db, v.id, audience_tokens)") < body.index("new_post = Submission(")
    assert "circle_rules.post_refusal(" in body and "own_video=bool(url and media_attach.own_video(g.db, v.id, url))" in body
    assert "audience=audience," in body and "post_public=not audience and not board.is_private," in body
    assert body.index("circle_store.set_post_circles(g.db, new_post.id, circle_ids)") < body.index("new_notif=Notification(")


def test_only_people_who_may_see_a_circle_post_are_told_about_it():
    body = func(POSTS, "submit_post", "\n@app.route")
    assert "if not new_post.is_public and not new_post.audience:" in body
    assert "eligible = circle_guard.eligible_ids(g.db, v.id, new_post.audience, board_id=new_post.board_id, circle_ids=circle_ids)" in body
    assert body.index("uids = [uid for uid in uids if uid in eligible]") < body.index("new_notif=Notification(")
    comments = read("ruqqus", "routes", "comments.py")
    assert comments.count("circle_ids=circle_guard.post_circles(g.db, [") == 2      # followers of the post, mentions


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
    assert body.index("get_post(pid, v=v)") < body.index("if post.author_id != v.id:") < body.index("circles.parse_audience(raw, chosen)")
    assert body.index("circles.parse_audience(raw, chosen)") < body.index("circle_store.resolve(g.db, v.id, tokens)")
    assert 'request.values.get("confirm") != "1"' in body and "409" in body
    assert "post.post_public = not audience and not post.board.is_private" in body
    assert "circle_store.set_post_circles(g.db, post.id, circle_ids)" in body
    assert "media_cdn.purge(media_attach.attached_paths(g.db, submission_id=post.id))" in body
    assert "post_refusal(" in body and "shared=" not in body          # a shared public post may move (helpers/sharing.py)
    assert "_shared_copies(post, v.id)" in body and "sharing.narrowing_note(" in body
    assert body.index("_withdraw(own, v.id, int(time.time()))") < body.index("g.db.commit()")


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
    assert '{% macro audience_field(selected, prefix, chosen=(), choices=(), layout="menu", noun="post") %}' in options
    assert 'name="audience"' in options and 'name="circles"' in options
    assert "post_options.audience_field(audience_now, \"post-audience\", circles_now, circle_choices(v))" in read("ruqqus", "templates", "submit.html")
    assert 'audience_field(0, "ic-audience", (), circle_choices(v))' in read("ruqqus", "templates", "partials", "inline_composer.html")
    drafts = read("ruqqus", "helpers", "post_drafts.py")
    assert 'circles.parse_audience(form.get("audience"), form.getlist("circles"))' in drafts and '"circles": list(circle_tokens),' in drafts
    assert 'data["audience"] = str(options["audience"])' in drafts and 'data["circles"] = [str(c) for c in options.get("circles") or []]' in drafts
    script = read("ruqqus", "assets", "js", "post_drafts.js")
    assert 'input[name="audience"]:checked' in script and 'input[name="circles"]:checked' in script
    choices = CIRCLES.split("def circle_choices(")[1].split("\n\n\n")[0]
    assert "circles.FRIENDS_TOKEN" in choices and "kinds=(circles.CUSTOM, circles.PAID)" in choices


# --- media --------------------------------------------------------------------------------------------

def test_a_file_in_a_circle_post_is_served_only_to_the_people_who_may_see_the_post_and_never_cached():
    body = MEDIA_ROUTE.split("def media_file(")[1]
    assert "attach.audience_context(g.db, asset) if live else (0, None, None, ())" in body
    assert "circle_guard.may_see(g.db, audience, circle_owner, v, board_id=board_id, circle_ids=circle_ids)" in body
    assert "mode = rules.access(asset.status, live, is_owner, audience, allowed)" in body
    # the private answer is the uncached one, and only a public one skips the session cookie
    assert 'response.headers["Cache-Control"] = FOREVER if mode == rules.PUBLIC else NO_STORE' in body
    assert re.search(r"if mode == rules\.PUBLIC:\s+g\.skip_session_cookie = True", body)
    attach = read("ruqqus", "helpers", "media", "attach.py")
    assert "def audience_context(db, asset):" in attach and "return 0, None, None, ()" in attach
    assert "SELECT circle_id FROM post_circles WHERE post_id = :i" in attach and "SELECT circle_id FROM story_circles WHERE story_id = :i" in attach


# --- docs ---------------------------------------------------------------------------------------------

def test_claude_md_describes_posts_made_for_an_audience():
    doc = read("CLAUDE.md")
    assert "### Posts and stories for circles" in doc
    for needle in ("fail closed", "post_public", "CircleOnly", "circle_clause", "get_post", "no-store"):
        assert needle in doc, needle
