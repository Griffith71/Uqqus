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
    assert "level=0" in fetch and "open_guilds_only=False" not in fetch      # what a visitor reads; `fetch` leaves out a guild gone private by default
    assert "member_boards" not in fetch and "admin_level" not in route       # an admin's other view is /admin/log
    assert "log_type=tab" in route and "suppression" not in route            # the tab picks the type; there is no 'suppression' tab


def test_a_line_of_the_log_never_carries_a_title_or_text_and_never_names_an_admin():
    rows = body(LOG_PAGES, "_rows").split("\n\n\ndef _deleted_words")[0]
    # a moderation line carries no words of the item: the only words a line draws are a deleted item's public copy (`_deleted_words`, slice D3a)
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
    assert "_ACTOR_MAY_BE_FILTERED" in ACTION_LOG and "actor_role <> 'admin' AND (actor_role <> 'author' OR " in ACTION_LOG and "NOT {_ACTOR_CREDIT_ANONYMOUS}" in ACTION_LOG
    # filtering by an admin (or a post's author) must not list their entries; an edit's editor is named only while its item is not anonymous


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
    assert 'redirect("/log?type=deletion", code=301)' in route and "render_template" not in route and "g.db" not in route


def test_nothing_links_to_or_names_the_deletion_log_any_more():
    for path in sorted((ROOT / "ruqqus" / "templates").rglob("*.html")):
        text = path.read_text(encoding="utf-8")
        assert "log/deleted" not in text and "Deletion log" not in text, path
    assert 'href="/log"' in read("ruqqus", "templates", "footer.html") and 'href="/log"' in read("ruqqus", "templates", "default.html")
    assert '"log": ("public_log",)' in read("ruqqus", "helpers", "traffic.py")
    assert "/log?guild={{ b.name | urlencode }}" in MODLOG


def test_the_delete_dialogs_and_the_privacy_page_say_what_is_kept_and_for_whom():
    for name in ("delete_post_modal.html", "delete_comment_modal.html"):
        text = read("ruqqus", "templates", name)
        # both ways, and the five minutes: it can't promise the outcome (the server decides, and an erase needs that nobody has replied)
        assert "Delete it" in text and "Remove my name" in text and "less than five minutes old" in text and "private copy" in text and "stays in the Log as Anonymous" in text, name
    privacy = read("ruqqus", "templates", "help", "privacy.html")
    assert "private copy of what it said for 90 days" in privacy and "never the title or the text" in privacy
    assert "stays in the [Log](/log) as Anonymous" in privacy and "five minutes" in privacy and "Remove my name" in privacy
    assert "private guild is visible only to that guild's members" in privacy
    assert "the Log keeps the words of a deleted post or comment" in read("ruqqus", "templates", "help", "terms.html")


# --- an author's own delete, and a hide, keep their text privately ------------------------------------------------------------------------

def test_an_authors_delete_is_an_erase_inside_the_grace_window_and_a_public_entry_after_it_and_never_puts_text_in_the_history_row():
    for src, fn, grace, action in ((POSTS, "delete_post_as", "snapshot_of(post, kind=action_log.GRACE)", "author_delete_post"),
                                   (COMMENTS, "delete_comment", "snapshot_of(c, post.board_id, comment=True, kind=action_log.GRACE)", "author_delete_comment")):
        part = body(src, fn)
        row = part.split('action="delete"')[1].split("))")[0]
        assert "previous_" not in row, fn                                           # the history row says it happened; the words are a copy in the Log
        assert "deletion.plan(" in part and "deletion.ERASE" in part, fn
        assert f"action_log.keep_snapshot(g.db, action_log.{grace})" in part, fn   # an erase keeps a private copy for the author and the admins, no entry
        assert f'"{action}"' in part and "role=action_log.AUTHOR" in part and "kind=action_log.DELETE" in part and "public=True" in part, fn
        assert "anonymise.anonymise_" in part, fn                                   # after the window the item is anonymous before it leaves
        assert "qr_limited_answer(v)" in part, fn                                   # a delete that publishes needs a full sign-in


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
    edit_part = '{% if entry.action == "edit" %}\n    {% if entry.summary and words_shown(entry.text_severity, v) %}'
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
    assert "sibling.previous_title = sibling.previous_url = sibling.previous_body = sibling.previous_body_html = None" in route
    assert "edit_group=row.edit_group" in route          # one edit is a row on every copy: hide them all
    assert '"hide_revision", v.id' in route and "require_reason=True" in route and "role=action_log.ADMIN" in route
    assert "/admin/history/{{ entry.id }}/hide" in HISTORY and "v.admin_level >= 3" in HISTORY


