"""The X-style profile header (templates/partials/profile_header.html) and what it stands on: the details a member
writes (helpers/profile_details.py), the lists behind the counts and who may open them (helpers/follow_lists.py,
routes/profile_lists.py), the "⋯" menu (assets/js/profile_actions.js, the curation picker's account mode) and account
reports (helpers/user_reports.py, routes/account_reports.py)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


HEADER = read("ruqqus", "templates", "partials", "profile_header.html")
USERPAGE = read("ruqqus", "templates", "userpage.html")


def test_the_profile_draws_one_header_for_every_width():
    assert '{% include "partials/profile_header.html" %}' in USERPAGE
    assert "{% block mobileUserBanner %}{% endblock %}" in USERPAGE
    assert "asset_url('js/profile_actions.js')" in USERPAGE
    assert '{% if v and v.id != u.id %}{% include "partials/report_account_modal.html" %}{% endif %}' in USERPAGE


def test_the_header_shows_what_an_x_profile_shows():
    for needle in ("profile-x-banner", "story_avatar(u, v,", "highlights_row(u, v)", "u.bio_html_for(v)",
                   "u.details_for(v)", "details.job", "details.location", "u.display_region_name",
                   'data-target="#aboutAccountModal"', "details.website", "Joined {{ u.joined_month }}",
                   "profile_lists(u, v)", "lists[key].open", "u.rep_class", "Follows you"):
        assert needle in HEADER, needle


def test_nothing_a_member_wrote_is_drawn_as_html_except_the_sanitized_bio():
    assert HEADER.count("| safe") == 1 and "{{ bio | safe }}" in HEADER
    assert '<a href="{{ details.website }}" rel="nofollow ugc noopener noreferrer" target="_blank">' in HEADER


def test_the_actions_for_someone_else_the_owner_and_a_visitor():
    other = HEADER.split("{% elif v %}")[1]
    assert 'action="/api/chat/start"' in HEADER and "circle-join" in HEADER and "toggleSub" in HEADER
    for item in ('data-curation-user="{{ u.username }}"', 'id="mute-profile"', 'id="unmute-profile"',
                 'data-profile-block="{{ u.username }}"', 'data-profile-report="{{ u.username }}"',
                 'data-circle-member="{{ u.username }}"'):
        assert item in other, item
    # the site's own account (id 1) can't be muted or blocked, so the menu doesn't offer it
    assert HEADER.index("{% if u.id != 1 %}") < HEADER.index('id="mute-profile"') < HEADER.index("data-profile-block") \
        < HEADER.index("{% endif %}", HEADER.index("data-profile-block"))
    own = HEADER.split("{% if own %}")[1].split("{% elif v %}")[0]
    assert 'href="/settings/profile#lists"' in own and "data-profile-report" not in own and "data-profile-block" not in own
    # a visitor is sent to sign in
    assert '/login?redirect={{ u.permalink | urlencode }}' in HEADER


def test_the_menu_script_checks_names_and_draws_text_only():
    js = read("ruqqus", "assets", "js", "profile_actions.js")
    assert "innerHTML" not in js and "/^[A-Za-z0-9_]{3,25}$/" in js
    assert "window.confirm('Block @'" in js and "'/settings/block?username=' + encodeURIComponent(name)" in js
    assert "'/api/user/' + encodeURIComponent(name) + '/report'" in js


def test_details_are_cleaned_rated_and_saved_with_the_form_key():
    settings = read("ruqqus", "routes", "settings.py")
    branch = settings.split('if request.values.get("details") is not None:')[1].split("\n    if request.values.get(\"bio\")")[0]
    for needle in ("profile_details.clean_text(", "profile_details.clean_website(", "filter_comment_html(",
                   "apply_user_severity(v)", "escape(website)"):
        assert needle in branch, needle
    store = read("ruqqus", "helpers", "word_filter_store.py")
    assert "rated_text(getattr(user, \"profile_job\"" in store
    page = read("ruqqus", "templates", "settings_profile.html")
    assert 'name="details" value="1"' in page and 'name="profile_website"' in page


def test_lists_settings_take_only_the_three_choices():
    routes = read("ruqqus", "routes", "profile_lists.py")
    assert "@validate_formkey\ndef settings_list_visibility(" in routes
    assert "follow_lists.parse_choice(raw)" in routes
    for name in ("following", "followers", "subscribers", "subscriptions"):
        assert f'@app.get("/@<username>/{name}")' in routes
    # a list is never a way round the profile's own refusals
    assert "u.is_blocking or u.is_blocked" in routes and "user_hidden(u, v)" in routes
    assert 'name="{{ key }}"' in read("ruqqus", "templates", "settings_profile.html")


def test_new_accounts_start_open_and_existing_ones_private():
    migration = read("scripts", "migrations", "2026-10-14_profile_details.sql")
    for name in ("following", "followers", "subscribers", "subscriptions"):
        assert f"ADD COLUMN IF NOT EXISTS {name}_visibility smallint DEFAULT 2 NOT NULL;" in migration
        assert f"    {name}_visibility smallint DEFAULT 2 NOT NULL" in read("schema.sql")
    assert "following_visibility=0, followers_visibility=0, subscribers_visibility=0, subscriptions_visibility=0," in \
        read("ruqqus", "routes", "login.py")
    assert "signup-lists-note" in read("ruqqus", "templates", "sign_up.html")


def test_reports_need_a_sign_in_and_the_form_key_and_the_queue_is_for_admins():
    routes = read("ruqqus", "routes", "account_reports.py")
    assert '@app.post("/api/user/<username>/report")\n@auth_required\n@validate_formkey' in routes
    assert '@app.get("/admin/user_reports")\n@admin_level_required(3)' in routes
    assert "@admin_level_required(3)\n@validate_formkey\ndef admin_user_reports_dismiss(" in routes
    page = read("ruqqus", "templates", "admin", "user_reports.html")
    assert "reporter" not in page.replace("Reporters are never named", "")
    assert "CREATE TABLE IF NOT EXISTS user_reports" in read("scripts", "migrations", "2026-10-14_profile_details.sql")
    assert "CREATE TABLE public.user_reports (" in read("schema.sql")


def test_adding_an_account_to_a_curation_checks_the_owner_and_the_account():
    curations = read("ruqqus", "routes", "curations.py")
    route = curations.split("def user_add_to_curation(")[1].split("\n@app.")[0]
    assert "_owned_curation_or_404(" in route and "curation_members.author_refusal(False, target)" in route
    assert "target.is_blocking or target.is_blocked" in curations


def test_route_modules_are_named_apart_from_their_helpers():
    # `from .x import *` in routes/__init__.py would replace a helper of the same name (the coauthors lesson)
    init = read("ruqqus", "routes", "__init__.py")
    assert "from .profile_lists import *" in init and "from .account_reports import *" in init
    assert not (ROOT / "ruqqus" / "routes" / "follow_lists.py").exists()
    assert not (ROOT / "ruqqus" / "routes" / "user_reports.py").exists()


def test_both_stylesheets_style_the_header():
    for sheet in ("main.scss", "main_dark.scss"):
        css = read("ruqqus", "assets", "style", sheet)
        for rule in (".profile-x {", ".profile-x-banner {", ".profile-x-avatar-img {", ".profile-x-counts {",
                     ".profile-x-following {"):
            assert rule in css, (sheet, rule)
        assert re.search(r"\$profile-x-surface: \$(white|gray-600);", css), sheet
