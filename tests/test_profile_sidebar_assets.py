"""A profile's right sidebar: the About box at the top (age, Rep and its counts, badges, guilds led, public curations),
then the shared part with Similar accounts in place of Who to follow and no Support. Other pages are unchanged."""
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
    for needle in ("account_age(u)", "u.created_date", "u.rep_class", "u.rep_label", "u.true_score",
                   "profile_guilds(u, v)", "profile_curations(u, v)", "Achievements", "Curations"):
        assert needle in about, needle
    assert about.count("u.badges") == 1          # the property commits: read it once
    assert ".author" not in about


def test_guilds_go_through_the_viewers_rules_and_curations_are_public_only():
    helper = read("ruqqus", "helpers", "profile_about.py")
    assert "b.can_view(v) and not board_hidden(b, v)" in helper
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