def test_the_history_page_offers_a_copy_only_to_whoever_may_open_it():
    for src, call in ((POSTS, "snapshot_for(g.db, post_id=post.id)"), (COMMENTS, "snapshot_for(g.db, comment_id=comment.id)")):
        assert call.replace(")", ", viewer=v)") in src
        # a visitor has no viewer, and snapshot_for(viewer=None) answers the newest copy of all: only a signed-in reader is asked
        assert "if v is not None and action_log." + call.replace(")", ", viewer=v)") in src
    assert "{% if copy_url %}" in HISTORY


def test_the_history_decides_whether_to_name_the_account_that_acted_on_the_item_that_account_owns():
    # a post's author hiding or restoring a comment under it acted as the POST's author: its anonymity is the post's (C6 of the review)
    assert ('{% set owner = target.post if (target_type == "comment" and entry.action in ("author_hide", "author_restore")) else target %}'
            in HISTORY)
    assert "anon_hidden(owner, v) and entry.actor_id == owner.author_id" in HISTORY
    assert "anon_hidden(target, v)" not in HISTORY
    # the not-shown line blames the administrators only when they are the cause (C8): a guild's or an author's hide has its own sentence
    assert "The earlier versions aren't shown while this is hidden." in HISTORY
    assert "(target.hidden_by_guild or target.author_hidden) and not target.is_banned and not target.purged_utc" in HISTORY


def test_the_private_copies_are_emptied_on_their_date_by_the_retention_step():
    retention = read("ruqqus", "helpers", "retention.py")
    # one statement, shared: helpers/action_log.py `SCRUB_DUE` is what the retention step runs and what `scrub_due` runs for a test
    assert '"private copies past their date", action_log.SCRUB_DUE' in retention and '"now": now' in retention
    log = read("ruqqus", "helpers", "action_log.py")
    assert "WHERE scrubbed_utc = 0 AND expires_utc > 0 AND expires_utc <= :now" in log and "SET title = NULL, url = NULL, body = NULL" in log


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
    comment = js.split("function removeComment(cid) {")[1].split("\n}\n")[0]
    assert "takedownDialog(" in comment and "ruleRequired: true" in comment      # the dialog asks for the reason and the rule (the owner, 2026-11-19)
    for fn in ("ban_post", "api_ban_comment"):
        assert 'action_log.parse_rule(request.form.get("rule"))' in body(ADMIN_API, fn), fn


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
    assert "level=0" in fetch and "open_guilds_only=False" not in fetch and "member_boards" not in fetch     # what /log shows, never more
    assert "post_hidden(post, v)" in body(LOG_PAGES, "post_log") and "in_private_guild_for(v)" in body(LOG_PAGES, "post_log")
    assert "comment_hidden(comment, v)" in body(LOG_PAGES, "comment_log")
    assert "_profile_or_404(username, v)" in body(LOG_PAGES, "profile_log")       # the profile's own refusals: it confirms nothing more


def test_nothing_about_a_post_or_comment_reaches_the_page_but_its_id():
    for fn in ("post_log", "comment_log", "profile_log", "_about_page"):
        src = body(LOG_PAGES, fn)
        assert ".title" not in src and "body_html" not in src and ".body" not in src, fn
    # no attribute of a post or comment is ever drawn: the one words a line may draw are a deleted item's public copy, a plain dict (`e.deleted`)
    assert not re.search(r"\.(title|body|body_html|url)\b", (ABOUT_PAGE + ROWS).replace("e.deleted.", ""))
    assert "| safe" not in ABOUT_PAGE and "| safe" not in ROWS and "|safe" not in ROWS
    assert '{% include "partials/log_rows.html" %}' in LOG_PAGE and '{% include "partials/log_rows.html" %}' in ABOUT_PAGE
    assert 'name="robots" content="noindex"' in ABOUT_PAGE


def test_an_anonymous_item_is_left_out_of_a_profiles_log_unless_the_reader_is_an_admin():
    scope = body(ACTION_LOG, "about_scope")
    assert "NOT COALESCE(s.is_anonymous, false)" in scope and "NOT COALESCE(c.is_anonymous, false)" in scope
    route = body(LOG_PAGES, "profile_log")
    assert "staff = bool(v and v.admin_level >= 3)" in route and "include_anonymous=staff" in route
    assert route.count("include_anonymous=staff") == 1                      # an author's own delete is an entry now, as Anonymous: no list of its own


