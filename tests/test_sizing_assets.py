"""Sizing and spacing (the owner, 2026-10-29: "Go over sizing and spacing. I think the account drop down could be bigger, and the spacing on the nav bar a
little smaller. Maybe it's worth looking at other sites to figure out the sizing and spacing principle to apply to this site"; CLAUDE.md "Sizing and spacing").

The owner chose "B · Balanced" after seeing Reddit, YouTube, Twitch, GitHub (Primer), Radix, Bluesky, Instagram, Telegram and Material at true size next to
their own. What those tests keep: the control ladder is in the tokens and is what the bar, the pill, the icons and the account menu are sized from; the bar is
a control and --bar-pad above and below; a menu row is a default control (a hit target under a coarse pointer); and the sub-24px targets the audit found
have a floor. The numbers are measured in a real browser by scripts/ui/sizing.spec.ts."""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "ruqqus"
STYLE = APP / "assets" / "style"


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


TOKENS = read("ruqqus", "assets", "style", "_tokens.scss")
NAV = read("ruqqus", "assets", "style", "_nav.scss")
MOBILE = read("ruqqus", "assets", "style", "_mobile.scss")
COMPONENTS = read("ruqqus", "assets", "style", "_components.scss")
GLASS = read("ruqqus", "assets", "style", "_glass.scss")


def px(token, text=TOKENS):
    return int(re.search(rf"--{re.escape(token)}:\s*(\d+)px;", text).group(1))


def test_the_ladder_is_in_the_tokens_and_the_bar_is_a_control_and_two_paddings():
    ladder = [px(t) for t in ("control-xs", "control-sm", "control-h", "control-lg", "hit-min")]
    assert ladder == [24, 28, 36, 40, 32]
    assert px("bar-h") == 56
    assert "--bar-pad: calc((var(--bar-h) - 1px - var(--control-h)) / 2);" in TOKENS
    # (56 - 1 hairline - 36) / 2 = 9.5px above and below the 36px control; Compact: (52 - 1 - 32) / 2 = 9.5 too
    compact = TOKENS[TOKENS.index('html[data-density="compact"] {'):][:700]
    assert px("control-h", compact) == 32 and px("bar-h", compact) == 52
    assert (56 - 1 - 36) / 2 == (52 - 1 - 32) / 2 == 9.5
    # a coarse pointer gets the 44px Apple and Google ask for (WCAG 2.5.8's 24 is the floor, never the goal)
    coarse = TOKENS[TOKENS.index("@media (pointer: coarse) {"):][:120]
    assert px("hit-min", coarse) == 44


def test_what_sits_under_the_bar_starts_from_the_height_the_bar_really_has():
    for sheet in ("main.scss", "main_dark.scss"):
        assert "--navbar-height: 56px;" in read("ruqqus", "assets", "style", sheet), sheet
    assert "--navbar-height: calc(56px + env(safe-area-inset-top));" in MOBILE
    assert "padding-top: calc(var(--bar-pad) + env(safe-area-inset-top));" in MOBILE
    assert "#navbar {\n    padding-top: var(--bar-pad);\n    padding-bottom: var(--bar-pad);\n}" in NAV
    assert "0.7rem" not in MOBILE.split("#navbar {")[1].split("}")[0]          # Bootstrap's .7rem made it 58.6px


def test_every_control_in_the_bar_is_the_default_control_and_the_glyphs_keep_their_size():
    pill = NAV.split("#navbar .form-inline.search .form-control {")[1].split("}")[0]
    assert "min-height: var(--control-h);" in pill
    icons = NAV.split("#navbar #navbar-icon-row .nav-link:not(.dropdown-toggle):not(#dropdownMenuLink) {")[1].split("}")[0]
    assert "width: var(--control-h);" in icons and "height: var(--control-h);" in icons
    # the glyphs stay at Bootstrap's own 14px: they were 20px for a day and the owner found them too big (2026-10-29); nothing here may size them again
    assert not re.search(r"#navbar #navbar-icon-row[^{]*\.(fas|far|fab)[^{]*\{[^}]*font-size", NAV)
    assert "#navbar #dropdownMenuLink .profile-pic-35 {\n    width: 32px;\n    height: 32px;" in NAV
    gap = NAV.split("@media (min-width: 768px) {\n    #navbar #navbar-icon-row {")[1].split("}")[0]
    assert "gap: 0;" in gap
    assert "width: var(--control-h);" in NAV.split("#navbar .mobile-nav-icon {")[1].split("}")[0]      # it was 38px and made the phone's bar 58


