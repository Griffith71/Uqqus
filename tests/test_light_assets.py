"""The light language (the owner's, 2026-10-29; CLAUDE.md "The light language"): shine and glow as a small visual
language. Light marks meaning, is added only along edges and corners (behind a label a surface only gets darker), is
still by default, and a glint plays once. Everything comes from the tokens in _tokens.scss, drawn from the page's own
brand colour, with one dial, --light-k. These tests keep the partial, the tokens and the chat client's markers in step."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "ruqqus"
STYLE = APP / "assets" / "style"
PARTIAL = (STYLE / "_light.scss").read_text(encoding="utf-8")

TOKENS = ("--light-k", "--glow", "--wash", "--ramp", "--glint", "--glint-brand", "--edge", "--ring", "--halo-sm", "--bloom-sm")
# gone with the owner's "remove the shine on buttons" (2026-10-29) and the flat bubble: nothing may bring them back unnoticed
REMOVED_TOKENS = ("--rim", "--depth", "--shine", "--glint-soft", "--halo", "--bloom")
# the surfaces that have been given light so far: the list grows phase by phase, and a rule on any other selector fails
# here, so light cannot creep onto cards, text or menus at rest
ALLOWED = (":root", ".chat-msg", ".chat-bubble", ".chat-list-row",
           ".btn-primary", ".btn-purple", ".btn-outline-primary", ".btn-outline-purple", ".feed-pill", ".explore-chip", ".space-tab",
           ".folder-chip", ".where-profile-input", ".audience-chip-input", ".nav-pills", ".page-item", ".search-tab", ".explore-tab",
           ".guild-tab", ".composer-mode", ".notif-tabs", "#sidebar-left", ".bottom-tab-create-icon", ".form-control", "#navbar",
           ".composer-card", ".inline-composer", ".settings-nav", ".explore-chips", ".card .voting", ".vote-pill", ".notif", ".nav-link", ".space-live-dot", ".happening-box", ".happening-row", ".live-badge",
           ".guild-chat-dot", ".live-new-pill", ".arrow-up", ".post-actions", "a.studio-card", ".poll-result", ".dropdown-menu", ".modal-content",
           ".toast", "#main-content-row > .sidebar:not(.sidebar-left)")


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


def compile_sheet(name, colour="23a9f2", site="23a9f2"):
    sass = pytest.importorskip("sass")
    scss = (STYLE / name).read_text(encoding="utf-8")
    scss = scss.replace("{boardcolor}", colour).replace("{maincolor}", site).replace("{ruqquspath}", str(APP))
    return sass.compile(string=scss)


def tokens(css):
    start = css.index(":root {\n  --font-sans")
    block = css[start:css.index("}", start)]
    return dict(re.findall(r"(--[\w-]+):\s*([^;]+);", block))


def compile_tokens(theme, colour):
    sass = pytest.importorskip("sass")
    src = (f'$theme: "{theme}"; $primary: #{colour}; $purple: #23a9f2; $upvote: #23a9f2; $downvote: #e8452c;\n'
           f'@import "{APP}/assets/style/_tokens";')
    return tokens(sass.compile(string=src))



def enclosing(text, pos):
    """The preludes of the at-rules (@media, @supports) that enclose `pos`, found by walking the braces."""
    stack = []
    body = re.sub(r"//[^\n]*", lambda m: " " * len(m.group(0)), text)[:pos]          # a comment may hold a brace
    for m in re.finditer(r"([^{};]*)\{|\}", body):
        if m.group(0) == "}":
            stack.pop() if stack else None
        else:
            stack.append(m.group(1).strip())
    return [x for x in stack if x.startswith("@")]


def alpha_percent(value):
    return float(re.search(r"calc\(([\d.]+)% \* var\(--light-k\)\)", value).group(1))


def test_the_partial_is_imported_after_every_older_partial_by_both_sheets():
    for sheet in ("main.scss", "main_dark.scss"):
        imports = re.findall(r'@import "\{ruqquspath\}/assets/style/(_\w+)";', read("ruqqus", "assets", "style", sheet))
        # after every older partial, `_mobile` the last of them (a later one, such as the text-size partial, may follow it)
        assert imports.index("_light") == imports.index("_mobile") + 1, (sheet, imports[-4:])


@pytest.mark.parametrize("name", ["main.scss", "main_dark.scss"])
def test_every_token_is_in_the_first_root_block_of_both_themes(name):
    t = tokens(compile_sheet(name))
    for token in TOKENS:
        assert token in t, (name, token)
    assert t["--light-k"].strip() == "1"                      # subtle: the owner's choice
    for token in REMOVED_TOKENS:
        assert token not in t, (name, token)
        assert f"var({token})" not in PARTIAL, token


def test_the_scroll_shade_only_darkens_so_white_text_stays_aa_along_the_whole_ramp():
    """Your bubble's text is white on --brand-deep, which has only 4.63:1 (room for about 1.6% white): so no light is ever laid
    over it, and the shade that follows the scroll is BLACK, 10% at most (5.5:1 at the bottom of the thread). The owner's rule:
    behind running text a surface only gets darker."""
    for theme in ("light", "dark"):
        t = compile_tokens(theme, "23a9f2")
        ramp = t["--ramp"]
        assert ramp.strip() == "color-mix(in srgb, #000 calc(10% * var(--light-k)), transparent)"
        deep = compile_deep(theme, "23a9f2")
        assert contrast("#ffffff", deep) >= 4.5
        assert contrast("#ffffff", blend(deep, "#000000", 0.10)) >= contrast("#ffffff", deep) + 0.8
    # the nine awkward guild colours: the deep blue is AA for each, and a darker one only raises it
    for colour in ("ffd60a", "fcd5ce", "1a1a2e", "e85d04", "2c7be5", "805ad5", "07c20c", "ffffff", "000000"):
        for theme in ("light", "dark"):
            deep = compile_deep(theme, colour)
            assert contrast("#ffffff", deep) >= 4.5, (theme, colour)
            assert contrast("#ffffff", blend(deep, "#000000", 0.10)) >= contrast("#ffffff", deep), (theme, colour)


def test_the_two_themes_draw_light_differently():
    light, dark = compile_tokens("light", "23a9f2"), compile_tokens("dark", "23a9f2")
    # light = polish: a small tinted shadow, no ring and no bloom (a bloom on white is a smudge)
    assert light["--ring"].strip() == "0 0 0 0 transparent" and light["--bloom-sm"].strip() == "0 0 0 0 transparent"
    assert "color-mix" in light["--halo-sm"]
    # dark = glow: a ring and a bloom, no shadow
    assert "color-mix" in dark["--ring"] and "color-mix" in dark["--bloom-sm"] and dark["--halo-sm"].strip() == "0 0 0 0 transparent"
    assert alpha_percent(light["--edge"]) > alpha_percent(dark["--edge"])      # a top edge on white, a hairline on black


def luminance(h):
    h = h.lstrip("#")
    c = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a, b):
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def blend(base, over, alpha):
    """`over` laid on `base` at `alpha` (0-1), as a hex colour."""
    b, o = base.lstrip("#"), over.lstrip("#")
    return "#" + "".join(f"{round(int(b[i:i + 2], 16) * (1 - alpha) + int(o[i:i + 2], 16) * alpha):02x}" for i in (0, 2, 4))


def glow_of(theme, colour):
    return compile_tokens(theme, colour)["--glow"].strip()


def compile_deep(theme, colour):
    sass = pytest.importorskip("sass")
    src = (f'$theme: "{theme}"; $primary: #{colour}; $purple: #23a9f2; $upvote: #23a9f2; $downvote: #e8452c;\n'
           f'@import "{APP}/assets/style/_tokens";')
    return tokens(sass.compile(string=src))["--brand-deep"].strip()


def test_a_guilds_colour_is_made_to_show_on_the_page_it_glows_on():
    # a navy guild's glow would vanish on black: it is lightened until it reads
    assert contrast(glow_of("dark", "1a1a2e"), "#1e1e1e") >= 4.5
    # a pale brand's tinted shadow is its darkened shade on a light page (a white glow is invisible there)
    assert glow_of("light", "ffffff") != "#ffffff" and contrast(glow_of("light", "ffffff"), "#ffffff") >= 3
    # the site's own colour is left alone
    assert glow_of("light", "23a9f2") == "#23a9f2" and glow_of("dark", "23a9f2") == "#23a9f2"


def test_a_warm_or_near_white_brand_is_damped():
    site = alpha_percent(compile_tokens("dark", "23a9f2")["--ring"])
    for colour in ("e85d04", "e4432d", "eb4963"):                       # beside the semantic reds
        assert alpha_percent(compile_tokens("dark", colour)["--ring"]) < site * 0.7, colour
    assert alpha_percent(compile_tokens("dark", "ffffff")["--ring"]) < site * 0.6      # a white bloom on black is a spotlight
    # a cool colour is not
    assert alpha_percent(compile_tokens("dark", "2c7be5")["--ring"]) == site


@pytest.mark.parametrize("name", ["main.scss", "main_dark.scss"])
@pytest.mark.parametrize("colour", ("ffd60a", "fcd5ce", "1a1a2e", "e85d04", "2c7be5", "805ad5", "07c20c", "ffffff", "000000"))
def test_the_nine_awkward_guild_colours_compile_with_the_light_language(name, colour):
    css = compile_sheet(name, colour)
    assert "@keyframes light-glint" in css and ".chat-bubble.bg-primary.is-new::after" in css


def test_the_partial_carries_no_placeholder_and_only_what_the_rules_allow():
    assert not re.search(r"\{boardcolor\}|\{maincolor\}|\{ruqquspath\}", PARTIAL)
    assert "transition: all" not in PARTIAL
    # one !important, and it is the one that has to be: Bootstrap's .shadow on the account and post menus is !important
    code = re.sub(r"//[^\n]*", lambda m: " " * len(m.group(0)), PARTIAL)
    important = [m.start() for m in re.finditer(r"!important", code)]
    assert len(important) == 1, important
    rule_start = code.rfind("{", 0, important[0])
    assert code[:rule_start].split("}")[-1].strip() == ".dropdown-menu.shadow"                      # the rule it is in
    for ms in re.findall(r"(\d+)ms", PARTIAL):
        assert int(ms) <= 300, ms                                       # the site's cap on a motion
    for seconds in re.findall(r"(?<![\d.])(\d*\.?\d+)s\b", re.sub(r"//[^\n]*", "", PARTIAL)):
        assert float(seconds) <= 0.3, seconds                           # a duration in seconds is no way round the cap
    # a glint host holds its glint: it is positioned and clips what it draws (a button holds none any more)
    for host in (".chat-list-row {", ".chat-msg .chat-bubble {"):
        rule = PARTIAL[PARTIAL.index(host):]
        rule = rule[:rule.index("}")]
        assert "overflow: hidden" in rule and "position: relative" in rule, host
    # a bubble is its own stacking context, so that the shade (z-index -1) sits between its fill and its text
    bubble = PARTIAL[PARTIAL.index(".chat-msg .chat-bubble {"):]
    assert "isolation: isolate;" in bubble[:bubble.index("}")]
    # shadows: at most four layers in one declaration (the rim of the floating pill is edge, halo, bloom and its own drop, and
    # the halo and the bloom are never both visible: a light page has one, a dark page the other)
    for decl in re.findall(r"box-shadow:\s*([^;]+);", PARTIAL):
        layers, depth, current = [], 0, ""
        for ch in decl:
            depth += (ch == "(") - (ch == ")")
            if ch == "," and depth == 0:
                layers.append(current)
                current = ""
            else:
                current += ch
        layers.append(current)
        assert len(layers) <= 4, decl
    # a rule on a surface nobody gave light to fails here
    body = re.sub(r"//[^\n]*", "", PARTIAL)
    depth = 0
    for match in re.finditer(r"([^{};]+)\{|\}", body):
        if match.group(0) == "}":
            depth -= 1
            continue
        selector = match.group(1).strip()
        depth += 1
        if selector.startswith("@") or re.match(r"^(from|to|\d+%)", selector):
            continue
        for part in (x.strip() for x in selector.split(",")):
            assert part.startswith(ALLOWED), part


def block_at(text, start):
    """The `{ ... }` block that opens first at or after `start`, found by walking the braces."""
    depth = 0
    for i in range(text.index("{", start), len(text)):
        depth += (text[i] == "{") - (text[i] == "}")
        if depth == 0:
            return text[start:i + 1]


def test_every_animation_is_gated_by_reduced_motion_and_moves_only_transform_and_opacity():
    for keyframes in re.finditer(r"@keyframes [\w-]+ \{", PARTIAL):
        i = keyframes.start()
        assert any("prefers-reduced-motion: no-preference" in a for a in enclosing(PARTIAL, i)), "an ungated @keyframes"
        block = block_at(PARTIAL, i)
        assert not re.search(r"box-shadow|filter|blur|background|width|height|top|left|bottom|right|margin", block), block
    animations = list(re.finditer(r"animation:\s*([^;]+);", PARTIAL))
    assert len(animations) >= 3
    for match in animations:
        i, animation = match.start(), match.group(1)
        assert any("prefers-reduced-motion: no-preference" in a for a in enclosing(PARTIAL, i)), animation
        assert "infinite" not in animation
        if animation.startswith("light-ramp"):
            continue                       # driven by a scroll timeline: it has no clock, so it can neither loop nor play on its own
        assert " 1 " in animation + " ", animation                   # once, never a loop


def test_the_effects_are_only_for_a_browser_that_has_color_mix():
    """Without it a shadow list with one of the tokens in it computes to `none`, and would take a menu's own shadow with it."""
    assert "@supports (color: color-mix(in srgb, red, blue)) {" in PARTIAL
    start = PARTIAL.index("@supports (color: color-mix(in srgb, red, blue)) {")
    for marker in (".chat-msg .chat-bubble {", ".chat-list-row {", ".dropdown-menu {", ".modal-content {", "@keyframes light-glint", "@keyframes light-ramp", "@media (forced-colors: active)"):
        pos = PARTIAL.index(marker)
        assert pos > start and any(a.startswith("@supports") for a in enclosing(PARTIAL, pos)), marker
    # the dial is not inside it: it only sets a number
    assert not any(a.startswith("@supports") for a in enclosing(PARTIAL, PARTIAL.index("--light-k: 0;")))


