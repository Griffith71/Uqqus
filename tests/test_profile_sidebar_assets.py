"""A profile's right sidebar: the About box at the top (Rep and its counts, badges, guilds led), then the shared
part with Similar accounts in place of Who to follow, the account's public curations in place of the Guilds / Create
Guild box, and no Support. It reads the same to the owner as to anyone else. Other pages are unchanged."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "ruqqus" / "templates"


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


def profile_sidebar():
    src = (TEMPLATES / "userpage.html").read_text(encoding="utf-8")
    start = src.index("{% block sidebarblock %}")
    return src[start:src.index("{% endblock %}", start)]


def test_the_profile_sidebar_is_about_then_the_shared_part():
    block = profile_sidebar()
    assert block.index("{% set profile_user = u %}") < block.index('{% include "partials/profile_about.html" %}') \
        < block.index('{% include "partials/sidebar_common.html" %}')
    assert "Guildmaster Of" not in block


def test_the_about_box_shows_what_was_asked_through_the_rules():
    about = (TEMPLATES / "partials" / "profile_about.html").read_text(encoding="utf-8")
    for needle in ("u.rep_class", "u.rep_label", "u.true_score", "profile_guilds(u, v)", "Achievements"):
        assert needle in about, needle
    assert "profile_curations(" not in about        # curations have their own box
    assert about.count("u.badges") == 1          # the property commits: read it once
    assert ".author" not in about


def test_guilds_go_through_the_viewers_rules_and_curations_are_public_only():
    helper = read("ruqqus", "helpers", "profile_about.py")
    # what a visitor could see, whoever looks: the owner sees their profile as others do
    assert "b.can_view(None) and not board_hidden(b, v)" in helper and "can_view(v)" not in helper
    assert "Curation.is_private.is_(False)" in helper and "not c.is_private" in helper
    assert "is_hidden(level, name_severity(" in helper


def test_similar_accounts_replace_who_to_follow_on_a_profile_only():
    late = (TEMPLATES / "partials" / "late_boxes.html").read_text(encoding="utf-8")
    assert re.search(r'\{% if profile_user %\}<div id="similar-accounts" class="late-box" '
                     r'data-src="/inpage/similar_accounts/\{\{ profile_user.username \}\}"></div>\n'
                     r'\{% elif v %\}<div id="who-to-follow"', late)
    shared = (TEMPLATES / "partials" / "sidebar_common.html").read_text(encoding="utf-8")
    support = shared.index('fa-heart mr-2"></i>Support')
    assert shared.rindex("{% if not profile_user %}", 0, support) > shared.rindex("{% endif %}", 0, support)
    js = read("ruqqus", "assets", "js", "who_to_follow.js")
    assert "['who-to-follow', 'similar-accounts'].forEach" in js
    assert "{% if v or u is defined %}<script src=\"{{ asset_url('js/who_to_follow.js') }}\"></script>{% endif %}" \
        in read("ruqqus", "templates", "default.html")


def test_the_similar_accounts_route_refuses_what_must_not_be_suggested_from():
    route = read("ruqqus", "routes", "suggestions.py").split("def similar_accounts_box(")[1]
    assert "@auth_desired\ndef similar_accounts_box(" in read("ruqqus", "routes", "suggestions.py")
    assert "u.is_deleted or u.is_banned or u.is_private or u.is_nofollow or user_hidden(u, v)" in route
    helper = read("ruqqus", "helpers", "suggestions.py").split("def similar_for(")[1].split("\ndef ")[0]
    for needle in ("_blocked_user_ids(v.id)", "v.following_ids", "filter_users(", "user_eligible(", "r[0] != owner.id"):
        assert needle in helper, needle
    store = read("ruqqus", "helpers", "similar_store.py")
    assert "MIN_SHARED_FOLLOWERS = 3" in store and store.count("s.is_anonymous = :no") == 1   # in the shared clause
    assert "_LIVE_PUBLIC" in store and store.count("{_LIVE_PUBLIC}") == 2                      # used on both sides


def test_visitors_get_a_sign_in_link_not_a_follow_button():
    row = (TEMPLATES / "partials" / "suggestion_row.html").read_text(encoding="utf-8")
    assert '{% if login_to %}\n  <a href="/login?redirect={{ login_to | urlencode }}"' in row
    box = (TEMPLATES / "partials" / "similar_accounts_box.html").read_text(encoding="utf-8")
    assert "login_to=(none if v else u.permalink)" in box


def test_both_stylesheets_style_the_about_box():
    for sheet in ("main.scss", "main_dark.scss"):
        css = read("ruqqus", "assets", "style", sheet)
        assert ".profile-about {" in css and ".profile-about-role {" in css, sheet


def test_the_profiles_curations_box_takes_the_guilds_boxs_place():
    shared = (TEMPLATES / "partials" / "sidebar_common.html").read_text(encoding="utf-8")
    assert '{% if profile_user %}\n{% include "partials/profile_curations.html" %}\n{% elif v and v.can_make_guild %}' in shared
    box = (TEMPLATES / "partials" / "profile_curations.html").read_text(encoding="utf-8")
    assert "profile_curations(profile_user, v)" in box and "{% if shown_curations %}" in box


def test_similar_accounts_on_ones_own_profile_are_the_list_others_get():
    helper = read("ruqqus", "helpers", "suggestions.py").split("def similar_for(")[1].split("\ndef ")[0]
    assert "following = v.following_ids if v and v.id != owner.id else set()" in helper
    assert "blocked = _blocked_user_ids(v.id) if v else set()" in helper


def test_every_account_shows_its_region_in_the_about_box_and_phones_in_the_header():
    about = (TEMPLATES / "partials" / "profile_about.html").read_text(encoding="utf-8")
    region = about.split('<div class="profile-about-fact profile-about-region">')[1].split('<div class="profile-about-fact">')[0]
    # drawn for every account: the region when the site has one, "Not known yet" otherwise; never behind a viewer check
    assert "{% if u.display_region %}" in region and "u.display_region_name" in region and "Not known yet" in region
    assert "v." not in region and "if v" not in region
    assert 'data-target="#aboutAccountModal"' in region and "region_country_list" in region
    header = (TEMPLATES / "partials" / "profile_header.html").read_text(encoding="utf-8")
    phone = header.split('<li class="d-lg-none">')[1].split("</li>")[0]
    assert "u.display_region_name" in phone and "Region not known yet" in phone
