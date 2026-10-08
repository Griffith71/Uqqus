"""The For You area in Settings > Content: a Reset button that saves a date. The feed does not read the date yet
(the owner asked for the area first and the effect when For You gets a real algorithm), so the card must say so,
and the model, schema and migration must agree on the one column."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


SETTINGS = read("ruqqus", "routes", "settings.py")
USER = read("ruqqus", "classes", "user.py")
CARD = read("ruqqus", "templates", "settings_filters.html")


def route():
    return SETTINGS.split("def settings_for_you_reset(")[1].split("\n@app.")[0]


def test_the_model_the_schema_and_the_migration_agree_on_the_column():
    assert "for_you_reset_utc = Column(Integer, default=0)" in USER
    users = read("schema.sql").split("CREATE TABLE public.users (")[1].split("\n);")[0]
    assert "    for_you_reset_utc integer DEFAULT 0 NOT NULL" in users
    migration = read("scripts", "migrations", "2026-10-10_for_you_reset.sql")
    assert "ALTER TABLE users ADD COLUMN IF NOT EXISTS for_you_reset_utc integer NOT NULL DEFAULT 0;" in migration


def test_the_route_needs_a_login_and_the_form_key_and_only_saves_the_callers_own_date():
    head = SETTINGS.split("def settings_for_you_reset(")[0].rsplit("\n\n\n", 1)[1]
    assert '@app.route("/settings/for_you/reset", methods=["POST"])' in head
    assert "@auth_required" in head and "@validate_formkey" in head
    body = route()
    assert "v.for_you_reset_utc = int(time.time())" in body
    assert body.count("g.db.add(v)") == 1 and "g.db.commit()" in body
    # the lists kept for a few minutes per member are dropped, so a later change shows at once
    for name in ("interest_subcats", "social_proof_post_ids", "for_you_idlist"):
        assert f"cache.delete_memoized(v.{name})" in body


def test_the_card_is_on_the_content_settings_page_and_is_honest_about_the_effect():
    assert 'id="for-you"' in CARD and 'id="for-you-reset"' in CARD and 'id="for-you-reset-date"' in CARD
    assert "post_toast('/settings/for_you/reset'" in CARD and "confirm(" in CARD
    assert "It does not change what you see yet." in CARD
    assert "{{ v.for_you_reset_date }}" in CARD and "| safe" not in CARD.split('id="for-you"')[1]


def test_the_date_is_shown_in_the_site_format_and_never_for_a_member_who_did_not_reset():
    body = USER.split("def for_you_reset_date(self):")[1].split("@property")[0]
    assert "if not self.for_you_reset_utc:" in body and "return None" in body
    assert 'time.strftime("%d %B %Y", time.gmtime(self.for_you_reset_utc))' in body


def test_the_claude_md_records_where_the_feed_will_read_it():
    doc = read("CLAUDE.md")
    assert "## For You settings (legacy app)" in doc
    assert "`User.interest_subcats`" in doc and "`social_proof_post_ids`" in doc
