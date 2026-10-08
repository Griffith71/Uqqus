"""Stories cross the schema, the rules, the store, the routes, the media rules, the profile page, the script and both
stylesheets. These checks keep the layers agreeing and keep the promises: a story is watched only by the people it was made
for, nothing names a viewer, the page draws text through textContent only, and a story's picture is never public-cached."""
import re
from pathlib import Path

from ruqqus.helpers import circles
from ruqqus.helpers import stories as rules

ROOT = Path(__file__).resolve().parent.parent


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


ROUTES = read("ruqqus", "routes", "stories.py")
STORE = read("ruqqus", "helpers", "story_store.py")
GUARD = read("ruqqus", "helpers", "circle_guard.py")
ATTACH = read("ruqqus", "helpers", "media", "attach.py")
JS = read("ruqqus", "assets", "js", "stories.js")
SHEETS = read("ruqqus", "templates", "partials", "story_sheets.html")
BITS = read("ruqqus", "templates", "partials", "story_bits.html")
QUEUE = read("ruqqus", "templates", "admin", "story_reports.html")
USERPAGE = read("ruqqus", "templates", "userpage.html")
DEFAULT = read("ruqqus", "templates", "default.html")
MAIN = read("ruqqus", "assets", "style", "main.scss")
DARK = read("ruqqus", "assets", "style", "main_dark.scss")
SCHEMA = read("schema.sql")
MIGRATION = read("scripts", "migrations", "2026-10-10_stories.sql")

TABLES = ("stories", "story_views", "highlights", "highlight_stories", "story_reports")


def func(source, name, nxt="\n@app."):
    return source.split(f"def {name}(")[1].split(nxt)[0]


def columns(sql, table, public=False):
    head = f"CREATE TABLE {'public.' if public else 'IF NOT EXISTS '}{table} ("
    body = sql.split(head)[1].split("\n);")[0]
    found = set()
    for line in body.splitlines():
        match = re.match(r"^    ([a-z_]+) ", line)
        if match and match.group(1) not in ("constraint", "primary", "unique", "check"):
            found.add(match.group(1))
    return found


# --- the schema ----------------------------------------------------------------------------------------------

def test_the_tables_are_the_same_in_the_schema_and_the_migration():
    for table in TABLES:
        assert columns(SCHEMA, table, public=True) == columns(MIGRATION, table), table


def test_the_constraints_that_carry_the_promises_are_in_both():
    for sql in (SCHEMA, MIGRATION):
        assert "CONSTRAINT stories_audience CHECK (audience IN (0, 1, 2))" in sql      # 3 and 8 are internal marks, never a choice
        assert "CONSTRAINT stories_kind CHECK (kind IN ('image', 'text', 'video'))" in sql
        assert "UNIQUE (story_id, viewer_id)" in sql
        assert "UNIQUE (story_id, reporter_id)" in sql
        assert "PRIMARY KEY (highlight_id, story_id)" in sql
        assert "media_assets_story_idx" in sql


def test_a_picture_belongs_to_one_story_and_the_model_knows_the_column():
    assert "story_id integer" in SCHEMA.split("CREATE TABLE public.media_assets (")[1].split("\n);")[0]
    assert "ALTER TABLE media_assets ADD COLUMN IF NOT EXISTS story_id integer;" in MIGRATION
    assert "story_id = Column(Integer, default=None)" in read("ruqqus", "classes", "media.py")


def test_there_are_no_foreign_keys_to_users():
    assert "REFERENCES users" not in MIGRATION      # users.id has no unique key in existing databases


# --- the routes ----------------------------------------------------------------------------------------------

def blocks():
    return [b for b in re.split(r"\n(?=@app\.route\()", ROUTES) if b.startswith("@app.route(")]


def test_every_route_needs_an_account_and_every_change_needs_the_form_key():
    found = blocks()
    assert len(found) == 13
    for block in found:
        head = block.split("def ")[0]
        assert any(d in head for d in ("@auth_required", "@is_not_banned", "@admin_level_required(3)")), head
        if '"POST"' in head:
            assert "@validate_formkey" in head, head


