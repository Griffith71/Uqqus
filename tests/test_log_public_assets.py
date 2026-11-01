"""The public Log's wiring (slice B of helpers/action_log.py): what everyone may read, what is private, and the places that used to
publish an author's text. The behaviour is in tests/test_action_log.py and tests/test_log_view.py; this reads the source the way the
project's other asset tests do."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


def body(source, name):
    return source.split(f"def {name}(", 1)[1].split("\n@app.", 1)[0]


LOG_PAGES = read("ruqqus", "routes", "log_pages.py")
BOARDS = read("ruqqus", "routes", "boards.py")
POSTS = read("ruqqus", "routes", "posts.py")
COMMENTS = read("ruqqus", "routes", "comments.py")
ADMIN_API = read("ruqqus", "routes", "admin_api.py")
ACTION_LOG = read("ruqqus", "helpers", "action_log.py")
HISTORY = read("ruqqus", "templates", "content_history.html")
ROWS_PARTIAL = read("ruqqus", "templates", "partials", "log_rows.html")
LOG_PAGE = read("ruqqus", "templates", "log.html")
COPY_PAGE = read("ruqqus", "templates", "log_copy.html")
MODLOG = read("ruqqus", "templates", "guild", "modlog.html")


# --- the public page ------------------------------------------------------------------------------------------------------------

def test_the_public_log_is_the_same_page_for_a_visitor_a_member_and_an_admin():
    route = body(LOG_PAGES, "public_log")
    assert "@auth_desired" in LOG_PAGES.split("def public_log(")[0].rsplit("@app.route", 1)[1]
    fetch = route.split("action_log.fetch(")[1].split(")\n    rows")[0]
    assert "level=0" in fetch and "open_guilds_only=True" in fetch           # what a visitor reads; a guild gone private is not named
    assert "member_boards" not in fetch and "admin_level" not in route       # an admin's other view is /admin/log
    assert "log_type=tab" in route and "suppression" not in route            # the tab picks the type; there is no 'suppression' tab


def test_a_line_of_the_log_never_carries_a_title_or_text_and_never_names_an_admin():
    rows = body(LOG_PAGES, "_rows")
    assert ".title" not in rows and "body" not in rows and "body_html" not in rows
    assert "log_view.site_account(site)" in rows and "role != action_log.GUILDMASTER" in rows
    assert "board_hidden(board, v)" in rows and "user_hidden(" in rows        # the reader's own word filter still applies
    page = LOG_PAGE
    assert "| safe" not in page and "|safe" not in page                       # a sentence is Markup built from escaped parts
    assert "{{ _('Reason: %(reason)s', reason=e.reason) }}" in ROWS_PARTIAL


def test_the_filters_are_numbers_and_names_only_and_a_private_guild_is_not_confirmed():
    route = body(LOG_PAGES, "public_log")
    assert "log_view.parse_cursor" in route and "log_view.parse_tab" in route and "action_log.parse_rule" in route
    assert "_id_of(get_guild" in route                                       # an unknown name lists nothing: -1 matches no row
    assert "actor_role <> 'admin'" in ACTION_LOG                             # filtering by an admin must not list their entries


def test_a_preserved_copy_opens_for_its_author_the_guilds_guildmasters_and_admins_and_is_a_404_for_everyone_else():
    for name in ("log_copy", "_item_copy"):
        src = body(LOG_PAGES, name)
        assert "abort(404)" in src and "private, no-store" in src and "noindex" in src, name
    assert "action_log.snapshot_visible(" in body(LOG_PAGES, "log_copy") and "action_log.may_open_copy(" in body(LOG_PAGES, "_item_copy")
    assert "if v is None:\n        abort(404)" in body(LOG_PAGES, "log_copy")
    assert COPY_PAGE.count("| safe") == 1 and "media_free(snapshot.body_html) | safe" in COPY_PAGE   # the stored, sanitized HTML only
    assert 'meta name="robots" content="noindex"' in COPY_PAGE


# --- the retired deletion log ------------------------------------------------------------------------------------------------------

def test_the_deletion_log_is_gone_and_its_address_only_redirects():
    for path in (("ruqqus", "routes", "deletion_log.py"), ("ruqqus", "helpers", "deletion_log.py"),
                 ("ruqqus", "templates", "deletion_log.html")):
        assert not ROOT.joinpath(*path).exists(), path
    assert "from .deletion_log import *" not in read("ruqqus", "routes", "__init__.py")
    route = body(LOG_PAGES, "deletion_log_retired").split("\n\n\n")[0]
    assert 'redirect("/log", code=301)' in route and "render_template" not in route and "g.db" not in route


def test_nothing_links_to_or_names_the_deletion_log_any_more():
    for path in sorted((ROOT / "ruqqus" / "templates").rglob("*.html")):
        text = path.read_text(encoding="utf-8")
        assert "log/deleted" not in text and "Deletion log" not in text, path
    assert 'href="/log"' in read("ruqqus", "templates", "footer.html") and 'href="/log"' in read("ruqqus", "templates", "default.html")
    assert '"log": ("public_log",)' in read("ruqqus", "helpers", "traffic.py")
    assert "/log?guild={{ b.name | urlencode }}" in MODLOG


def test_the_delete_dialogs_and_the_privacy_page_say_what_is_kept_and_for_whom():
    for name in ("delete_post_modal.html", "delete_comment_modal.html"):
        assert "keeps a private copy for 90 days" in read("ruqqus", "templates", name), name
    privacy = read("ruqqus", "templates", "help", "privacy.html")
    assert "private copy of what it said for 90 days" in privacy and "never the title or the text" in privacy
    assert "private guild is visible only to that guild's members" in privacy


# --- an author's own delete, and a hide, keep their text privately ------------------------------------------------------------------------

def test_an_authors_delete_keeps_its_text_as_a_private_copy_and_not_in_the_public_history_row():
    for src, fn, kw in ((POSTS, "delete_post_as", "snapshot_of(post)"), (COMMENTS, "delete_comment", "snapshot_of(c, c.post.board_id, comment=True)")):
        part = body(src, fn)
        row = part.split('action="delete"')[1].split("))")[0]
        assert "previous_" not in row, fn
        assert f"action_log.keep_snapshot(g.db, action_log.{kw})" in part, fn


def test_a_hide_keeps_the_text_as_a_private_copy_tied_to_its_entry_and_cites_a_rule():
    for src, fn in ((BOARDS, "mod_hide_bid_pid"), (COMMENTS, "mod_hide_comment")):
        part = body(src, fn)
        row = part.split('action="guild_hide"')[1].split("))")[0]
        assert "previous_" not in row, fn
        assert "action_log.annotate(g.db, ma.id, rule=request.form.get(\"rule\")" in part and "snapshot=action_log.snapshot_of(" in part, fn
    author = body(COMMENTS, "author_hide_comment")
    assert "previous_" not in author.split('action="author_hide"')[1].split("))")[0]
    assert "action_log.keep_snapshot(" in author


def test_only_an_edits_history_row_carries_text_and_the_page_draws_only_that():
    for src in (POSTS, COMMENTS):
        for row in re.findall(r"ContentEditHistory\((.*?)\n\s*\)\)", src, re.S):
            if 'action="edit"' not in row:
                assert "previous_" not in row, row[:80]
    assert 'entry.action == "edit"' in HISTORY and "entry.hidden_utc" in HISTORY
    edit_part = '{% if entry.action == "edit" %}\n    {% if entry.summary %}'
    assert "previous_body_html" in HISTORY.split(edit_part)[1] and "previous_body_html" not in HISTORY.split(edit_part)[0]


def test_an_edit_can_carry_a_summary_and_the_history_shows_it():
    assert 'summary=action_log.clean_reason(form.get("edit_summary"), 256) or None' in POSTS
    assert 'summary=action_log.clean_reason(request.form.get("edit_summary"), 256) or None' in COMMENTS
    assert 'name="edit_summary"' in read("ruqqus", "templates", "submission.html")
    assert 'id="comment-edit-summary-{{ c.base36id }}"' in read("ruqqus", "templates", "comments.html")
    assert "form.append('edit_summary'" in read("ruqqus", "assets", "js", "all_js.js")
    assert "entry.summary" in HISTORY


def test_an_admin_hides_one_version_and_it_is_logged_with_a_reason():
    route = body(ADMIN_API, "admin_hide_revision")
    assert "@admin_level_required(3)" in ADMIN_API.split("def admin_hide_revision(")[0].rsplit("@app.route", 1)[1]
    assert "@validate_formkey" in ADMIN_API.split("def admin_hide_revision(")[0].rsplit("@app.route", 1)[1]
    assert 'row.action != "edit"' in route and "if not reason:" in route
    assert "row.previous_title = row.previous_url = row.previous_body = row.previous_body_html = None" in route
    assert '"hide_revision", v.id' in route and "require_reason=True" in route and "role=action_log.ADMIN" in route
    assert "/admin/history/{{ entry.id }}/hide" in HISTORY and "v.admin_level >= 3" in HISTORY


def test_the_history_page_offers_a_copy_only_to_whoever_may_open_it():
    for src, call in ((POSTS, "snapshot_for(g.db, post_id=post.id)"), (COMMENTS, "snapshot_for(g.db, comment_id=comment.id)")):
        assert f"action_log.may_open_copy(action_log.{call}, v)" in src
    assert "{% if copy_url %}" in HISTORY


def test_the_private_copies_are_emptied_on_their_date_by_the_retention_step():
    retention = read("ruqqus", "helpers", "retention.py")
    assert "private copies past their date" in retention and "expires_utc <= :now" in retention and '"now": now' in retention
    assert "scrubbed_utc = :now WHERE scrubbed_utc = 0" in retention


# --- the guild's own log ---------------------------------------------------------------------------------------------------------------

def test_a_guild_log_is_read_through_the_entries_audience_and_confirms_nothing():
    gate = body(BOARDS, "_guild_log_gate")
    assert "not board.can_view(v)" in gate and "abort(404)" in gate and "abort(403)" not in gate
    assert "hide_reason=True" in gate                                        # a banned guild's log no longer prints the ban reason
    reader = body(BOARDS, "_guild_log_reader")
    assert "action_log.not_hidden_from(level, members)" in reader and "action_log.member_board_ids(g.db, v.id)" in reader
    for fn in ("board_mod_log", "mod_log_item"):
        assert "_guild_log_reader(board, v)" in body(BOARDS, fn), fn
    assert "{% if b.ban_reason and not hide_reason %}" in read("ruqqus", "templates", "board_banned.html")


def test_a_hidden_posts_title_is_for_the_guilds_guildmasters_and_admins_only():
    assert "titles_shown=bool(v and (v.admin_level>=3 or board.leads(v)))" in BOARDS.replace("titles_shown=titles_shown", "titles_shown=bool(v and (v.admin_level>=3 or board.leads(v)))")
    assert "ma.string_for(titles_shown)" in MODLOG and "ma.string |" not in MODLOG
    model = read("ruqqus", "classes", "mod_logs.py")
    assert 'label = self.target_post.title if titles else f"#{self.target_post.base36id}"' in model
    assert '"title": lambda: self.target_post.title if titles else f"#{self.target_post.base36id}"' in model


def test_the_guild_log_json_adds_the_rule_and_reason_and_still_names_no_admin():
    assert 'data["rule"] = note["rule_key"]' in body(BOARDS, "_guild_log_json") and 'data["reason"]' in body(BOARDS, "_guild_log_json")
    assert "'mod': app.config['SITE_NAME'] if self.by_admin" in read("ruqqus", "classes", "mod_logs.py").replace('"mod": app', "'mod': app").replace("self.by_admin else", "self.by_admin")


# --- reasons ----------------------------------------------------------------------------------------------------------------------------------

def test_an_admins_removal_of_a_post_or_comment_must_say_why_in_public():
    for fn in ("ban_post", "api_ban_comment"):
        part = body(ADMIN_API, fn)
        assert 'if not action_log.clean_reason(request.form.get("reason")):' in part, fn
        assert part.index("clean_reason") < part.index(".is_banned = True"), fn      # refused before anything is changed
    form = read("ruqqus", "templates", "submission.html")
    assert 'name="reason" placeholder="Removal Reason (public, required)" required' in form and 'name="rule"' in form
    js = read("ruqqus", "assets", "js", "all_js.js")
    assert "function removeComment(cid) {\n  var reason = window.prompt(" in js and "{reason: reason.trim()}" in js


def test_an_exile_may_carry_the_rule_and_the_reason_and_hide_revision_needs_one():
    assert 'action_log.annotate(g.db, ma.id, rule=request.form.get("rule"), reason=request.form.get("reason"))' in BOARDS
    assert '"hide_revision",' in ACTION_LOG.split("REASON_REQUIRED = {")[1].split("}")[0]


# --- the tables -------------------------------------------------------------------------------------------------------------------------------

def test_the_second_migration_moves_the_text_out_of_the_public_history_and_is_safe_to_run_twice():
    migration = read("scripts", "migrations", "2026-12-14_log_public.sql")
    assert "ADD COLUMN IF NOT EXISTS hidden_utc" in migration and "ADD COLUMN IF NOT EXISTS hidden_by" in migration
    assert migration.count("action IN ('delete', 'guild_hide', 'author_hide')") == 3
    assert "REFERENCES" not in migration
    update = migration.split("UPDATE public.content_edit_history")[1]
    assert "previous_title IS NOT NULL OR previous_url IS NOT NULL" in update           # only rows that still hold text: the second run finds none
    schema = read("schema.sql")
    assert "    hidden_utc bigint DEFAULT 0 NOT NULL,\n    hidden_by integer" in schema
    assert "('2026-12-14_log_public.sql')" in schema.split("schema_migrations", 1)[-1]
    model = read("ruqqus", "classes", "content_history.py")
    assert "hidden_utc = Column(" in model and "summary = Column(String(256)" in model


def test_claude_md_describes_the_log_as_it_is_now():
    doc = read("CLAUDE.md")
    assert "## Deletion log (legacy app)" not in doc
    for needle in ("Slice B", "/log", "preserved copy", "log_view.py"):
        assert needle in doc, needle


# --- the log of one post, one comment or one account -----------------------------------------------------------------------------------------

ABOUT_PAGE = read("ruqqus", "templates", "log_about.html")
ROWS = ROWS_PARTIAL


def test_a_posts_comments_and_accounts_log_are_the_public_entries_about_it_and_nothing_more():
    for fn in ("post_log", "comment_log", "profile_log"):
        decorators = LOG_PAGES.split(f"def {fn}(")[0].rsplit("@app.route", 1)[1]
        assert "@auth_desired" in decorators, fn
        assert "_about_page(v," in body(LOG_PAGES, fn), fn
    page = body(LOG_PAGES, "_about_page")
    fetch = page.split("action_log.fetch(")[1].split(")")[0]
    assert "level=0" in fetch and "open_guilds_only=True" in fetch and "member_boards" not in fetch     # what /log shows, never more
    assert "post_hidden(post, v)" in body(LOG_PAGES, "post_log") and "in_private_guild_for(v)" in body(LOG_PAGES, "post_log")
    assert "comment_hidden(comment, v)" in body(LOG_PAGES, "comment_log")
    assert "_profile_or_404(username, v)" in body(LOG_PAGES, "profile_log")       # the profile's own refusals: it confirms nothing more


def test_nothing_about_a_post_or_comment_reaches_the_page_but_its_id():
    for fn in ("post_log", "comment_log", "profile_log", "_about_page", "_delete_rows"):
        src = body(LOG_PAGES, fn)
        assert ".title" not in src and "body_html" not in src and ".body" not in src, fn
    assert not re.search(r"\.(title|body|body_html|url)\b", ABOUT_PAGE + ROWS)          # no attribute of a post or comment is ever drawn
    assert "| safe" not in ABOUT_PAGE and "| safe" not in ROWS and "|safe" not in ROWS
    assert '{% include "partials/log_rows.html" %}' in LOG_PAGE and '{% include "partials/log_rows.html" %}' in ABOUT_PAGE
    assert 'name="robots" content="noindex"' in ABOUT_PAGE


def test_an_anonymous_item_is_left_out_of_a_profiles_log_unless_the_reader_is_an_admin():
    scope = body(ACTION_LOG, "about_scope")
    assert "NOT COALESCE(s.is_anonymous, false)" in scope and "NOT COALESCE(c.is_anonymous, false)" in scope
    route = body(LOG_PAGES, "profile_log")
    assert "staff = bool(v and v.admin_level >= 3)" in route and "include_anonymous=staff" in route
    assert route.count("include_anonymous=staff") + route.count('"include_anonymous": staff') == 2     # the entries and the author's own deletes


def test_an_authors_own_deletes_are_for_the_author_and_the_admins_and_carry_no_words():
    rule = body(LOG_PAGES, "_may_see_deletes")
    assert "v.admin_level >= 3" in rule and "v.id == author_id" in rule
    for fn in ("post_log", "comment_log", "profile_log"):
        assert "_may_see_deletes(v," in body(LOG_PAGES, fn), fn
        assert "else None" in body(LOG_PAGES, fn), fn                                    # anyone else is handed no deletes at all
    reader = body(ACTION_LOG, "author_deletes")
    assert "previous_" not in reader and "SELECT h.id, h.created_utc, h.actor_id, h.target_submission_id, h.target_comment_id" in reader
    assert "h.action = 'delete'" in reader


def test_the_logs_are_linked_from_where_you_would_look_for_them():
    assert 'href="{{ u.permalink }}/log"' in read("ruqqus", "templates", "partials", "profile_header.html")
    assert 'href="/{{ target_type }}/{{ target.base36id }}/log"' in HISTORY
    assert '"author_delete_post"' in read("ruqqus", "classes", "mod_logs.py") and '"author_delete_comment"' in read("ruqqus", "classes", "mod_logs.py")
