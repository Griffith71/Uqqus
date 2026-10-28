"""Similar guilds' wiring (the owner, 2026-10-26): the route and what it refuses, the placeholder in the guild sidebar above the
ad, its own script (who_to_follow.js is not loaded for a visitor on a guild page), the visitor's Join link, and the strings the
rules of the guild pages keep out of the guild templates."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "ruqqus"
TEMPLATES = ROOT / "templates"


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


def test_the_route_refuses_what_a_suggestion_must_not_be_made_from():
    routes = read("routes", "suggestions.py")
    assert "@app.get(\"/inpage/similar_guilds/<name>\")" in routes
    body = routes.split("def similar_guilds_box(")[1]
    assert "@limiter.limit(\"120/minute\", key_func=member_limit_key)\n@auth_desired\ndef similar_guilds_box(" in routes
    for needle in ("not b", "b.is_banned", "b.is_private", "b.is_circle", "b.id in (1, 2)", "board_hidden(b, v)"):
        assert needle in body, needle
    assert routes.count("@auth_required") == 2                    # tests/test_suggestions.py: visitors get this box too
    assert "get_guild(name, v=v, graceful=True)" in body


def test_the_box_sits_above_the_ad_and_loads_after_the_page():
    sidebar = read("templates", "partials", "guild_sidebar.html")
    assert sidebar.rstrip().endswith('{{ ad_slot("sidebar") }}')
    assert sidebar.index('{% include "partials/similar_guilds_slot.html" %}') < sidebar.rindex('{{ ad_slot("sidebar") }}')
    slot = read("templates", "partials", "similar_guilds_slot.html")
    assert '<div id="similar-guilds" class="late-box" data-src="/inpage/similar_guilds/{{ b.name }}"></div>' in slot
    assert "asset_url('js/similar_guilds.js')" in slot and " defer" in slot
    assert "late_box_h:similar-guilds" in slot                      # keeps its height, so the ad below does not jump
    assert "ad_slot" not in slot.replace("sidebar ad", "")           # tests/test_ads_assets.py lists the calls; this is not one
    for private in ("b.is_banned", "b.is_private", "b.is_circle", "b.id in (1, 2)"):
        assert private in slot, private                              # no request for a guild the route would refuse


def test_the_script_fills_the_slot_like_the_other_late_boxes_and_has_its_own_loading():
    js = read("assets", "js", "similar_guilds.js")
    assert "fill_late_box(slot, html)" in js and "slot.offsetParent === null" in js and "classList.contains('embedded')" in js
    assert "DOMContentLoaded" in js and "/inpage/" not in js        # the address is the slot's data-src
    # a visitor on a guild page does not get who_to_follow.js (default.html loads it for members and profiles only)
    assert "{% if v or u is defined %}<script src=\"{{ asset_url('js/who_to_follow.js') }}\"></script>{% endif %}" in read("templates", "default.html")


def test_a_visitor_is_asked_to_join_not_to_follow():
    row = read("templates", "partials", "suggestion_row.html")
    assert re.search(r'<a href="/login\?redirect=\{\{ login_to \| urlencode \}\}" class="btn btn-primary btn-sm ml-2">'
                     r'\{% if kind == "guilds" %\}\{\{ pgettext\("button", "Join"\) \}\}\{% else %\}\{\{ pgettext\("button", "Follow"\) \}\}\{% endif %\}</a>', row)
    box = read("templates", "partials", "similar_guilds_box.html")
    assert "login_to=(none if v else b.permalink)" in box and 'suggestion_row("guilds"' in box
    assert "{% if picks %}" in box                                  # nothing is drawn when there is nobody to suggest


def test_the_guild_templates_keep_the_strings_the_site_wide_layer_is_kept_out_by():
    # tests/test_happening_assets.py: a guild and a post draw their own sidebar, none of the site-wide boxes
    for name in ("board.html", "submission.html", "partials/guild_sidebar.html", "partials/similar_guilds_slot.html"):
        src = read("templates", name)
        assert "happening" not in src and "late_boxes" not in src, name


def test_the_ranking_is_cached_for_everyone_and_the_viewers_rules_come_after():
    sg = read("helpers", "suggestions.py")
    assert "@_memo(seconds=GUILD_CACHE_SECONDS)\ndef similar_guild_candidates(board_id):" in sg
    helper = sg.split("def similar_guilds_for(")[1].split("\ndef ")[0]
    for needle in ("filter_boards(", "BoardBlock", "BanRelationship", "shows_sensitive(v)", "similar_guild_eligible("):
        assert needle in helper, needle
    store = read("helpers", "similar_guilds_store.py")
    assert "MIN_SHARED = 5" in store and "MIN_MEMBERS = 25" in store
    assert "stored_subscriber_count" not in store.split("_SIZES")[1].split("_PUBLIC_GUILD")[0]      # sizes are counted live
