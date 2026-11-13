"""Ads: the pages that may show one, the scripts, ads.txt, the consent link and headers, and the boxes' room in
both stylesheets (helpers/ads.py, routes/ads.py, partials/ad_slot.html, assets/js/ads.js)."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import jinja2
import pytest

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "ruqqus" / "templates"


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


def test_only_the_opt_in_pages_draw_an_ad():
    calls = {}
    for path in TEMPLATES.rglob("*.html"):
        for page in re.findall(r'ad_slot\("(\w+)"\)', path.read_text(encoding="utf-8")):
            calls.setdefault(str(path.relative_to(TEMPLATES)), []).append(page)
    # the sidebar slot: the site-wide boxes (late_boxes.html) and, at the foot of a guild's own sidebar, the guild page's
    # and a guild post's (partials/guild_sidebar.html; the owner kept one there, 2026-10-23)
    assert calls == {"home.html": ["feed"], "submission.html": ["post"], "partials/late_boxes.html": ["sidebar"],
                     "partials/guild_sidebar.html": ["sidebar"]}


def test_a_page_that_shows_no_posts_never_gets_an_ad():
    route = read("ruqqus", "routes", "ads.py")
    place = route.split("def _place(")[1].split("\n\n\n")[0]
    assert 'if listing:' in place and place.rstrip().endswith("return None")
    assert "embedded=bool(request.args.get(\"embed\"))" in route
    assert "ads.allows(page, v, embedded" in route and 'v = context.get("v")' in route


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
    assert 'aria-label="{{ _(\'Advertisement\') }}"' in slot and '>{{ _("Advertisement") }}<' in slot


def test_ads_txt_consent_link_and_referrer_policy():
    route = read("ruqqus", "routes", "ads.py")
    assert '@app.get("/ads.txt")' in route and "abort(404)" in route
    assert "from .ads import *" in read("ruqqus", "routes", "__init__.py")
    footer = read("ruqqus", "templates", "footer.html")
    assert '{% if ads_live() %}' in footer and "data-ad-consent" in footer
    privacy = read("ruqqus", "templates", "help", "privacy.html")
    assert '<h2 id="ads">' in privacy and "data-ad-consent" in privacy and "{{ ads_script(consent=True) }}" in privacy
    assert privacy.index('<h2 id="ads">') > privacy.index("{% endfilter %}")       # the markdown filter keeps no id
    main = read("ruqqus", "__main__.py")
    assert '"strict-origin-when-cross-origin" if ads.live(ads.config()) else "same-origin"' in main
    assert "fc.showRevocationMessage()" in read("ruqqus", "assets", "js", "ads.js")


def test_premium_lists_no_ads_as_a_perk():
    page = read("ruqqus", "templates", "settings_premium.html")
    assert "{% if ads_live() %}" in page and '</span>{{ _("No ads") }}' in page


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


# ---- what a reader is told goes by ads_live; only /ads.txt goes by a client id alone

def test_reader_facing_copy_goes_by_ads_live():
    route = read("ruqqus", "routes", "ads.py")
    assert "if cfg is None:\n        abort(404)" in route.split("def ads_txt")[1].split("\n\n\n")[0]
    assert "not ads.live(cfg)" in route.split("def ads_script(")[1].split("\n\n\n")[0]
    for name in ("help/privacy.html", "help/faq.html", "footer.html", "settings_premium.html"):
        text = read("ruqqus", "templates", *name.split("/"))
        assert "ads_live()" in text, name
        assert "ads_configured()" not in text, name


def test_the_faq_says_it_takes_no_advertising_only_while_that_is_true():
    faq = read("ruqqus", "templates", "help", "faq.html")
    assert faq.index("{% if ads_live() %}") < faq.index("It does show ads from Google AdSense") < faq.index("{% else %}") \
        < faq.index("does not accept advertising or investment funding") < faq.index("{% endif %}", faq.index("does not accept advertising"))


# ---- the page as a whole

def test_one_unsafe_card_means_no_slot_and_the_cards_carry_the_mark():
    route = read("ruqqus", "routes", "ads.py")
    slot = route.split("def ad_slot(")[1].split("\n\n\n")[0]
    assert slot.index("_page_safe(context)") < slot.index("g.ads_used = True")
    assert "ads.post_page_allows(post, drawn)" in route and "ads.page_allows(" in route
    assert 'context.get("pinned_posts")' in route       # a guild's Pinned row is on the page too
    assert '"{{ ad_safe_mark(p) }}>' in read("ruqqus", "templates", "submission_listing.html")


def test_a_search_has_no_ad_because_its_query_is_in_the_address():
    from ruqqus.helpers import ads
    assert ads.SEARCH_ENDPOINTS == {"search", "search_guild"}
    search = read("ruqqus", "routes", "search.py")
    for name in ads.SEARCH_ENDPOINTS:
        assert f"def {name}(" in search, name
    place = read("ruqqus", "routes", "ads.py").split("def _place(")[1].split("\n\n\n")[0]
    assert place.index("request.endpoint in ads.SEARCH_ENDPOINTS") < place.index("context.get(")


def test_the_script_skips_hidden_boxes_caps_feed_ads_and_stops_on_an_unsafe_card():
    js = read("ruqqus", "assets", "js", "ads.js")
    assert "if (!slot.getClientRects().length) return;" in js
    assert "var MAX_FEED = 6;" in js and "placed < MAX_FEED" in js
    place = js.split("function placeFeedAds()")[1]
    assert place.index("""document.querySelector('[data-ad-safe="0"]')""") < place.index("cloneNode")
    assert "stopped = true" in js.split("function stop()")[1].split("}")[0]
    assert "ad-slot-empty" in js.split("function stop()")[1]
    assert js.index("if (window.adsHeld) return;") < js.index("placeFeedAds = placeFeedAds")


def test_the_consent_link_waits_for_googles_api():
    js = read("ruqqus", "assets", "js", "ads.js")
    click = js.split("addEventListener('click'")[1]
    assert "window.loadAdTag()" in click and "CONSENT_API_READY" in click and "fc.showRevocationMessage()" in click
    assert "location.href = link.href" in click


# ---- the tag, the pre-key and a signed-out browser's chat

TAG = "pagead2.googlesyndication.com/pagead/js/adsbygoogle.js"


def test_the_tag_is_never_a_plain_script_element():
    partial = read("ruqqus", "templates", "partials", "ad_script.html")
    assert "<script async" not in partial and not re.search(r'<script[^>]*googlesyndication', partial)
    assert partial.count(TAG) == 1 and "document.createElement(\"script\")" in partial


def test_the_order_in_the_partial_is_the_promise():
    partial = read("ruqqus", "templates", "partials", "ad_script.html")
    order = ["requestNonPersonalizedAds = 1", 'sessionStorage.removeItem("uqqus_chat_prekey")', 'localStorage.removeItem("uqqus_chat_prekey")',
             'document.addEventListener("resume", forgetIfRunning, true)', 'document.addEventListener("visibilitychange", forgetIfRunning, true)',
             'window.addEventListener("focus", forgetIfRunning, true)',
             "window.loadAdTag = function", "forgetPrekey();", "{{ chat_held(owner) }}", 'document.createElement("script")', TAG, "{% if not on_press %}"]
    at = 0
    for part in order:                      # each after the one before it
        at = partial.index(part, at)
    assert "visitor" not in partial.split("<script>")[1]     # the check is for every page: `owner` says whose chat may be here


def test_the_hold_is_one_check_for_the_tag_and_for_a_box_that_folds_before_it_paints():
    hold = read("ruqqus", "templates", "partials", "ad_hold.html")
    # the index (token.js) and what the client before it kept, which chat still clears when it next starts
    for key in ("uqqus_chat_devices", "ruqqus_chat_session", "ruqqus_call_session"):
        assert f'localStorage.getItem("{key}")' in hold, key
    slot = read("ruqqus", "templates", "partials", "ad_slot.html")
    assert '{% if page != "feed" %}' in slot and "{{ chat_held(owner) }}" in slot and 'classList.add("ad-slot-empty")' in slot
    assert "localStorage" not in slot and "localStorage.getItem" not in read("ruqqus", "templates", "partials", "ad_script.html")
    route = read("ruqqus", "routes", "ads.py")
    assert 'render_template("partials/ad_slot.html", page=page, cfg=cfg, owner=_chat_owner(v))' in route
    assert '"partials/ad_script.html", cfg=cfg, owner=_chat_owner(v),' in route
    assert "chat_devices.mxid_of(g.db, v.id) if v else None" in route
    tokens = read("ruqqus", "assets", "chat_src", "src", "token.js")
    # the hold reads what chat writes: the same key, and an entry is the owner's by the same field chat picks its own entries by
    assert 'export const INDEX_KEY = "uqqus_chat_devices";' in tokens
    assert 'LEGACY_KEYS = ["ruqqus_chat_session", "ruqqus_call_session"]' in tokens
    assert "if (e.u !== u || e.k !== k || !LIVE.has(e.s)) continue;" in tokens and "currentEntry(idx, boot.mxid, kind)" in tokens
    assert "return !e || !owner || e.u !== owner;" in hold


def test_the_pre_key_is_kept_where_a_tag_in_another_tab_cant_read_it():
    """localStorage is shared by every tab: a tag running in one reads the key the moment a sign-in in another writes it."""
    js = read("ruqqus", "assets", "js", "chat_key.js")
    assert "sessionStorage.setItem(STORE, JSON.stringify({ v: 1, name: accountName(account), k: text, at: at, ref: ref || '' }));" in js
    # the one thing chat_key.js keeps in shared storage is a time (when a password change was sent), never the key
    assert re.findall(r"localStorage\.setItem\(([^,]+),", js) == ["CHANGED"] and "var CHANGED = 'uqqus_chat_prekey_before';" in js
    keylock = read("ruqqus", "assets", "chat_src", "src", "keylock.js")
    assert "sessionStorage.getItem(PREKEY_STORE)" in keylock
    assert re.findall(r"localStorage\.getItem\(([^)]+)\)", keylock) == ["PASSWORD_CHANGED_KEY"]
    assert 'export const PASSWORD_CHANGED_KEY = "uqqus_chat_prekey_before";' in keylock
    # wherever it goes, an older copy goes too, or it would sit in shared storage for good
    for text in (keylock, read("ruqqus", "templates", "partials", "ad_script.html"), read("ruqqus", "templates", "partials", "forget_prekey.html"),
                 read("ruqqus", "assets", "js", "all_js.js")):
        assert re.search(r"sessionStorage\.removeItem\(", text) and re.search(r"localStorage\.removeItem\(", text)


def test_consent_waits_only_when_the_tag_is_on_the_page():
    js = read("ruqqus", "assets", "js", "consent.js")
    assert """if (!document.querySelector('script[src*="adsbygoogle.js"]')) return then();""" in js
    # the tag is put in by a script at the end of <body>, before DOMContentLoaded, and consent.js asks at DOMContentLoaded or later
    assert 'if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);' in js
    assert read("ruqqus", "templates", "default.html").count("{{ ads_script() }}\n</body>") == 1


def test_no_ads_until_is_set_wherever_a_password_is_typed_and_goes_with_the_sign_in():
    """chat_key.js writes the pre-key on the sign-in and sign-up forms and on the password change form."""
    login = read("ruqqus", "routes", "login.py")
    assert login.count("ads_rules.hold_after_password(") == 2 and "_hold_ads" not in login
    change = read("ruqqus", "routes", "settings.py").split("def settings_security_post(")[1].split("\n\n\n")[0]
    assert change.index('request.form.get("new_password")') < change.index("ads_rules.hold_after_password(flask_session,")
    assert "data-chat-rekey" in read("ruqqus", "templates", "settings_security.html")
    assert '"no_ads_until"' in read("ruqqus", "helpers", "session_cookie.py").split("SIGN_IN_KEYS =")[1].split("\n")[0]
    from ruqqus.helpers import ads
    assert ads.PREKEY_GRACE == 15 * 60
    held = {}
    ads.hold_after_password(held, 100)
    assert held == {}       # ads off: nothing written


def test_no_ads_until_is_only_read_for_a_page_drawn_for_a_member():
    route = read("ruqqus", "routes", "ads.py")
    assert 'return bool(v) and ads.held_after_sign_in(session.get("no_ads_until"), int(time.time()))' in route
    assert route.count('session.get("no_ads_until")') == 1


ME = "@ruqqus_5:site"
OTHER = "@ruqqus_6:site"
INDEX = "uqqus_chat_devices"


def _session(owner):
    """What the client before the index kept (a legacy key), until chat next starts here and clears it."""
    return json.dumps({"user_id": owner, "device_id": "D"})


def _entry(owner, device="UQD000000000", kind="chat", state="out"):
    local = owner[1:].split(":")[0]
    return {"u": owner, "d": device, "k": kind, "p": f"uqqus-{kind}-{local}-{device}", "kid": "k", "s": state, "x": 0, "gen": 1}


def _index(*entries, owner=None):
    """The index chat keeps (assets/chat_src/src/token.js), with these entries."""
    return json.dumps({"v": 2, "owner": owner, "epoch": 1, "entries": list(entries)})


def _script(owner=None, personalised=False, on_press=False):
    environment = jinja2.Environment(autoescape=True, loader=jinja2.FileSystemLoader(TEMPLATES))
    environment.globals["asset_url"] = lambda path: "/assets/" + path
    html = environment.get_template("partials/ad_script.html").render(
        cfg={"client": "ca-pub-1234567890123456"}, owner=owner, personalised=personalised, on_press=on_press)
    return re.search(r"<script>(.*?)</script>", html, re.S).group(1), html


def _slot_fold(owner=None):
    """The small script after a post or sidebar box, with what it needs from the page around it."""
    environment = jinja2.Environment(autoescape=True, loader=jinja2.FileSystemLoader(TEMPLATES))
    environment.globals["_"] = lambda text: text
    html = environment.get_template("partials/ad_slot.html").render(
        page="post", cfg={"client": "ca-pub-1", "slots": {"post": "2"}, "feed_layout_key": ""}, owner=owner)
    return re.search(r"<script>(.*?)</script>", html, re.S).group(1)


def _run(script, stored, throws=False, tab=None, after=""):
    """The partial's own script under node, with a stand-in page: what it fetched, what it left in storage, in what order."""
    harness = """
      var stored = %s, tab = %s, throws = %s, events = [], listeners = {}, slots = [{ classes: [], classList: { add: function (name) { this.owner.classes.push(name); } } }];
      slots[0].classList.owner = slots[0];
      global.window = global;
      global.addEventListener = function (name, listener, capture) { events.push("listen window " + name + (capture ? " capture" : "")); listeners[name] = listener; };
      global.location = { reload: function () { events.push("reload"); } };
      global.localStorage = {
        getItem: function (key) { if (throws) throw new Error("blocked"); return key in stored ? stored[key] : null; },
        removeItem: function (key) { if (throws) throw new Error("blocked"); events.push("remove " + key); delete stored[key]; }
      };
      global.sessionStorage = {
        removeItem: function (key) { if (throws) throw new Error("blocked"); events.push("tab remove " + key); delete tab[key]; }
      };
      global.document = {
        currentScript: { previousElementSibling: slots[0] },
        createElement: function () { events.push("create"); return {}; },
        head: { appendChild: function (tag) { events.push("load " + tag.src + " nonpersonal=" + !!window.adsbygoogle.requestNonPersonalizedAds); } },
        querySelectorAll: function () { return slots; },
        addEventListener: function (name, listener, capture) {
          events.push("listen document " + name + (capture ? " capture" : "")); listeners["document " + name] = listener;
        }
      };
      %s
      %s
      console.log(JSON.stringify({ events: events, stored: stored, tab: tab, held: !!window.adsHeld, slots: slots[0].classes,
                                   press: typeof window.loadAdTag }));
    """ % (json.dumps(stored), json.dumps(tab or {}), "true" if throws else "false", script, after)
    out = subprocess.run(["node", "-e", harness], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


@node
def test_a_visitors_browser_with_nothing_of_chats_loads_the_tag_non_personalised():
    got = _run(_script()[0], {})
    assert not got["held"] and got["slots"] == []
    assert got["events"][-1].startswith("load https://pagead2.googlesyndication.com/") and got["events"][-1].endswith("nonpersonal=true")


@node
@pytest.mark.parametrize("stored", [
    {INDEX: _index(_entry(OTHER))},
    {INDEX: _index(_entry(ME, state="removed"))},
    {INDEX: "not json"},
    {"ruqqus_chat_session": "{}"},
    {"ruqqus_call_session": _session(ME)},
])
def test_a_visitors_browser_holding_any_chat_device_asks_google_for_nothing_and_folds(stored):
    got = _run(_script()[0], {**stored, "uqqus_chat_prekey": "x"}, tab={"uqqus_chat_prekey": "x"})
    assert got["held"] and got["slots"] == ["ad-slot-empty"]
    assert not any(event.startswith(("create", "load")) for event in got["events"])
    assert "uqqus_chat_prekey" not in got["stored"] and "uqqus_chat_prekey" not in got["tab"]
    assert all(key in got["stored"] for key in stored)                 # what is kept is chat's to keep


@node
def test_an_index_with_no_entries_holds_nothing():
    """Log out on a browser that never used chat leaves only a counter (all_js.js leaveChatSessions)."""
    got = _run(_script()[0], {INDEX: _index()})
    assert not got["held"] and got["events"][-1].startswith("load ")


@node
def test_storage_that_throws_counts_as_nothing_held():
    got = _run(_script()[0], {INDEX: _index(_entry(OTHER))}, throws=True)
    assert not got["held"] and got["events"][-1].startswith("load ")
    assert _run(_script(ME)[0], {INDEX: _index(_entry(OTHER))}, throws=True)["events"][-1].startswith("load ")
    assert _run(_script(ME)[0], {"ruqqus_chat_session": _session(OTHER)}, throws=True)["events"][-1].startswith("load ")


@node
@pytest.mark.parametrize("stored", [
    {INDEX: _index(_entry(ME), _entry(ME, "UQC000000000", "call", "in"), _entry(ME, "UQR000000000", state="removed"), owner=ME)},
    {INDEX: _index(_entry(ME, state="forget"))},
    {"ruqqus_chat_session": _session(ME), "ruqqus_call_session": _session(ME)},
])
def test_a_members_own_chat_is_the_accepted_risk_and_the_prekey_goes_before_the_tag(stored):
    got = _run(_script(ME, personalised=True)[0], {**stored, "uqqus_chat_prekey": "x"}, tab={"uqqus_chat_prekey": "x"})
    acted = [event for event in got["events"] if not event.startswith("listen ")]
    assert not got["held"] and acted[:3] == ["tab remove uqqus_chat_prekey", "remove uqqus_chat_prekey", "create"]
    assert got["events"][-1].endswith("nonpersonal=false")


@node
@pytest.mark.parametrize("stored", [
    {INDEX: _index(_entry(OTHER))},
    {INDEX: _index(_entry(ME), _entry(OTHER, state="removed"), owner=ME)},       # one device of one's own doesn't excuse another's
    {INDEX: _index({"d": "UQD000000000"})},                                       # an entry that doesn't say whose it is
    {INDEX: _index(None)},
    {INDEX: "not json"},
    {INDEX: json.dumps({"v": 1, "entries": []})},                                 # another version: can't be read
    # what replaced an unreadable index (a Log out, a deletion): its stores are still here, named by no entry, until a chat start lists them
    {INDEX: json.dumps({"v": 2, "owner": None, "epoch": 1, "entries": [], "unlisted": True})},
    {INDEX: json.dumps({"v": 2, "owner": ME, "epoch": 1, "entries": [_entry(ME)], "unlisted": True})},
    {INDEX: "null"},
    {INDEX: _index(_entry(ME)), "ruqqus_chat_session": _session(OTHER)},          # a legacy key of another account
    {"ruqqus_chat_session": _session(ME), "ruqqus_call_session": _session(OTHER)},
    {"ruqqus_call_session": json.dumps({"device_id": "D"})},
    {"ruqqus_chat_session": "{}"},
    {"ruqqus_chat_session": "null"},
    {"ruqqus_call_session": "not json"},
])
def test_chat_left_by_another_account_holds_the_tag_on_a_members_page_too(stored):
    """Account A logged out: its locked devices are still here. B's acceptance of Google's script is about B's own chat."""
    got = _run(_script(ME)[0], stored)
    assert got["held"] and got["slots"] == ["ad-slot-empty"]
    assert not any(event.startswith(("create", "load")) for event in got["events"])
    assert all(key in got["stored"] for key in stored)


@node
@pytest.mark.parametrize("owner,stored,folds", [
    (None, {INDEX: _index(_entry(ME))}, True), (None, {INDEX: _index()}, False), (None, {}, False),
    (ME, {INDEX: _index(_entry(ME), owner=ME)}, False), (ME, {INDEX: _index(_entry(OTHER))}, True), (ME, {INDEX: "{"}, True),
    (ME, {"ruqqus_chat_session": _session(OTHER)}, True), (ME, {"ruqqus_chat_session": _session(ME)}, False), (ME, {}, False)])
def test_a_box_folds_before_it_paints_by_the_same_rule(owner, stored, folds):
    got = _run(_slot_fold(owner), stored)
    assert (got["slots"] == ["ad-slot-empty"]) is folds
    assert _run(_slot_fold(owner), {INDEX: "x"}, throws=True)["slots"] == []


@node
def test_a_page_the_back_button_brings_back_takes_a_key_written_since():
    """A visitor's page with the tag, kept in the browser's back-forward cache while this tab signed in, comes back with the tag running."""
    written_since = 'tab.uqqus_chat_prekey = "x"; listeners.pageshow({ persisted: %s });'
    assert _run(_script()[0], {}, after=written_since % "true")["tab"] == {}
    # an ordinary load isn't that, and a page that never loaded the tag has nothing running to keep it from
    assert _run(_script()[0], {}, after=written_since % "false")["tab"] == {"uqqus_chat_prekey": "x"}
    assert _run(_script(ME)[0], {INDEX: _index(_entry(OTHER))}, after=written_since % "true")["tab"] == {"uqqus_chat_prekey": "x"}


@node
@pytest.mark.parametrize("event", ["document resume", "document visibilitychange", "focus"])
def test_a_restored_page_takes_the_key_before_anything_of_the_tag_hears_it(event):
    """Chromium gives a page out of the back-forward cache `resume` and `visibilitychange` before `pageshow` (measured in ads.spec.ts):
    the key a sign-in in this tab wrote while the page was away goes on each, in a listener with capture put on before the tag was."""
    got = _run(_script()[0], {}, after='tab.uqqus_chat_prekey = "x"; listeners[%s]({});' % json.dumps(event))
    assert got["tab"] == {} and "reload" not in got["events"]
    load = next(i for i, e in enumerate(got["events"]) if e.startswith("load "))
    where = "listen " + (event if event.startswith("document ") else "window " + event) + " capture"
    assert got["events"].index(where) < load
    # a page that never loaded the tag has nothing running to keep the key from: chat in this tab may still take it
    held = _run(_script(ME)[0], {INDEX: _index(_entry(OTHER))}, after='tab.uqqus_chat_prekey = "x"; listeners[%s]({});' % json.dumps(event))
    assert held["tab"] == {"uqqus_chat_prekey": "x"}


@node
@pytest.mark.parametrize("owner,since,reloads", [
    (None, _index(_entry(OTHER)), True),           # a visitor's page with the tag; this tab then signed in and opened chat
    (ME, _index(_entry(OTHER)), True),             # A's page; A's sign-in ended, B signed in and opened chat
    (ME, _index(_entry(ME), owner=ME), False),     # only the page's own account's chat: the accepted risk
    (None, None, False),                           # nothing of chat came while it was away
])
def test_a_page_the_back_button_brings_back_asks_the_hold_again(owner, since, reloads):
    """The tag came back running: chat data another account left while the page was away holds it, and the page is drawn afresh."""
    put = "" if since is None else "stored[%s] = %s; " % (json.dumps(INDEX), json.dumps(since))
    got = _run(_script(owner)[0], {}, after=put + "listeners.pageshow({ persisted: true });")
    assert any(event.startswith("load ") for event in got["events"])          # the tag was running when the page went
    assert ("reload" in got["events"]) is reloads
    if reloads:
        assert got["events"][-1] == "reload"
    # an ordinary load isn't a restore
    assert "reload" not in _run(_script(owner)[0], {}, after=put + "listeners.pageshow({ persisted: false });")["events"]


@node
def test_on_the_privacy_page_a_viewer_with_no_ads_loads_nothing_until_they_press():
    script = _script(ME, on_press=True)[0]
    assert [e for e in _run(script, {"uqqus_chat_prekey": "x"})["events"] if not e.startswith("listen ")] == []
    assert _run(script, {})["press"] == "function"
    pressed = _run(script + "\nwindow.loadAdTag();", {"uqqus_chat_prekey": "x"}, tab={"uqqus_chat_prekey": "x"})
    pressed["events"] = [e for e in pressed["events"] if not e.startswith("listen ")]
    assert pressed["events"][:2] == ["tab remove uqqus_chat_prekey", "remove uqqus_chat_prekey"] and pressed["events"][-1].startswith("load ")


def test_the_privacy_page_says_what_the_hold_is_and_where_it_stops():
    page = read("ruqqus", "templates", "help", "privacy.html")
    ads_part = page[page.index('<h2 id="ads">'):]
    assert "kept only in the tab you typed the password in" in ads_part and "you sign in with your password, sign up or change your password" in ads_part
    assert "chat data of anyone but the account you are signed in with" in ads_part
    # the route that ends a QR sign-in's limits holds the ads back too (routes/qr_login_pages.py), and the password's form writes the key
    assert "ads_rules.hold_after_password(" in read("ruqqus", "routes", "qr_login_pages.py")
    assert "or enter your password after signing in with a QR code" in ads_part
    cookie_part = page[:page.index("These cookies are set only when you choose something")]
    assert "After you sign in with your password, sign up or change your password" in cookie_part
    assert "or enter your password after signing in with a QR code{% endif %}, it also holds the time" in cookie_part
    assert "a page already open in another tab, with the script running, keeps it until you leave that page" in ads_part


# ---- what a page is judged by

def test_only_a_posts_own_page_counts_as_a_post_page():
    from ruqqus.helpers import ads
    assert ads.POST_ENDPOINTS == {"post_base36id_with_comments", "post_pid_comment_cid"}
    assert "def post_base36id_with_comments(" in read("ruqqus", "routes", "posts.py")
    assert "def post_pid_comment_cid(" in read("ruqqus", "routes", "comments.py")
    place = read("ruqqus", "routes", "ads.py").split("def _place(")[1].split("\n\n\n")[0]
    assert "(post, board) if request.endpoint in ads.POST_ENDPOINTS else None" in place
    # these extend default.html, so they inherit the sidebar, and they hand it a post whose old revisions nothing reads
    for page in ("collab/revisions.html", "collab/written_by.html", "collab/fork.html"):
        assert page in read("ruqqus", "routes", "collab.py")


def test_the_privacy_page_lists_what_is_checked_and_what_isnt():
    page = read("ruqqus", "templates", "help", "privacy.html")
    ads_part = page[page.index('<h2 id="ads">'):]
    for part in ("a post on this site that another post quotes", "by a sensitive account", "a live thread that is still live", "a rated update or summary"):
        assert part in ads_part, part
    unchecked = ads_part[ads_part.index("That is all we check."):].split("</p>")[0]
    for part in ("Comments that load later", "a post quoted from elsewhere", "a post's own picture", "a profile's bio", "the other boxes in the sidebar"):
        assert part in unchecked, part
    assert "anything on it" not in ads_part


def test_a_page_is_judged_by_its_quote_cards_and_its_live_thread():
    route = read("ruqqus", "routes", "ads.py")
    safe = route.split("def _page_safe(")[1].split("\n\n\n")[0]
    assert "ads.quote_allows(quote_of(post, v))" in safe and "_live_allows(post)" in safe
    assert "ads.quote_allows(quote_of(card, v)) for card in posts" in safe
    assert "ads.quote_allows(quote_of(post, context.get(\"v\")))" in route.split("def ad_safe_mark(")[1]
    panel = read("ruqqus", "templates", "partials", "live_panel.html")
    assert "u.body_html | safe" in panel and "live_updates_of(p, v)" in panel     # what _live_allows reads is what this draws