def test_the_moderators_queue_is_for_admins_only():
    for block in blocks():
        if '"/admin/' in block.split("def ")[0]:
            assert "@admin_level_required(3)" in block.split("def ")[0]
    assert "/admin/story_reports" in read("ruqqus", "templates", "admin", "admin_home.html")


def test_every_answer_to_a_member_is_private_and_uncached():
    after_helpers = ROUTES.split("# ------------------------------------------------------------------ watching")[1]
    assert "jsonify(" not in after_helpers                      # only `_private` (no-store) and `_fail` build a response
    assert '"Cache-Control"] = "private, no-store"' in ROUTES


def test_the_author_is_the_signed_in_member_never_a_field():
    body = func(ROUTES, "story_create")
    assert "story_store.create(g.db, v.id," in body
    assert 'request.form.get("user' not in body and 'request.form.get("author' not in body
    for name in ("story_delete", "highlight_create", "highlight_rename", "highlight_delete"):
        assert "v.id" in func(ROUTES, name, "\n@app.") or "v.id" in func(ROUTES, name, "\n\n\n")


def test_a_story_is_made_through_every_rule():
    body = func(ROUTES, "story_create")
    for needed in ("rules.parse_kind(", "circles.parse_audience(", "rules.clean_text(", "rules.parse_video(", "rules.refusal(",
                   "story_store.count_today(", "rules.DAILY_MAX", "get_filter(g.db).severity(", "story_store.attach_picture(",
                   "media_safety.scan_later(", 'kinds=("image",)', "429"):
        assert needed in body, needed
    assert body.index("story_store.count_today(") < body.index("story_store.create(")
    assert body.index("story_store.create(") < body.index("story_store.attach_picture(") < body.index("g.db.commit()")


def test_watching_viewing_and_reporting_all_ask_the_store_who_may_watch():
    for name in ("stories_of", "story_view", "story_report"):
        assert "story_store.watchable(" in func(ROUTES, name), name
    assert "story_store.highlight_stories(" in func(ROUTES, "highlight_of")
    assert "story_store.watchable(" in func(ROUTES, "story_ring", "\n\n\ndef ")
    assert "story_store.rings(" in func(ROUTES, "story_ring", "\n\n\ndef ")
    assert "story_store.highlights(" in func(ROUTES, "profile_highlights", "\n\n\napp.jinja_env")


def test_a_visitor_is_shown_no_ring_and_no_highlight():
    assert "if viewer is None:\n        return None" in func(ROUTES, "story_ring", "\n\n\ndef ")
    assert "if viewer is None:\n        return []" in func(ROUTES, "profile_highlights", "\n\n\napp.jinja_env")


def test_the_store_decides_in_one_place():
    allowed = func(STORE, "_allowed", "\n\n\ndef ")
    for needed in ("circle_store.blocked_between(", "is_hidden(viewer_level(viewer), row.word_severity)", "circle_guard.may_see("):
        assert needed in allowed
    assert allowed.index("blocked_between") < allowed.index("may_see(")
    for name in ("watchable", "rings", "highlights", "highlight_stories"):
        assert "_allowed(" in func(STORE, name, "\n\n\n") or "_allowed(" in func(STORE, name, "\n\ndef "), name


def test_nothing_ever_names_a_viewer():
    selects = re.findall(r"SELECT ([^\"]*?) FROM story_views", STORE)
    assert sorted(selects) == ["story_id", "story_id, COUNT(*)"]
    assert "viewer_id" not in JS and "viewers" not in JS
    assert "viewers" not in ROUTES and "view_counts" in ROUTES


def test_the_reasons_and_texts_are_cut_to_their_limits_before_storing():
    assert "rules.parse_reason(" in func(ROUTES, "story_report")
    assert "rules.parse_title(" in func(ROUTES, "highlight_create") and "rules.parse_title(" in func(ROUTES, "highlight_rename")


def test_removing_a_story_by_an_admin_resolves_its_reports():
    body = func(ROUTES, "admin_story_remove", "\n\n\n@app.")
    assert "story_store.remove(" in body and "story_store.resolve_reports(" in body


def test_the_routes_and_the_page_globals_are_registered():
    assert "from .stories import *" in read("ruqqus", "routes", "__init__.py")
    assert "story_ring=story_ring, profile_highlights=profile_highlights, story_backgrounds=rules.BACKGROUNDS" in ROUTES