def test_the_accessibility_media_features_are_handled():
    assert "@media (prefers-reduced-transparency: reduce), (prefers-contrast: more) {\n    :root {\n        --light-k: 0;" in PARTIAL
    forced = PARTIAL[PARTIAL.index("@media (forced-colors: active)"):]
    for need in ("border-color: CanvasText;", "border-inline-start-color: Highlight;", "border: 1px solid CanvasText;"):
        assert need in forced, need


def test_the_glint_travels_from_the_start_side_in_the_mirrored_sheet_too():
    """The band is symmetric and moves with literal offsets, which the mirror flips."""
    from ruqqus.helpers import css_flip
    css = compile_sheet("main.scss")
    rtl = css_flip.flip(css)
    def block(text, name="light-glint"):
        start = text.index("@keyframes " + name)
        depth = 0
        for i in range(text.index("{", start), len(text)):
            depth += (text[i] == "{") - (text[i] == "}")
            if depth == 0:
                return text[start:i + 1]
    assert "translateX(-70%)" in block(css) and "translateX(70%)" in block(css)
    assert block(rtl).index("translateX(70%)") < block(rtl).index("translateX(-70%)")        # from the right
    assert block(css).index("translateX(-70%)") < block(css).index("translateX(70%)")        # from the left
    # the shade that follows the scroll has nothing horizontal in it: the mirror must leave it exactly as it is
    for needle in ("view-timeline: --bubble block;", "animation-timeline: --bubble;", "animation-range: cover 0% cover 100%;", "z-index: -1;", "inset: 0;"):
        assert css.count(needle) == rtl.count(needle) >= 1, needle
    # and the mirror changes nothing structural (the same count of rules and declarations, as test_css_flip checks)
    assert (rtl.count("{"), rtl.count("}"), rtl.count(";")) == (css.count("{"), css.count("}"), css.count(";"))