def test_an_authors_own_delete_is_an_entry_drawn_as_anonymous_with_words_only_from_a_public_copy():
    rows = body(LOG_PAGES, "_rows")
    assert "log_view.anonymous_actor(numbers.get(item))" in rows and "action_log.deleted_texts(" in rows        # who: nobody named; the words: only a public `delete` copy
    assert "_may_see_deletes" not in LOG_PAGES and "_delete_rows" not in LOG_PAGES and "author_deletes" not in ACTION_LOG
    words = LOG_PAGES.split("def _deleted_words(")[1].split("\n\n\n")[0]
    assert "deletion.text_view(" in words and "log_view.without_links(log_view.without_media(" in words      # the rules, and no link or picture
    assert "_clean_wo_links.clean(" in words                                                                  # cleaned again with the site's own cleaner


def test_the_logs_are_linked_from_where_you_would_look_for_them():
    assert 'href="{{ u.permalink }}/log"' in read("ruqqus", "templates", "partials", "profile_header.html")
    assert 'href="/{{ target_type }}/{{ target.base36id }}/log"' in HISTORY
    assert '"author_delete_post"' in read("ruqqus", "classes", "mod_logs.py") and '"author_delete_comment"' in read("ruqqus", "classes", "mod_logs.py")


# --- the first slice of the audit (2026-11-05) ----------------------------------------------------------------------------------------------

def test_the_snapshot_kind_migration_is_in_the_schema_and_its_ledger_and_safe_to_run_twice():
    schema = read("schema.sql")
    for name, markers in (("2026-12-21_log_snapshot_kinds.sql", ("ADD COLUMN IF NOT EXISTS kind", "ADD COLUMN IF NOT EXISTS hider_id",
                                                                  "WHERE kind IS NULL", "log_snapshots_kind")),
                          ("2026-12-22_edit_groups.sql", ("ADD COLUMN IF NOT EXISTS edit_group", "CREATE INDEX IF NOT EXISTS"))):
        migration = read("scripts", "migrations", name)
        for marker in markers:
            assert marker in migration, (name, marker)
        assert f"('{name}')" in schema.split("schema_migrations", 1)[-1], name
        assert "REFERENCES" not in migration
    table = schema.split("CREATE TABLE public.log_snapshots (", 1)[1].split(");", 1)[0]
    assert "kind character varying(12) NOT NULL" in table and "hider_id integer" in table
    assert "CHECK (kind IN ('hide', 'author_hide', 'grace', 'delete', 'legal', 'evidence'))" in table      # `evidence` since 2027-01-08_evidence.sql
    assert "    edit_group bigint" in schema.split("CREATE TABLE public.content_edit_history (", 1)[1].split(");", 1)[0]
    assert "edit_group = Column(BigInteger" in read("ruqqus", "classes", "content_history.py")


def test_a_guildmaster_opens_only_a_guilds_hide_and_every_writer_says_what_its_copy_is():
    visible = body(ACTION_LOG, "snapshot_visible")
    # a guild's guildmasters open a guild's hide and, since 2026-11-14, the evidence an exile relied on (helpers/sanction_evidence.py): nothing else
    assert 'guild_mod and kind in (HIDE, EVIDENCE)' in visible and 'snapshot.get("hider_id") == viewer_id' in visible
    assert "SNAPSHOT_KINDS" in ACTION_LOG and "raise ValueError" in body(ACTION_LOG, "_keep")      # no default kind
    for src, fn, kind in ((POSTS, "delete_post_as", "kind=action_log.GRACE"), (COMMENTS, "delete_comment", "kind=action_log.GRACE"),
                          (COMMENTS, "author_hide_comment", "kind=action_log.AUTHOR_HIDE, hider_id=v.id"),
                          (COMMENTS, "mod_hide_comment", "kind=action_log.HIDE"), (BOARDS, "mod_hide_bid_pid", "kind=action_log.HIDE")):
        assert kind in body(src, fn), (fn, kind)
    assert "legal_hold=True" in body(ADMIN_API, "admin_csam_nuke") or "legal_hold=True" in ADMIN_API        # a legal hold is kind legal by itself


