"""An account's "Keep my posts out of All" (helpers/all_feed.py; the owner, 2026-10-27): the column, its migration and schema
mirror, the settings switch and route, and that All applies it with its guild copies following their guild."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


def test_the_column_is_off_for_everyone_and_mirrored():
    migration = read("scripts", "migrations", "2026-12-05_account_all_opt_out.sql")
    assert "ALTER TABLE users ADD COLUMN IF NOT EXISTS all_opt_out boolean DEFAULT false NOT NULL;" in migration
    schema = read("schema.sql")
    assert "    all_opt_out boolean DEFAULT false NOT NULL," in schema.split("CREATE TABLE public.users (")[1].split(");")[0]
    assert "('2026-12-05_account_all_opt_out.sql')" in schema
    assert "all_opt_out = Column(Boolean, default=False)" in read("ruqqus", "classes", "user.py")


def test_all_applies_it_and_hands_the_same_rule_to_the_family_step():
    front = read("ruqqus", "routes", "front.py").split("def frontlist(")[1].split("\ndef ")[0]
    assert "posts = posts.filter(all_feed.in_all(Submission, v, User))" in front
    assert "posts = one_per_family(posts, Submission, lambda original: all_feed.in_all(original, v, User))" in front
    assert front.index("hide_sensitive(posts, v, Submission)") < front.index("all_feed.in_all(Submission, v, User)")
    rule = read("ruqqus", "helpers", "all_feed.py")
    assert "func.coalesce(model.is_anonymous, False) == True" in rule and "func.coalesce(model.repost_id, 0) != 0" in rule


def test_the_switch_is_the_members_own_with_the_form_key_and_drops_their_cached_feeds():
    route = read("ruqqus", "routes", "settings.py").split("def settings_all_opt_out(")[1].split("\n@app.")[0]
    head = read("ruqqus", "routes", "settings.py").split("def settings_all_opt_out(")[0][-200:]
    assert "@auth_required\n@validate_formkey\n" in head
    assert 'v.all_opt_out = request.values.get("on") == "true"' in route and "muting_rules.clear_cached_feeds(v)" in route
    page = read("ruqqus", "templates", "settings_filters.html")
    assert re.search(r'id="all-opt-out"\{% if v.all_opt_out %\} checked\{% endif %\} onchange="post_toast\(\'/settings/all_opt_out\?on=\' \+ this.checked\)"', page)
    assert "anonymous posts always appear in All" in page
