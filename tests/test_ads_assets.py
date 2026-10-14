"""Ads: the pages that may show one, the scripts, ads.txt, the consent link and headers, and the boxes' room in
both stylesheets (helpers/ads.py, routes/ads.py, partials/ad_slot.html, assets/js/ads.js)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "ruqqus" / "templates"


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


def test_only_the_opt_in_pages_draw_an_ad():
    calls = {}
    for path in TEMPLATES.rglob("*.html"):
        for page in re.findall(r'ad_slot\("(\w+)"\)', path.read_text(encoding="utf-8")):
            calls.setdefault(str(path.relative_to(TEMPLATES)), []).append(page)
    assert calls == {"home.html": ["feed"], "submission.html": ["post"], "partials/late_boxes.html": ["sidebar"]}


def test_a_page_that_shows_no_posts_never_gets_an_ad():
    route = read("ruqqus", "routes", "ads.py")
    place = route.split("def _place(")[1].split("\n\n\n")[0]
    assert 'if listing:' in place and place.rstrip().endswith("return None")
    assert "embedded=bool(request.args.get(\"embed\"))" in route
    assert "ads.allows(page, context.get(\"v\")" in route


def test_the_scripts_come_once_and_only_after_a_box_was_drawn():
    route = read("ruqqus", "routes", "ads.py")
    script = route.split("def ads_script(")[1].split("\n\n\n")[0]
    assert 'not (g.get("ads_used") or consent)' in script and 'g.get("ads_script_done")' in script
    assert "g.ads_used = True" in route
    assert read("ruqqus", "templates", "default.html").count("{{ ads_script() }}\n</body>") == 1
    tag = read("ruqqus", "templates", "partials", "ad_script.html")
    assert "https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client={{ cfg.client }}" in tag
    assert "asset_url('js/ads.js')" in tag


def test_the_feed_ad_sits_outside_the_post_list_and_the_continuous_feed_gets_ads():
    home = read("ruqqus", "templates", "home.html")
    posts = home.index('<div class="posts" id="posts">')
    template = home.index('<template id="feed-ad">')
    assert template > home.index("</div>", home.index('{% include "submission_listing.html" %}', posts))
    js = read("ruqqus", "assets", "js", "ads.js")
    assert "var EVERY = 8;" in js and "window.placeFeedAds = placeFeedAds;" in js
    assert "if (window.placeFeedAds) setTimeout(window.placeFeedAds, 0)" in read("ruqqus", "assets", "js", "all_js.js")
    assert "innerHTML" not in js


def test_the_slot_draws_only_checked_values():
    slot = read("ruqqus", "templates", "partials", "ad_slot.html")
    assert "| safe" not in slot and "{{ cfg.client }}" in slot and "{{ cfg.slots[page] }}" in slot
    assert 'aria-label="Advertisement"' in slot and ">Advertisement<" in slot


def test_ads_txt_consent_link_and_referrer_policy():
    route = read("ruqqus", "routes", "ads.py")
    assert '@app.get("/ads.txt")' in route and "abort(404)" in route
    assert "from .ads import *" in read("ruqqus", "routes", "__init__.py")
    footer = read("ruqqus", "templates", "footer.html")
    assert '{% if ads_configured() %}' in footer and "data-ad-consent" in footer
    privacy = read("ruqqus", "templates", "help", "privacy.html")
    assert '<h2 id="ads">' in privacy and "data-ad-consent" in privacy and "{{ ads_script(consent=True) }}" in privacy
    assert privacy.index('<h2 id="ads">') > privacy.index("{% endfilter %}")       # the markdown filter keeps no id
    main = read("ruqqus", "__main__.py")
    assert '"strict-origin-when-cross-origin" if ads.config() else "same-origin"' in main
    assert "fc.showRevocationMessage()" in read("ruqqus", "assets", "js", "ads.js")


def test_premium_lists_no_ads_as_a_perk():
    page = read("ruqqus", "templates", "settings_premium.html")
    assert "{% if ads_configured() %}" in page and "</span>No ads" in page


def test_both_stylesheets_keep_the_boxes_room():
    for sheet in ("main.scss", "main_dark.scss"):
        css = read("ruqqus", "assets", "style", sheet)
        for rule in (".ad-slot {", ".ad-slot-label {", ".ad-slot.ad-slot-empty {"):
            assert rule in css, (sheet, rule)
        assert re.search(r"\.ad-slot-post ins\.adsbygoogle \{\s+min-height: 280px;", css), sheet
        assert re.search(r"\.ad-slot-sidebar ins\.adsbygoogle \{\s+min-height: 250px;", css), sheet


def test_settings_are_documented_and_empty_by_default():
    env = read(".env.example")
    compose = read("docker-compose.yml")
    for name in ("ADSENSE_CLIENT", "ADSENSE_SLOT_FEED", "ADSENSE_SLOT_POST", "ADSENSE_SLOT_SIDEBAR", "ADSENSE_FEED_LAYOUT_KEY"):
        assert f"\n{name}=\n" in env, name
        assert f"- {name}=${{{name}:-}}" in compose, name
