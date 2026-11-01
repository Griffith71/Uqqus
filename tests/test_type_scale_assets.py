"""One type scale across the site (the owner, 2026-10-30: "I want the text sizing to be consistent across the website"). _tokens.scss defines the
steps (12 / 13 / 14 / 15 / 18 / 22px at the 14px root); about 25 legacy rules had been written for a 16px root and came out at 9.6, 10.5, 11,
11.4, 11.7, 12.25, 13.1, 15.75, 16 and 17.5px. _type_scale.scss snaps each to its nearest step, after the legacy rules; the right sidebar's box
titles are the left sidebar's small uppercase label. What these keep, without a browser: that the partial is in both sheets in the one place
that works, that it uses tokens only, that it covers the rules the audit found, and that the label style has one letter-spacing. The sizes
themselves, on every page, are measured by scripts/ui/type_scale.spec.ts."""
import re
from pathlib import Path

import pytest

STYLE = Path(__file__).resolve().parent.parent / "ruqqus" / "assets" / "style"
SHEETS = ("main.scss", "main_dark.scss")
PARTIAL = (STYLE / "_type_scale.scss").read_text(encoding="utf-8")
TOKENS = (STYLE / "_tokens.scss").read_text(encoding="utf-8")
NAV = (STYLE / "_nav.scss").read_text(encoding="utf-8")
HAPPENING = (STYLE / "_happening.scss").read_text(encoding="utf-8")

STEPS = {"--text-xs": 12, "--text-sm": 13, "--text-base": 14, "--text-body": 15, "--text-title": 18, "--text-title-lg": 22}

# every legacy rule the audit (26 pages, each text element traced to its winning rule) found off the steps, and the step it takes
SNAPPED = {
    "--text-xs": [".circle-tag", ".post-type-choice", ".audience-note", ".composer-count", ".composer-tool-text", ".explore-meta", ".explore-chip",
                  ".explore-desc", ".space-card-meta", ".space-card-rec", ".space-tab", ".where-chip", ".audience-hint", ".audience-title",
                  ".audience-manage", ".composer-pill > summary", ".inline-composer .ic-head .composer-pill > summary",
                  ".composer-who .composer-where-pill > summary", ".user-name .user-handle"],
    "--text-sm": [".explore-lead", ".explore-empty", ".search-check", ".composer-more > summary"],
    "--text-body": [".explore-section-title"],
    "--text-title": ["h5", ".h5", ".profile-x-name"],
}


def rule_for(selector):
    """The declarations of the rule in the partial whose selector list holds `selector`."""
    for block in re.finditer(r"([^{}/]+?)\{([^}]*)\}", re.sub(r"//[^\n]*", "", PARTIAL)):
        if selector in [s.strip() for s in block.group(1).split(",")]:
            return block.group(2)
    raise AssertionError(f"{selector} is in no rule of _type_scale.scss")


def test_the_steps_are_the_tokens_the_site_already_defines():
    for token, px in STEPS.items():
        want = "1rem" if token == "--text-base" else f"calc({px}rem / 14)"
        assert f"{token}: {want};" in TOKENS, token


@pytest.mark.parametrize("step, selectors", SNAPPED.items())
def test_every_stray_rule_the_audit_found_takes_its_nearest_step(step, selectors):
    for selector in selectors:
        assert re.search(rf"font-size: var\({re.escape(step)}\);", rule_for(selector)), (selector, step)


def test_the_partial_uses_tokens_only():
    body = re.sub(r"//[^\n]*", "", PARTIAL)
    assert not re.search(r"font-size:\s*[\d.]+(px|rem|em|%)", body), "a size in the partial is a token, never a number"
    assert body.count("font-size:") == len(re.findall(r"font-size: var\(--text-[\w-]+\);", body))
    assert "!important" not in body and "@media" not in body              # it wins by order, and a phone rule (_mobile.scss) may still set its own


@pytest.mark.parametrize("sheet", SHEETS)
def test_both_sheets_import_it_where_it_works(sheet):
    imports = re.findall(r'@import "\{ruqquspath\}/assets/style/(_[\w-]+)";', (STYLE / sheet).read_text(encoding="utf-8"))
    assert imports.count("_type_scale") == 1, sheet
    # after every partial that sets a legacy size it replaces, before the motion and phone partials (a phone rule can still set its own size),
    # and so before the chain _light > _glass > _text_size > _post_rail that other tests keep in order
    assert imports.index("_screens") < imports.index("_type_scale") < imports.index("_motion") < imports.index("_mobile") < imports.index("_light"), (sheet, imports)


@pytest.mark.parametrize("sheet", SHEETS)
def test_the_smallest_text_class_is_12px_in_both_sheets(sheet):
    css = (STYLE / sheet).read_text(encoding="utf-8")
    assert '$small-font-size: #{"calc(12rem / 14)"};' in css and '$extra-small-font-size: #{"calc(12rem / 14)"};' in css


def test_the_right_sidebars_box_titles_are_the_left_sidebars_label():
    # 12px, semibold, grey capitals: the left sidebar's headings (_nav.scss `.sidebar-head-title` with `.text-small`) and the Post type group labels
    title = NAV.split("#main-content-row > .sidebar:not(.sidebar-left) .sidebar-section .title {", 1)[1].split("}", 1)[0]
    for declaration in ("color: var(--text-muted);", "font-size: var(--text-xs);", "font-weight: var(--weight-semibold);",
                        "letter-spacing: var(--tracking-label);", "text-transform: uppercase;"):
        assert declaration in title, declaration


def test_every_small_uppercase_label_has_one_letter_spacing():
    assert "--tracking-label: 0.025rem;" in TOKENS
    assert "letter-spacing: var(--tracking-label);" in NAV.split("#sidebar-left .sidebar-head-title {", 1)[1].split("}", 1)[0]
    head = HAPPENING.split(".happening-box .happening-head {", 1)[1].split("}", 1)[0]
    assert "letter-spacing: var(--tracking-label);" in head and "text-transform: uppercase;" in head and "font-size: var(--text-xs);" in head
    assert "0.04em" not in HAPPENING


def test_what_is_left_off_the_scale_is_said_in_the_partial():
    # the heading ladder above 22px, code, badges and the logo are proportions or a ladder of their own; a reader of the partial is told
    for left_alone in ("page titles above 22px", "the logo", "a badge's 75%", "code's 87.5%"):
        assert left_alone in PARTIAL.replace("\n// ", " "), left_alone