def test_the_role_and_the_audience_come_from_who_acted_and_never_from_a_guildmasters_words():
    assert "(admin_level or 0) >= 3 and any(mark in lowered for mark in ADMIN_MARKERS)" in ACTION_LOG
    assert 'lowered = (note or "").lower() if trusted else ""' in ACTION_LOG
    record = body(ACTION_LOG, "record")
    assert "trusted=resolved in (ADMIN, SYSTEM)" in record
    model = read("ruqqus", "classes", "mod_logs.py")
    assert "def title_text(self, titles=True):" in model
    assert "action.title_text(titles_shown)" in MODLOG


def test_the_public_page_leaves_out_a_private_guild_without_being_asked_to():
    assert "open_guilds_only=None" in ACTION_LOG and "open_guilds_only = level < 3" in body(ACTION_LOG, "fetch")
    for fn in ("public_log", "_about_page"):
        assert "open_guilds_only=False" not in body(LOG_PAGES, fn), fn
    assert "SITE_BOARDS" in ACTION_LOG and "action_log.SITE_BOARDS" in body(LOG_PAGES, "_rows")           # +general / +systemprofile are no guilds


def test_an_entry_never_pairs_an_account_with_an_anonymous_item():
    record = body(ACTION_LOG, "record")
    assert "_is_anonymous(db, post_id, comment_id)" in record and "post_id = comment_id = None" in record
    assert "_NOT_PAIRED_WITH_ANONYMOUS" in body(ACTION_LOG, "fetch") and "_NOT_PAIRED_WITH_ANONYMOUS" in body(ACTION_LOG, "about_scope")
    rows = body(LOG_PAGES, "_rows")
    assert rows.index('if e["target_user_id"]:') < rows.index('elif e["target_submission_id"]:')       # "exiled user @x", not "exiled #a1b2c"


def test_a_deleted_items_history_is_the_authors_and_the_admins_and_its_address_names_nobody():
    from_visibility = read("ruqqus", "helpers", "visibility.py")
    assert "def history_open(item, v):" in from_visibility and "v.admin_level >= 3 or anonymity.is_author(item, v)" in from_visibility
    for src, fn in ((POSTS, "post_history"), (COMMENTS, "comment_history")):
        assert "if not history_open(" in body(src, fn), fn
    assert "(self.deleted_utc or 0) > 0" in body(read("ruqqus", "classes", "submission.py"), "permalink")
    assert "(self.deleted_utc or 0) > 0" in body(read("ruqqus", "classes", "comment.py"), "permalink")
    text_rule = body(read("ruqqus", "helpers", "visibility.py"), "history_text_shown")
    assert "hidden_by_guild" in text_rule and "author_hidden" in text_rule                              # M2


def test_the_log_pages_are_noindex_rate_limited_and_disallowed_to_crawlers():
    for fn in ("public_log", "post_log", "comment_log", "profile_log"):
        decorators = LOG_PAGES.split(f"def {fn}(")[0].rsplit("@app.route", 1)[1]
        assert "@limiter.limit(LOG_RATE)" in decorators, fn
    assert "X-Robots-Tag" in body(LOG_PAGES, "_noindex") and "_noindex(render_template(" in body(LOG_PAGES, "public_log")
    assert "_noindex(render_template(" in body(LOG_PAGES, "_about_page")
    assert 'name="robots" content="noindex"' in read("ruqqus", "templates", "log.html")
    robots = read("ruqqus", "assets", "robots.txt")
    for line in ("Disallow: /log$", "Disallow: /log?", "Disallow: /log/", "Disallow: /post/*/log", "Disallow: /comment/*/log",
                 "Disallow: /@*/log"):
        assert line in robots, line
    # a rule is a prefix (RFC 9309, `*` any run, `$` the end): the Log is shut out, the sign-in pages are not (C9 of the review of 2026-11-06:
    # a bare "Disallow: /log" also matched /login, /login/qr/... and /logout)
    import re
    rules = [l.split(":", 1)[1].strip() for l in robots.split("User-Agent: DotBot")[0].splitlines() if l.startswith("Disallow:")]

    def blocked(path):
        return any(re.match(re.escape(r).replace(r"\*", ".*").replace(r"\$", "$"), path) for r in rules if r)

    for path in ("/log", "/log?guild=pets", "/log/deleted", "/log/12/copy", "/post/abc/log", "/comment/abc/copy", "/@someone/log"):
        assert blocked(path), path
    for path in ("/login", "/login/qr/start", "/logout", "/logos", "/post/abc", "/@someone"):
        assert not blocked(path), path
