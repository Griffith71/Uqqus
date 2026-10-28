"""The wiring of the "What's happening" layer (routes/happening_pages.py, templates/partials/happening_box.html and happening_rows.html,
templates/happening.html, assets/js/happening.js, happening_page.js, time_phrases.js, assets/style/_happening.scss).
The promises, as source checks like tests/test_trending_assets.py:
- guild and post pages never carry the box (the owner's 2026-10-23 rule): only sidebar_common reaches late_boxes;
- the box is private and always asked again (one Cache-Control, assigned, not added), asked for by a member-keyed limit, and 204 when unchanged;
- nothing here loads a thing from another site, and the scripts persist nothing about the reader;
- the script never polls a sidebar nobody can see, stops on a 429, and keeps the reader's time zone to itself;
- the page and the admin routes check what they must (viewer passed, level, form key); admin text stays English;
- the styles are tokens only and in both sheets."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "ruqqus"


def read(*parts):
    return APP.joinpath(*parts).read_text(encoding="utf-8")


ROUTES = read("routes", "happening_pages.py")
BOX = read("templates", "partials", "happening_box.html")
ROWS = read("templates", "partials", "happening_rows.html")
PAGE = read("templates", "happening.html")
ADMIN = read("templates", "admin", "happening.html")
BOX_JS = read("assets", "js", "happening.js")
PAGE_JS = read("assets", "js", "happening_page.js")
TIME_JS = read("assets", "js", "time_phrases.js")
SCSS = read("assets", "style", "_happening.scss")
OURS = {"routes": ROUTES, "box": BOX, "rows": ROWS, "page": PAGE, "box.js": BOX_JS, "page.js": PAGE_JS, "time.js": TIME_JS,
        "scss": SCSS}


def route_block(name):
    """The decorators and the body of one route function."""
    start = ROUTES.index(f"def {name}(")
    top = ROUTES.rfind("\n\n\n", 0, start) + 3
    end = ROUTES.find("\n\n\n", start)
    return ROUTES[top:end if end != -1 else len(ROUTES)]


# --- where it shows ---------------------------------------------------------------------------------------------------------

def test_guild_and_post_pages_never_carry_the_box():
    for name in ("board.html", "submission.html", "partials/guild_sidebar.html"):
        source = read("templates", *name.split("/"))
        assert "happening" not in source and "late_boxes" not in source, name
    # the one way in is the shared sidebar, as before
    users = [p.name for p in (APP / "templates").rglob("*.html") if "partials/late_boxes.html" in p.read_text(encoding="utf-8")]
    assert users == ["sidebar_common.html"]


def test_the_late_box_slot_falls_back_to_the_trending_box():
    late = read("templates", "partials", "late_boxes.html")
    assert re.search(r'\{% if happening_enabled\(\) %\}\s*<div id="happening-box" class="late-box" data-src="/inpage/happening"></div>\s*'
                     r'\{% else %\}\s*<div id="trending-box" class="late-box" data-src="/inpage/trending"></div>\s*\{% endif %\}', late)
    assert late.index("happening-box") < late.index("who-to-follow")                      # still above Who to follow
    assert 'box.id === "happening-box"' in late and "29.8rem" in late                       # a first visit reserves what it holds
    default = read("templates", "default.html")
    assert default.index("js/time_phrases.js") < default.index("js/happening.js")         # the helpers load first
    assert re.search(r"^<script src=\"\{\{ asset_url\('js/happening.js'\) \}\}\"></script>$", default, re.M)   # for everyone, no {% if %}
    assert "happening_enabled" in ROUTES and "HAPPENING_ENABLED" in ROUTES


def test_the_ways_to_it_follow_the_switch():
    assert "happening_enabled()" in read("templates", "partials", "explore.html")
    assert "happening_enabled()" in read("templates", "footer.html")
    assert 'href="/happening"' in read("templates", "partials", "explore.html")
    routes_init = read("routes", "__init__.py")
    assert routes_init.index("from .trending import *") < routes_init.index("from .happening_pages import *")


# --- the box endpoint ---------------------------------------------------------------------------------------------------------

def test_the_box_endpoint_is_cheap_private_and_conditional():
    block = route_block("happening_box_fragment")
    assert '@app.get("/inpage/happening")' in block and "@auth_desired" in block             # visitors too
    assert '@limiter.limit("120/minute", key_func=member_limit_key)' in block                  # per account, not per address
    assert "abort(404)" in block and "enabled()" in block                                      # off means 404
    assert 'request.args.get("rev") == str(model["rev"])' in block and 'return "", 204' in block
    assert "v=v" in block
    main = (APP / "__main__.py").read_text(encoding="utf-8")
    assert re.search(r'request\.path\.startswith\("/inpage/happening"\):\s*\n\s*response\.headers\["Cache-Control"\] = "private, no-cache"', main)
    assert "add(" not in main.split('"/inpage/happening"', 1)[1].split("\n", 3)[1]            # assigned, never added (two headers)


def test_a_far_off_month_cannot_break_the_calendar_page():
    assert "ev.MAX_YEAR" in route_block("_page") or "ev.MAX_YEAR" in ROUTES.split("def _page(")[1]
    from ruqqus.helpers import guild_events as grid
    assert grid.month_grid(2200, 12)                                                          # the bound is inside what a date holds


def test_every_page_and_admin_route_checks_what_it_must():
    for name in ("happening_overview", "happening_tab"):
        assert "@auth_desired" in route_block(name)
    assert "v=v" in ROUTES.split("def _page(")[1].split("\n\n\n")[0]                           # the layout reads the viewer
    assert "@admin_level_required(3)" in route_block("admin_happening")
    assert "@admin_level_required(3)" in route_block("admin_happening_event")
    for name in ("admin_happening_create", "admin_happening_edit", "admin_happening_state", "admin_happening_hide",
                 "admin_happening_unhide", "admin_happening_seed"):
        block = route_block(name)
        assert "@app.post(" in block and "@admin_level_required(4)" in block and "@validate_formkey" in block, name
    for name in ("admin_happening_create", "admin_happening_edit", "admin_happening_state"):
        assert "ev.Refused" in route_block(name)                                               # a bad entry is said, not a 500
    assert "store.update(" in ROUTES and "expected" in ROUTES                                  # compare-and-swap, not last write wins


def test_the_admin_page_stays_english_and_names_its_form_key():
    assert "{{ _(" not in ADMIN and "pgettext(" not in ADMIN                                  # CLAUDE.md "Stays English"
    assert ADMIN.count('name="formkey" value="{{ v.formkey }}"') >= 4
    assert "UTC" in ADMIN and "never rewrite" in ADMIN.lower()


# --- nothing from another site, nothing kept about the reader -----------------------------------------------------------------------

def test_nothing_here_loads_from_another_site():
    pattern = re.compile(r"(?:https?:)?//(?!www\.w3\.org)[a-z0-9.-]+\.[a-z]{2,}", re.I)
    for name, source in OURS.items():
        # a comment may name a web address; code and markup may not
        code = re.sub(r"/\*.*?\*/|//[^\n]*|\{#.*?#\}|<!--.*?-->", "", source, flags=re.S)
        assert not pattern.findall(code), (name, pattern.findall(code))
    for source in (BOX, ROWS, PAGE):
        assert "<script src=\"http" not in source and "<iframe" not in source and "<img" not in source   # no widget, no hotlinked picture
    assert 'rel="noopener noreferrer nofollow"' in ROWS                                         # a link out sends no referrer
    assert "{{ r.line1 | safe" not in ROWS and "| safe" not in BOX                             # a source's words are escaped (a hot title is stored HTML)
    assert ROWS.count("| safe") == 1 and "p.title | safe" in ROWS


def test_the_scripts_keep_nothing_about_the_reader():
    assert BOX_JS.count("localStorage.setItem") == 1 and "'late_box_h:'" in BOX_JS            # only the box's height, like the others
    for name, source in (("box.js", BOX_JS), ("page.js", PAGE_JS), ("time.js", TIME_JS)):
        code = re.sub(r"/\*.*?\*/|//[^\n]*", "", source, flags=re.S)
        assert "document.cookie" not in code and "sessionStorage" not in code and "XMLHttpRequest" not in code, name
        assert "fetch(" not in code or name == "box.js", name
    # the time zone is read only to write a sentence on the page; nothing is asked of the server with it
    fetches = re.findall(r"fetch\([^)]*\)", BOX_JS)
    assert fetches and all("zone" not in f.lower() and "tz" not in f.lower() for f in fetches)
    assert "?tz=" not in BOX_JS and "timeZone" not in BOX_JS


def test_the_box_script_behaves_itself():
    assert "classList.contains('embedded')" in BOX_JS                                          # not in the side-panel frames
    assert "slot.getClientRects().length > 0 && !document.hidden" in BOX_JS                    # not while nobody can see it
    for needle in ("visibilitychange", "pageshow", "matchMedia(", "MutationObserver", "AbortController"):
        assert needle in BOX_JS, needle
    assert "r.status === 429 || r.status >= 500" in BOX_JS and "blockedUntil" in BOX_JS         # a 429 stops it: thirty bring a ban
    assert re.search(r"if \(Date\.now\(\) < blockedUntil\) \{ schedule\(\); return; \}", BOX_JS)      # and it comes back when the block ends
    assert "FETCH_TIMEOUT_MS" in BOX_JS and "isBox(html)" in BOX_JS                              # a hung or foreign answer is not the box
    assert "channel.postMessage(html === undefined ? { same: true }" in BOX_JS                  # an unchanged answer is shared too
    assert "if (slot.getClientRects().length === 0) return;" in BOX_JS and "if (height > 0)" in BOX_JS   # never remember 0
    assert "BroadcastChannel('happening-box')" in BOX_JS                                       # one poller per browser
    assert "'rev='" in BOX_JS.replace('"', "'") or "rev=" in BOX_JS                            # asks with the signature it holds
    assert "fill_late_box(slot, html)" in BOX_JS and "swap(" in BOX_JS                         # the first fill, then only what changed
    assert "hovering()" in BOX_JS                                                              # never under the pointer
    assert "untilMidnight" in BOX_JS and "dayChanged" in BOX_JS                                # the reader's day rolls over
    assert "Math.random()" in BOX_JS                                                           # jitter
    assert "innerHTML" not in re.sub(r"holder\.innerHTML = html\.trim\(\);|a\.innerHTML = b\.innerHTML;|served\[name\] = s\.innerHTML;|"
                                     r"served\[name\] = b\.innerHTML;|served\[name\] !== b\.innerHTML", "", BOX_JS)


def test_the_page_script_waits_for_the_helpers():
    assert "DOMContentLoaded" in PAGE_JS and "window.TimePhrases" in PAGE_JS                  # it sits above the scripts it needs
    assert "time_phrases.js" not in PAGE                                                       # default.html loads it, once
    assert "js/happening_page.js" in PAGE and "asset_url(" in PAGE
    assert "<script src=\"/assets" not in PAGE and "?v=" not in PAGE                           # never a hand-written asset address


# --- the markup ---------------------------------------------------------------------------------------------------------------------

def test_the_box_always_says_something_and_does_not_shout():
    for line in ("Nothing is trending right now.", "Nothing live right now.", "Nothing marked for today."):
        assert f'_("{line}")' in BOX, line
    markup = re.sub(r"\{#.*?#\}", "", BOX, flags=re.S)                                          # a comment may say what is not there
    assert "aria-live" not in markup and 'role="status"' in markup and "data-happening-status" in markup
    assert "{% if v" not in BOX and "v.id" not in BOX                                          # the same shape for visitors and members
    assert BOX.count("today=true") == 1 and "data-today-empty" in BOX                            # today's rows come hidden: the script picks
    assert 'data-rev="{{ m.rev }}"' in BOX and 'data-next="{{ m.next }}"' in BOX and 'data-day="{{ m.day }}"' in BOX


def test_a_row_says_its_kind_in_words_and_carries_what_the_script_needs():
    assert "happening-badge" in ROWS and "{{ r.badge }}" in ROWS                               # a label, never colour alone
    for attribute in ("data-until-utc", "data-utc", "data-date", "data-end-date", "data-key"):
        assert attribute in ROWS, attribute
    assert 'title="{{ r.line1 }}"' in ROWS                                                     # a cut title is whole on hover
    assert "<table class=\"cal\"" in PAGE and "<caption" in PAGE and 'scope="col"' in PAGE     # a table, not an ARIA grid
    assert 'role="grid"' not in PAGE and 'aria-current="date"' in PAGE
    assert "ui_dir()" in PAGE                                                                  # the chevrons turn for right-to-left


# --- the styles ----------------------------------------------------------------------------------------------------------------------

def test_the_styles_are_tokens_only_and_in_both_sheets():
    code = re.sub(r"/\*.*?\*/|//[^\n]*", "", SCSS, flags=re.S)
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", code), "a raw colour"
    assert not re.search(r"\$(primary|purple|info|upvote|downvote|black|white)\b", code), "a raw brand variable"
    assert "!important" not in code and "@supports" not in code                                # libsass; no fighting the legacy sheet
    assert "--text-subtle" not in code                                                         # about 2:1, decorative only
    assert re.search(r"transition|animation", code) is None                                    # nothing moves on its own
    for sheet in ("main.scss", "main_dark.scss"):
        text = read("assets", "style", sheet)
        mark = '@import "{ruqquspath}/assets/style/_happening";'
        assert mark in text and text.index(mark) < text.index('@import "{ruqquspath}/assets/style/_motion";'), sheet
    assert "{boardcolor}" not in SCSS and "{ruqquspath}" not in SCSS


def test_the_box_is_grouped_into_wells_with_a_heading_row_each_that_always_exists():
    # the owner, 2026-10-26: "segment each a bit more". A heading row on the panel (label, and the link at its end) above a well
    # of rows; the row is always there so a link arriving moves nothing, and the old foot line under each list is gone
    markup = re.sub(r"\{#.*?#\}", "", BOX, flags=re.S)
    assert markup.count('class="happening-top"') == 3 and markup.count('class="happening-well"') == 3
    assert "happening-foot" not in markup + SCSS and "happening-foot" not in PAGE
    assert 'class="happening-head{% if m.live_on %} is-on{% endif %}" id="happening-h-live"' in markup
    for section in ("trending", "live", "today"):                                              # happening.js swaps a section's inner HTML
        assert f'data-section="{section}"' in markup
    assert BOX_JS.count("[data-section=") >= 1 and "happening-foot" not in BOX_JS
    code = re.sub(r"/\*.*?\*/|//[^\n]*", "", SCSS, flags=re.S)
    assert "--happening-head: 2rem" in code and "height: var(--happening-head)" in code
    # concentric: the panel's 16px (--radius-lg) minus the 8px mat is the well's 8px (--radius-sm)
    well = code[code.index(".happening-well {"):].split("}")[0]
    assert "var(--radius-sm)" in well and "var(--surface-raised)" in well and "var(--shadow-sm), 0 0 0 1px var(--border)" in well
    # only a link answers the pointer, hover only for a real pointer, press at once, and a row is one fixed height in the box
    assert "@media (hover: hover) and (pointer: fine) {\n    a.happening-link:hover" in code
    assert "a.happening-link:active {\n    background-color: var(--press);" in code
    assert ".happening-box .happening-link {\n    align-items: center;\n    height: var(--happening-row);\n    overflow: hidden;" in code
    # a bold or muted utility class is !important and the stylesheet may not be: the colours and weights are set in the stylesheet
    assert "font-weight-bold" not in ROWS and "text-muted" not in ROWS.split("macro topic_row")[1].split("endmacro")[0].split("happening-main")[0]


def test_live_and_today_are_not_drawn_while_empty_and_trending_always_is():
    # the owner, 2026-10-27: "if there is nothing live or nothing in the calendar for today, make those parts completely disappear"
    markup = re.sub(r"\{#.*?#\}", "", BOX, flags=re.S)
    live = re.search(r'<section class="happening-section" data-section="live"[^>]*>', markup).group(0)
    today = re.search(r'<section class="happening-section" data-section="today"[^>]*>', markup).group(0)
    trending = re.search(r'<section class="happening-section" data-section="trending"[^>]*>', markup).group(0)
    assert "{% if not m.live %} hidden{% endif %}" in live
    assert '{% if not (m.today | selectattr("today_utc") | list) %} hidden{% endif %}' in today
    assert "hidden" not in trending                                                          # the box never vanishes
    for line in ("Nothing live right now.", "Nothing marked for today."):                    # still there for when it has something
        assert f'_("{line}")' in markup
    assert "a.hidden = b.hidden;" in BOX_JS                                                  # an answer shows or hides the section
    apply_today = TIME_JS.split("function applyToday(")[1].split("\n  }\n")[0]
    assert "list.closest('[data-section=\"today\"]')" in apply_today and "section.hidden = shown === 0;" in apply_today
    assert BOX_JS.index("T.applyToday(") < BOX_JS.index("storeHeight();", BOX_JS.index("function enhance"))     # the height is kept after it
    assert "[data-section" not in PAGE                                                       # the page's columns keep their empty lines


def test_the_page_uses_the_same_idiom_and_a_row_never_prints_its_date_twice():
    assert PAGE.count('class="happening-col-top"') >= 4 and PAGE.count('class="happening-well"') >= 4
    assert 'class="cal-wrap"' in PAGE and PAGE.index('class="cal-wrap"') < PAGE.index('<table class="cal"')
    assert "r.line2 == r.when_text" in ROWS                                                    # a civil date was printed by both


def test_the_pieces_are_marked_for_translation_and_use_only_free_icons():
    free = (ROOT / "tests" / "data" / "fa_free_6_4_0_names.txt").read_text(encoding="utf-8").split()
    names = set(free) | {n.removeprefix("fa-") for n in free}
    for source in (BOX, ROWS, PAGE):
        for name in re.findall(r"\bfa-([a-z0-9-]+)\b", source):
            if name in ("fw", "lg", "2x", "stack", "inverse"):
                continue
            if name == "chevron":                                    # fa-chevron-{{ 'left' or 'right' }} by direction
                assert {"chevron-left", "chevron-right"} <= names
                continue
            assert name in names, name
    for source in (BOX, PAGE):
        source = re.sub(r"\{% block pagetype %\}.*?\{% endblock %\}", "", source)                # the page's id, not a word
        text = re.sub(r"\{#.*?#\}|\{%.*?%\}|\{\{.*?\}\}|<[^>]+>", "", source, flags=re.S)
        assert not re.search(r"[A-Za-z]{3,}", re.sub(r"\s+", " ", text)), text[:200]           # every word is inside _() or a value
