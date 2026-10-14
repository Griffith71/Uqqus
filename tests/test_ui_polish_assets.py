"""The interface polish of 2026-10-10 (the design-guidelines audit and emil-design-eng's motion rules) on the core
surfaces: keyboard focus, contrast in the light theme, touch targets, press feedback and the dialogs, sheets, menus
and toasts. Measured with Playwright on the Docker site; these checks keep the stylesheets from drifting back."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "ruqqus"
STYLES = {name: (ROOT / "assets" / "style" / name).read_text(encoding="utf-8") for name in ("main.scss", "main_dark.scss")}
LIGHT = STYLES["main.scss"]


def polish(css):
    return css.split("// Interface polish (2026-10-10)")[1]


def test_both_stylesheets_carry_the_polish():
    for name, css in STYLES.items():
        assert "// Interface polish (2026-10-10)" in css, name


def test_keyboard_focus_is_always_visible():
    for name, css in STYLES.items():
        assert "body:not(.using-mouse) :focus-visible:not(.form-control)" in polish(css), name


def test_motion_is_feedback_only_and_respects_reduced_motion():
    for name, css in STYLES.items():
        block = polish(css)
        assert not re.search(r"transition:\s*all", css), name
        motion = block.split("@media (prefers-reduced-motion: no-preference) {", 1)[1]
        assert ".btn:active:not(:disabled):not(.disabled)" in motion and "scale(0.97)" in motion, name
        assert ".modal.fade .modal-dialog" in motion and "scale(0.96)" in motion, name     # never from scale(0)
        assert "animation: menu-fade-in" in motion, name                                   # opacity only: Popper owns transform
        assert "@media (prefers-reduced-motion: reduce)" in block, name
        assert "$ease-drawer" in block and "translate3d(0, 100%, 0)" in block, name        # the phone sheet
        for duration in re.findall(r"(\d+)ms", block):
            assert int(duration) <= 300, (name, duration)


def test_hover_only_where_a_pointer_hovers_and_touch_targets_grow():
    for name, css in STYLES.items():
        block = polish(css)
        assert "@media (hover: none)" in block and "@media (hover: hover)" in block, name
        coarse = block.split("@media (pointer: coarse) {")[1].split("\n}\n")[0]
        assert "inset: -12px -8px;" in coarse and ":not(.dropdown-toggle)" in coarse, name


def test_light_theme_text_passes_contrast():
    assert "$link-color: darken($primary, 20%);" in LIGHT.split('@import "{ruqquspath}/assets/style/node_modules/bootstrap')[0]
    assert "$muted: #5f6e82;" in LIGHT and "$gray-600: #5f6e82;" in LIGHT
    for needle in ("a.text-gray-500 {", ".badge.text-warning {", "a.text-warning, button.text-warning", ".score-up {"):
        assert needle in polish(LIGHT), needle
    # the dark theme keeps the brand blue on near-black
    assert "$link-color" not in STYLES["main_dark.scss"]


def test_no_icon_from_the_pro_only_sets():
    # Font Awesome 6 Free (cdnjs) draws a box for these: regular-only names and the light/duotone styles
    pro_regular = ("ban", "check", "cloud", "code", "crown", "exclamation-triangle", "feather-alt", "history", "link",
                   "long-arrow-right", "plus", "sync", "thumbtack", "times")
    pat = re.compile(r"\bfar fa-(%s)(?![a-z0-9-])" % "|".join(pro_regular))
    files = list((ROOT / "templates").rglob("*.html")) + [p for p in (ROOT / "assets" / "js").glob("*.js")
                                                          if p.name != "chat_bundle.js"]
    for path in files:
        src = path.read_text(encoding="utf-8")
        assert not pat.search(src), path.name
        assert not re.search(r"\b(fal|fad) fa-", src), path.name
