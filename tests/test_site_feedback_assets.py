"""Feedback about the site, in the source: the migration and schema, the routes and what guards them, where the sheet
is mounted, what the page sends, and the places a per-account feature must reach (account closure, the data export,
retention, the privacy page)."""
import json
import re
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from ruqqus.helpers import account_cleanup, data_export, retention
from ruqqus.helpers import site_feedback as rules

ROOT = Path(__file__).resolve().parent.parent
RUQQUS = ROOT / "ruqqus"


def read(*parts):
    return RUQQUS.joinpath(*parts).read_text(encoding="utf-8")


ROUTES = read("routes", "site_feedback.py")
HELPER = read("helpers", "site_feedback.py")
IMAGE = read("helpers", "feedback_image.py")
MODAL = read("templates", "partials", "site_feedback_modal.html")
ADMIN = read("templates", "admin", "site_feedback.html")
DEFAULT = read("templates", "default.html")
JS = read("assets", "js", "site_feedback.js")
MIGRATION = (ROOT / "scripts" / "migrations" / "2026-11-30_site_feedback.sql").read_text(encoding="utf-8")
SCHEMA = (ROOT / "schema.sql").read_text(encoding="utf-8")
NOW = 2_000_000_000
DAY = 86400


def route(src, path):
    """The decorators and the first lines of the function under a route."""
    start = re.search(rf'@app\.(?:get|post)\("{re.escape(path)}"\)', src).start()
    return src[start:src.index("\n\n\n", start)]


def test_the_name_does_not_collide_with_show_fewer_posts_like_this():
    assert not (RUQQUS / "classes" / "site_feedback.py").exists()      # a class module would hide the helper (star import)
    assert "from .site_feedback import *" in read("routes", "__init__.py")
    for table in ("site_feedback", "site_feedback_images"):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in MIGRATION and f"CREATE TABLE public.{table} " in SCHEMA
    assert "REFERENCES" not in MIGRATION                                # no foreign key to users, as the other tables


def test_the_member_route_is_signed_in_checked_rate_limited_and_stops_big_requests():
    body = route(ROUTES, "/api/feedback")
    assert "@is_not_banned" in body and "@validate_formkey" in body
    assert '@limiter.limit("10/hour", key_func=member_limit_key)' in body
    assert "REQUEST_MAX_BYTES" in body and "DAILY_LIMIT" in body
    assert "request.max_content_length = REQUEST_MAX_BYTES" in body     # also when a client sends no length


def test_every_admin_route_needs_level_3_and_a_change_needs_the_form_key():
    for path in ("/admin/feedback", "/admin/feedback/<int:feedback_id>/image", "/admin/feedback/<int:feedback_id>/status"):
        assert "@admin_level_required(3)" in route(ROUTES, path), path
    assert "@validate_formkey" in route(ROUTES, "/admin/feedback/<int:feedback_id>/status")
    assert "_back()" in route(ROUTES, "/admin/feedback/<int:feedback_id>/status")
    assert 'startswith("/admin/feedback")' in ROUTES                    # the page returns only to the inbox


def test_a_screenshot_is_served_only_to_admins_typed_by_its_bytes_and_never_cached():
    body = route(ROUTES, "/admin/feedback/<int:feedback_id>/image")
    assert "media_rules.sniff(" in body and "abort(404)" in body
    assert '"Cache-Control"] = "no-store"' in body and '"X-Content-Type-Options"] = "nosniff"' in body
    assert "sandbox; default-src 'none'" in body
    assert "svg" not in ROUTES.lower() and "html" not in IMAGE.lower().split('"""')[2]


def test_the_sheet_is_for_members_outside_embeds_and_every_entry_point_opens_it():
    assert """{% if v and not request.args.get('embed') %}{% include "partials/site_feedback_modal.html" %}{% endif %}""" in DEFAULT
    assert "{% if v and not request.args.get('embed') %}<script src=\"{{ asset_url('js/site_feedback.js') }}\"></script>{% endif %}" in DEFAULT
    # one entry: the account menu (partials/account_menu.html, which default.html and header.html both include), and a
    # phone opens that from the avatar in the top bar too. (The phone menu in default.html is drawn only for visitors,
    # `{% if not v %}`, so an entry there would never be seen.)
    menu = read("templates", "partials", "account_menu.html")
    assert DEFAULT.count("data-feedback-open") == 0 and menu.count("data-feedback-open") == 1
    assert menu.index("data-feedback-open") < menu.index("data-logout")      # in the last group, above Log out
    assert 'src="/assets' not in MODAL and 'src="/assets' not in ADMIN and "?v=" not in MODAL