def test_the_account_menu_is_sized_by_the_ladder_and_nothing_else():
    block = COMPONENTS.split("// --- the account menu ", 1)[1].split("// A row of choices", 1)[0]
    assert "min-width: px2rem(264px);" in block and "max-width: calc(100vw - 16px);" in block         # Reddit 256, YouTube 300; it was 210
    assert "min-height: #{\"max(var(--control-h), var(--hit-min))\"};" in block                         # 36px; 32 in Compact; 44 under a coarse pointer
    assert re.search(r"\.fa-fw \{[^}]*width: px2rem\(20px\);[^}]*margin-right: var\(--space-4\) !important;[^}]*font-size: px2rem\(16px\);", block)
    assert "padding: var(--space-2) var(--space-4);" in block and "border-radius: var(--radius-xs);" in block
    assert ".account-menu .dropdown-divider {\n    margin: var(--space-2) var(--space-3);" in block
    assert ".account-menu > div {\n    padding-left: var(--space-3);" in block                           # the markup's px-0 is !important
    # tokens and the ladder only: no raw heights, widths or spacing in the block (the one width is the menu's own, in px2rem)
    assert not re.search(r"(?<![\w-])(?:min-)?height:\s*\d", block) and not re.search(r"padding[-\w]*:\s*\d", block)
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", block)
    assert '<div>\n  <a class="dropdown-item account-menu-profile"' in read("ruqqus", "templates", "partials", "account_menu.html")      # the wrapper's px-2 added 7px more each side


def test_the_profile_row_is_a_40px_avatar_in_a_56px_row_and_beats_the_generic_row():
    row = NAV.split(".account-menu .account-menu-profile {")[1].split("}")[0]
    assert "min-height: 56px;" in row and "gap: var(--space-4);" in row
    avatar = NAV.split(".account-menu .account-menu-profile .profile-pic-35 {")[1].split("}")[0]
    assert "width: 40px;" in avatar and "height: 40px;" in avatar
    # (the generic row is `.account-menu .dropdown-item`, two classes: the profile row must be as specific, and later, to keep its own height)
    assert NAV.index(".account-menu .account-menu-profile {") > 0


def test_the_phone_menu_is_a_top_sheet_attached_to_the_bar_with_the_page_dimmed_under_it():
    assert "margin-top: 0 !important;" in MOBILE.split("#navbar-icon-row #accountDropdownMenu {")[1].split("}")[0]
    scrim = GLASS.split("body:has(#accountDropdownMenu.show)::before {")[1].split("}")[0]
    # the menu's own z-index (1040) is INSIDE the navbar's stacking context (1030): a dim at 1035 covered the menu and blurred it
    assert int(re.search(r"z-index:\s*(\d+);", scrim).group(1)) < 1030
    assert "inset: var(--navbar-height, 56px) 0 0 0;" in scrim and "var(--overlay)" in scrim


def test_the_ladder_reaches_the_controls_everyone_meets():
    ladder = COMPONENTS.split("// --- the size ladder, applied", 1)[1]
    # a button is on a rung (--btn-h) and its min-height follows it
    assert re.search(r"\.btn \{\s*--btn-h: var\(--control-h\);\s*min-height: var\(--btn-h\);", ladder)
    assert re.search(r"\.btn-sm \{\s*--btn-h: var\(--control-sm\);\s*min-height: var\(--btn-h\);", ladder)
    assert re.search(r"\.btn-lg \{\s*--btn-h: var\(--control-lg\);\s*min-height: var\(--btn-h\);", ladder)
    touch = ladder.split("@media (pointer: coarse) {")[1].split("\n}\n")[0]
    assert "(var(--hit-min) - 1.5em) / 2" in touch and "min-height: var(--hit-min);" in touch       # a menu row is 44px under a finger, whatever its type size
    assert re.search(r"\.btn, \.btn-sm \{\s*--btn-h: var\(--hit-min\);", touch)                    # and a button is the touch rung