# --- the audiences and the media -----------------------------------------------------------------------------

def test_the_audiences_are_the_circle_ones_plus_one_internal_mark():
    assert circles.STORY_OPEN == 8 and circles.STORY_OPEN not in circles.CHOICES
    assert sorted(circles.CHOICES) == [0, 1, 2]
    assert GUARD.index("circles.STORY_OPEN") < GUARD.index("circles.GUILD", GUARD.index("def may_see("))


def test_a_storys_picture_is_judged_by_its_story_and_never_public_cached():
    audience = func(ATTACH, "audience_of", "\n\n\ndef ")
    assert "asset.story_id" in audience and "story.audience or circles.STORY_OPEN" in audience
    assert "circles.GUILD, None" in audience                                    # a story that is gone is for nobody
    live = func(ATTACH, "is_live", "\n\n\ndef ")
    assert "asset.story_id" in live and "highlight_stories" in live and "deleted_utc" in live


def test_a_storys_text_goes_through_the_word_filter_and_is_cleaned():
    assert "word_severity" in STORE and "get_filter(g.db).severity(body)" in ROUTES
    assert "_CONTROL.sub" in read("ruqqus", "helpers", "stories.py")


# --- the page ------------------------------------------------------------------------------------------------

def test_the_script_draws_only_with_text_nodes():
    for sink in (".innerHTML", "insertAdjacentHTML", "outerHTML", "document.write", "eval(", "new Function(", "setTimeout('", 'setTimeout("'):
        assert sink not in JS, sink
    assert "textContent" in JS
    assert "story.picture.indexOf('/media/') === 0" in JS                       # a picture address must be one of this site's media paths
    assert "YOUTUBE_ID.test(" in JS and "BACKGROUND.test(" in JS                # a video id and a colour name are checked before they are used


def test_every_id_the_script_looks_up_is_in_the_sheets():
    wanted = set(re.findall(r"getElementById\('([^']+)'\)", JS)) | set(re.findall(r"field\('([^']+)'\)", JS))
    assert len(wanted) >= 25
    for ident in wanted:
        if ident.startswith("story-audience-"):                                  # made by the loop over the three audiences
            assert 'id="story-audience-{{ value }}"' in SHEETS and ident[-1] in "012", ident
        else:
            assert f'id="{ident}"' in SHEETS, ident
    for ident in ("story-pane-text", "story-pane-image", "story-pane-video"):
        assert f'id="{ident}"' in SHEETS, ident


def test_the_script_and_the_routes_use_the_same_addresses():
    for needed in ("'/api/stories/'", "'/view'", "'/delete'", "'/report'", "'/api/stories/archive'", "'/api/highlights/'", "'/api/highlights'", "'/rename'", "'/api/stories'"):
        assert needed in JS, needed
    for needed in ('"/api/stories/<username>"', '"/api/stories/<int:sid>/view"', '"/api/stories/<int:sid>/delete"', '"/api/stories/<int:sid>/report"',
                   '"/api/stories/archive"', '"/api/highlights/<int:hid>"', '"/api/highlights"', '"/api/highlights/<int:hid>/rename"',
                   '"/api/highlights/<int:hid>/delete"', '"/api/stories"'):
        assert needed in ROUTES, needed


def test_the_picture_uses_the_linked_storage_uploader_not_this_site():
    assert 'data-media-main="story-media"' in SHEETS and 'id="story-media"' in SHEETS
    assert 'accept="image/*"' in SHEETS
    assert "/assets/js/media_upload.js" in DEFAULT


def test_the_sheets_hold_nothing_a_member_wrote():
    expressions = set(re.findall(r"\{\{\s*([^}]*?)\s*\}\}", SHEETS))
    assert expressions <= {"name", "value", "label", "loop.first"} or all(e in ("name", "value", "label") for e in expressions - {"loop.first"}), expressions
    for source in (SHEETS, BITS, QUEUE):
        assert "|safe" not in source and "| safe" not in source