def test_the_sheet_offers_the_kinds_the_server_accepts_and_the_limit_it_enforces():
    assert re.findall(r'name="kind" id="[^"]+" value="(\w+)"', MODAL) == list(rules.KINDS)
    assert f'maxlength="{rules.MESSAGE_MAX}"' in MODAL and f"0 / {rules.MESSAGE_MAX}" in MODAL
    assert f'maxlength="{rules.RESPONSE_MAX}"' in ADMIN
    assert 'accept="image/png,image/jpeg,image/webp"' in MODAL
    assert "nothing else" in MODAL                                      # the sentence that says what is attached
    assert 'role="status"' in MODAL and 'aria-live="polite"' in MODAL and 'aria-label="{{ _(\'Close\') }}"' in MODAL


def test_the_page_sends_only_the_facts_the_server_reads():
    sent = re.search(r"function context\(\) \{\s*return JSON.stringify\(\{(.*?)\}\);", JS, re.S).group(1)
    keys = re.findall(r"^\s*(\w+):", sent, re.M)
    assert set(keys) == {"path", "theme", "density", "width", "height", "lang"}
    page, context = rules.parse_context(json.dumps({k: "x" for k in keys}), "")
    assert page == "" and json.loads(context) == {}                     # and the server drops what is not valid
    assert "/api/feedback" in JS and "formkey()" in JS
    assert "http://" not in JS and "https://" not in JS                 # nothing leaves for another site
    assert "createImageBitmap" in JS and "toBlob" in JS                 # the screenshot is drawn again first
    assert "innerHTML" not in JS                                        # messages go in as text


def test_nothing_a_member_reads_says_reply():
    # the site's word is "comment" for a reply (tests/test_terminology.py); the admins' answer is a response
    for name, src in (("modal", MODAL), ("js", JS), ("routes", ROUTES), ("helper", HELPER), ("image", IMAGE)):
        assert not re.search(r"\brepl(?:y|ies|ied|ying)\b", src, re.I), name


def test_a_closing_account_takes_its_feedback_and_screenshots_with_it():
    names = [name for name, _ in account_cleanup.STEPS]
    assert names.index("remove feedback screenshots") < names.index("remove feedback")    # found through the items
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE site_feedback (id INTEGER PRIMARY KEY, user_id INTEGER)"))
        conn.execute(text("CREATE TABLE site_feedback_images (feedback_id INTEGER PRIMARY KEY, data BLOB)"))
        conn.execute(text("INSERT INTO site_feedback VALUES (1, 5), (2, 6)"))
        conn.execute(text("INSERT INTO site_feedback_images VALUES (1, x'00'), (2, x'00')"))
    with Session(engine) as db:
        account_cleanup.close_account(db, 5, 1000)
        assert db.execute(text("SELECT id FROM site_feedback")).scalars().all() == [2]
        assert db.execute(text("SELECT feedback_id FROM site_feedback_images")).scalars().all() == [2]


def test_the_data_export_has_the_members_own_feedback():
    sql = dict(data_export.SECTIONS)["feedback"]
    assert "WHERE user_id = :u" in sql and "data" not in sql.replace("created_utc", "")      # no screenshot bytes
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("""CREATE TABLE site_feedback (id INTEGER PRIMARY KEY, user_id INTEGER, kind TEXT, message TEXT,
            page TEXT, context TEXT, status TEXT, response TEXT, created_utc INTEGER)"""))
        conn.execute(text("INSERT INTO site_feedback VALUES (1, 5, 'bug', 'mine', '/', '{}', 'open', '', 1), "
                          "(2, 6, 'idea', 'theirs', '/', '{}', 'open', '', 2)"))
    with Session(engine) as db:
        rows = data_export.build(db, 5)["feedback"]
        assert [r["message"] for r in rows] == ["mine"]


def test_screenshots_go_a_month_after_the_item_is_closed_and_never_before():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE site_feedback (id INTEGER PRIMARY KEY, resolved_utc INTEGER)"))
        conn.execute(text("CREATE TABLE site_feedback_images (feedback_id INTEGER PRIMARY KEY, data BLOB)"))
        age = retention.FEEDBACK_IMAGE_DAYS * DAY
        conn.execute(text("INSERT INTO site_feedback VALUES (1, 0), (2, :recent), (3, :old)"),
                     {"recent": NOW - age + DAY, "old": NOW - age - DAY})
        conn.execute(text("INSERT INTO site_feedback_images VALUES (1, x'00'), (2, x'00'), (3, x'00')"))
    with Session(engine) as db:
        done = retention.prune(db, NOW)
        assert done["screenshots of closed feedback"] == 1
        assert db.execute(text("SELECT feedback_id FROM site_feedback_images ORDER BY 1")).scalars().all() == [1, 2]


def test_the_admin_page_links_the_inbox_and_the_privacy_page_names_the_feedback():
    assert "[Feedback from members](/admin/feedback) ({{ feedback_waiting() }} waiting)" in read("templates", "admin", "admin_home.html")
    assert 'name="formkey" value="{{ v.formkey }}"' in ADMIN
    privacy = read("templates", "help", "privacy.html")
    assert '"Send feedback"' in privacy and "screenshot" in privacy and "deleted with your account" in privacy
