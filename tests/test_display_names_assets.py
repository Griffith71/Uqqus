"""A display name crosses the schema, the model, Settings > Profile, the word filter, the anonymous stand-in, the bylines,
the JSON, the bot forms and the admins' clear button. The rules are tests/test_display_names.py."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


def func(source, name, nxt="\n\n\n"):
    return source.split(f"def {name}(")[1].split(nxt)[0]


MIGRATION = read("scripts", "migrations", "2026-10-17_display_name.sql")
SCHEMA = read("schema.sql")
USER = read("ruqqus", "classes", "user.py")
SETTINGS = read("ruqqus", "routes", "settings.py")
TEMPLATES = ROOT / "ruqqus" / "templates"


def test_the_migration_the_schema_and_the_model_agree():
    column = "display_name character varying(50) DEFAULT '' NOT NULL"
    assert column in MIGRATION and column in SCHEMA.split("CREATE TABLE public.users (")[1].split("\n);")[0]
    assert 'display_name = Column(String(50), default="")' in USER


def test_the_shown_name_is_the_display_name_or_the_handle_and_the_json_carries_it():
    assert "return self.display_name or self.username" in func(USER, "shown_name", "\n    @property")
    assert "'display_name': self.display_name or ''" in func(USER, "json_raw", "\n    def ")


def test_settings_saves_it_free_with_the_rules_and_the_impersonation_check():
    route = func(SETTINGS, "settings_profile_post")
    saving = route.split('request.values.get("display_name_form") is not None:')[1].split('if request.values.get("details") is not None:')[0]
    assert "display_names.clean(request.values.get(\"display_name\", \"\"))" in saving
    assert "display_name_store.takes_a_handle(g.db, v.id, new_name)" in saving
    assert "apply_user_severity(v)" in saving                                    # re-stamped on every write
    for money in ("coin_balance", "name_changed_utc"):
        assert money not in saving, money                                          # free, and no 7-day wait
    assert "@validate_formkey" in SETTINGS.split("def settings_profile_post(")[0].rsplit("\n\n\n", 1)[1]


def test_the_username_box_is_no_longer_called_the_display_name():
    page = read("ruqqus", "templates", "settings_profile.html")
    new = page.split('id="display-name"')[1].split('id="username"')[0]
    assert 'name="display_name"' in new and 'name="display_name_form"' in new and 'maxlength="50"' in new
    assert 'autocomplete="nickname"' in new and "at no cost" in new
    old = page.split('id="username"')[1].split("</form>")[0]
    assert 'action="/settings/name_change"' in old and "Change Username" in old and "Change Display Name" not in page
    assert "Changing it costs 20 Coins" in old


def test_the_word_filter_rates_it_with_the_username():
    store = read("ruqqus", "helpers", "word_filter_store.py")
    rating = func(store, "apply_user_severity", "\n\ndef ")
    assert "max(name_severity(user.username, db), f.severity(getattr(user, \"display_name\", \"\") or \"\"))" in rating


def test_an_anonymous_byline_never_has_one():
    stand_in = read("ruqqus", "helpers", "anonymity.py").split("class AnonymousAuthor:")[1].split("ANONYMOUS = ")[0]
    assert 'display_name = ""' in stand_in and "shown_name = LABEL" in stand_in


def test_every_byline_draws_the_display_name_then_the_handle():
    pattern = re.compile(r'<span class="user-name-inner">\{\{ (?P<who>[^}]*?)\.shown_name \}\}</span>\{\{ handle_tag\((?P=who)\) \}\}')
    counts = {name: len(pattern.findall((TEMPLATES / name).read_text(encoding="utf-8")))
              for name in ("submission_listing.html", "submission.html", "comments.html", "partials/coauthors.html")}
    assert counts == {"submission_listing.html": 6, "submission.html": 6, "comments.html": 1, "partials/coauthors.html": 1}
    for path in TEMPLATES.rglob("*.html"):
        for line in path.read_text(encoding="utf-8").splitlines():
            assert not re.search(r'user-name-inner">\{\{ [^}]*\.username \}\}', line), (path.name, line[:120])
    helper = func(read("ruqqus", "helpers", "jinja2.py"), "handle_tag")
    assert "if not getattr(u, \"display_name\", None)" in helper and "escape(u.username)" in helper


def test_the_profile_and_the_lists_lead_with_the_display_name():
    header = read("ruqqus", "templates", "partials", "profile_header.html")
    assert '<h1 class="profile-x-name" id="profile-x-name">{{ u.shown_name }}' in header
    assert '<div class="profile-x-handle">@{{ u.username }}' in header                 # the handle is always shown there
    for name in ("follow_list.html", "partials/suggestion_row.html", "user_listing.html"):
        assert "display_name" in (TEMPLATES / name).read_text(encoding="utf-8"), name
    for style in ("main.scss", "main_dark.scss"):
        assert ".user-name .user-handle" in read("ruqqus", "assets", "style", style)


def test_the_panels_that_draw_names_from_json_get_it_too():
    assert '"display_name": owner.display_name or ""' in read("ruqqus", "routes", "stories.py")
    chat = read("ruqqus", "routes", "chat.py")
    assert chat.count('"display_name": ') == 2
    assert "display_name" in read("ruqqus", "assets", "js", "stories.js")


def test_bots_have_one_on_the_proposal_form_and_on_their_page():
    routes = read("ruqqus", "routes", "bot_accounts.py")
    helper = func(routes, "_display_name")
    assert "display_names.clean(raw)" in helper and "display_name_store.takes_a_handle(g.db, account_id, name)" in helper
    assert "_display_name(request.form.get(\"display_name\"), None)" in func(routes, "settings_bots_apply")
    assert "account.display_name, error = _display_name(" in func(routes, "settings_bot_profile")
    assert 'name="display_name"' in read("ruqqus", "templates", "settings_bots.html")
    assert 'name="display_name"' in read("ruqqus", "templates", "settings_bot.html")


def test_an_admin_can_clear_one_and_the_account_is_told():
    route = func(SETTINGS, "admin_clear_display_name")
    assert "v.admin_level <= (target.admin_level or 0)" in route                   # the same bar as banning
    assert 'target.display_name = ""' in route and "apply_user_severity(target)" in route and "send_notification(target" in route
    head = SETTINGS.split("def admin_clear_display_name(")[0].rsplit("\n\n\n", 1)[1]
    assert "@admin_level_required(3)" in head and "@validate_formkey" in head
    page = read("ruqqus", "templates", "userpage.html")
    assert 'action="/admin/display_name/clear"' in page and "v.admin_level > u.admin_level" in page


def test_claude_md_describes_display_names():
    claude = read("CLAUDE.md")
    assert "## Display names (legacy app)" in claude
    section = claude.split("## Display names (legacy app)")[1].split("\n## ")[0]
    for part in ("helpers/display_names.py", "display_name_store", "shown_name", "handle_tag", "name_severity",
                 "2026-10-17_display_name.sql", "mentions inside post and comment text", "Anonymous", "/admin/display_name/clear"):
        assert part in section, part