def test_the_chat_client_is_wired_to_the_fresh_logic():
    """The behaviour is run in node below (fresh.js is pure); this keeps index.js and ui.js using it."""
    index, ui = read("ruqqus", "assets", "chat_src", "src", "index.js"), read("ruqqus", "assets", "chat_src", "src", "ui.js")
    assert 'import { createFresh } from "./fresh.js";' in index
    assert "client.on(RoomEvent.Timeline, (event, room, toStartOfTimeline, removed, data) => {" in index
    assert "state.fresh.note(event, toStartOfTimeline, data, isShownMessage(event));" in index
    assert "state.fresh = createFresh();\n  state.active = {" in index                  # a new conversation starts quiet
    assert "const age = state.fresh.age(event);" in index and "isNew: age >= 0," in index and "newAge: age," in index
    # every list is observed when the lists are fetched, not only the tab on show
    assert "noteUnreadCounts([...inbox, ...group, ...request, ...channel, ...guild]," in index
    # the bubble: the class, a negative delay so a redraw carries on, and the class goes when the glint ends
    assert 'bubble.classList.add("is-new");' in ui and 'bubble.style.setProperty("--glint-delay", `${-Math.max(0, item.newAge)}ms`);' in ui
    # only the glint's own end counts: the scroll-linked shade is an animation on the same bubble, and its events reach it too
    assert 'if (e.animationName !== "light-glint") return;' in ui and 'bubble.addEventListener("animationend", glintEnded);' in ui
    assert 'bubble.removeEventListener("animationend", glintEnded);' in ui and 'bubble.classList.remove("is-new");' in ui
    assert '{ once: true });\n  }\n  const shared' not in ui
    assert 'if (convo.unread_count > 0) row.classList.add("is-unread");' in ui and "const bumpAge = bumps.age(convo.key);" in ui