def test_the_profile_draws_the_ring_and_the_highlights_for_the_desktop_and_the_phone():
    assert 'from "partials/story_bits.html" import story_avatar, highlights_row' in USERPAGE
    assert "{% set story_ui = true %}" in USERPAGE
    assert USERPAGE.count("story_avatar(u, v,") == 2 and USERPAGE.count("highlights_row(u, v)") == 2
    assert "story_ring(u, v)" in BITS and "profile_highlights(u, v)" in BITS
    assert "data-story-add" in BITS and "data-highlight-new" in BITS and "{{ h.title }}" in BITS
    assert "if ring %} data-story-user=" in BITS and 'role="button"' in BITS      # only a ring can be clicked, and the button is the picture
    assert BITS.count('role="button"') == 1                                     # the + is a real button of its own, not inside another one


def test_the_viewer_and_the_script_are_on_pages_that_need_them_for_signed_in_members():
    assert "{% if v and story_ui and not request.args.get('embed') %}{% include \"partials/story_sheets.html\" %}{% endif %}" in DEFAULT
    assert "{% if v and story_ui and not request.args.get('embed') %}<script src=\"/assets/js/stories.js?v=1\"></script>{% endif %}" in DEFAULT


def test_the_queue_forms_carry_the_form_key():
    assert QUEUE.count('name="formkey" value="{{ v.formkey }}"') == 2
    assert "/admin/story_reports/{{ r.story_id }}/remove" in QUEUE and "/admin/story_reports/{{ r.story_id }}/dismiss" in QUEUE


# --- the stylesheets -----------------------------------------------------------------------------------------

def test_every_text_card_colour_has_a_rule_in_both_stylesheets():
    for name in rules.BACKGROUNDS:
        for sheet in (MAIN, DARK):
            assert f".story-bg-{name} " in sheet, name


def test_every_class_the_script_and_templates_set_has_a_rule_in_both_stylesheets():
    used = set()
    for source in (JS, SHEETS, BITS):
        for literal in re.findall(r"['\"]((?:story|highlight)-[a-z0-9 -]+)['\"]", source):
            used.update(literal.split())
        for literal in re.findall(r'class="([^"]*)"', source):
            used.update(w for w in literal.split() if re.fullmatch(r"(?:story|highlight)[a-z0-9-]*", w))
    used = {u for u in used if re.fullmatch(r"(?:story|highlight)[a-z0-9-]*", u)}
    used = {u for u in used if not u.startswith("story-bg-") and not u.startswith(("story-audience-", "story-pane-"))}
    ids = set(re.findall(r'id="([^"]+)"', SHEETS)) | set(re.findall(r"getElementById\('([^']+)'\)", JS))
    used -= ids
    used -= {"story-ring-unseen", "story-ring-seen", "story-ring-own"}         # built from the ring state, checked below
    assert used
    for cls in sorted(used):
        for sheet in (MAIN, DARK):
            assert f".{cls}" in sheet, cls


def test_the_rings_the_viewer_and_the_sheets_are_styled_in_both():
    for sheet in (MAIN, DARK):
        for needed in (".story-ring-unseen", ".story-ring-own", ".story-ring::before", ".story-viewer[hidden]", "body.story-open", ".story-stage-video",
                       ".story-swatch:has(input:checked)", ".highlight-new", ".highlight-pick"):
            assert needed in sheet, needed
    assert MAIN.count(".story-") == DARK.count(".story-") and MAIN.count(".highlight") == DARK.count(".highlight")


def test_the_ring_states_the_server_sends_are_the_ones_the_stylesheets_know():
    assert (rules.UNSEEN, rules.SEEN) == ("unseen", "seen")
    assert 'return "own" if' in ROUTES
    for state in ("unseen", "seen", "own"):
        assert f"story-ring-{state}" in MAIN or state == "seen"
    assert ".story-ring::before {" in MAIN                                       # the grey ring is the base; unseen and own add the gradient


# --- the docs ------------------------------------------------------------------------------------------------

def test_the_project_notes_describe_stories():
    notes = read("CLAUDE.md")
    assert "## Stories (legacy app)" in notes
    section = notes.split("## Stories (legacy app)")[1].split("\n## ")[0]
    for needed in ("story_store._allowed", "watchable", "STORY_OPEN", "Highlight", "viewer", "tests/test_stories_assets.py"):
        assert needed in section, needed
