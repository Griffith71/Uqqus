"""Icons come from one font the site ships, so a PC and a Mac draw the same thing.

The site loads Font Awesome 6.4.0 FREE from cdnjs (templates/default.html). A class that only the Pro set has draws as
nothing, and a font the site never loads ("Font Awesome 5 Pro") or a symbol character (a shape, an arrow, an emoji)
is drawn by whatever font each operating system picks. `tests/data/fa_free_6_4_0_names.txt` is every `fa-*` name in
that stylesheet (regenerate it from https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAMES = set((ROOT / "tests" / "data" / "fa_free_6_4_0_names.txt").read_text(encoding="utf-8").split())
BS = chr(92)

# classes the markup writes that no stylesheet defines: harmless, and not icons
NOT_ICONS = {"rotate--45"}          # a pin's tilt; only the unused board_*.scss define it


def sources():
    templates = list((ROOT / "ruqqus" / "templates").rglob("*.html"))
    scripts = [p for p in (ROOT / "ruqqus" / "assets" / "js").glob("*.js") if "min" not in p.name and "bundle" not in p.name]
    return templates + scripts


def live_styles():
    return [ROOT / "ruqqus" / "assets" / "style" / name for name in ("main.scss", "main_dark.scss")]


def test_the_list_is_the_free_set_the_site_loads():
    default = (ROOT / "ruqqus" / "templates" / "default.html").read_text(encoding="utf-8")
    assert "font-awesome/6.4.0/css/all.min.css" in default
    assert len(NAMES) > 2000 and {"xmark", "circle-plus", "chevron-left", "bell-slash", "binoculars"} <= NAMES
    assert not {"telescope", "bell-on", "comment-alt-smile", "arrow-alt-up", "lock-alt"} & NAMES    # Pro only


def test_every_icon_class_exists_in_the_font_the_site_loads():
    missing = {}
    for path in sources():
        text = path.read_text(encoding="utf-8", errors="ignore")
        for found in re.finditer(r"(?<![A-Za-z0-9_-])fa-([a-z0-9-]+)", text):
            name = found.group(1)
            if name.endswith("-") or name in NAMES or name in NOT_ICONS:      # "fa-align-" + x is built in a script
                continue
            missing.setdefault(name, set()).add(path.name)
    assert not missing, {name: sorted(where) for name, where in missing.items()}


def test_no_live_stylesheet_asks_for_a_font_the_site_never_loads():
    for path in live_styles():
        text = path.read_text(encoding="utf-8")
        assert "Font Awesome 5 Pro" not in text, path.name
        for family in re.findall(r'font-family:\s*"(Font Awesome[^"]*)"', text):
            assert family == "Font Awesome 6 Free", (path.name, family)


def test_the_sidebar_fold_toggle_and_the_collapse_plus_are_drawn_from_the_font():
    for path in live_styles():
        text = path.read_text(encoding="utf-8")
        for glyph in (BS + "25C0", BS + "25B6"):
            assert glyph not in text, (path.name, glyph)                      # emoji on Apple, a box elsewhere
        toggle = text.split(".sidebar-left .collapsed-toggle::before")[1].split("}")[0]
        assert BS + "f053" in toggle and '"Font Awesome 6 Free"' in toggle and "font-weight: 900" in toggle
        folded = text.split(".sidebar-left.sidebar-collapsed .collapsed-toggle::before")[1].split("}")[0]
        assert BS + "f054" in folded and '"Font Awesome 6 Free"' in folded
        collapsed = text.split(".comment.collapsed .comment-collapse::before")[1].split("}")[0]
        assert BS + "f055" in collapsed and '"Font Awesome 6 Free"' in collapsed


def test_no_symbol_character_stands_in_for_an_icon():
    # arrows, shapes, dingbats, misc symbols and emoji are drawn by each system's own font
    ranges = [(0x2190, 0x21FF), (0x2300, 0x23FF), (0x25A0, 0x25FF), (0x2600, 0x27BF), (0x2900, 0x2BFF), (0x1F000, 0x1FAFF)]
    allowed_in_prose = {0x22EF}          # "⋯" appears in code comments naming the profile menu, never drawn
    found = {}
    for path in sources() + live_styles():
        for char in path.read_text(encoding="utf-8", errors="ignore"):
            code = ord(char)
            if code not in allowed_in_prose and any(a <= code <= b for a, b in ranges):
                found.setdefault("U+%04X" % code, set()).add(path.name)
    assert not found, {code: sorted(where) for code, where in found.items()}


def test_a_removed_post_shows_an_icon_not_a_character():
    for name in ("submission.html", "submission_banned.html", "submission_listing.html"):
        source = (ROOT / "ruqqus" / "templates" / name).read_text(encoding="utf-8")
        assert "✖" not in source and 'class="fas fa-xmark"' in source, name


def test_the_notification_bell_swaps_two_icons_that_exist():
    script = (ROOT / "ruqqus" / "assets" / "js" / "all_js.js").read_text(encoding="utf-8")
    toggle = script.split("$('.bell-button').click(")[1].split("});")[0]
    assert "toggleClass('fa-bell')" in toggle and "toggleClass('fa-bell-slash')" in toggle and "fa-bell-on" not in toggle
    for name in ("board.html", "partials/profile_header.html"):
        source = (ROOT / "ruqqus" / "templates" / name).read_text(encoding="utf-8")
        assert "fa-bell-on" not in source and "fa-bell text-" in source and "fa-bell-slash" in source, name