FRESH_JS = (ROOT / "ruqqus" / "assets" / "chat_src" / "src" / "fresh.js").as_uri()


def run_node(body):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    script = f'import {{ createFresh, createUnreadBumps, FRESH_MS, FRESH_WITHIN, SEEN_MS, BUMP_MS }} from "{FRESH_JS}";\n' \
             'const ev = (id, o = {}) => ({ getId: () => o.id2 ?? id, getTxnId: () => o.txn, getTs: () => o.ts ?? 0, localTimestamp: o.lt });\n' + body
    out = subprocess.run([node, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_a_message_glints_once_and_only_when_it_arrives_live():
    r = run_node("""
const f = createFresh(); const T = 1_000_000; const live = { liveEvent: true };
const out = {};
out.live = f.note(ev("$a", { lt: T }), false, live, true, T);
out.age0 = f.age(ev("$a"), T); out.age500 = f.age(ev("$a"), T + 500); out.gone = f.age(ev("$a"), T + FRESH_MS + 1);
const g = createFresh();
out.scrollback = g.note(ev("$b", { lt: T }), true, live, true, T);          // history scrolled back to
out.notLive = g.note(ev("$c", { lt: T }), false, { liveEvent: false }, true, T);
out.noData = g.note(ev("$c2", { lt: T }), false, undefined, true, T);
out.relation = g.note(ev("$d", { lt: T }), false, live, false, T);          // an edit or a reaction
out.late = g.note(ev("$e", { lt: T - FRESH_WITHIN - 1 }), false, live, true, T);          // landed late (a reconnect)
out.skewed = g.note(ev("$f", { ts: 5, lt: T }), false, live, true, T);        // the server's clock is far away; this device's is right
out.replay = [g.note(ev("$g", { lt: T }), false, live, true, T), g.note(ev("$g", { lt: T }), false, live, true, T + 100)];
console.log(JSON.stringify(out));
""")
    assert r["live"] is True and r["age0"] == 0 and r["age500"] == 500 and r["gone"] == -1
    assert (r["scrollback"], r["notLive"], r["noData"], r["relation"], r["late"]) == (False, False, False, False, False)
    assert r["skewed"] is True, "a skewed server clock must not hide a new message"
    assert r["replay"] == [True, False], "a replayed event must not glint twice"


def test_your_own_message_keeps_its_glint_when_the_server_confirms_it():
    r = run_node("""
const f = createFresh(); const T = 1_000_000; const live = { liveEvent: true };
const local = ev("~!room:txn1", { txn: "txn1", lt: T });                       // the local echo
const out = { first: f.note(local, false, live, true, T) };
const confirmed = ev("$real", { txn: "txn1", lt: T });                         // the same message once it has its real id
out.ageAfter = f.age(confirmed, T + 400);                                      // found by the transaction id
out.again = f.note(confirmed, false, live, true, T + 500);                     // and never glints a second time
console.log(JSON.stringify(out));
""")
    assert r["first"] is True and r["ageAfter"] == 400 and r["again"] is False


def test_what_has_glinted_is_forgotten_after_a_minute_and_the_maps_do_not_grow():
    r = run_node("""
const f = createFresh(); const T = 1_000_000; const live = { liveEvent: true };
f.note(ev("$a", { lt: T }), false, live, true, T);
const out = { sameMinute: f.note(ev("$a", { lt: T + 100 }), false, live, true, T + 100) };
out.afterAMinute = f.note(ev("$a", { lt: T + SEEN_MS + 2 }), false, live, true, T + SEEN_MS + 2);
console.log(JSON.stringify(out));
""")
    assert r == {"sameMinute": False, "afterAMinute": True}


def test_a_conversation_row_glints_when_its_unread_count_goes_up_and_not_otherwise():
    r = run_node("""
const b = createUnreadBumps(); const T = 1_000_000; const out = {};
const c = (key, n) => ({ key, unread_count: n });
b.observe([c("c1", 2), c("c2", 0)], null, T);                                   // the first sighting
out.first = [b.age("c1", T), b.age("c2", T)];
b.observe([c("c1", 2), c("c2", 0)], null, T + 1000);                            // a redraw that changes nothing
out.same = b.age("c1", T + 1000);
b.observe([c("c1", 3), c("c2", 0)], null, T + 2000);                            // a message arrived
out.up = [b.age("c1", T + 2000), b.age("c1", T + 2300), b.age("c1", T + 2000 + BUMP_MS + 1)];
b.observe([c("c1", 1), c("c2", 0)], null, T + 4000);                            // read some: down
out.down = b.age("c1", T + 4000);
b.observe([c("c1", 1), c("c2", 5)], "c2", T + 5000);                            // the open conversation: you are reading it
out.active = b.age("c2", T + 5000);
b.observe([c("c1", 1)], null, T + 6000);                                        // c2 left the list: forgotten
b.observe([c("c1", 1), c("c2", 9)], null, T + 7000);                            // and back: a first sighting again
out.back = b.age("c2", T + 7000);
console.log(JSON.stringify(out));
""")
    assert r["first"] == [-1, -1] and r["same"] == -1
    assert r["up"][0] == 0 and r["up"][1] == 300 and r["up"][2] == -1
    assert r["down"] == -1 and r["active"] == -1 and r["back"] == -1


def test_the_unread_wash_is_mirrored_for_right_to_left_languages():
    from ruqqus.helpers import css_flip
    css = compile_sheet("main.scss")
    rule = lambda text: text[text.index(".chat-list-row.is-unread:not(.bg-light)"):][:200]
    assert "to right" in rule(css) and "to left" in rule(css_flip.flip(css))
    assert "inset -3px 0 0" in css_flip.flip(css)[css_flip.flip(css).index(".chat-list-row.is-unread {"):][:120]      # the bar is on the same side


def test_a_message_glint_is_a_strip_along_the_top_edge_never_across_the_text():
    rule = PARTIAL[PARTIAL.index(".chat-bubble.is-new::after, .chat-list-row.is-bumped::after {"):]
    rule = rule[:rule.index("}")]
    assert "inset: 0 0 auto 0;" in rule and "height: 5px;" in rule                 # running text: only the edge is lit
    assert "linear" in PARTIAL and "cubic-bezier(0.4" not in PARTIAL                # an even pace across the strip


def test_your_message_shade_follows_the_scroll_through_a_timeline_named_on_the_bubble():
    """The owner (2026-10-29): on TikTok the shine in messages happens while you scroll. What the messengers with public source do
    (Signal, Telegram, Messenger) is one colour ramp pinned to the screen. Here it is a scroll-driven animation (no script, off the
    main thread): a black layer under the text whose opacity follows the bubble's travel through the thread."""
    selector = ".chat-msg .chat-bubble.bg-primary:not(.font-italic)::before {"
    rule = block_at(PARTIAL, PARTIAL.index(selector))
    for need in ("content: \"\";", "position: absolute;", "inset: 0;", "z-index: -1;", "pointer-events: none;", "background: var(--ramp);",
                 "animation: light-ramp linear both;", "animation-timeline: --bubble;", "animation-range: cover 0% cover 100%;"):
        assert need in rule, need
    assert rule.index("animation: light-ramp") < rule.index("animation-timeline")       # the shorthand resets the timeline
    # the timeline is NAMED and defined on the bubble: an anonymous view() on the pseudo-element would look at the bubble itself,
    # which overflow: hidden makes a scroll container
    host = PARTIAL[PARTIAL.index(".chat-msg .chat-bubble.bg-primary:not(.font-italic) {\n            view-timeline"):]
    assert "view-timeline: --bubble block;" in host[:host.index("}")]
    # only for a reader who allows motion and a browser that has the feature (without it the bubble is the flat deep blue)
    for pos in (PARTIAL.index(selector), PARTIAL.index("view-timeline: --bubble block;"), PARTIAL.index("@keyframes light-ramp")):
        gates = enclosing(PARTIAL, pos)
        assert any("prefers-reduced-motion: no-preference" in g for g in gates) and "@supports (animation-timeline: view())" in gates
    ramp = block_at(PARTIAL, PARTIAL.index("@keyframes light-ramp"))
    assert re.sub(r"\s+", " ", ramp) == "@keyframes light-ramp { from { opacity: 1; } to { opacity: 0; } }"      # dark below, plain above
    # a forced-colours page has no fill of ours for it to darken
    forced = PARTIAL[PARTIAL.index("@media (forced-colors: active)"):]
    assert ".chat-msg .chat-bubble.bg-primary:not(.font-italic)::before {\n        display: none;" in forced
    # the compiled sheet keeps the order the browser needs
    css = compile_sheet("main.scss")
    compiled = css[css.index(".chat-msg .chat-bubble.bg-primary:not(.font-italic)::before"):][:500]
    assert compiled.index("animation: light-ramp linear both;") < compiled.index("animation-timeline: --bubble;")
    # ... and the bubble's glint (is-new, ::after) is untouched
    assert ".chat-bubble.is-new::after, .chat-list-row.is-bumped::after {" in PARTIAL


def test_a_pale_or_light_brand_gets_a_glow_that_shows_on_white():
    assert contrast(glow_of("light", "ffd60a"), "#ffffff") >= 3                       # yellow on white: darkened
    assert glow_of("light", "23a9f2") == "#23a9f2"                                    # the site's own colour is left as it is


def test_the_calls_and_spaces_speaking_rings_get_a_bloom_and_a_call_tile_a_ring_above_its_video():
    call, space = read("ruqqus", "templates", "call.html"), read("ruqqus", "templates", "space.html")
    bloom = "box-shadow: 0 0 24px -3px color-mix(in srgb, var(--speaking) 75%, transparent);"
    assert bloom in call[call.index(".call-tile.speaking {"):][:260]
    # an absolutely placed <video> covers an outline: the ring is a pseudo-element above it
    assert ".call-tile::after { content: \"\"; position: absolute; inset: 0;" in call and ".call-tile.speaking::after { opacity: 1; }" in call
    for selector in (".space-person.speaking .space-face", ".space-video.speaking"):
        rule = space[space.index(selector + " {"):]
        assert "box-shadow: 0 0 22px -4px color-mix(in srgb, var(--speaking) 55%, transparent);" in rule[:rule.index("}")], selector
    # the dial and reduced motion govern them too
    for page in (call, space):
        assert "@media (prefers-reduced-transparency: reduce), (prefers-contrast: more)" in page
    assert ".space-video { transition: none; }" in space.replace("\n", " ") or ".space-face, .space-video { transition: none; }" in space


# ---- phase 2: buttons, pills, tabs and selection -------------------------------------------------------------------

def test_the_phase_2_tokens_are_there_in_both_themes():
    for theme in ("light", "dark"):
        t = compile_tokens(theme, "23a9f2")
        for token in ("--halo-sm", "--bloom-sm", "--lit", "--rail", "--rail-up", "--soft-a", "--hairline"):
            assert token in t, (theme, token)
    light, dark = compile_tokens("light", "23a9f2"), compile_tokens("dark", "23a9f2")
    # a light page: a small tinted shadow, no bloom, no lit rail; a dark page: the opposite
    assert "color-mix" in light["--halo-sm"] and light["--bloom-sm"].strip() == light["--lit"].strip() == "0 0 0 0 transparent"
    assert light["--rail"].strip() == light["--rail-up"].strip() == "0 0 0 0 transparent"
    assert dark["--halo-sm"].strip() == "0 0 0 0 transparent" and all("color-mix" in dark[k] for k in ("--bloom-sm", "--lit", "--rail", "--rail-up"))
    assert dark["--rail"].startswith("inset")             # inside the tab: a scrolling tab bar would cut a shadow below it
    assert light["--soft-a"].strip() == "0%" and light["--hairline"].strip() == "0 0 0 0 transparent"
    assert dark["--soft-a"].strip() == "40%" and "color-mix" in dark["--hairline"]


def test_buttons_pills_and_chips_are_flat():
    """The owner (2026-10-29): "remove the shine on buttons". The big sites' primary controls (TikTok, Instagram, Threads, Discord,
    Mastodon, Bluesky, Stripe, Radix, Material) are a flat fill with a darker hover and press, and nothing sweeps across them.
    Nothing in this partial may give a control a gradient, a highlight, a glow, a sweep, a pseudo-element or a clip."""
    code = re.sub(r"//[^\n]*", "", PARTIAL)
    control = r"\.btn-(?:primary|purple|outline-primary|outline-purple)|\.feed-pill|\.live-new-pill|\.bottom-tab-create-icon|\.explore-chip(?![-\w]*s\b)|\.space-tab|\.folder-chip|\.where-chip|\.audience-chip|\.nav-pills|\.page-item"
    for rule in re.finditer(r"([^{};]+)\{([^{}]*)\}", code):
        selector, body = rule.group(1), rule.group(2)
        if not re.search(control, selector) or "forced-colors" in "".join(enclosing(PARTIAL, PARTIAL.index(selector.strip()[:40]))):
            continue
        assert not re.search(r"box-shadow|background-image|overflow|animation|::before|::after", selector + body), selector.strip()
    for gone in ("light-sweep", "glint-soft", ".feed-pill", ".live-new-pill", ".bottom-tab-create-icon", ".explore-chips", ".shadow-md"):
        assert gone not in re.sub(r"//[^\n]*", "", PARTIAL).replace("forced", ""), gone


def test_the_selected_row_bar_is_logical_and_the_unified_focus_ring_is_the_sites():
    assert "inset-inline-start: 0;" in PARTIAL[PARTIAL.index('a[aria-current="page"]::before'):]
    assert "box-shadow: 0 0 0 3px var(--focus-ring), var(--bloom-sm);" in PARTIAL
    for selector in (".form-control:focus", ".composer-card .composer-title-input:focus", ".inline-composer .post-editor:focus-within"):
        assert selector in PARTIAL, selector


def test_a_selected_state_still_differs_from_an_unselected_one_under_forced_colours():
    """The browser draws no gradient and no shadow there, so the rim, the ring and the lit bar are gone: something that is not a
    colour of ours has to say which chip, tab, row or vote is the selected one, and no row may move when it becomes one."""
    forced = PARTIAL[PARTIAL.index("@media (forced-colors: active)"):]
    chips = forced[forced.index(".explore-chip.active, .explore-chip.active:hover"):]
    chips = chips[:chips.index("}")]
    for selector in (".space-tab.active", ".nav-pills .nav-link.active", ".page-item.active .page-link", ".audience-chip-input:checked + .audience-chip"):
        assert selector in chips, selector
    assert "outline: 2px solid Highlight;" in chips and "outline-offset: -2px;" in chips      # an outline: nothing moves
    tabs = forced[forced.index(".search-tab.active, .explore-tab.active"):]
    assert "border-bottom-color: Highlight;" in tabs[:tabs.index("}")]
    rows = forced[forced.index("#sidebar-left .guild-recommendations-item > a[aria-current=\"page\"], .arrow-up.active"):]
    assert "outline: 2px solid Highlight;" in rows[:rows.index("}")]
    # an unread row and notification reserve the bar's width, so they do not move
    assert ".chat-list-row, .notif {\n        border-inline-start: 4px solid transparent;" in forced


@pytest.mark.parametrize("name", ["main.scss", "main_dark.scss"])
@pytest.mark.parametrize("colour", ("ffd60a", "1a1a2e", "e85d04", "ffffff", "000000"))
def test_the_awkward_guild_colours_compile_with_phase_2(name, colour):
    css = compile_sheet(name, colour)
    assert "@keyframes light-ramp" in css and "#sidebar-left .guild-recommendations-item > a[aria-current=\"page\"]::before" in css
    assert ".btn-primary::before" not in css and "light-sweep" not in css


def test_a_hover_or_press_only_raises_a_white_labels_contrast():
    """A flat fill under a short white label is the owner's one exception to AA (2.62:1 on #23A9F2); a darker hover (_components.scss,
    14% black) and press (20%) can only improve it, which a white sheen never could (10% white: 2.38:1)."""
    assert contrast("#ffffff", "#23a9f2") >= 2.6
    assert contrast("#ffffff", blend("#23a9f2", "#000000", 0.14)) > contrast("#ffffff", "#23a9f2")
    assert contrast("#ffffff", blend("#23a9f2", "#ffffff", 0.10)) < 2.4


# ---- phase 3: notifications and live signals -----------------------------------------------------------------------

def test_an_unread_notification_has_a_lit_stripe_with_literal_offsets_and_a_spill_only_on_a_dark_page():
    rule = PARTIAL[PARTIAL.index(".notif.is-new {"):]
    rule = rule[:rule.index("}")]
    assert "inset 3px 0 0 var(--glow)" in rule and "inset 14px 0 16px -12px" in rule     # literal x offsets: the mirror flips them
    assert "calc(var(--soft-a) * var(--light-k))" in rule
    assert float(compile_tokens("dark", "e85d04")["--soft-a"].strip().rstrip("%")) < 40       # a warm brand is damped


def test_the_live_marks_are_lit_and_still():
    assert "box-shadow: 0 0 0 3px color-mix(in srgb, var(--danger) calc(20% * var(--light-k)), transparent);" in PARTIAL
    assert "infinite" not in PARTIAL and ".space-live-dot," in PARTIAL
    # a pulse would be a loop: the only animations are the one-shots
    names = set(re.findall(r"animation:\s*([\w-]+) ", PARTIAL))
    assert names == {"light-glint", "light-pop", "light-ramp"}, names


def test_a_count_badge_pops_once_when_the_number_rises_and_the_script_never_pops_on_load():
    js = read("ruqqus", "assets", "js", "chat_badge.js")
    assert "var last = parseInt(badge.textContent, 10) || 0;" in js          # the server's number is the starting point
    assert "if (Math.min(n, 100) > Math.min(last, 100)) pop();" in js and "last = n;" in js      # what it shows: 99+ is one number
    assert 'badge.addEventListener("animationend", function () { badge.classList.remove("is-popped"); });' in js
    assert ".nav-link .badge-count.is-popped {" in PARTIAL and "@keyframes light-pop" in PARTIAL


def test_the_notification_stylesheet_is_still_free_of_animation_and_hex():
    notif = read("ruqqus", "assets", "style", "_notifications.scss")
    assert "animation" not in notif and not re.search(r"#[0-9a-fA-F]{3,8}\b", notif)


# ---- phases 4 and 5: posts, votes, rewards and surfaces ---------------------------------------------------------------

def test_a_cast_vote_is_lit_in_the_upvote_colour_and_a_feed_card_gets_nothing_at_rest():
    rule = PARTIAL[PARTIAL.index(".arrow-up.active:not(.vote-locked), .card .voting .arrow-up.active:not(.vote-locked):hover {"):]
    rule = rule[:rule.index("\n}")]
    assert "var(--upvote)" in rule and "var(--glow)" not in rule                   # a guild's page keeps the upvote blue
    assert "calc(var(--soft-a) * var(--light-k)" in rule                            # a ring and a bloom on a dark page only
    body = re.sub(r"//[^\n]*", "", PARTIAL)
    for selector_list in re.findall(r"([^{};]+)\{", body):
        for part in selector_list.split(","):
            target = re.split(r"[\s>+~]+", part.strip())[-1]                         # the element the rule is ABOUT
            assert not re.match(r"^\.card\b|^\.posts\b", target), part              # nothing on a card (it traps the menu, and the feed only grows)


def test_the_raised_surfaces_catch_a_top_edge_without_losing_their_own_shadow():
    for selector, shadow in ((".dropdown-menu", "--shadow-md"), (".modal-content", "--shadow-lg"), (".toast", "--shadow-lg")):
        assert f"{selector} {{\n    box-shadow: var(--edge), var({shadow});" in PARTIAL, selector
    assert "box-shadow: var(--hairline);" in PARTIAL


def test_nothing_in_the_partial_loops_or_moves_on_load():
    assert "infinite" not in PARTIAL and "alternate" not in PARTIAL
    names = set(re.findall(r"animation:\s*([\w-]+) ", PARTIAL))
    assert names == {"light-glint", "light-pop", "light-ramp"}, names


def test_the_browser_check_for_real_pixels_is_in_scripts_ui():
    spec = read("scripts", "ui", "light.spec.ts")
    assert "toBeGreaterThanOrEqual(4.5)" in spec and "reducedMotion" in spec and "forcedColors" in spec and 'contrast: "more"' in spec
    assert '"light": "playwright test light.spec.ts"' in read("scripts", "ui", "package.json")


# ---- what the second review found ------------------------------------------------------------------------------------

def test_a_pressed_button_is_a_deeper_fill_even_with_focus():
    """A mouse press focuses the button, and Bootstrap's own `:active:focus` rule is as specific as these, so both are listed.
    The press only DARKENS (a white label gains contrast: 2.62:1 on the bare fill, 3.97:1 at 20% black), as every site above."""
    rule = PARTIAL[PARTIAL.index(".btn-primary:not(:disabled):not(.disabled):active, .btn-primary:not(:disabled):not(.disabled):active:focus,"):]
    rule = rule[:rule.index("\n}")]
    assert ".btn-purple:not(:disabled):not(.disabled).active:focus" in rule
    assert "background-color: color-mix(in srgb, var(--brand-fill) 80%, #000);" in rule and "border-color: color-mix(in srgb, var(--brand-fill) 80%, #000);" in rule
    assert "box-shadow" not in rule and "background-image" not in rule
    assert contrast("#ffffff", blend("#23a9f2", "#000000", 0.20)) >= 3.9


def test_a_lit_state_keeps_its_tint_under_the_pointer_and_the_phone_pill_is_not_tinted_twice():
    assert ".post-actions a.post-action.post-action-bookmark.is-on:hover" in PARTIAL
    assert ".card .voting .arrow-up.active:not(.vote-locked):hover" in PARTIAL
    rule = PARTIAL[PARTIAL.index(".vote-pill .arrow-up.active:not(.vote-locked), "):]
    assert "background-color: transparent;" in rule[:rule.index("}")] and "box-shadow: none;" in rule[:rule.index("}")]


def test_the_menus_people_use_most_get_the_edge_and_every_tab_bar_gets_the_rail():
    assert ".dropdown-menu.shadow {\n    box-shadow: var(--edge), var(--shadow-md) !important;" in PARTIAL
    assert ".settings-nav > .nav-item > .nav-link.active {\n    box-shadow: inset 0 -2px 0 var(--brand), var(--rail);" in PARTIAL    # keeps the underline it draws
    assert ".inline-composer .form-control.ic-title:focus-visible {\n    outline: 2px solid var(--link);" in PARTIAL            # the feed composer's title had no focus mark
    # (the Explore chip row's negative side margins, made for a bloom the chips no longer have, pushed it 10px past a phone's edge)
    assert ".explore-chips" not in re.sub(r"//[^\n]*", "", PARTIAL)