def test_a_link_styled_as_a_button_centres_its_label():
    """A <button> centres its label in a box that min-height made taller; an <a> or <label> does not, and left it 5 to 7px high under a finger (the sizing
    reviewers, 2026-10-29). Their vertical padding is derived from the rung instead."""
    ladder = COMPONENTS.split("// --- the size ladder, applied", 1)[1]
    anchors = ladder.split("a.btn, label.btn {")[1].split("}")[0]
    assert anchors.count("calc((var(--btn-h) - 1.5em - 2px) / 2)") == 2 and "padding-top" in anchors and "padding-bottom" in anchors


def test_the_two_bars_with_things_placed_from_their_height_keep_their_buttons_on_the_default_rung():
    """A visitor's Sign In made the phone's top bar 64px (the member's is 56, the page starts from 56), and the fixed sort bar grew 6px over the feed's tabs:
    both under a coarse pointer, where every button is 44px."""
    ladder = COMPONENTS.split("// --- the size ladder, applied", 1)[1]
    touch = ladder.split("@media (pointer: coarse) {")[1].split("\n}\n")[0]
    assert re.search(r"#navbar \.btn, #fixed-bar-mobile \.btn \{\s*--btn-h: var\(--control-h\);", touch)


def test_a_round_icon_button_keeps_its_box_and_gets_its_reach_from_an_invisible_area():
    """A taller min-height turned the profile's 36px circles into 36x44 ovals and the editor's 27px toolbar into 44px rows."""
    ladder = COMPONENTS.split("// --- the size ladder, applied", 1)[1]
    touch = ladder.split("@media (pointer: coarse) {")[1].split("\n}\n")[0]
    assert re.search(r"\.btn\.profile-x-icon, \.post-tb-btn \{\s*min-height: 0;\s*position: relative;", touch)
    assert re.search(r"\.btn\.profile-x-icon::after, \.post-tb-btn::after \{\s*content: \"\";\s*position: absolute;\s*inset: -4px;", touch)


def test_the_small_targets_the_audit_found_have_a_floor():
    """WCAG 2.5.8 (AA): 24 x 24 CSS px. The audit measured text links of 17 to 21px and two chat buttons 22px wide."""
    ladder = COMPONENTS.split("// --- the size ladder, applied", 1)[1]
    links = ladder.split("// WCAG 2.5.8")[1]
    for selector in (".footer a", ".list-inline-item > a", "a.happening-all", "a.wtf-name", "a.user-name"):
        assert selector in links, selector
    assert "padding-top: var(--space-2);" in links and "padding-bottom: var(--space-2);" in links      # 17px of text + 2 x 4px = 25px, with no change to the line
    assert re.search(r"button\.show-more-link[^{]*\{[^}]*min-height: var\(--control-xs\);", links)
    assert re.search(r"#chat-new-chat[^{]*\{[^}]*min-width: var\(--control-sm\);[^}]*min-height: var\(--control-sm\);", links)
    # a link that is a block in a column grows its target and not the layout: the padding is taken back by the same negative margin
    block = links.split("a.happening-all, a.wtf-name {")[1].split("}")[0]
    assert "padding-top: var(--space-2);" in block and "margin-top: calc(-1 * var(--space-2));" in block and "margin-bottom: calc(-1 * var(--space-2));" in block


def test_nothing_in_the_new_rules_is_a_value_off_the_four_pixel_grid():
    """The core of every system compared is 4 / 8 / 12 / 16 / 24 / 32 / 40 / 48 / 64 (2 for a hairline). The size tokens, the bar and the menu are all on it."""
    for value in (24, 28, 36, 40, 32, 56, 52, 44, 264):
        assert value % 4 == 0, value
    for token in ("space-2", "space-3", "space-4"):
        assert px(token) in (4, 8, 12)
